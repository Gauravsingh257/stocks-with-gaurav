"""
dashboard/backend/state_bridge.py

Redis-first snapshot reader for the dashboard API.

ARCHITECTURE:
  The engine (Railway worker) writes a complete snapshot to Redis every scan
  cycle via engine_runtime.write_engine_snapshot(). This module reads ONLY
  from Redis — no live engine import, no in-process globals, no SQLite
  fallback. This guarantees:
    - All API instances serve identical data (no per-process divergence)
    - Data survives web service restarts
    - Zero dependency on engine process being co-located

FALLBACK SAFETY:
  If the Redis snapshot key has expired (engine down), the last known good
  snapshot is preserved in a dedicated Redis key (snapshot:last_known_good)
  and served with a stale=True flag. The API NEVER returns empty data.
"""

import json
import logging
import time as _time_mod
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

_IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

# Redis keys
_SNAPSHOT_KEY = "engine:snapshot"
_LAST_KNOWN_GOOD_KEY = "snapshot:last_known_good"
_LAST_KNOWN_GOOD_TTL = 86400  # 24h — survive overnight, full market close
_ENGINE_LAST_WRITE_TS_KEY = "engine:last_write_ts"
_SNAPSHOT_META_KEY = "snapshot:meta"
_SNAPSHOT_WRITE_STATUS_KEY = "snapshot:write_status"
_SNAPSHOT_VERSION_KEY = "snapshot:version"

_WRITE_STALE_THRESHOLD_SEC = int(__import__("os").getenv("SNAPSHOT_WRITE_STALE_SEC", "120"))

# Empty scaffold — returned only if NO snapshot has EVER been written
_EMPTY_SNAPSHOT: Dict[str, Any] = {
    "active_trades": [],
    "active_trade_count": 0,
    "zone_state": {},
    "daily_pnl_r": 0.0,
    "consecutive_losses": 0,
    "signals_today": 0,
    "traded_today": [],
    "circuit_breaker_active": False,
    "market_regime": "NEUTRAL",
    "max_daily_loss_r": -3.0,
    "max_daily_signals": 5,
    "engine_mode": "UNKNOWN",
    "active_strategies": {},
    "index_only": True,
    "paper_mode": False,
    "setup_d_state": {},
    "setup_e_state": {},
    "adaptive_intel": {
        "setup_multipliers": {},
        "recent_blocks": [],
        "recent_ai_scores": [],
    },
    "engine_live": False,
    "engine_running": False,
    "engine_heartbeat_age_sec": None,
    "engine_started_at": None,
    "engine_version": None,
    "engine_last_cycle": None,
    "engine_last_cycle_age_sec": None,
    "snapshot_time": None,
    "index_ltp": {},
    "data_source": "none",
    "redis_available": False,
    "stale": True,
    "stale_reason": "no_snapshot_ever_written",
}


def _get_redis():
    """Get Redis client from cache module (shared connection pool)."""
    try:
        from dashboard.backend.cache import _get_redis as _cache_redis
        return _cache_redis()
    except Exception:
        return None


def _read_snapshot_from_redis(r) -> Optional[Dict]:
    """Read the live engine snapshot from Redis. Returns None if missing/expired."""
    try:
        raw = r.get(_SNAPSHOT_KEY)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("Failed to read engine snapshot from Redis: %s", e)
        return None


def _read_last_known_good(r) -> Optional[Dict]:
    """Read the fallback snapshot. Returns None if never written."""
    try:
        raw = r.get(_LAST_KNOWN_GOOD_KEY)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("Failed to read last_known_good snapshot: %s", e)
        return None


def _snapshot_is_valid(snap: Dict) -> bool:
    """Strict but safe: reject clearly bad snapshots (empty dict, wrong types)."""
    if not isinstance(snap, dict) or not snap:
        return False
    # Must at least have expected core keys with correct container types.
    if "active_trades" in snap and not isinstance(snap.get("active_trades"), list):
        return False
    if "index_ltp" in snap and snap.get("index_ltp") is not None and not isinstance(snap.get("index_ltp"), dict):
        return False
    # Reject fully empty payloads (prevents poisoning the UI with blanks)
    idx = snap.get("index_ltp") or {}
    has_index = isinstance(idx, dict) and len(idx) > 0
    has_trades = isinstance(snap.get("active_trades"), list) and len(snap.get("active_trades")) > 0
    has_zone = isinstance(snap.get("zone_state"), dict) and len(snap.get("zone_state")) > 0
    has_setups = any(isinstance(snap.get(k), dict) and len(snap.get(k)) > 0 for k in ("setup_d_state", "setup_e_state"))
    if not (has_index or has_trades or has_zone or has_setups):
        return False
    return True


def _engine_write_is_stale(r) -> bool:
    """Write watchdog: if engine hasn't written recently, treat snapshot as stale."""
    try:
        raw = r.get(_ENGINE_LAST_WRITE_TS_KEY)
        if raw is None:
            return True
        ts = float(raw)
        return (_time_mod.time() - ts) > _WRITE_STALE_THRESHOLD_SEC
    except Exception:
        return True


def _write_last_known_good(r, snapshot: Dict) -> None:
    """Persist a good snapshot as fallback (24h TTL)."""
    try:
        r.setex(_LAST_KNOWN_GOOD_KEY, _LAST_KNOWN_GOOD_TTL, json.dumps(snapshot, default=str))
    except Exception as e:
        logger.debug("Failed to write last_known_good: %s", e)


def _enrich_snapshot(snap: Dict, source: str, stale: bool, stale_reason: str = "") -> Dict:
    """Add metadata fields to a snapshot before returning to API callers."""
    r = _get_redis()

    # Engine health from dedicated Redis keys
    heartbeat_age = None
    engine_running = False
    engine_live = False
    engine_started_at = None
    engine_version = None
    last_cycle_ts = None
    last_cycle_age = None

    if r is not None:
        try:
            from dashboard.backend.cache import (
                get_engine_heartbeat_ts,
                get_engine_started_at as _get_started,
                get_engine_version as _get_version,
                get_engine_last_cycle as _get_cycle,
                ENGINE_HEARTBEAT_STALE_SEC,
                ENGINE_HEARTBEAT_OFFLINE_SEC,
                is_redis_available as _is_redis_available,
            )
            ts = get_engine_heartbeat_ts()
            if ts is not None:
                heartbeat_age = round(_time_mod.time() - ts, 2)
                engine_running = heartbeat_age <= ENGINE_HEARTBEAT_STALE_SEC
                engine_live = heartbeat_age <= ENGINE_HEARTBEAT_OFFLINE_SEC

            engine_started_at = _get_started()
            engine_version = _get_version()
            cycle_ts = _get_cycle()
            if cycle_ts is not None:
                last_cycle_ts = cycle_ts
                last_cycle_age = round(_time_mod.time() - cycle_ts, 2)
        except Exception as e:
            logger.debug("Engine metadata read failed: %s", e)

    redis_up = r is not None

    # Index LTP: prefer snapshot data, fill missing from Redis LTP keys
    index_ltp = snap.get("index_ltp") or {}
    if isinstance(index_ltp, dict):
        try:
            from dashboard.backend.cache import get_ltp
            for label, redis_sym in (("NIFTY 50", "NIFTY"), ("NIFTY BANK", "BANKNIFTY")):
                if label not in index_ltp:
                    ltp = get_ltp(redis_sym)
                    if ltp is not None:
                        index_ltp[label] = float(ltp)
        except Exception:
            pass

    snap.update({
        "engine_live": engine_live,
        "engine_running": engine_running,
        "engine_heartbeat_age_sec": heartbeat_age,
        "engine_started_at": engine_started_at,
        "engine_version": engine_version,
        "engine_last_cycle": last_cycle_ts,
        "engine_last_cycle_age_sec": last_cycle_age,
        "snapshot_time": datetime.now(_IST).isoformat(),
        "index_ltp": index_ltp,
        "data_source": source,
        "redis_available": redis_up,
        "stale": stale,
    })
    if stale_reason:
        snap["stale_reason"] = stale_reason
    return snap


def get_engine_snapshot() -> Dict:
    """
    Returns the engine snapshot for all API consumers.

    Priority:
      1. Fresh Redis snapshot (engine:snapshot) — source="redis", stale=False
      2. Last-known-good fallback (snapshot:last_known_good) — source="last_known_good", stale=True
      3. Empty scaffold — source="none", stale=True

    NEVER returns an empty active_trades when a previous snapshot exists.
    """
    r = _get_redis()

    if r is not None:
        # Try fresh snapshot first (but protect against invalid/corrupt snapshots)
        snap = _read_snapshot_from_redis(r)
        if snap is not None:
            # Data integrity check: reject invalid snapshots and fall back
            if not _snapshot_is_valid(snap):
                fallback = _read_last_known_good(r)
                if fallback is not None:
                    return _enrich_snapshot(
                        fallback,
                        source="last_known_good",
                        stale=True,
                        stale_reason="invalid_snapshot",
                    )
                # No fallback available — return empty scaffold
                empty = dict(_EMPTY_SNAPSHOT)
                empty["snapshot_time"] = datetime.now(_IST).isoformat()
                empty["stale_reason"] = "invalid_snapshot_no_fallback"
                return empty

            # Write watchdog: if engine hasn't written recently, force fallback even if key exists
            if _engine_write_is_stale(r):
                fallback = _read_last_known_good(r)
                if fallback is not None:
                    return _enrich_snapshot(
                        fallback,
                        source="last_known_good",
                        stale=True,
                        stale_reason="engine_write_stale",
                    )

            _write_last_known_good(r, snap)
            return _enrich_snapshot(snap, source="redis", stale=False)

        # Fresh snapshot expired — use last known good
        fallback = _read_last_known_good(r)
        if fallback is not None:
            logger.info("[StateBridge] Serving last_known_good snapshot (engine snapshot expired)")
            return _enrich_snapshot(fallback, source="last_known_good", stale=True,
                                   stale_reason="engine_snapshot_expired")

    # Redis completely unavailable or no data ever written
    logger.warning("[StateBridge] No snapshot available — returning empty scaffold")
    empty = dict(_EMPTY_SNAPSHOT)
    empty["snapshot_time"] = datetime.now(_IST).isoformat()
    return empty


def get_snapshot_debug() -> Dict:
    """Debug endpoint: return raw snapshot metadata for observability."""
    r = _get_redis()
    if r is None:
        return {"redis_available": False, "error": "Redis not connected"}

    result: Dict[str, Any] = {"redis_available": True}

    try:
        ttl = r.ttl(_SNAPSHOT_KEY)
        result["engine_snapshot_key"] = _SNAPSHOT_KEY
        result["engine_snapshot_ttl_sec"] = ttl if ttl >= 0 else None
        result["engine_snapshot_exists"] = ttl != -2

        lkg_ttl = r.ttl(_LAST_KNOWN_GOOD_KEY)
        result["last_known_good_key"] = _LAST_KNOWN_GOOD_KEY
        result["last_known_good_ttl_sec"] = lkg_ttl if lkg_ttl >= 0 else None
        result["last_known_good_exists"] = lkg_ttl != -2

        # Engine write watchdog
        try:
            raw = r.get(_ENGINE_LAST_WRITE_TS_KEY)
            if raw is not None:
                ts = float(raw)
                result["engine_last_write_ts"] = ts
                result["engine_last_write_age_sec"] = round(_time_mod.time() - ts, 2)
                result["engine_write_stale_threshold_sec"] = _WRITE_STALE_THRESHOLD_SEC
        except Exception:
            pass

        # Snapshot meta/debug keys
        for k, outk in (
            (_SNAPSHOT_META_KEY, "snapshot_meta"),
            (_SNAPSHOT_WRITE_STATUS_KEY, "snapshot_write_status"),
            (_SNAPSHOT_VERSION_KEY, "snapshot_version"),
        ):
            try:
                v = r.get(k)
                if v is not None:
                    # meta is json; others are strings
                    if k == _SNAPSHOT_META_KEY:
                        result[outk] = json.loads(v)
                    else:
                        result[outk] = v
            except Exception:
                pass

        # Snapshot age
        snap = _read_snapshot_from_redis(r)
        if snap and "timestamp" in snap:
            result["snapshot_timestamp"] = snap["timestamp"]
        elif snap and "snapshot_time" in snap:
            result["snapshot_timestamp"] = snap["snapshot_time"]

        # Key inventory
        keys_to_check = [
            "engine_lock", "engine_heartbeat", "engine_started_at",
            "engine_version", "engine_last_cycle", "engine:snapshot",
            "snapshot:last_known_good", "ltp:NIFTY", "ltp:BANKNIFTY",
        ]
        key_status = {}
        for k in keys_to_check:
            try:
                t = r.ttl(k)
                key_status[k] = {"exists": t != -2, "ttl": t if t >= 0 else ("no_expiry" if t == -1 else None)}
            except Exception:
                key_status[k] = {"exists": False, "ttl": None}
        result["redis_keys"] = key_status

        try:
            gv = r.get("snapshot:global_version")
            result["api_snapshot_global_version"] = int(gv) if gv is not None else 0
        except Exception:
            pass

    except Exception as e:
        result["error"] = str(e)

    try:
        from dashboard.backend.redis_endpoint_cache import endpoint_debug_inventory
        result["api_endpoint_snapshots"] = endpoint_debug_inventory()
    except Exception as e:
        result["api_endpoint_snapshots"] = {"error": str(e)}

    result["checked_at"] = datetime.now(_IST).isoformat()
    return result


def is_engine_live() -> bool:
    """Check if engine is alive based on Redis heartbeat."""
    try:
        from dashboard.backend.cache import get_engine_heartbeat_ts, ENGINE_HEARTBEAT_OFFLINE_SEC
        ts = get_engine_heartbeat_ts()
        if ts is None:
            return False
        return (_time_mod.time() - ts) <= ENGINE_HEARTBEAT_OFFLINE_SEC
    except Exception:
        return False


def get_market_regime() -> str:
    """Read market regime from the latest snapshot."""
    r = _get_redis()
    if r is not None:
        snap = _read_snapshot_from_redis(r)
        if snap and isinstance(snap.get("market_regime"), str):
            return snap["market_regime"]
    return "NEUTRAL"
