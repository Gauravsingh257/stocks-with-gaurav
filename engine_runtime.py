"""
Engine runtime for 24/7 Railway deployment.

Provides:
- Redis lock: only one engine instance runs (acquire with TTL, refresh periodically).
- Heartbeat: dedicated thread writes engine_heartbeat = time.time() every 30s for dashboard.
- Signal deduplication: unique signal_id (strategy_timeframe_symbol_timestamp) in Redis before sending Telegram.
- Safe shutdown: on SIGTERM/SIGINT release Redis lock and exit cleanly.

All keys use constants shared with dashboard.backend.cache for heartbeat/status.
"""

import logging
import os
import signal
import sys
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("engine_runtime")

# Redis key names (must match dashboard.backend.cache)
ENGINE_LOCK_KEY = "engine_lock"
ENGINE_HEARTBEAT_KEY = "engine_heartbeat"
ENGINE_STARTED_AT_KEY = "engine_started_at"
ENGINE_VERSION_KEY = "engine_version"
ENGINE_LAST_CYCLE_KEY = "engine_last_cycle"
ENGINE_SNAPSHOT_KEY = "engine:snapshot"
ENGINE_SNAPSHOT_TTL_SEC = 600  # 10 min — gives engine ample time between cycles
ENGINE_LAST_WRITE_TS_KEY = "engine:last_write_ts"
SNAPSHOT_VERSION_KEY = "snapshot:version"
SNAPSHOT_META_KEY = "snapshot:meta"
SNAPSHOT_WRITE_STATUS_KEY = "snapshot:write_status"
SNAPSHOT_SIZE_KEY = "snapshot:size_bytes"
SNAPSHOT_SYMBOL_COUNT_KEY = "snapshot:symbol_count"

SNAPSHOT_REFRESH_INTERVAL_SEC = int(os.getenv("SNAPSHOT_REFRESH_INTERVAL_SEC", "15"))
SNAPSHOT_WRITE_STALE_SEC = int(os.getenv("SNAPSHOT_WRITE_STALE_SEC", "120"))
LTP_KEY_NIFTY = "ltp:NIFTY"
LTP_KEY_BANKNIFTY = "ltp:BANKNIFTY"
LTP_TTL_SEC = 300

# Lock: shorter TTL so a frozen (non-crashing) engine doesn't block restarts for long
LOCK_TTL = 600
LOCK_REFRESH_INTERVAL = 120
ENGINE_LOCK_TTL_SEC = LOCK_TTL
ENGINE_LOCK_REFRESH_INTERVAL_SEC = LOCK_REFRESH_INTERVAL
ENGINE_HEARTBEAT_INTERVAL_SEC = 30
SIGNAL_DEDUPE_TTL_SEC = 3600

_redis_client = None
_lock_holder = False
_lock_refresh_at = 0.0
_shutdown_registered = False
_heartbeat_thread_started = False
_watchdog_thread_started = False
_last_cycle_local: float = 0.0  # Updated by write_last_cycle(); watched by watchdog

_snapshot_refresher_started = False

# Watchdog: if main loop doesn't update in this many seconds → force exit (Railway restarts)
# TRADE_MONITOR inner loop pings write_last_cycle() every 10s, so 180s gives ample margin.
ENGINE_CYCLE_WATCHDOG_SEC = 180  # 3 min; TRADE_MONITOR keeps-alive every 10s

# Crash-loop detection (Redis-based, survives restarts)
_CRASH_LOOP_WINDOW_SEC = 300     # 5 min window
_CRASH_LOOP_MAX_KILLS = 3        # if >=3 kills in 5 min → critical alert

# Loop stage tracker: set by engine before each major phase so crash alerts
# include WHERE the engine was stuck (e.g. "DATA_FETCH", "STRATEGY", "SIGNAL").
engine_stage: str = "INIT"


def _get_redis():
    """Lazy-init Redis client from REDIS_URL. Returns None if not set or connection fails."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        log.debug("REDIS_URL not set — Redis lock/heartbeat disabled")
        return None
    try:
        import redis
        _redis_client = redis.from_url(url, decode_responses=True)
        _redis_client.ping()
        log.info("Engine runtime: Redis connected")
        return _redis_client
    except Exception as e:
        log.warning("Engine runtime: Redis unavailable — %s", e)
        return None


def acquire_engine_lock() -> bool:
    """
    Try to acquire the global engine lock in Redis (NX + TTL).
    On success, writes engine_started_at = time.time() for dashboard uptime.
    Returns True if this process acquired the lock, False if another instance holds it.
    
    P0-4: On Railway (RAILWAY_ENVIRONMENT set), Redis is REQUIRED — fail-closed.
    Locally (no RAILWAY_ENVIRONMENT), allow run without Redis for dev convenience.
    """
    r = _get_redis()
    if r is None:
        if os.getenv("RAILWAY_ENVIRONMENT"):
            log.critical("Redis unavailable on Railway — refusing to start (fail-closed). "
                         "Risk: duplicate engine instances without Redis lock.")
            return False
        log.warning("No Redis — running without lock (local dev mode)")
        return True  # Local dev: allow run without Redis
    try:
        acquired = r.set(ENGINE_LOCK_KEY, str(os.getpid()), nx=True, ex=ENGINE_LOCK_TTL_SEC)
        if acquired:
            global _lock_holder, _lock_refresh_at
            _lock_holder = True
            _lock_refresh_at = time.time()
            r.set(ENGINE_STARTED_AT_KEY, str(time.time()), ex=86400)  # 24h TTL for uptime display
            log.info("Engine lock acquired (Redis). PID=%s", os.getpid())
            return True
        log.warning("Another engine instance holds the lock. Exiting.")
        return False
    except Exception as e:
        log.error("Failed to acquire engine lock: %s", e)
        if os.getenv("RAILWAY_ENVIRONMENT"):
            log.critical("Redis lock acquisition failed on Railway — refusing to start (fail-closed).")
            return False
        return False


def set_engine_version(version: str) -> None:
    """Write running engine version to Redis (call after acquiring lock). TTL 24h."""
    r = _get_redis()
    if r is None:
        return
    try:
        r.set(ENGINE_VERSION_KEY, str(version), ex=86400)
        log.info("Engine version written to Redis: %s", version)
    except Exception as e:
        log.debug("set_engine_version failed: %s", e)


def refresh_engine_lock() -> bool:
    """Refresh lock TTL if we hold it. Call periodically from main loop."""
    global _lock_holder, _lock_refresh_at
    if not _lock_holder:
        return False
    r = _get_redis()
    if r is None:
        return True
    now = time.time()
    if now - _lock_refresh_at < ENGINE_LOCK_REFRESH_INTERVAL_SEC:
        return True
    try:
        # Only refresh if we still own the lock (value matches our PID)
        current = r.get(ENGINE_LOCK_KEY)
        if current == str(os.getpid()):
            r.expire(ENGINE_LOCK_KEY, ENGINE_LOCK_TTL_SEC)
            _lock_refresh_at = now
            log.debug("Engine lock TTL refreshed")
            return True
        _lock_holder = False
        return False
    except Exception as e:
        log.warning("Failed to refresh engine lock (Redis error — keeping run): %s", e)
        return True  # Don't exit on transient Redis errors; assume we still hold the lock


def release_engine_lock() -> None:
    """Release the engine lock (delete key). Call on shutdown."""
    global _lock_holder
    if not _lock_holder:
        return
    r = _get_redis()
    if r is None:
        return
    try:
        current = r.get(ENGINE_LOCK_KEY)
        if current == str(os.getpid()):
            r.delete(ENGINE_LOCK_KEY)
            log.info("Engine lock released (Redis)")
        _lock_holder = False
    except Exception as e:
        log.error("Failed to release engine lock: %s", e)
    _lock_holder = False


def write_heartbeat() -> None:
    """Write current timestamp to engine_heartbeat key. Used by heartbeat_loop every 30s."""
    r = _get_redis()
    if r is None:
        return
    try:
        r.set(ENGINE_HEARTBEAT_KEY, str(time.time()), ex=120)  # 2 min TTL if engine dies
    except Exception as e:
        log.debug("Heartbeat write failed: %s", e)


def write_engine_snapshot(snapshot: dict) -> None:
    """
    Atomically write dashboard snapshot to Redis using a pipeline.

    Writes:
      engine:snapshot (TTL 600s) — primary live data
      snapshot:last_known_good (TTL 24h) — fallback for when engine is down

    Uses Redis MULTI/EXEC pipeline so both keys update atomically —
    no partial state visible to readers.
    """
    r = _get_redis()
    if r is None:
        return

    def _snapshot_symbol_count(snap: dict) -> int:
        """Best-effort count of unique symbols represented in the snapshot."""
        syms: set[str] = set()
        try:
            for t in (snap.get("active_trades") or []):
                if isinstance(t, dict):
                    s = str(t.get("symbol") or "").strip()
                    if s:
                        syms.add(s.upper())
        except Exception:
            pass
        for k in ("zone_state", "setup_d_state", "setup_e_state"):
            try:
                d = snap.get(k)
                if isinstance(d, dict):
                    for s in d.keys():
                        if s:
                            syms.add(str(s).upper())
            except Exception:
                pass
        return len(syms)

    def _snapshot_has_any_payload(snap: dict) -> bool:
        """Return True if the snapshot has non-empty payload (prevents bad empty overwrites)."""
        if not isinstance(snap, dict) or not snap:
            return False
        # Consider index_ltp as a critical non-empty signal that the snapshot isn't blank.
        idx = snap.get("index_ltp")
        if isinstance(idx, dict) and any(isinstance(v, (int, float)) for v in idx.values()):
            return True
        # Any of these being non-empty counts as "payload".
        for k in ("active_trades", "zone_state", "setup_d_state", "setup_e_state"):
            v = snap.get(k)
            if isinstance(v, list) and len(v) > 0:
                return True
            if isinstance(v, dict) and len(v) > 0:
                return True
        # Fallback: signals_today > 0 implies something happened today (avoid overriding with 0-only blanks)
        try:
            if int(snap.get("signals_today") or 0) > 0:
                return True
        except Exception:
            pass
        return False

    # Start refresh-ahead thread (keeps TTL alive without rewriting data)
    _ensure_snapshot_refresher()

    try:
        import json
        now = time.time()
        symbol_count = _snapshot_symbol_count(snapshot)
        is_valid = _snapshot_has_any_payload(snapshot)

        # Minimum threshold protection (only applies to symbol-bearing payload; avoid blocking market-close snapshots)
        min_symbols = int(os.getenv("SNAPSHOT_MIN_SYMBOLS", "5"))
        if is_valid and symbol_count > 0 and symbol_count < min_symbols:
            is_valid = False

        if not is_valid:
            meta = {
                "ts": now,
                "ok": False,
                "reason": "invalid_or_empty_snapshot",
                "symbol_count": symbol_count,
            }
            try:
                pipe = r.pipeline(transaction=True)
                pipe.setex(SNAPSHOT_META_KEY, 600, json.dumps(meta, default=str))
                pipe.setex(SNAPSHOT_WRITE_STATUS_KEY, 600, "skipped_invalid")
                pipe.execute()
            except Exception:
                pass
            return  # CRITICAL: do not overwrite engine:snapshot nor last_known_good

        # Versioning for readers/debug: monotonic-ish via ms timestamp
        version = int(now * 1000)
        snapshot["_written_at"] = now
        snapshot["_version"] = version

        payload = json.dumps(snapshot, default=str)
        size_bytes = len(payload.encode("utf-8"))

        meta = {
            "ts": now,
            "ok": True,
            "version": version,
            "size_bytes": size_bytes,
            "symbol_count": symbol_count,
        }

        pipe = r.pipeline(transaction=True)
        # Main + fallback snapshot keys
        pipe.setex(ENGINE_SNAPSHOT_KEY, ENGINE_SNAPSHOT_TTL_SEC, payload)
        pipe.setex("snapshot:last_known_good", 86400, payload)

        # Write watchdog + debug/meta keys
        pipe.setex(ENGINE_LAST_WRITE_TS_KEY, 600, str(now))
        pipe.setex(SNAPSHOT_VERSION_KEY, 86400, str(version))
        pipe.setex(SNAPSHOT_META_KEY, 600, json.dumps(meta, default=str))
        pipe.setex(SNAPSHOT_WRITE_STATUS_KEY, 600, "success")
        pipe.setex(SNAPSHOT_SIZE_KEY, 600, str(size_bytes))
        pipe.setex(SNAPSHOT_SYMBOL_COUNT_KEY, 600, str(symbol_count))
        pipe.execute()
    except Exception as e:
        try:
            r.setex(SNAPSHOT_WRITE_STATUS_KEY, 600, "failed")
        except Exception:
            pass
        log.debug("write_engine_snapshot failed: %s", e)


def _snapshot_refresher_loop() -> None:
    """Refresh-ahead: keep snapshot TTL alive without rewriting the payload."""
    while True:
        try:
            r = _get_redis()
            if r is not None:
                ttl = r.ttl(ENGINE_SNAPSHOT_KEY)
                # If key exists and TTL is getting low, extend it. Do NOT overwrite value.
                if isinstance(ttl, int) and ttl > 0 and ttl < (ENGINE_SNAPSHOT_TTL_SEC // 2):
                    pipe = r.pipeline(transaction=True)
                    pipe.expire(ENGINE_SNAPSHOT_KEY, ENGINE_SNAPSHOT_TTL_SEC)
                    pipe.execute()
        except Exception:
            pass
        time.sleep(max(5, SNAPSHOT_REFRESH_INTERVAL_SEC))


def _ensure_snapshot_refresher() -> None:
    """Start refresher thread once. Safe to call repeatedly."""
    global _snapshot_refresher_started
    if _snapshot_refresher_started:
        return
    _snapshot_refresher_started = True
    t = threading.Thread(target=_snapshot_refresher_loop, daemon=True, name="snapshot-refresher")
    t.start()


def set_index_ltp(nifty: Optional[float] = None, banknifty: Optional[float] = None) -> None:
    """
    Write NIFTY/BANKNIFTY LTP to Redis so dashboard command bar shows live index prices.
    Keys match dashboard.backend.cache (ltp:NIFTY, ltp:BANKNIFTY). Call when engine has LTP.
    """
    r = _get_redis()
    if r is None:
        return
    try:
        if nifty is not None:
            r.setex(LTP_KEY_NIFTY, LTP_TTL_SEC, str(nifty))
        if banknifty is not None:
            r.setex(LTP_KEY_BANKNIFTY, LTP_TTL_SEC, str(banknifty))
    except Exception as e:
        log.debug("set_index_ltp failed: %s", e)


# Redis runtime health: consecutive failures before forced shutdown on Railway
_REDIS_HEALTH_MAX_FAILURES = 5  # 5 × 30s heartbeat interval = 2.5 min tolerance
_redis_consecutive_failures: int = 0


def _heartbeat_loop() -> None:
    """Dedicated loop: write heartbeat every 30s. Runs in daemon thread so engine stall doesn't stop heartbeat.
    P0-R2: Also monitors Redis connectivity — if lost on Railway for too long, force shutdown."""
    global _redis_consecutive_failures
    while True:
        write_heartbeat()
        # P0-R2: Explicit Redis health probe (write_heartbeat swallows errors)
        if os.getenv("RAILWAY_ENVIRONMENT"):
            try:
                r = _get_redis()
                if r is not None:
                    r.ping()
                    _redis_consecutive_failures = 0
                else:
                    raise ConnectionError("Redis client is None")
            except Exception:
                # Force client re-creation on next call
                global _redis_client
                _redis_client = None
                _redis_consecutive_failures += 1
                log.warning("Redis health check failed (%d/%d)",
                            _redis_consecutive_failures, _REDIS_HEALTH_MAX_FAILURES)
                if _redis_consecutive_failures >= _REDIS_HEALTH_MAX_FAILURES:
                    log.critical("Redis unreachable for %d consecutive heartbeats on Railway — "
                                 "forcing shutdown (risk: duplicate instances without lock)",
                                 _redis_consecutive_failures)
                    try:
                        import requests as _req
                        _bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
                        _cid = os.getenv("TELEGRAM_CHAT_ID", "")
                        if _bot and _cid:
                            _req.post(
                                f"https://api.telegram.org/bot{_bot}/sendMessage",
                                data={"chat_id": _cid,
                                      "text": "🚨 ENGINE SHUTDOWN: Redis lost for >2.5 min on Railway. "
                                              "Lock integrity at risk. Forcing restart."},
                                timeout=5,
                            )
                    except Exception:
                        pass
                    os._exit(1)
        time.sleep(ENGINE_HEARTBEAT_INTERVAL_SEC)


def start_heartbeat_thread() -> None:
    """Start the daemon thread that writes engine_heartbeat every 30 seconds. Idempotent."""
    global _heartbeat_thread_started
    if _heartbeat_thread_started:
        return
    _heartbeat_thread_started = True
    t = threading.Thread(target=_heartbeat_loop, daemon=True)
    t.start()
    log.info("Engine heartbeat thread started (interval=%ss)", ENGINE_HEARTBEAT_INTERVAL_SEC)


def write_last_cycle() -> None:
    """
    Call this at the TOP of each main scan loop iteration.
    Updates both local timestamp (watched by watchdog) and Redis key (shown on dashboard).
    """
    global _last_cycle_local
    _last_cycle_local = time.time()
    r = _get_redis()
    if r is None:
        return
    try:
        r.set(ENGINE_LAST_CYCLE_KEY, str(time.time()), ex=300)
    except Exception as e:
        log.debug("write_last_cycle Redis failed: %s", e)


# ── CRITICAL: Use safe_sleep() instead of time.sleep() in ALL engine code ────
# Direct time.sleep(>60s) will trigger the watchdog and crash the engine.
# safe_sleep() pings the watchdog every 10 seconds during the wait.
def safe_sleep(total_seconds: int) -> None:
    """
    Sleep for `total_seconds` in 10-second chunks, pinging the watchdog
    before each chunk.  This prevents the watchdog from killing the engine
    during legitimate idle periods (market closed, signal window paused, etc.).

    ALWAYS use this instead of time.sleep() in engine code.
    """
    chunks = max(1, total_seconds // 10)
    remainder = total_seconds % 10
    for _ in range(chunks):
        write_last_cycle()
        time.sleep(10)
    if remainder > 0:
        write_last_cycle()
        time.sleep(remainder)


def set_engine_stage(stage: str) -> None:
    """Update the current engine stage label (e.g. 'DATA_FETCH', 'STRATEGY', 'SIGNAL').
    Included in watchdog crash alerts for instant debugging."""
    global engine_stage
    engine_stage = stage


def _watchdog_loop() -> None:
    """
    Daemon thread: checks that the main engine loop runs at least every
    ENGINE_CYCLE_WATCHDOG_SEC seconds.  If it stalls longer than that,
    it is considered frozen — we force os._exit(1) so Railway restarts the container.

    The heartbeat thread runs independently and keeps beating even if the main
    loop is frozen, which is why we need a SEPARATE watchdog keyed to main-loop progress.
    """
    # Give the engine 5 minutes to do initial heavy imports / prefetch before watching.
    time.sleep(300)
    log.info("Watchdog active (threshold=%ss)", ENGINE_CYCLE_WATCHDOG_SEC)
    while True:
        time.sleep(30)
        if _last_cycle_local == 0.0:
            continue  # not yet started
        age = time.time() - _last_cycle_local
        if age > ENGINE_CYCLE_WATCHDOG_SEC:
            _stage = engine_stage
            log.error(
                "ENGINE STUCK at stage '%s' — main loop last cycled %.0fs ago "
                "(limit %ss). Forcing exit so Railway can restart.",
                _stage, age, ENGINE_CYCLE_WATCHDOG_SEC,
            )
            # Crash-loop detection via Redis (survives process restarts)
            _is_crash_loop = False
            _recent_count = 1
            try:
                _r = _get_redis()
                if _r:
                    _crash_key = "engine:watchdog_kills"
                    _r.rpush(_crash_key, str(time.time()))
                    _r.ltrim(_crash_key, -10, -1)
                    _r.expire(_crash_key, _CRASH_LOOP_WINDOW_SEC)
                    _recent = _r.lrange(_crash_key, 0, -1) or []
                    _cutoff = time.time() - _CRASH_LOOP_WINDOW_SEC
                    _recent_count = sum(1 for ts in _recent if float(ts) > _cutoff)
                    _is_crash_loop = _recent_count >= _CRASH_LOOP_MAX_KILLS
            except Exception:
                pass

            # Send Telegram alert with stage info (best-effort, 5s timeout)
            try:
                import os as _os
                import requests as _req
                _bot = _os.getenv("TELEGRAM_BOT_TOKEN", "")
                _cid = _os.getenv("TELEGRAM_CHAT_ID", "")
                if _bot and _cid:
                    if _is_crash_loop:
                        _msg = (
                            f"🚨 CRASH LOOP DETECTED — engine restarted "
                            f"{_recent_count} times in 5 min.\n"
                            f"Last stage: {_stage}\n"
                            "Investigation needed. Check Railway deploy logs."
                        )
                    else:
                        _msg = (
                            f"⚠️ ENGINE STUCK at: {_stage}\n"
                            f"Main loop frozen for {int(age)}s. Forcing restart."
                        )
                    _req.post(
                        f"https://api.telegram.org/bot{_bot}/sendMessage",
                        data={"chat_id": _cid, "text": _msg},
                        timeout=5,
                    )
            except Exception:
                pass
            import os
            os._exit(1)


def start_watchdog_thread() -> None:
    """Start the cycle watchdog daemon thread. Call once after engine starts. Idempotent."""
    global _watchdog_thread_started
    if _watchdog_thread_started:
        return
    _watchdog_thread_started = True
    t = threading.Thread(target=_watchdog_loop, daemon=True, name="engine-watchdog")
    t.start()
    log.info("Engine cycle watchdog started (max_stall=%ss)", ENGINE_CYCLE_WATCHDOG_SEC)


def should_send_signal(signal_id: str) -> bool:
    """
    Returns True if this signal_id has not been sent recently (dedupe).
    If True, caller should send Telegram then call mark_signal_sent(signal_id).
    """
    r = _get_redis()
    if r is None:
        return True
    try:
        exists = r.get(signal_id)
        return exists is None
    except Exception as e:
        log.debug("Signal dedupe check failed: %s", e)
        return True


def mark_signal_sent(signal_id: str) -> None:
    """Mark signal_id as sent so duplicates are skipped. TTL = 1 hour."""
    r = _get_redis()
    if r is None:
        return
    try:
        r.setex(signal_id, SIGNAL_DEDUPE_TTL_SEC, "sent")
    except Exception as e:
        log.debug("Signal dedupe mark failed: %s", e)


def register_shutdown(release_lock_fn: Optional[Callable[[], None]] = None) -> None:
    """
    Register SIGTERM/SIGINT handlers to release Redis lock and optionally call release_lock_fn.
    Idempotent.
    """
    global _shutdown_registered
    if _shutdown_registered:
        return
    _shutdown_registered = True

    def _handle(signum, frame):
        reason = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        log.info("Engine shutting down safely (reason: %s)", reason)
        release_engine_lock()
        if release_lock_fn:
            try:
                release_lock_fn()
            except Exception as e:
                log.error("Shutdown callback error: %s", e)
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
        log.info("Engine runtime: SIGTERM/SIGINT handlers registered")
    except (OSError, AttributeError):
        pass
