"""
services/portfolio_tracker.py

Dedicated price tracker for portfolio_positions.
Reuses the hybrid Kite+yfinance price fetcher from trade_tracker.
Runs as a daemon thread — updates every 2min (market) / 15min (off-hours).

Auto-resolves: TARGET_HIT when CMP >= target_2 (or target_1 if no target_2).
              STOP_HIT when CMP <= stop_loss.
On exit: journals the trade + sends Telegram alert.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

log = logging.getLogger("services.portfolio_tracker")

_tracker_thread: threading.Thread | None = None


def _update_portfolio_prices() -> int:
    """Fetch live prices and update all ACTIVE portfolio positions. Returns count updated."""
    from dashboard.backend.db.portfolio import get_portfolio, update_position_price
    from services.trade_tracker import _fetch_cmp_batch, _is_market_hours

    positions = get_portfolio(include_closed=False)
    if not positions:
        return 0

    symbols = list({p["symbol"] for p in positions})
    prices = _fetch_cmp_batch(symbols)

    updated = 0
    today = date.today()

    for pos in positions:
        sym = pos["symbol"]
        cmp = prices.get(sym)
        if cmp is None:
            continue

        entry = float(pos["entry_price"])
        sl = float(pos["stop_loss"])
        t1 = float(pos["target_1"]) if pos.get("target_1") else None
        t2 = float(pos["target_2"]) if pos.get("target_2") else None
        max_target = t2 or t1 or entry * 1.20

        pl = round(cmp - entry, 2)
        pl_pct = round((cmp - entry) / entry * 100, 2) if entry else 0.0
        dd = round(min(pl, 0.0), 2)
        dd_pct = round(min(pl_pct, 0.0), 2)

        # Days held
        try:
            created = datetime.fromisoformat(pos["created_at"]).date()
            days_held = (today - created).days
        except Exception:
            days_held = int(pos.get("days_held", 0))

        # Track high/low since entry
        prev_high = float(pos.get("high_since_entry") or cmp)
        prev_low = float(pos.get("low_since_entry") or cmp)
        high_since = max(prev_high, cmp)
        low_since = min(prev_low, cmp)

        # Auto-resolve status
        old_status = pos.get("status", "ACTIVE")
        new_status = "ACTIVE"
        exit_reason = None

        if cmp >= max_target:
            new_status = "TARGET_HIT"
            exit_reason = "TARGET_HIT"
        elif cmp <= sl:
            new_status = "STOP_HIT"
            exit_reason = "STOP_HIT"

        update_position_price(
            pos["id"],
            current_price=cmp,
            profit_loss=pl,
            profit_loss_pct=pl_pct,
            drawdown=dd,
            drawdown_pct=dd_pct,
            high_since_entry=high_since,
            low_since_entry=low_since,
            days_held=days_held,
            status=new_status,
        )

        # On SL/Target hit: close and journal
        if new_status != "ACTIVE" and old_status == "ACTIVE":
            try:
                from services.portfolio_manager import close_portfolio_position
                close_portfolio_position(pos["id"], cmp, exit_reason)
                log.info("Portfolio auto-close: %s %s at %.2f (PL: %.2f%%)",
                         sym, exit_reason, cmp, pl_pct)
            except Exception:
                log.exception("Failed to auto-close %s", sym)

        updated += 1

    if updated:
        log.debug("Portfolio tracker: updated %d positions", updated)
    return updated


def _portfolio_tracker_loop() -> None:
    from services.trade_tracker import _current_interval
    log.info("Portfolio tracker started")
    while True:
        try:
            _update_portfolio_prices()
        except Exception:
            log.exception("Portfolio tracker loop error")
        interval = _current_interval()
        time.sleep(interval)


def start_portfolio_tracker() -> None:
    """Start the background portfolio price tracker. Call once from main.py startup."""
    global _tracker_thread
    if _tracker_thread is not None and _tracker_thread.is_alive():
        return
    _tracker_thread = threading.Thread(
        target=_portfolio_tracker_loop, daemon=True, name="portfolio-tracker"
    )
    _tracker_thread.start()
    log.info("Portfolio tracker thread launched")
