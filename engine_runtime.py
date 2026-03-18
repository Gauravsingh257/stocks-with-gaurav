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

# Watchdog: if main loop doesn't update in this many seconds → force exit (Railway restarts)
ENGINE_CYCLE_WATCHDOG_SEC = 180  # 3 min; allows for slow data fetches

# Crash-loop detection: track watchdog kills to detect infinite restart loops
_watchdog_kill_times: list = []   # timestamps of recent os._exit(1) calls
_CRASH_LOOP_WINDOW_SEC = 300     # 5 min window
_CRASH_LOOP_MAX_KILLS = 3        # if >3 kills in 5 min → critical alert


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
    """
    r = _get_redis()
    if r is None:
        return True  # No Redis: allow run (e.g. local dev without Redis)
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


def write_last_cycle() -> None:
    """Write current timestamp to engine_last_cycle (call after each completed scan cycle)."""
    r = _get_redis()
    if r is None:
        return
    try:
        r.set(ENGINE_LAST_CYCLE_KEY, str(time.time()), ex=300)  # 5 min TTL
    except Exception as e:
        log.debug("write_last_cycle failed: %s", e)


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


def _heartbeat_loop() -> None:
    """Dedicated loop: write heartbeat every 30s. Runs in daemon thread so engine stall doesn't stop heartbeat."""
    while True:
        try:
            write_heartbeat()
        except Exception as e:
            log.debug("Heartbeat loop: %s", e)
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
            log.error(
                "ENGINE STUCK — main loop last cycled %.0fs ago (limit %ss). "
                "Forcing exit so Railway can restart.",
                age, ENGINE_CYCLE_WATCHDOG_SEC,
            )
            # Crash-loop detection via Redis (survives process restarts)
            _is_crash_loop = False
            try:
                _r = _get_redis()
                if _r:
                    _crash_key = "engine:watchdog_kills"
                    _r.rpush(_crash_key, str(time.time()))
                    _r.ltrim(_crash_key, -10, -1)   # Keep last 10 entries
                    _r.expire(_crash_key, _CRASH_LOOP_WINDOW_SEC)
                    _recent = _r.lrange(_crash_key, 0, -1) or []
                    _cutoff = time.time() - _CRASH_LOOP_WINDOW_SEC
                    _recent_count = sum(1 for ts in _recent if float(ts) > _cutoff)
                    _is_crash_loop = _recent_count >= _CRASH_LOOP_MAX_KILLS
            except Exception:
                pass

            # Send Telegram alert (best-effort, 5s timeout)
            try:
                import os as _os
                import requests as _req
                _bot = _os.getenv("TELEGRAM_BOT_TOKEN", "")
                _cid = _os.getenv("TELEGRAM_CHAT_ID", "")
                if _bot and _cid:
                    if _is_crash_loop:
                        _msg = (
                            "🚨 CRASH LOOP DETECTED — engine has restarted "
                            f"{len(_watchdog_kill_times)} times in 5 min.\n"
                            "Investigation needed. Check Railway deploy logs."
                        )
                    else:
                        _msg = "⚠️ ENGINE STUCK — main loop frozen. Forcing restart."
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
