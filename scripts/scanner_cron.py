"""
scripts/scanner_cron.py — Scanner producer entry point (separate Railway worker).

Isolated from the live trading engine: it only reads the Kite token (Redis) and
writes scanner:* snapshots. It NEVER imports the engine loop or touches trade state.

Run modes:
  python -m scripts.scanner_cron --once         # single run, then exit (cron-style)
  python -m scripts.scanner_cron --loop         # long-lived; runs on a schedule
  python -m scripts.scanner_cron --once --mode full

Scheduling (loop mode): a daily refresh after market close, plus optional intraday
refreshes during market hours. The canonical, confirmed-at-close result is the
post-close run. Env:
  SCANNER_MODE                liquid|full         (default liquid)
  SCANNER_INTRADAY_REFRESH    1 to also run every SCANNER_INTRADAY_MIN minutes
  SCANNER_INTRADAY_MIN        minutes between intraday refreshes (default 30)

This file deliberately contains no Kite calls itself — it delegates to
services.scanners.runner so all data access stays in one tested place.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

# Ensure project root on path when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("scanner_cron")

_IST = timezone(timedelta(hours=5, minutes=30))


def _load_env() -> None:
    """Best-effort .env load for local runs (Railway injects real env vars)."""
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    try:
        for line in open(path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception as exc:
        log.warning("could not load .env: %s", exc)


def run_once(mode: str | None = None) -> dict:
    from services.scanners.runner import run_all

    mode = mode or os.getenv("SCANNER_MODE", "liquid")
    t0 = time.time()
    log.info("=== scanner run START (mode=%s) ===", mode)
    try:
        summary = run_all(mode=mode)
        log.info(
            "=== scanner run DONE in %.1fs: %s ===",
            time.time() - t0,
            ", ".join(f"{s['name']}/{s['timeframe']}={s['hits']}" for s in summary.get("scanners", [])),
        )
        return summary
    except Exception:
        log.exception("scanner run FAILED (snapshots preserved by LKG)")
        return {"error": True}


def _is_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:  # Sat/Sun
        return False
    t = now.time()
    return (t >= datetime(2000, 1, 1, 9, 15).time()) and (t <= datetime(2000, 1, 1, 15, 35).time())


def run_loop(mode: str | None = None) -> None:
    """Long-lived loop: post-close daily run + optional intraday refreshes.

    Kept intentionally simple (no external scheduler dependency). Sleeps in short
    ticks and triggers when the target window is reached. Idempotent: re-running a
    scan only overwrites the snapshot with a fresh (or LKG-preserved) result.
    """
    intraday = os.getenv("SCANNER_INTRADAY_REFRESH", "0").strip().lower() in {"1", "true", "yes"}
    intraday_min = int(os.getenv("SCANNER_INTRADAY_MIN", "30"))
    post_close_done_for: str | None = None
    last_intraday = 0.0

    log.info("scanner loop started (intraday_refresh=%s every=%dmin)", intraday, intraday_min)
    # Kick off one run immediately so a fresh deploy has data without waiting.
    run_once(mode)

    while True:
        now = datetime.now(_IST)
        today = now.date().isoformat()

        # Post-close canonical run (once per weekday, ~15:45 IST).
        if now.weekday() < 5 and now.time() >= datetime(2000, 1, 1, 15, 45).time():
            if post_close_done_for != today:
                log.info("post-close trigger for %s", today)
                run_once(mode)
                post_close_done_for = today

        # Optional intraday refreshes (provisional, forming candle).
        if intraday and _is_market_hours(now):
            if time.time() - last_intraday >= intraday_min * 60:
                log.info("intraday refresh trigger")
                run_once(mode)
                last_intraday = time.time()

        time.sleep(60)


def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description="SMC scanner producer")
    ap.add_argument("--once", action="store_true", help="run a single scan and exit")
    ap.add_argument("--loop", action="store_true", help="run continuously on a schedule")
    ap.add_argument("--mode", default=None, help="universe mode: liquid|full")
    args = ap.parse_args()

    if args.loop:
        run_loop(args.mode)
        return 0
    # Default to a single run.
    summary = run_once(args.mode)
    return 1 if summary.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
