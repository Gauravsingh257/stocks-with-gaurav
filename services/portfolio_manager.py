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

MAX_SWING = int(os.getenv("MAX_SWING_PORTFOLIO", "10"))
MAX_LONGTERM = int(os.getenv("MAX_LONGTERM_PORTFOLIO", "10"))


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
) -> int:
    """
    Promote a recommendation/signal into the persistent portfolio.
    Returns position ID or raises ValueError if portfolio is full or symbol already active.

    STRICT SLOT CONTROL: Will NEVER exceed MAX positions. Only adds when a slot is free.
    """
    from dashboard.backend.db.portfolio import add_position, get_portfolio_counts

    horizon = horizon.upper()
    max_pos = MAX_SWING if horizon == "SWING" else MAX_LONGTERM

    # Pre-check: strict slot enforcement (add_position also checks, this is defense-in-depth)
    counts = get_portfolio_counts()
    current = counts.get(horizon.lower(), 0)
    if current >= max_pos:
        raise ValueError(f"{horizon} portfolio full ({current}/{max_pos}) — close a position first")

    return add_position({
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
    })


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
    """
    Auto-promote top recommendations into empty portfolio slots using the scoring engine.
    Called by agents after a scan, or by the tracking loop.

    STRICT SLOT CONTROL: rechecks capacity before each promotion to prevent races.
    """
    from services.idea_selector import select_swing_ideas, select_longterm_ideas

    promoted = 0

    for horizon, selector in [("SWING", select_swing_ideas), ("LONGTERM", select_longterm_ideas)]:
        ideas = selector()  # already respects slot limits
        for idea in ideas:
            try:
                promote_to_portfolio(
                    symbol=idea["symbol"],
                    horizon=horizon,
                    entry_price=idea["entry_price"],
                    stop_loss=idea["stop_loss"],
                    target_1=idea.get("target_1"),
                    target_2=idea.get("target_2"),
                    confidence_score=idea.get("confidence_score", 0),
                    reasoning=idea.get("reasoning", ""),
                    recommendation_id=idea.get("recommendation_id"),
                    current_price=idea.get("scan_cmp"),
                )
                promoted += 1
                log.info("Auto-promoted %s to %s portfolio (score=%.1f)",
                         idea["symbol"], horizon, idea.get("selection_score", 0))
            except ValueError as exc:
                log.debug("Skip promote %s: %s", idea["symbol"], exc)
                if "full" in str(exc).lower():
                    break  # No point trying more if portfolio is full

    if promoted:
        log.info("Auto-promoted %d positions into portfolio", promoted)
    return promoted


def get_portfolio_summary() -> dict:
    """Full portfolio state for API consumption."""
    from dashboard.backend.db.portfolio import get_portfolio, get_portfolio_counts, get_journal_stats

    swing = get_portfolio("SWING")
    longterm = get_portfolio("LONGTERM")
    counts = get_portfolio_counts()
    swing_stats = get_journal_stats("SWING")
    longterm_stats = get_journal_stats("LONGTERM")
    overall_stats = get_journal_stats()

    return {
        "swing": {
            "positions": swing,
            "count": counts["swing"],
            "max": counts["swing_max"],
            "journal_stats": swing_stats,
        },
        "longterm": {
            "positions": longterm,
            "count": counts["longterm"],
            "max": counts["longterm_max"],
            "journal_stats": longterm_stats,
        },
        "overall_stats": overall_stats,
    }


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
