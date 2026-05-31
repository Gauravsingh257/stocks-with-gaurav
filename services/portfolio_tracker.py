"""
services/portfolio_tracker.py

Thin runner for the shared PositionTrackingService (PR-A refactor). The
per-position tracking logic (CMP → P&L → SL/Target/Structure exit → journal)
now lives in services/position_tracking_service.py and runs against pluggable
stores (services/position_stores.py), so it can be reused for the per-user
book without duplication. This module keeps:
  - the daemon loop + cadence (2min market / 15min off-hours)
  - arm-on-tap promotion of Final Trade Ideas (system portfolio)
Behavior is unchanged from before the refactor for the system portfolio.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("services.portfolio_tracker")

_tracker_thread: threading.Thread | None = None

# One shared engine instance; PR-A registers only the system store. The
# per-user store plugs into this same list in PR-B (no engine change).
from services.position_stores import PortfolioPositionStore, UserPositionStore  # noqa: E402
from services.position_tracking_service import PositionTrackingService  # noqa: E402

# One shared engine, two stores: the system book + the per-user book. Same
# tracking logic for both (UserPositionStore is flag-gated via
# USER_POSITION_TRACKING and maps its own status vocabulary).
_service = PositionTrackingService([PortfolioPositionStore(), UserPositionStore()])


def _update_portfolio_prices() -> int:
    """Back-compat shim — drives the shared engine. Kept so existing callers
    (routes/portfolio.py /refresh-prices) work unchanged."""
    return _service.tick()


def _promote_final_ideas_on_tap() -> int:
    """Arm-on-tap: during market hours, promote any Final Trade Idea now trading
    in its entry zone (CMP-buy) into the SYSTEM portfolio. The Final Trade Ideas
    feed IS the armed set — select_from_final_ideas re-checks live CMP each
    cycle. Flag-gated + best-effort; never raises into the loop.

    (Watchlist arm-on-tap with the paper-trigger/confirm flow is a later PR; it
    will live in a generalized EntryTriggerService.)"""
    if os.getenv("PORTFOLIO_SOURCE_FINAL_IDEAS", "1").strip().lower() not in {"1", "true", "yes"}:
        return 0
    try:
        from services.trade_tracker import _is_market_hours
        if not _is_market_hours():
            return 0
        from services.idea_selector import select_from_final_ideas
        from services.portfolio_manager import promote_to_portfolio
        from dashboard.backend.db.portfolio import get_portfolio_counts

        promoted = 0
        for horizon in ("SWING", "LONGTERM"):
            counts = get_portfolio_counts()
            room = max(0, counts.get(f"{horizon.lower()}_max", 20) - counts.get(horizon.lower(), 0))
            if room <= 0:
                continue
            for idea in select_from_final_ideas(horizon, max_picks=room):
                try:
                    promote_to_portfolio(
                        symbol=idea["symbol"], horizon=idea["horizon"],
                        entry_price=idea["entry_price"], stop_loss=idea["stop_loss"],
                        target_1=idea.get("target_1"), target_2=idea.get("target_2"),
                        confidence_score=idea.get("confidence_score", 0),
                        reasoning=idea.get("reasoning", ""),
                        recommendation_id=idea.get("recommendation_id"),
                        current_price=idea.get("scan_cmp"),
                    )
                    promoted += 1
                    log.info("[ArmOnTap] promoted %s into %s portfolio", idea["symbol"], horizon)
                except ValueError:
                    pass  # full / already held
        return promoted
    except Exception:
        log.exception("[ArmOnTap] tick failed")
        return 0


def _portfolio_tracker_loop() -> None:
    from services.trade_tracker import _current_interval
    log.info("Portfolio tracker started")
    while True:
        try:
            _update_portfolio_prices()
        except Exception:
            log.exception("Portfolio tracker loop error")
        try:
            _promote_final_ideas_on_tap()
        except Exception:
            log.exception("Portfolio tracker: arm-on-tap error")
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
