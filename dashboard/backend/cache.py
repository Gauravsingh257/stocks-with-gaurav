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

# Worker heartbeat: last time market_engine.py successfully updated cache (epoch seconds)
MARKET_ENGINE_LAST_UPDATE_KEY = "market_engine:last_update"
WORKER_HEARTBEAT_TTL = 60  # seconds; if worker dies, key expires and health reports stale

# Real-time LTP (from Kite WebSocket tick stream); no TTL so value persists
LTP_KEY_PREFIX = "ltp:"
LTP_TTL = 300  # 5 min TTL so stale data expires if tick stream stops
LTP_UPDATES_CHANNEL = "ltp_updates"
CANDLE_KEY_PREFIX = "candle:"
CANDLE_MAX_BARS = 500
CANDLE_TTL = 86400  # 24h for tick-built candles


def ltp_key(symbol: str) -> str:
    """Redis key for live LTP. symbol: NIFTY or BANKNIFTY."""
    return f"{LTP_KEY_PREFIX}{symbol}"


def candle_key(symbol: str, interval: str) -> str:
    """Redis key for tick-aggregated candles. interval: 1m, 5m, 15m."""
    return f"{CANDLE_KEY_PREFIX}{interval}:{symbol}"


def set_ltp(symbol: str, value: float) -> None:
    """Set LTP in Redis (and optional in-memory) for real-time command bar."""
    key = ltp_key(symbol)
    r = _get_redis()
    if r is not None:
        try:
            r.setex(key, LTP_TTL, str(value))
        except Exception as e:
            log.debug("Redis set_ltp error %s: %s", key, e)
        return
    with _memory_lock:
        _memory_cache[key] = (value, time.time() + LTP_TTL)


def get_ltp(symbol: str) -> float | None:
    """Get LTP from Redis (or in-memory). Returns None if missing."""
    key = ltp_key(symbol)
    r = _get_redis()
    if r is not None:
        try:
            raw = r.get(key)
            if raw is None:
                return None
            return float(raw)
        except (TypeError, ValueError) as e:
            log.debug("Redis get_ltp parse error %s: %s", key, e)
            return None
    with _memory_lock:
        entry = _memory_cache.get(key)
        if entry is None:
            return None
        val, expires_at = entry
        if time.time() > expires_at:
            del _memory_cache[key]
            return None
        return float(val) if isinstance(val, (int, float)) else None


def publish_ltp_update(payload: dict) -> None:
    """Publish LTP payload to Redis channel for WebSocket broadcast. Keys: NIFTY 50, NIFTY BANK."""
    r = _get_redis()
    if r is None:
        return
    try:
        import json
        r.publish(LTP_UPDATES_CHANNEL, json.dumps(payload, default=str))
    except Exception as e:
        log.debug("Redis publish_ltp error: %s", e)


def get_candle_list(symbol: str, interval: str) -> list:
    """Get list of candles from Redis (tick-built). Returns [] if missing."""
    key = candle_key(symbol, interval)
    raw = get(key)
    if isinstance(raw, list):
        return raw
    return []


def append_candle(symbol: str, interval: str, candle: dict) -> None:
    """Append one candle and trim to CANDLE_MAX_BARS. Candle: {time, open, high, low, close, volume}."""
    key = candle_key(symbol, interval)
    data = get_candle_list(symbol, interval)
    data.append(candle)
    data = data[-CANDLE_MAX_BARS:]
    r = _get_redis()
    if r is not None:
        try:
            r.setex(key, CANDLE_TTL, json.dumps(data, default=str))
        except Exception as e:
            log.debug("Redis append_candle error %s: %s", key, e)
        return
    with _memory_lock:
        _memory_cache[key] = (data, time.time() + CANDLE_TTL)


def is_redis_available() -> bool:
    """True if Redis is connected (for health endpoint)."""
    return _get_redis() is not None
