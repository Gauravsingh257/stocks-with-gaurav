"""
services/trade_tracker.py

Background price tracker for research running_trades.

On startup (called from main.py), seeds a running_trade row for every active
stock_recommendation that doesn't have one yet, then starts a daemon thread
that polls prices and updates each RUNNING trade with live price, P&L %,
drawdown, high/low since entry, days held, and auto-resolves status to
TARGET_HIT or STOP_HIT.

Pricing: Kite API (real-time) during market hours, yfinance (delayed) after hours.
On exit: Telegram alert + journal sync + auto-replacement scan.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

log = logging.getLogger("services.trade_tracker")

TRACKER_INTERVAL_MARKET_S = int(os.getenv("TRACKER_INTERVAL_MARKET_S", "120"))   # 2 min during market
TRACKER_INTERVAL_OFF_S = int(os.getenv("TRACKER_INTERVAL_OFF_S", "900"))         # 15 min after hours
TRACKER_INTERVAL_S = int(os.getenv("TRACKER_INTERVAL_S", "300"))                 # legacy fallback

_tracker_thread: threading.Thread | None = None


# ---------------------------------------------------------------------------
# Market hours helper
# ---------------------------------------------------------------------------

def _is_market_hours() -> bool:
    """True during Mon-Fri 09:15-15:30 IST (when Kite LTP is useful)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(15, 30)


def _current_interval() -> int:
    """Return appropriate polling interval based on market hours."""
    return TRACKER_INTERVAL_MARKET_S if _is_market_hours() else TRACKER_INTERVAL_OFF_S


# ---------------------------------------------------------------------------
# Kite CMP fetch (real-time during market hours)
# ---------------------------------------------------------------------------

def _fetch_cmp_kite(symbols: list[str]) -> dict[str, float]:
    """Fetch CMP for multiple symbols via Kite LTP API. Returns {symbol: price}."""
    try:
        from config.kite_auth import get_api_key, get_access_token
        from kiteconnect import KiteConnect
    except ImportError:
        return {}

    api_key = get_api_key()
    token = get_access_token()
    if not api_key or not token:
        return {}

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(token)
        # Kite LTP accepts NSE:SYMBOL format
        instruments = [f"NSE:{s.replace('NSE:', '').strip()}" for s in symbols]
        ltp_data = kite.ltp(instruments)
        result: dict[str, float] = {}
        for inst, data in ltp_data.items():
            sym = inst.replace("NSE:", "")
            price = data.get("last_price")
            if price and price > 0:
                result[sym] = float(price)
        return result
    except Exception as exc:
        log.warning("Kite LTP fetch failed (will fallback to yfinance): %s", exc)
        return {}


# ---------------------------------------------------------------------------
# yfinance CMP fetch (fallback / after hours)
# ---------------------------------------------------------------------------

def _yf_symbol(nse_symbol: str) -> str:
    s = nse_symbol.replace("NSE:", "").replace("BSE:", "").strip()
    if not s.endswith(".NS") and not s.endswith(".BO"):
        s = s + ".NS"
    return s


def _fetch_cmp(symbol: str) -> float | None:
    try:
        import yfinance as yf  # noqa: PLC0415
        t = yf.Ticker(_yf_symbol(symbol))
        price = t.fast_info.get("lastPrice") or t.fast_info.get("regularMarketPrice")
        return float(price) if price else None
    except Exception as exc:
        log.debug("CMP fetch failed for %s: %s", symbol, exc)
        return None


def _fetch_cmp_batch_yf(symbols: list[str]) -> dict[str, float]:
    """Fetch CMP for multiple symbols via yfinance; returns {symbol: cmp}."""
    result: dict[str, float] = {}
    for sym in symbols:
        cmp = _fetch_cmp(sym)
        if cmp is not None:
            result[sym] = cmp
    return result


# ---------------------------------------------------------------------------
# Hybrid price fetcher — Kite first during market hours, yfinance fallback
# ---------------------------------------------------------------------------

def _fetch_cmp_batch(symbols: list[str]) -> dict[str, float]:
    """Fetch CMP using Kite during market hours, yfinance otherwise."""
    if not symbols:
        return {}

    result: dict[str, float] = {}

    if _is_market_hours():
        result = _fetch_cmp_kite(symbols)
        # Fallback for any symbols Kite missed
        missing = [s for s in symbols if s not in result]
        if missing:
            yf_prices = _fetch_cmp_batch_yf(missing)
            result.update(yf_prices)
    else:
        result = _fetch_cmp_batch_yf(symbols)

    return result


# ---------------------------------------------------------------------------
# Seed running_trades from recommendations that lack a live tracker row
# ---------------------------------------------------------------------------

def seed_running_trades() -> int:
    """
    For each active SWING recommendation without an active running_trade,
    create a running_trade row so we can track it. Returns number of rows seeded.

    LONGTERM is intentionally excluded — long-term ideas are research
    recommendations (6-24 month horizon), not executed positions. They are
    surfaced via the Long-Term Ideas card. The user (or a separate broker fill
    feed) decides when to actually enter, and only then should a running trade
    be created.

    Only seeds when entry is actually triggered:
    - MARKET orders: always seed (immediate execution)
    - LIMIT orders: only seed if CMP has reached the entry price
    """
    from dashboard.backend.db import (  # noqa: PLC0415
        get_stock_recommendations,
        get_active_running_trade_by_symbol,
        create_running_trade,
    )

    seeded = 0
    for horizon in ("SWING",):
        rows = get_stock_recommendations(horizon, limit=50)
        for row in rows:
            symbol = row["symbol"]
            existing = get_active_running_trade_by_symbol(symbol)
            if existing:
                continue  # already tracked
            entry = float(row["entry_price"])
            entry_type = (row.get("entry_type") or "MARKET").upper()

            # ── Entry validation: skip LIMIT orders where price hasn't reached entry ──
            if entry_type == "LIMIT":
                cmp = _fetch_cmp(symbol)
                if cmp is None:
                    log.debug("Skip %s: no CMP available for LIMIT entry check", symbol)
                    continue
                # For LONG bias: CMP must be at or below entry (price actually visited limit).
                # Allow only 0.25% slack for tick noise / fast moves through the level.
                if cmp > entry * 1.0025:
                    log.debug("Skip %s: LIMIT entry %.2f not triggered (CMP %.2f above entry)",
                              symbol, entry, cmp)
                    continue
            else:
                cmp = _fetch_cmp(symbol) or entry

            stop = float(row["stop_loss"]) if row.get("stop_loss") else entry * 0.95
            targets = row.get("targets", [])
            cmp = _fetch_cmp(symbol) or entry
            dist_target = round(float(targets[0]) - cmp, 2) if targets else None
            dist_sl = round(cmp - stop, 2)
            pl = round(cmp - entry, 2)
            pl_pct = round((cmp - entry) / entry * 100, 2) if entry else 0.0
            create_running_trade({
                "symbol": symbol,
                "recommendation_id": row["id"],
                "entry_price": entry,
                "stop_loss": stop,
                "targets": targets,
                "current_price": cmp,
                "profit_loss": pl,
                "profit_loss_pct": pl_pct,
                "drawdown": min(pl, 0.0),
                "drawdown_pct": min(pl_pct, 0.0),
                "high_since_entry": cmp,
                "low_since_entry": cmp,
                "days_held": 0,
                "distance_to_target": dist_target,
                "distance_to_stop_loss": dist_sl,
                "status": "RUNNING",
                "entry_triggered_at": datetime.now(_IST).isoformat(),
            })
            seeded += 1
    if seeded:
        log.info("Seeded %d new running_trade rows", seeded)
    return seeded


def purge_untriggered_running_trades() -> int:
    """
    Cancel running_trade rows that should not exist:
    1. LIMIT-entry recommendations where CMP never reached the entry price.
    2. Any LONGTERM-horizon running trade — long-term ideas are recommendations,
       not executed positions, and should not appear in the Running Trades Monitor.
    Returns count of rows purged.
    """
    from dashboard.backend.db import (  # noqa: PLC0415
        list_running_trades,
        get_connection,
    )

    rows = list_running_trades(limit=200, active_only=True)
    if not rows:
        return 0

    purged = 0
    conn = get_connection()
    try:
        for row in rows:
            rec_id = row.get("recommendation_id")
            if not rec_id:
                continue
            rec = conn.execute(
                "SELECT entry_type, scan_cmp, agent_type FROM stock_recommendations WHERE id = ?",
                (rec_id,),
            ).fetchone()
            if not rec:
                continue

            # ── Rule 2: cancel any LONGTERM running trade ──
            if (rec["agent_type"] or "").upper() == "LONGTERM":
                conn.execute(
                    "UPDATE running_trades SET status = 'CANCELLED' WHERE id = ?",
                    (row["id"],),
                )
                purged += 1
                log.info("Purged LONGTERM running trade (recos are not executions): %s", row["symbol"])
                continue

            # ── Rule 1: LIMIT entries where CMP never reached entry ──
            if (rec["entry_type"] or "MARKET").upper() != "LIMIT":
                continue
            entry = float(row["entry_price"])
            cmp = _fetch_cmp(row["symbol"])
            if cmp is not None and cmp > entry * 1.0025:
                conn.execute(
                    "UPDATE running_trades SET status = 'CANCELLED' WHERE id = ?",
                    (row["id"],),
                )
                purged += 1
                log.info("Purged untriggered running trade: %s (LIMIT entry %.2f, CMP %.2f)",
                         row["symbol"], entry, cmp)
        if purged:
            conn.commit()
    finally:
        conn.close()

    if purged:
        log.info("Purged %d running trades", purged)
    return purged


# ---------------------------------------------------------------------------
# Core update loop
# ---------------------------------------------------------------------------

def _update_all_running_trades() -> int:
    """Fetch live prices and update all RUNNING trades. Returns count updated."""
    from dashboard.backend.db import list_running_trades, update_running_trade, log_signal_event  # noqa: PLC0415

    rows = list_running_trades(limit=200, active_only=True)
    if not rows:
        return 0

    symbols = list({r["symbol"] for r in rows})
    prices = _fetch_cmp_batch(symbols)

    updated = 0
    exits_swing: list[str] = []
    exits_longterm: list[str] = []
    today = date.today()
    for row in rows:
        sym = row["symbol"]
        cmp = prices.get(sym)
        if cmp is None:
            continue

        entry = float(row["entry_price"])
        stop = float(row["stop_loss"])
        targets = row.get("targets", [])
        max_target = float(targets[-1]) if targets else entry * 1.20

        pl = round(cmp - entry, 2)
        pl_pct = round((cmp - entry) / entry * 100, 2) if entry else 0.0
        dd = round(min(pl, 0.0), 2)
        dd_pct = round(min(pl_pct, 0.0), 2)

        # Days held: date diff from created_at
        try:
            created = datetime.fromisoformat(row["created_at"]).date()
            days_held = (today - created).days
        except Exception:
            days_held = int(row.get("days_held", 0))

        dist_target = round(max_target - cmp, 2) if targets else None
        dist_sl = round(cmp - stop, 2)

        # Auto-resolve status
        old_status = row.get("status", "RUNNING")
        if targets and cmp >= float(targets[-1]):
            status = "TARGET_HIT"
        elif cmp <= stop:
            status = "STOP_HIT"
        else:
            status = "RUNNING"

        # Track high/low since entry properly (don't overwrite with current)
        prev_high = float(row.get("high_since_entry") or cmp)
        prev_low = float(row.get("low_since_entry") or cmp)
        high_since = max(prev_high, cmp)
        low_since = min(prev_low, cmp)

        update_running_trade(
            row["id"],
            current_price=cmp,
            profit_loss=pl,
            profit_loss_pct=pl_pct,
            drawdown=dd,
            drawdown_pct=dd_pct,
            high_since_entry=high_since,
            low_since_entry=low_since,
            days_held=days_held,
            distance_to_target=dist_target,
            distance_to_stop_loss=dist_sl,
            status=status,
        )

        # ── On status transition to TARGET_HIT or STOP_HIT ──
        if status != old_status and status in ("TARGET_HIT", "STOP_HIT"):
            rec_id = row.get("recommendation_id")

            # 1. Log signal event
            try:
                log_signal_event(
                    symbol=sym,
                    event_type=status,
                    source="trade_tracker",
                    recommendation_id=rec_id,
                    running_trade_id=row["id"],
                    details={"cmp": cmp, "entry": entry, "stop": stop, "pl_pct": pl_pct},
                )
                log.info("Trade %s %s at %.2f (PL: %.2f%%)", sym, status, cmp, pl_pct)
            except Exception:
                log.exception("Failed to log signal event for %s", sym)

            # 2. Update recommendation status + exit data
            _sync_recommendation_status(rec_id, status, exit_price=cmp)

            # 3. Send Telegram alert
            _send_exit_alert(sym, status, entry, cmp, pl_pct, days_held, targets, stop)

            # 4. Log exit to journal
            _log_exit_to_journal(sym, entry, cmp, pl_pct, days_held, status, row)

            # 5. Track which horizon had the exit for replacement scan
            horizon = _get_recommendation_horizon(rec_id)
            if horizon == "SWING":
                exits_swing.append(sym)
            elif horizon == "LONGTERM":
                exits_longterm.append(sym)

        updated += 1

    log.info("Tracker: updated %d running trades", updated)

    # Auto-trigger replacement scans for vacated slots
    if exits_swing:
        _trigger_replacement_scan("SWING", exits_swing)
    if exits_longterm:
        _trigger_replacement_scan("LONGTERM", exits_longterm)

    return updated


# ---------------------------------------------------------------------------
# Exit handling helpers
# ---------------------------------------------------------------------------

def _sync_recommendation_status(recommendation_id: int | None, status: str,
                                exit_price: float | None = None) -> None:
    """Update the parent stock_recommendation status + exit fields."""
    if not recommendation_id:
        return
    try:
        from dashboard.backend.db import get_connection
        conn = get_connection()
        try:
            now_ist = datetime.now(_IST).isoformat()
            conn.execute(
                """UPDATE stock_recommendations
                   SET status = ?, exit_price = ?, exit_date = ?, exit_reason = ?
                   WHERE id = ? AND status = 'ACTIVE'""",
                (status, exit_price, now_ist, status, recommendation_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.exception("Failed to sync recommendation %d status to %s", recommendation_id, status)


def _get_recommendation_horizon(recommendation_id: int | None) -> str | None:
    """Look up the agent_type (SWING/LONGTERM) for a recommendation."""
    if not recommendation_id:
        return None
    try:
        from dashboard.backend.db import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT agent_type FROM stock_recommendations WHERE id = ?",
                (recommendation_id,),
            ).fetchone()
            return row["agent_type"] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _send_exit_alert(
    symbol: str, status: str, entry: float, exit_price: float,
    pl_pct: float, days_held: int, targets: list, stop: float,
) -> None:
    """Send Telegram notification on SL/Target hit."""
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("SMC_PRO_CHAT_ID", "")
        if not bot_token or not chat_id:
            log.warning("Telegram not configured — skipping exit alert for %s", symbol)
            return

        emoji = "🎯" if status == "TARGET_HIT" else "🛑"
        result_text = "TARGET HIT" if status == "TARGET_HIT" else "STOP LOSS HIT"
        pl_sign = "+" if pl_pct >= 0 else ""

        msg = (
            f"{emoji} <b>Research {result_text}</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"Entry: ₹{entry:.2f}\n"
            f"Exit: ₹{exit_price:.2f}\n"
            f"P&L: {pl_sign}{pl_pct:.2f}%\n"
            f"Days Held: {days_held}\n"
        )
        if targets:
            msg += f"Targets: {', '.join(f'₹{float(t):.2f}' for t in targets)}\n"
        msg += f"Stop Loss: ₹{stop:.2f}"

        import requests
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
        }, timeout=10)
        log.info("Telegram exit alert sent for %s (%s)", symbol, status)
    except Exception:
        log.exception("Failed to send Telegram exit alert for %s", symbol)


def _log_exit_to_journal(
    symbol: str, entry: float, exit_price: float, pl_pct: float,
    days_held: int, status: str, row: dict,
) -> None:
    """POST the exit to dashboard journal for trade history."""
    try:
        import requests
        backend_url = os.getenv("DASHBOARD_BACKEND_URL", "https://web-production-2781a.up.railway.app")
        sync_key = os.getenv("TRADES_SYNC_KEY", "").strip()

        # Calculate R-multiple
        entry_val = float(entry)
        stop_val = float(row.get("stop_loss", entry * 0.95))
        risk = abs(entry_val - stop_val)
        pnl_r = round((exit_price - entry_val) / risk, 2) if risk > 0 else 0.0

        result_label = "WIN" if status == "TARGET_HIT" else "LOSS"
        today_str = date.today().isoformat()

        headers = {"Content-Type": "application/json"}
        if sync_key:
            headers["X-Sync-Key"] = sync_key

        requests.post(
            f"{backend_url}/api/journal/trade",
            json={
                "date": today_str,
                "symbol": symbol,
                "direction": "LONG",
                "setup": f"RESEARCH_{status}",
                "entry": entry_val,
                "exit_price": exit_price,
                "result": result_label,
                "pnl_r": pnl_r,
            },
            headers=headers,
            timeout=10,
        )
        log.info("Journal exit logged for %s (R: %.2f)", symbol, pnl_r)
    except Exception:
        log.exception("Failed to log journal exit for %s", symbol)


def _trigger_replacement_scan(horizon: str, exited_symbols: list[str]) -> None:
    """Trigger a background replacement scan to fill vacated slots."""
    def _run_scan():
        try:
            if horizon == "SWING":
                from agents.swing_alpha_agent import SwingTradeAlphaAgent
                from agents.base import AgentResult
                agent = SwingTradeAlphaAgent()
                r = AgentResult(agent_name=agent.name)
                agent.run(r)
                log.info("Replacement scan (SWING): %s", r.summary)
            elif horizon == "LONGTERM":
                from agents.longterm_investment_agent import LongTermInvestmentAgent
                from agents.base import AgentResult
                agent = LongTermInvestmentAgent()
                r = AgentResult(agent_name=agent.name)
                agent.run(r)
                log.info("Replacement scan (LONGTERM): %s", r.summary)
        except Exception:
            log.exception("Replacement scan failed for %s", horizon)

    log.info("Triggering replacement scan for %s (exited: %s)", horizon, exited_symbols)
    threading.Thread(target=_run_scan, daemon=True, name=f"replacement-{horizon}").start()


# ---------------------------------------------------------------------------
# Background daemon
# ---------------------------------------------------------------------------

def _tracker_loop() -> None:
    log.info("Trade tracker started (market=%ds, off=%ds)", TRACKER_INTERVAL_MARKET_S, TRACKER_INTERVAL_OFF_S)
    # One-time cleanup of incorrectly seeded LIMIT trades on startup
    try:
        purge_untriggered_running_trades()
    except Exception:
        log.exception("Purge untriggered trades failed (non-fatal)")
    while True:
        try:
            seed_running_trades()
            _update_all_running_trades()
        except Exception:
            log.exception("Trade tracker loop error")
        interval = _current_interval()
        time.sleep(interval)


def start_trade_tracker() -> None:
    """Start the background price tracker. Call once from main.py startup."""
    global _tracker_thread
    if _tracker_thread is not None and _tracker_thread.is_alive():
        return
    _tracker_thread = threading.Thread(
        target=_tracker_loop, daemon=True, name="trade-tracker"
    )
    _tracker_thread.start()
    log.info("Trade tracker thread launched")


def refresh_now() -> dict:
    """Trigger an immediate update cycle (used by the /refresh API endpoint)."""
    purged = 0
    try:
        purged = purge_untriggered_running_trades()
    except Exception:
        log.exception("Purge untriggered trades failed (non-fatal)")
    seeded = seed_running_trades()
    updated = _update_all_running_trades()
    return {"seeded": seeded, "updated": updated, "purged": purged}
