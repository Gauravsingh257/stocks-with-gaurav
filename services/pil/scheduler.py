"""
services/pil/scheduler.py
=========================
Independent PIL scheduler (Parts 7/8/10 automation). A single daemon thread,
started from the dashboard lifespan, that:

  * generates the DAILY report every evening (default 18:00 IST),
  * generates the MONTHLY report on the 1st (for the previous month),
  * re-evaluates intelligence alerts each tick (Part 10, added in Commit 7).

It idles cheaply until PIL_ENABLED + PIL_REPORTS_ENABLED (re-read live each tick),
so with the flags off it does nothing. Mirrors the gated momentum-tracker
pattern. Never influences an engine — it only reads and writes pil_* tables /
sends Telegram summaries.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta

log = logging.getLogger("pil.scheduler")
_IST = timezone(timedelta(hours=5, minutes=30))

_started = False
_lock = threading.Lock()
_TICK_SECONDS = 300


def _report_hour() -> int:
    import os
    try:
        return int(os.getenv("PIL_DAILY_REPORT_HOUR", "18"))
    except ValueError:
        return 18


def _prev_month(d: datetime) -> str:
    first = d.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def _loop() -> None:
    last_daily = None      # YYYY-MM-DD
    last_monthly = None    # YYYY-MM
    log.info("[PIL] scheduler loop started (idles until flags enabled)")
    while True:
        try:
            from services.pil import config as pil_config
            if pil_config.reports_enabled():
                now = datetime.now(_IST)
                today = now.date().isoformat()

                # daily report after the configured evening hour
                if now.hour >= _report_hour() and last_daily != today:
                    try:
                        from services.pil import reports
                        reports.generate_and_store("daily", today, notify=True)
                        last_daily = today
                        log.info("[PIL] daily report generated for %s", today)
                    except Exception as exc:
                        log.error("[PIL] daily report failed: %s", exc)

                # monthly report on the 1st, for the previous month
                if now.day == 1:
                    ym = _prev_month(now)
                    if last_monthly != ym:
                        try:
                            from services.pil import reports
                            reports.generate_and_store("monthly", ym, notify=True)
                            last_monthly = ym
                            log.info("[PIL] monthly report generated for %s", ym)
                        except Exception as exc:
                            log.error("[PIL] monthly report failed: %s", exc)

            # alert evaluation (Part 10) — safe no-op until alerts module exists
            if pil_config.alerts_enabled():
                try:
                    from services.pil import alerts
                    alerts.evaluate(notify=True)
                except ImportError:
                    pass
                except Exception as exc:
                    log.error("[PIL] alert evaluation failed: %s", exc)

        except Exception as exc:  # never let the loop die
            log.error("[PIL] scheduler tick error: %s", exc)
        time.sleep(_TICK_SECONDS)


def start_pil_scheduler() -> None:
    """Start the PIL scheduler thread once. Safe to call unconditionally from the
    lifespan — the loop itself idles until the flags are on."""
    global _started
    with _lock:
        if _started:
            return
        t = threading.Thread(target=_loop, daemon=True, name="pil-scheduler")
        t.start()
        _started = True
