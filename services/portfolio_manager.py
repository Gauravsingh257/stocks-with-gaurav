"""
services/portfolio_manager.py

Central portfolio manager — maintains two persistent buckets:
  - SWING (max 10 positions)
  - LONGTERM (max 10 positions)

Stocks stay until SL/Target hit or manual close. No auto-expiry.
Integrates with trade_tracker for live price updates.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("services.portfolio_manager")

# Single source of truth for portfolio capacity lives in db/portfolio.py
# (env PORTFOLIO_MAX_SWING / PORTFOLIO_MAX_LONGTERM, default 20). This module
# previously had its OWN duplicate cap (MAX_SWING_PORTFOLIO=10), so raising one
# without the other left promotion silently blocked. Import the canonical
# values so there is exactly one knob.
from dashboard.backend.db.portfolio import (  # noqa: E402
    MAX_SWING_POSITIONS as MAX_SWING,
    MAX_LONGTERM_POSITIONS as MAX_LONGTERM,
)


def promote_to_portfolio(
    symbol: str,
    horizon: str,
    entry_price: float,
    stop_loss: float,
    target_1: float | None = None,
    target_2: float | None = None,
    confidence_score: float = 0.0,
    reasoning: str = "",
    recommendation_id: int | None = None,
    current_price: float | None = None,
    pending: bool = True,
) -> int:
    """
    Promote a recommendation/signal into the persistent portfolio.
    Returns position ID or raises ValueError if the book is full or the symbol
    is already committed (ACTIVE or armed PENDING).

    ARM-ON-TAP: `pending=True` (default for the AUTOMATED promotion paths) arms
    the idea as PENDING — it does NOT become a live position and accrues NO P&L
    until its planned entry is genuinely traded through (the price tracker flips
    it to ACTIVE on the real tap). `pending=False` is a genuine manual entry
    (e.g. the /add endpoint) that goes live immediately.

    STRICT SLOT CONTROL: an ACTIVE or PENDING position both hold a slot, so the
    book NEVER commits more than MAX per horizon.
    """
    from dashboard.backend.db.portfolio import add_position, get_portfolio_counts, reentry_guard_blocks

    horizon = horizon.upper()
    max_pos = MAX_SWING if horizon == "SWING" else MAX_LONGTERM

    # Pre-check: strict slot enforcement against COMMITTED slots (active+armed).
    counts = get_portfolio_counts()
    used = counts.get(f"{horizon.lower()}_used", counts.get(horizon.lower(), 0))
    if used >= max_pos:
        raise ValueError(f"{horizon} portfolio full ({used}/{max_pos}) — close a position first")

    # Defense-in-depth: setup-aware re-entry guard. Blocks re-promotion of the
    # same failed setup (same entry, recently structure-broken/stopped, still
    # below 200-DMA); a genuinely new setup passes. The selectors filter these
    # earlier, but this catches any other promotion path (manual/agent).
    if reentry_guard_blocks(symbol, horizon, float(entry_price), cmp=current_price):
        raise ValueError(f"{symbol} re-entry blocked — same failed setup within cooldown")

    # Risk engine: stop-width cap + risk-normalized (liquidity/ATR-adjusted)
    # sizing. Fully flag-gated — with RISK_ENGINE_ENABLED=0 it accepts every
    # valid long at equal weight (legacy behavior). Every decision is logged.
    _rsize: dict = {}
    try:
        from services.risk_engine import evaluate_promotion
        decision = evaluate_promotion(symbol, horizon, float(entry_price), float(stop_loss))
        if not decision.accepted:
            raise ValueError(f"{symbol} rejected by risk engine: {decision.reason}")
        _rsize = {
            "position_size": decision.new_position_value,
            "risk_weight_pct": decision.risk_weight_pct,
            "atr_pct": decision.atr_pct,
            "turnover_cr": decision.turnover_cr,
        }
    except ValueError:
        raise
    except Exception:
        log.exception("risk engine promotion eval failed for %s — proceeding unsized", symbol)

    position_id = add_position({
        "symbol": symbol,
        "horizon": horizon,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "confidence_score": confidence_score,
        "reasoning": reasoning,
        "recommendation_id": recommendation_id,
        "current_price": current_price or entry_price,
        "arm_ref_price": current_price or entry_price,
        "status": "PENDING" if pending else "ACTIVE",
        **_rsize,
    })

    # Telegram alert. Armed → "awaiting entry" (no fill claimed); live entry →
    # the existing "Portfolio Entry" alert. The genuine fill alert for an armed
    # idea is sent by the tracker when it actually triggers. Best-effort.
    try:
        payload = {
            "position_id": position_id, "symbol": symbol, "horizon": horizon,
            "entry_price": entry_price, "stop_loss": stop_loss,
            "target_1": target_1, "target_2": target_2,
            "confidence_score": confidence_score,
        }
        if pending:
            _send_portfolio_armed_alert(payload)
        else:
            _send_portfolio_entry_alert(payload)
    except Exception:
        log.exception("Failed to send %s alert for position %s (%s)",
                      "armed" if pending else "entry", position_id, symbol)

    return position_id


def close_portfolio_position(position_id: int, exit_price: float, reason: str = "MANUAL") -> dict:
    """
    Close a position and journal it. Sends Telegram alert + triggers replacement scan.
    Returns journal entry dict.
    """
    from dashboard.backend.db.portfolio import close_position, get_position_by_id

    pos = get_position_by_id(position_id)
    if not pos:
        raise ValueError(f"Position {position_id} not found")

    result = close_position(position_id, exit_price, reason)

    # Telegram alert (non-blocking)
    try:
        _send_portfolio_exit_alert(result)
    except Exception:
        log.exception("Failed to send exit alert for position %d", position_id)

    # Trigger replacement scan if slot opened
    try:
        from services.trade_tracker import _trigger_replacement_scan
        _trigger_replacement_scan(pos["horizon"], [pos["symbol"]])
    except Exception:
        log.debug("Replacement scan trigger skipped")

    return result


def auto_promote_from_recommendations() -> int:
    """Arm empty slots from genuinely-actionable ideas (both horizons).

    Delegates to the gated select_and_promote so there is exactly ONE promotion
    policy: arm-on-tap from Final Trade Ideas, no ungated force-fill. A slot with
    no qualifying candidate stays vacant.
    """
    from services.idea_selector import select_and_promote

    promoted = select_and_promote("SWING") + select_and_promote("LONGTERM")
    if promoted:
        log.info("Armed %d positions into portfolio (awaiting entry)", promoted)
    return promoted


def get_portfolio_summary() -> dict:
    """Full portfolio state for API consumption.

    `positions` includes armed (PENDING) rows so the UI can show 'Awaiting Entry',
    but `count` is the LIVE (ACTIVE) count and `journal_stats` come only from
    closed trades — so armed ideas never touch analytics, hit-rate, or return.
    `pending`/`used` expose the armed count and committed-slot total.
    """
    from dashboard.backend.db.portfolio import get_portfolio, get_portfolio_counts, get_journal_stats

    swing = get_portfolio("SWING", include_pending=True)
    longterm = get_portfolio("LONGTERM", include_pending=True)
    counts = get_portfolio_counts()
    swing_stats = get_journal_stats("SWING")
    longterm_stats = get_journal_stats("LONGTERM")
    overall_stats = get_journal_stats()

    return {
        "swing": {
            "positions": swing,
            "count": counts["swing"],
            "pending": counts["swing_pending"],
            "used": counts["swing_used"],
            "max": counts["swing_max"],
            "journal_stats": swing_stats,
        },
        "longterm": {
            "positions": longterm,
            "count": counts["longterm"],
            "pending": counts["longterm_pending"],
            "used": counts["longterm_used"],
            "max": counts["longterm_max"],
            "journal_stats": longterm_stats,
        },
        "overall_stats": overall_stats,
    }


def _send_portfolio_entry_alert(position: dict) -> None:
    """Send Telegram notification on portfolio ENTRY.

    Added 2026-05-25 after user reported missing alerts. Previously the
    portfolio_manager sent Telegram on close_position only — entries
    (manual + auto-promoted from SwingTradeAlphaAgent) went silent, so
    when 8 positions accumulated in the swing portfolio over the week
    the user had never been pinged on any of them.

    Posts to the same TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID (MTF Alerts)
    that exits use. Best-effort; never raises — promotion already
    committed before this is called."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("SMC_PRO_CHAT_ID", "")
    if not bot_token or not chat_id:
        return

    horizon = position.get("horizon", "SWING").upper()
    horizon_emoji = "📈" if horizon == "SWING" else "🌱"
    entry = position.get("entry_price", 0)
    sl = position.get("stop_loss", 0)
    t1 = position.get("target_1")
    t2 = position.get("target_2")
    conf = position.get("confidence_score", 0)

    # Risk-reward on T1 (fall back to T2 if T1 absent)
    rr_target = t1 if t1 is not None else t2
    rr = None
    if rr_target and entry and sl and (entry - sl) > 0:
        rr = round((rr_target - entry) / (entry - sl), 2)

    lines = [
        f"{horizon_emoji} <b>Portfolio Entry — {horizon}</b>",
        "",
        f"<b>{position['symbol']}</b>",
        f"Entry: ₹{entry:.2f}",
        f"SL: ₹{sl:.2f}",
    ]
    if t1 is not None:
        lines.append(f"Target 1: ₹{t1:.2f}")
    if t2 is not None:
        lines.append(f"Target 2: ₹{t2:.2f}")
    if rr is not None:
        lines.append(f"R:R: 1:{rr}")
    if conf:
        lines.append(f"Confidence: {conf:.0f}/100")

    msg = "\n".join(lines)

    import requests
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception:
        log.warning("Portfolio entry alert: Telegram post failed (best-effort)")


def _send_portfolio_armed_alert(position: dict) -> None:
    """Telegram notification when an idea is ARMED (awaiting entry) — no fill yet.

    OFF by default. An armed idea is not an entry signal: it only says the system
    is watching a level, and most arms expire without ever triggering, so these
    flooded the channel with messages that required no action and often described
    a position that never existed. The actionable alerts are unaffected — the fill
    alert is sent by the tracker when the entry is genuinely traded through
    (send_portfolio_triggered_alert), and a manual/live entry still sends
    _send_portfolio_entry_alert.

    Set PORTFOLIO_ARMED_ALERTS=1 to turn them back on; no redeploy needed.
    """
    if os.getenv("PORTFOLIO_ARMED_ALERTS", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("SMC_PRO_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    horizon = position.get("horizon", "SWING").upper()
    entry = position.get("entry_price", 0)
    sl = position.get("stop_loss", 0)
    t1 = position.get("target_1")
    lines = [
        f"⏳ <b>Armed — Awaiting Entry ({horizon})</b>",
        "",
        f"<b>{position['symbol']}</b>",
        f"Planned entry: ₹{entry:.2f}",
        f"SL: ₹{sl:.2f}" + (f" · T1: ₹{t1:.2f}" if t1 is not None else ""),
        "<i>Enters only if price trades through the entry. No P&amp;L until then.</i>",
    ]
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                      json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML"},
                      timeout=10)
    except Exception:
        log.warning("Portfolio armed alert: Telegram post failed (best-effort)")


def send_portfolio_triggered_alert(symbol: str, horizon: str, entry_price: float,
                                   trigger_price: float, stop_loss: float,
                                   target_1: float | None = None) -> None:
    """Telegram notification when an armed idea's entry ACTUALLY triggers (the
    real fill). Called by the price tracker on arm→active. Best-effort."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("SMC_PRO_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    lines = [
        f"✅ <b>Entry Triggered ({horizon.upper()})</b>",
        "",
        f"<b>{symbol}</b>",
        f"Entry ₹{entry_price:.2f} traded through (now ₹{trigger_price:.2f})",
        f"SL: ₹{stop_loss:.2f}" + (f" · T1: ₹{target_1:.2f}" if target_1 is not None else ""),
    ]
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                      json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML"},
                      timeout=10)
    except Exception:
        log.warning("Portfolio triggered alert: Telegram post failed (best-effort)")


def _send_portfolio_exit_alert(result: dict) -> None:
    """Send Telegram notification on portfolio exit."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("SMC_PRO_CHAT_ID", "")
    if not bot_token or not chat_id:
        return

    emoji = "🎯" if result.get("exit_reason") == "TARGET_HIT" else "🛑"
    pl_sign = "+" if result.get("pnl_pct", 0) >= 0 else ""

    msg = (
        f"{emoji} <b>Portfolio Exit — {result.get('horizon', 'SWING')}</b>\n\n"
        f"<b>{result['symbol']}</b>\n"
        f"Entry: ₹{result['entry']:.2f}\n"
        f"Exit: ₹{result['exit']:.2f}\n"
        f"P&L: {pl_sign}{result['pnl_pct']:.2f}%\n"
        f"Reason: {result.get('exit_reason', 'MANUAL')}\n"
    )

    import requests
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
    }, timeout=10)
