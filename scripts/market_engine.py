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

INTERVAL = 5   # seconds — loop interval
OHLC_EVERY_N = 2   # do OHLC every 2nd loop → 10s
OHLC_SYMBOLS = ["NIFTY 50", "NIFTY BANK"]
OHLC_INTERVAL = "15minute"
OHLC_DAYS = 7


def main():
    if not os.getenv("REDIS_URL", "").strip():
        log.warning("REDIS_URL not set — worker will not run. Set REDIS_URL and Kite credentials.")
        sys.exit(0)

    from dashboard.backend.cache import (
        set as cache_set,
        ohlc_key,
        OI_SNAPSHOT_KEY,
        MARKET_DATA_TTL,
        MARKET_ENGINE_LAST_UPDATE_KEY,
        WORKER_HEARTBEAT_TTL,
        is_redis_available,
    )
    if not is_redis_available():
        log.error("Redis not available — check REDIS_URL")
        sys.exit(1)

    from dashboard.backend.kite_auth import get_access_token_from_redis_only, get_access_token

    log.info("Market engine worker started — OI every %ss, OHLC every %ss", INTERVAL, INTERVAL * OHLC_EVERY_N)
    tick = 0
    last_token = None

    while True:
        try:
            # ── Token: Redis first, then env/file; wait if none (e.g. before morning login) ─
            redis_token = get_access_token_from_redis_only()
            current_token = redis_token or get_access_token()
            if not current_token:
                log.warning("Kite token missing — waiting for admin login at /api/kite/login")
                time.sleep(30)
                continue

            # ── Force reconnect when Redis token changes (instant after admin login) ─
            if redis_token is not None and redis_token != last_token:
                try:
                    from dashboard.backend.routes.charts import _reset_kite
                    _reset_kite()
                    last_token = redis_token
                    log.info("Kite token updated — client reconnected")
                except Exception as e:
                    log.debug("Kite reset failed: %s", e)

            # ── OHLC every 10s (halves Kite load) ────────────────────────────
            if tick % OHLC_EVERY_N == 0:
                try:
                    from dashboard.backend.routes.charts import _fetch_ohlc, _kite_symbol
                    for sym in OHLC_SYMBOLS:
                        kite_sym = _kite_symbol(sym)
                        candles = _fetch_ohlc(kite_sym, OHLC_INTERVAL, OHLC_DAYS)
                        cache_set(ohlc_key(kite_sym, OHLC_INTERVAL), candles, MARKET_DATA_TTL)
                    log.debug("OHLC cached for %s", OHLC_SYMBOLS)
                except Exception as e:
                    log.warning("OHLC fetch failed: %s", e)

            # ── OI snapshot every 5s ────────────────────────────────────────
            try:
                from agents.oi_intelligence_agent import generate_snapshot
                snapshot = generate_snapshot()
                cache_set(OI_SNAPSHOT_KEY, snapshot, MARKET_DATA_TTL)
                log.debug("OI snapshot cached")
            except Exception as e:
                log.warning("OI snapshot failed: %s", e)

            # ── Heartbeat for health endpoint (detect worker failure) ─────────
            cache_set(MARKET_ENGINE_LAST_UPDATE_KEY, time.time(), WORKER_HEARTBEAT_TTL)

        except Exception as e:
            log.exception("Worker loop error: %s", e)

        tick += 1
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
