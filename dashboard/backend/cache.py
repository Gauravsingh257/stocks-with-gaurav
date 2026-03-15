"""
dashboard/backend/cache.py
Redis-backed market data cache with in-memory TTL fallback.

Cache keys (TTL 5s for market data — never hit Kite/OI repeatedly):
  - ohlc:{symbol}:{interval}  → list of candle dicts (JSON)
  - oi_snapshot               → OI intelligence snapshot (JSON)
  - option_chain:{symbol}     → option chain payload (JSON)

If REDIS_URL is not set, uses in-memory dict with expiry (single-instance only).
"""

import json
import logging
import os
import time
from threading import Lock
from typing import Any

log = logging.getLogger("dashboard.cache")

# TTL seconds for all market data (avoid hammering Kite/OI APIs)
MARKET_DATA_TTL = 5

_redis_client: Any = None
_redis_available = False
_memory_cache: dict[str, tuple[Any, float]] = {}
_memory_lock = Lock()


def _get_redis():
    """Lazy-init Redis client. Returns None if REDIS_URL not set or connection fails."""
    global _redis_client, _redis_available
    if _redis_client is not None:
        return _redis_client if _redis_available else None
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        log.debug("REDIS_URL not set — using in-memory cache")
        return None
    try:
        import redis
        _redis_client = redis.from_url(url, decode_responses=True)
        _redis_client.ping()
        _redis_available = True
        log.info("Redis cache connected")
        return _redis_client
    except Exception as e:
        log.warning("Redis unavailable (%s) — using in-memory cache", e)
        _redis_available = False
        return None


def get(key: str) -> Any | None:
    """Get value from Redis or in-memory cache. Returns None if missing or expired."""
    r = _get_redis()
    if r is not None:
        try:
            raw = r.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            log.debug("Redis get error %s: %s", key, e)
            return None

    with _memory_lock:
        entry = _memory_cache.get(key)
        if entry is None:
            return None
        val, expires_at = entry
        if time.time() > expires_at:
            del _memory_cache[key]
            return None
        return val


def set(key: str, value: Any, ttl_seconds: int = MARKET_DATA_TTL) -> None:
    """Set value in Redis or in-memory cache with TTL."""
    r = _get_redis()
    if r is not None:
        try:
            r.setex(key, ttl_seconds, json.dumps(value, default=str))
        except Exception as e:
            log.debug("Redis set error %s: %s", key, e)
        return

    with _memory_lock:
        _memory_cache[key] = (value, time.time() + ttl_seconds)


def ohlc_key(symbol: str, interval: str) -> str:
    return f"ohlc:{symbol}:{interval}"


def option_chain_key(symbol: str) -> str:
    return f"option_chain:{symbol}"


OI_SNAPSHOT_KEY = "oi_snapshot"


def is_redis_available() -> bool:
    """True if Redis is connected (for health endpoint)."""
    return _get_redis() is not None
