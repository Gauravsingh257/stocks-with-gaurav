#!/usr/bin/env python3
"""
Market data worker: pushes OHLC and OI snapshot to Redis every 5 seconds.
Run as a separate process (e.g. second Railway service or systemd unit).
API reads from cache only — never hits Kite repeatedly.

Usage:
  REDIS_URL=redis://... KITE_API_KEY=... KITE_ACCESS_TOKEN=... python scripts/market_engine.py

Without REDIS_URL, the worker exits (API will fall back to Kite/generate_snapshot on demand).
"""

import os
import sys
import time
import logging
from pathlib import Path

# Ensure repo root is on path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger("market_engine")

INTERVAL = 5  # seconds
OHLC_SYMBOLS = ["NIFTY 50", "NIFTY BANK"]
OHLC_INTERVAL = "15minute"
OHLC_DAYS = 7


def main():
    if not os.getenv("REDIS_URL", "").strip():
        log.warning("REDIS_URL not set — worker will not run. Set REDIS_URL and Kite credentials.")
        sys.exit(0)

    from dashboard.backend.cache import (
        set as cache_set,
        get as cache_get,
        ohlc_key,
        OI_SNAPSHOT_KEY,
        MARKET_DATA_TTL,
        is_redis_available,
    )
    if not is_redis_available():
        log.error("Redis not available — check REDIS_URL")
        sys.exit(1)

    log.info("Market engine worker started — OHLC + OI every %ss", INTERVAL)

    while True:
        try:
            # ── OHLC (Kite) ─────────────────────────────────────────────────
            try:
                from dashboard.backend.routes.charts import _fetch_ohlc, _kite_symbol
                for sym in OHLC_SYMBOLS:
                    kite_sym = _kite_symbol(sym)
                    candles = _fetch_ohlc(kite_sym, OHLC_INTERVAL, OHLC_DAYS)
                    cache_set(ohlc_key(kite_sym, OHLC_INTERVAL), candles, MARKET_DATA_TTL)
                log.debug("OHLC cached for %s", OHLC_SYMBOLS)
            except Exception as e:
                log.warning("OHLC fetch failed: %s", e)

            # ── OI snapshot ──────────────────────────────────────────────────
            try:
                from agents.oi_intelligence_agent import generate_snapshot
                snapshot = generate_snapshot()
                cache_set(OI_SNAPSHOT_KEY, snapshot, MARKET_DATA_TTL)
                log.debug("OI snapshot cached")
            except Exception as e:
                log.warning("OI snapshot failed: %s", e)

        except Exception as e:
            log.exception("Worker loop error: %s", e)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
