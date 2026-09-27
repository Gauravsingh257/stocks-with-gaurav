"""
scripts/prewarm_fundamentals.py — paced out-of-band refresh of the fundamentals cache.

    python -m scripts.prewarm_fundamentals                 # the scan universe, production pacing
    python -m scripts.prewarm_fundamentals --dry-run       # what would be refreshed, no network

Run by agents/runner.py at 06:45 IST on weekdays when FUND_COVERAGE_FIX=1, so the
08:30 Swing and 08:40 Long-Term scans read a warm cache instead of bursting Yahoo
past its rate limit. Hard stop at FUND_PREWARM_STOP_IST (default 08:15) whatever
time it started, so it can never overlap the scans. See services/fundamentals_prewarm.py.

Env: FUND_PREWARM_RPM (50) · FUND_PREWARM_WORKERS (2) · FUND_PREWARM_MAX_AGE_H (12)
     FUND_PREWARM_RETRY_PASSES (2) · FUND_PREWARM_COOLDOWN_S (300) · FUND_PREWARM_STOP_IST (08:15)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # rate-limit noise is counted, not logged per symbol

IST = ZoneInfo("Asia/Kolkata")


def seconds_until_stop(now: datetime, stop: str) -> float:
    hh, mm = (int(x) for x in stop.split(":"))
    end = datetime.combine(now.date(), dtime(hh, mm), tzinfo=IST)
    return max(0.0, (end - now).total_seconds())


def alphabetical_quintiles(symbols: list[str], ok: set[str]) -> list[float]:
    """Share with real fundamentals per A→Z quintile — the bias the old burst produced."""
    ordered = sorted(symbols)
    n = len(ordered)
    out = []
    for q in range(5):
        part = ordered[q * n // 5:(q + 1) * n // 5]
        out.append(round(sum(1 for s in part if s in ok) / len(part), 3) if part else 0.0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from services import fundamental_analysis as fa
    from services.fundamentals_prewarm import prewarm, prewarm_order
    from services.universe_manager import load_nse_universe

    symbols = load_nse_universe(int(os.getenv("RESEARCH_AGENT_TARGET_UNIVERSE", "2200"))).symbols
    max_age = float(os.getenv("FUND_PREWARM_MAX_AGE_H", "12"))
    if args.dry_run:
        stale = [s for s in symbols if (a := fa.cache_age_hours(s)) is None or a >= max_age]
        print(json.dumps({"universe": len(symbols), "would_refresh": len(stale),
                          "first": prewarm_order(stale, datetime.now(IST).date().isoformat())[:5]}))
        return 0

    budget = seconds_until_stop(datetime.now(IST), os.getenv("FUND_PREWARM_STOP_IST", "08:15"))
    if budget <= 0:
        print(json.dumps({"skipped": "past FUND_PREWARM_STOP_IST — never run into the scans"}))
        return 0

    rep = prewarm(
        symbols,
        rpm=float(os.getenv("FUND_PREWARM_RPM", "50")),
        workers=int(os.getenv("FUND_PREWARM_WORKERS", "2")),
        max_age_h=max_age,
        retry_passes=int(os.getenv("FUND_PREWARM_RETRY_PASSES", "2")),
        cooldown_s=float(os.getenv("FUND_PREWARM_COOLDOWN_S", "300")),
        deadline_s=budget,
        day=datetime.now(IST).date().isoformat(),
    )
    ok = {s for s in symbols if (snap := fa._load_cache(s)) and snap.get("data_source") != "hash"}
    rep["real_fundamentals_in_cache"] = len(ok)
    rep["coverage_pct"] = round(len(ok) / len(symbols) * 100, 1) if symbols else None
    rep["alphabetical_quintile_coverage"] = alphabetical_quintiles(symbols, ok)
    rep["still_failed_count"] = len(rep.pop("still_failed"))
    print(json.dumps(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
