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
_redis_last_ok: float = 0.0        # epoch sec of last successful ping
_redis_last_attempt: float = 0.0   # epoch sec of last connect attempt
_REDIS_RETRY_SEC = 30.0            # how long to wait before retrying a failed connection
_memory_cache: dict[str, tuple[Any, float]] = {}
_memory_lock = Lock()


def _get_redis():
    """
    Lazy-init Redis client with automatic reconnect every 30 s.
    If Redis was unavailable it will be retried after _REDIS_RETRY_SEC — this means
    a Redis restart no longer permanently blanks the dashboard.
    """
    global _redis_client, _redis_available, _redis_last_ok, _redis_last_attempt
    now = time.time()

    # Fast path: already connected
    if _redis_available and _redis_client is not None:
        return _redis_client

    # No REDIS_URL configured — in-memory only
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None

    # Rate-limit reconnect attempts so we don't spam the log
    if now - _redis_last_attempt < _REDIS_RETRY_SEC:
        return None

    _redis_last_attempt = now
    try:
        import redis as _redis_lib
        if _redis_client is None:
            _redis_client = _redis_lib.from_url(url, decode_responses=True)
        _redis_client.ping()
        was_down = not _redis_available
        _redis_available = True
        _redis_last_ok = now
        if was_down:
            log.info("Redis cache connected/reconnected")
        return _redis_client
    except Exception as e:
        if _redis_available:
            log.warning("Redis connection lost (%s) — falling back to in-memory cache", e)
        else:
            log.debug("Redis still unavailable: %s", e)
        _redis_available = False
        return None


def get_redis_status() -> dict:
    """Return Redis connectivity info for /api/system/health."""
    available = _redis_available and _redis_client is not None
    last_ok_sec_ago = round(time.time() - _redis_last_ok, 1) if _redis_last_ok > 0 else None
    return {"available": available, "last_ok_sec_ago": last_ok_sec_ago}


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

# Engine worker (smc_mtf_engine_v4) heartbeat for Railway 24/7 — written by engine every 30s
ENGINE_HEARTBEAT_KEY = "engine_heartbeat"
ENGINE_STARTED_AT_KEY = "engine_started_at"
ENGINE_VERSION_KEY = "engine_version"
ENGINE_LAST_CYCLE_KEY = "engine_last_cycle"
ENGINE_SNAPSHOT_KEY = "engine:snapshot"  # Full snapshot from engine (standalone mode)
ENGINE_HEARTBEAT_STALE_SEC = 60   # if older than this, dashboard shows ENGINE STALE
ENGINE_HEARTBEAT_OFFLINE_SEC = 120  # if older than this, engine_status = offline

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


def get_ltp_with_age(symbol: str) -> tuple[float, int] | None:
    """Get LTP + age in seconds from Redis (or in-memory).

    Returns ``(price, age_sec)`` or ``None`` if the key is missing / expired.
    Age is derived from the remaining TTL of the Redis key (``LTP_TTL - ttl``).
    For in-memory fallback, age is computed from the stored write time.

    This is the function imported by ``services.price_resolver._read_ws_cache``
    to power the Tier-1 (WebSocket cache) price resolution path.
    """
    key = ltp_key(symbol)
    r = _get_redis()
    if r is not None:
        try:
            raw = r.get(key)
            if raw is None:
                return None
            price = float(raw)
            ttl = r.ttl(key)
            # ttl > 0  → key exists with expiry; compute age = LTP_TTL - remaining
            # ttl == -1 → key exists, no expiry (treat as age 0)
            # ttl == -2 → key doesn't exist (shouldn't reach here)
            if isinstance(ttl, int) and ttl > 0:
                age = max(0, LTP_TTL - ttl)
            else:
                age = 0
            return (price, age)
        except (TypeError, ValueError) as e:
            log.debug("Redis get_ltp_with_age parse error %s: %s", key, e)
            return None
    # In-memory fallback
    with _memory_lock:
        entry = _memory_cache.get(key)
        if entry is None:
            return None
        val, expires_at = entry
        if time.time() > expires_at:
            del _memory_cache[key]
            return None
        age = max(0, int(time.time() - (expires_at - LTP_TTL)))
        if isinstance(val, (int, float)):
            return (float(val), age)
        return None


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


def upsert_candle(symbol: str, interval: str, candle: dict) -> None:
    """
    Write candle with duplicate prevention: if the last stored candle has the same
    timestamp (minute boundary), overwrite it; otherwise append. Then trim to CANDLE_MAX_BARS.
    Ensures Redis candles merge cleanly with Kite historical (no duplicate times).
    """
    key = candle_key(symbol, interval)
    data = get_candle_list(symbol, interval)
    candle_time = int(candle.get("time", 0))
    if data and len(data) > 0:
        try:
            last_ts = int(data[-1].get("time", -1))
        except (TypeError, ValueError):
            last_ts = -1
        if last_ts == candle_time:
            data[-1] = candle
        else:
            data.append(candle)
    else:
        data.append(candle)
    data = data[-CANDLE_MAX_BARS:]
    r = _get_redis()
    if r is not None:
        try:
            r.setex(key, CANDLE_TTL, json.dumps(data, default=str))
        except Exception as e:
            log.debug("Redis upsert_candle error %s: %s", key, e)
        return
    with _memory_lock:
        _memory_cache[key] = (data, time.time() + CANDLE_TTL)


def is_redis_available() -> bool:
    """True if Redis is connected (for health endpoint)."""
    return _get_redis() is not None


def get_engine_heartbeat_ts() -> float | None:
    """
    Return last engine heartbeat timestamp (epoch seconds) from Redis.
    Used by dashboard when engine runs as separate Railway service (no in-process engine).
    Returns None if key missing or Redis unavailable.
    """
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(ENGINE_HEARTBEAT_KEY)
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def get_engine_started_at() -> float | None:
    """
    Return engine start timestamp (epoch seconds) from Redis.
    Set when the engine acquires the lock. Use with time.time() to show uptime (e.g. "3h 25m").
    Returns None if key missing or Redis unavailable.
    """
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(ENGINE_STARTED_AT_KEY)
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def get_engine_version() -> str | None:
    """
    Return running engine version string from Redis (e.g. "v4.2.1").
    Set when the engine acquires the lock. Returns None if key missing or Redis unavailable.
    """
    r = _get_redis()
    if r is None:
        return None
    try:
        return r.get(ENGINE_VERSION_KEY)
    except Exception:
        return None


def get_engine_last_cycle() -> float | None:
    """
    Return last scan cycle timestamp (epoch seconds) from Redis.
    Updated after each completed strategy scan cycle. Use with time.time() for engine_last_cycle_age_sec.
    Returns None if key missing or Redis unavailable.
    """
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(ENGINE_LAST_CYCLE_KEY)
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def get_engine_snapshot_from_redis() -> dict | None:
    """
    Return the engine snapshot written by engine_runtime.write_engine_snapshot (standalone mode).
    Contains: active_trades, signals_today, daily_pnl_r, traded_today, index_ltp, timestamp.
    Returns None if key missing, expired, or Redis unavailable.
    """
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(ENGINE_SNAPSHOT_KEY)
        if raw is None:
            return None
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
