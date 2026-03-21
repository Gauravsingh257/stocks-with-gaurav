"""
services/trade_tracker.py

Background price tracker for research running_trades.

On startup (called from main.py), seeds a running_trade row for every active
stock_recommendation that doesn't have one yet, then starts a daemon thread
that polls yfinance every TRACKER_INTERVAL_S seconds and updates each RUNNING
trade with live price, P&L %, drawdown, high/low since entry, days held, and
auto-resolves status to TARGET_HIT or STOP_HIT.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

log = logging.getLogger("services.trade_tracker")

TRACKER_INTERVAL_S = int(__import__("os").getenv("TRACKER_INTERVAL_S", "300"))  # 5 min default

_tracker_thread: threading.Thread | None = None


# ---------------------------------------------------------------------------
# yfinance CMP fetch
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


def _fetch_cmp_batch(symbols: list[str]) -> dict[str, float]:
    """Fetch CMP for multiple symbols; returns {symbol: cmp}."""
    result: dict[str, float] = {}
    for sym in symbols:
        cmp = _fetch_cmp(sym)
        if cmp is not None:
            result[sym] = cmp
    return result


# ---------------------------------------------------------------------------
# Seed running_trades from recommendations that lack a live tracker row
# ---------------------------------------------------------------------------

def seed_running_trades() -> int:
    """
    For each active SWING/LONGTERM recommendation without an active running_trade,
    create a running_trade row so we can track it. Returns number of rows seeded.
    """
    from dashboard.backend.db import (  # noqa: PLC0415
        get_stock_recommendations,
        get_active_running_trade_by_symbol,
        create_running_trade,
    )

    seeded = 0
    for horizon in ("SWING", "LONGTERM"):
        rows = get_stock_recommendations(horizon, limit=50)
        for row in rows:
            symbol = row["symbol"]
            existing = get_active_running_trade_by_symbol(symbol)
            if existing:
                continue  # already tracked
            entry = float(row["entry_price"])
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
            })
            seeded += 1
    if seeded:
        log.info("Seeded %d new running_trade rows", seeded)
    return seeded


# ---------------------------------------------------------------------------
# Core update loop
# ---------------------------------------------------------------------------

def _update_all_running_trades() -> int:
    """Fetch live prices and update all RUNNING trades. Returns count updated."""
    from dashboard.backend.db import list_running_trades, update_running_trade  # noqa: PLC0415

    rows = list_running_trades(limit=200, active_only=True)
    if not rows:
        return 0

    symbols = list({r["symbol"] for r in rows})
    prices = _fetch_cmp_batch(symbols)

    updated = 0
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
        if targets and cmp >= float(targets[-1]):
            status = "TARGET_HIT"
        elif cmp <= stop:
            status = "STOP_HIT"
        else:
            status = "RUNNING"

        update_running_trade(
            row["id"],
            current_price=cmp,
            profit_loss=pl,
            profit_loss_pct=pl_pct,
            drawdown=dd,
            drawdown_pct=dd_pct,
            high_since_entry=cmp,
            low_since_entry=cmp,
            days_held=days_held,
            distance_to_target=dist_target,
            distance_to_stop_loss=dist_sl,
            status=status,
        )
        updated += 1

    log.info("Tracker: updated %d running trades", updated)
    return updated


# ---------------------------------------------------------------------------
# Background daemon
# ---------------------------------------------------------------------------

def _tracker_loop() -> None:
    log.info("Trade tracker started (interval=%ds)", TRACKER_INTERVAL_S)
    while True:
        try:
            seed_running_trades()
            _update_all_running_trades()
        except Exception:
            log.exception("Trade tracker loop error")
        time.sleep(TRACKER_INTERVAL_S)


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
    seeded = seed_running_trades()
    updated = _update_all_running_trades()
    return {"seeded": seeded, "updated": updated}
