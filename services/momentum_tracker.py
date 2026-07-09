"""
services/momentum_tracker.py
=============================
Independent scheduler for the Momentum Portfolio. Runs in its OWN daemon thread,
completely separate from the Swing/LT portfolio_tracker (which is untouched). The
scheduler only ever calls MomentumPortfolioManager.run().

Doubly gated: does nothing unless BOTH MOMENTUM_TRACKER_ENABLED=1 and
MOMENTUM_PORTFOLIO_ENABLED=1. Safe to start unconditionally at boot — it idles
until enabled.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("services.momentum_tracker")

_thread: threading.Thread | None = None


def run_once() -> dict:
    """One orchestration cycle. Also the manual trigger used by the API."""
    from services.momentum_portfolio_manager import MomentumPortfolioManager
    return MomentumPortfolioManager().run()


def _loop() -> None:
    from services.momentum_engine.config import cfg
    from services.trade_tracker import _current_interval
    log.info("Momentum tracker thread started (idle until enabled)")
    while True:
        try:
            c = cfg()
            if c["MOMENTUM_TRACKER_ENABLED"] and c["MOMENTUM_PORTFOLIO_ENABLED"]:
                res = run_once()
                if res.get("status") != "disabled":
                    log.info("[MomentumTracker] cycle: armed=%d exits=%s",
                             len(res.get("armed", [])), (res.get("exits") or {}).get("exited"))
        except Exception:
            log.exception("Momentum tracker loop error")
        try:
            interval = _current_interval()
        except Exception:
            interval = 900
        time.sleep(interval)


def start_momentum_tracker() -> None:
    """Launch the momentum scheduler once (idempotent). Call from app startup."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True, name="momentum-tracker")
    _thread.start()
    log.info("Momentum tracker launched")
