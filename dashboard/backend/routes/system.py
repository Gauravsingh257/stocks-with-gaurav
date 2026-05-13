"""
dashboard/backend/routes/system.py
System health, version, diagnostics, and tactical plan endpoints.

GET /api/system/health         — DB, WS, engine live status, uptime, market_status, worker heartbeat
GET /api/system/version        — backend + engine + agent version strings
GET /api/system/tactical-plan  — today's tactical plan from PreMarketBriefing
"""

import json
import logging
import time
import threading
from datetime import datetime, time as dtime
from pathlib import Path

import os

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends

from dashboard.backend.ops_auth import verify_ops_key

router = APIRouter(prefix="/api/system", tags=["system"])

BACKEND_VERSION = "1.2.0"
AGENT_VERSION   = "2.0.0"
_start_time     = time.time()

_kite_status_cache: dict = {"connected": None, "checked_at": 0.0, "fail_streak": 0}
_kite_status_lock = threading.Lock()
_KITE_CHECK_INTERVAL_SEC = 300  # only call k.profile() once every 5 minutes
_KITE_FAIL_THRESHOLD = 3        # require 3 consecutive failures before marking expired
_log = logging.getLogger("dashboard.system")

# NSE market hours IST
MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PREMARKET_START = dtime(9, 0)


def _market_status_now() -> str:
    """Return 'open' | 'closed' | 'premarket' based on current IST."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).time()
    if MARKET_OPEN <= now_ist <= MARKET_CLOSE:
        return "open"
    if PREMARKET_START <= now_ist < MARKET_OPEN:
        return "premarket"
    return "closed"


def _check_kite_cached() -> tuple:
    """
    Return (kite_connected: bool, disconnect_reason: str | None).

    Uses a two-tier verification strategy:
    1. If the cached result is fresh (< _KITE_CHECK_INTERVAL_SEC), return it immediately.
    2. Otherwise, try k.margins() (lightweight, reliable). If that fails, try k.profile().
       If both fail _KITE_FAIL_THRESHOLD times in a row, mark as expired.

    NEVER calls _reset_kite() — the Charts module handles its own refresh
    via token-change detection in _get_kite().
    """
    now = time.time()
    with _kite_status_lock:
        cached = _kite_status_cache
        elapsed = now - cached["checked_at"]
        if cached["connected"] is not None and elapsed < _KITE_CHECK_INTERVAL_SEC:
            reason = None if cached["connected"] else "token_expired"
            return cached["connected"], reason

    from dashboard.backend.routes.charts import _get_kite
    k = _get_kite()
    if k is None:
        with _kite_status_lock:
            _kite_status_cache["connected"] = False
            _kite_status_cache["checked_at"] = now
            _kite_status_cache["fail_streak"] = _kite_status_cache.get("fail_streak", 0) + 1
        return False, "token_expired"

    api_ok = False
    last_err = None
    for method_name in ("margins", "profile"):
        try:
            getattr(k, method_name)()
            api_ok = True
            break
        except Exception as exc:
            last_err = exc
            _log.debug("Kite %s() failed: %s — trying next", method_name, exc)

    if api_ok:
        with _kite_status_lock:
            _kite_status_cache["connected"] = True
            _kite_status_cache["checked_at"] = now
            _kite_status_cache["fail_streak"] = 0
        return True, None

    with _kite_status_lock:
        streak = _kite_status_cache.get("fail_streak", 0) + 1
        _kite_status_cache["fail_streak"] = streak
        _kite_status_cache["checked_at"] = now
        if streak >= _KITE_FAIL_THRESHOLD:
            _kite_status_cache["connected"] = False
            _log.warning("Kite API failed %d times consecutively: %s", streak, last_err)
            return False, "token_expired"
        # Below threshold: assume connected if token has significant TTL left,
        # or if we were previously connected. Short-cache (30s) so we re-check soon.
        _kite_status_cache["checked_at"] = now - _KITE_CHECK_INTERVAL_SEC + 30  # re-check in 30s
        _log.debug("Kite API transient failure (%d/%d) — will retry in 30s", streak, _KITE_FAIL_THRESHOLD)
        _kite_status_cache["connected"] = True
        return True, None


def invalidate_kite_status_cache():
    """Call this when a new token is stored so the next health check re-verifies immediately."""
    with _kite_status_lock:
        _kite_status_cache["connected"] = None
        _kite_status_cache["checked_at"] = 0.0
        _kite_status_cache["fail_streak"] = 0


@router.post("/client-trust-metrics")
def client_trust_metrics(payload: Optional[dict[str, Any]] = Body(default=None)):
    """
    Ingest anonymous browser trust counters (WS reconnects, tab resume, reconcile rejects).
    Stored in Redis hash metrics:client_trust:{YYYYMMDD} for ops dashboards.
    """
    raw = payload or {}
    counters = raw.get("counters")
    if not isinstance(counters, dict):
        return {"ok": False}
    try:
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore

        day = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d")
        hk = f"metrics:client_trust:{day}"
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return {"ok": True, "stored": False}
        pipe = r.pipeline()
        for k, v in counters.items():
            if not isinstance(k, str) or len(k) > 64 or not k.strip():
                continue
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n <= 0 or n > 50_000:
                continue
            pipe.hincrby(hk, k, n)
        pipe.execute()
        r.expire(hk, 86400 * 14)
    except Exception:
        pass
    return {"ok": True, "stored": True}


@router.get("/health")
def system_health():
    """Full system health — engine_status, kite_connected, ws_clients, latency_ms, DB, uptime."""
    start_ns = time.perf_counter_ns()

    # ── DB connectivity ───────────────────────────────────────
    db_ok    = False
    db_rows  = 0
    db_error = None
    try:
        from dashboard.backend.db import get_connection
        conn    = get_connection()
        db_rows = conn.execute("SELECT COUNT(*) AS c FROM trades").fetchone()["c"]
        conn.close()
        db_ok   = True
    except Exception as e:
        db_error = str(e)

    # ── WebSocket clients ────────────────────────────────────
    ws_clients = 0
    try:
        from dashboard.backend.websocket import manager
        ws_clients = manager.client_count
    except Exception:
        pass

    # ── Engine snapshot + status (running | stale | offline) ───
    engine_version = "v4"
    engine_live    = False
    engine_mode    = "UNKNOWN"
    engine_running = False
    last_snapshot  = None
    engine_started_at = None
    engine_last_cycle = None
    engine_last_cycle_age_sec = None
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot
        snap           = get_engine_snapshot()
        engine_live    = snap.get("engine_live", False)
        engine_mode    = snap.get("engine_mode", "UNKNOWN")
        last_snapshot  = snap.get("snapshot_time")
        engine_version = snap.get("engine_version", "v4")
        engine_running = snap.get("engine_running", False)
        engine_started_at = snap.get("engine_started_at")
        engine_last_cycle = snap.get("engine_last_cycle")
        engine_last_cycle_age_sec = snap.get("engine_last_cycle_age_sec")
    except Exception:
        pass

    if engine_running and engine_live:
        engine_status = "running"
    elif engine_live:
        engine_status = "stale"
    else:
        engine_status = "offline"

    # ── Kite connected (token valid) + token_present + token_expires_in_hours + hint + reason ─
    kite_connected = False
    token_present = False
    token_expires_in_hours = None
    kite_last_login_utc = None
    token_source = None
    kite_hint = None
    kite_disconnect_reason = None
    try:
        from dashboard.backend.kite_auth import (
            get_access_token as get_kite_token,
            get_access_token_ttl_seconds,
            get_last_login_utc,
            get_token_source,
        )
        token_present = bool(get_kite_token())
        ttl_sec = get_access_token_ttl_seconds()
        if ttl_sec is not None and ttl_sec > 0:
            token_expires_in_hours = round(ttl_sec / 3600, 1)
        kite_last_login_utc = get_last_login_utc()
        token_source = get_token_source()
        if token_present:
            kite_connected, kite_disconnect_reason = _check_kite_cached()
        if not kite_connected:
            if token_source == "env_or_file" and token_present:
                kite_hint = (
                    "Token from env/file is expired. Log in at /api/kite/login. "
                    "If using morning login: remove KITE_ACCESS_TOKEN from Railway Variables so the system uses the Redis token. "
                    "Ensure Zerodha app redirect URL: https://web-production-2781a.up.railway.app/api/kite/callback"
                )
            elif token_source == "redis" and token_present:
                kite_hint = "Token in Redis invalid or expired. Log in again at /api/kite/login."
            else:
                kite_hint = "Log in at /api/kite/login or set KITE_ACCESS_TOKEN."
    except Exception:
        kite_hint = "Log in at /api/kite/login or set KITE_ACCESS_TOKEN."

    if token_source is None:
        try:
            from dashboard.backend.kite_auth import get_token_source, get_last_login_utc
            token_source = get_token_source()
            kite_last_login_utc = get_last_login_utc()
        except Exception:
            pass

    # ── Worker heartbeat (detect market_engine.py failure) ────
    # Only downgrade engine_status when worker is actually in use (key exists and stale).
    # Standalone Railway: Web does not run market_engine.py, so key is missing — keep engine_status from snapshot.
    worker_status = None
    market_data_last_update_ts = None
    try:
        from dashboard.backend.cache import (
            get as cache_get,
            MARKET_ENGINE_LAST_UPDATE_KEY,
            is_redis_available,
        )
        if is_redis_available():
            raw = cache_get(MARKET_ENGINE_LAST_UPDATE_KEY)
            if raw is not None:
                try:
                    ts = float(raw)
                    market_data_last_update_ts = ts
                    if (time.time() - ts) > 15:
                        worker_status = "stale"
                        engine_status = "stale"
                    else:
                        worker_status = "running"
                except (TypeError, ValueError):
                    worker_status = "stale"
                    engine_status = "stale"
            # else: key missing (e.g. standalone mode) — do not overwrite engine_status; heartbeat is source of truth
    except Exception:
        pass

    # ── Market status (open | closed | premarket) ──────────────
    market_status = _market_status_now()

    # ── Agent scheduler ──────────────────────────────────────
    scheduler_running = False
    try:
        from agents.runner import get_scheduler_running
        scheduler_running = get_scheduler_running()
    except Exception:
        pass

    uptime_s = int(time.time() - _start_time)
    hours, rem = divmod(uptime_s, 3600)
    mins,  sec = divmod(rem, 60)
    latency_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 2)

    engine_uptime_seconds = None
    engine_uptime_human = None
    if engine_started_at is not None:
        try:
            engine_uptime_seconds = int(time.time() - float(engine_started_at))
            if engine_uptime_seconds < 0:
                engine_uptime_seconds = 0
            eh, er = divmod(engine_uptime_seconds, 3600)
            em, _ = divmod(er, 60)
            engine_uptime_human = f"{eh}h {em}m"
        except (TypeError, ValueError):
            pass

    out = {
        "engine_status":    engine_status,
        "engine_version":   engine_version,
        "engine_started_at": engine_started_at,
        "engine_uptime_seconds": engine_uptime_seconds,
        "engine_uptime_human": engine_uptime_human,
        "engine_last_cycle": engine_last_cycle,
        "engine_last_cycle_age_sec": engine_last_cycle_age_sec,
        "kite_connected":   kite_connected,
        "token_present":    token_present,
        "token_expires_in_hours": token_expires_in_hours,
        "token_source":     token_source,
        "kite_last_login_utc": kite_last_login_utc,
        "ws_clients":       ws_clients,
        "latency_ms":       latency_ms,
        "market_status":    market_status,
        "market_data_last_update_ts": market_data_last_update_ts,
        "worker_status":    worker_status,
        "backend_version":  BACKEND_VERSION,
        "agent_version":    AGENT_VERSION,
        "engine_version":   engine_version,
        "engine_live":      engine_live,
        "engine_mode":      engine_mode,
        "db_connected":     db_ok,
        "db_trade_rows":    db_rows,
        "db_error":         db_error,
        "scheduler_running": scheduler_running,
        "last_snapshot":    last_snapshot,
        "uptime_seconds":   uptime_s,
        "uptime_human":     f"{hours}h {mins}m {sec}s",
        "timestamp":        datetime.utcnow().isoformat() + "Z",
    }
    if kite_hint is not None:
        out["kite_hint"] = kite_hint
    if kite_disconnect_reason is not None:
        out["kite_disconnect_reason"] = kite_disconnect_reason
    # When market is closed, last_cycle is only updated during 9:15–15:30 IST — not a sign engine is down
    if market_status == "closed" and engine_last_cycle_age_sec is not None and engine_last_cycle_age_sec > 3600:
        out["engine_status_hint"] = "Market closed. Engine status is heartbeat-based 24/7; last_cycle updates during market hours (9:15–15:30 IST)."
    return out


@router.get("/version")
def system_version():
    """Return software version strings."""
    return {
        "backend":  BACKEND_VERSION,
        "agents":   AGENT_VERSION,
        "engine":   "v4",
        "frontend": "1.0.0",
    }


@router.get("/tactical-plan")
def tactical_plan():
    """
    Return today's tactical plan generated by PreMarketBriefing (Daily Tactical Controller).
    Falls back to the most recent plan if none exists for today.
    """
    try:
        from dashboard.backend.db import get_connection
        today = datetime.now().date().isoformat()
        conn  = get_connection()

        # Try today's plan first
        row = conn.execute(
            """SELECT new_value, timestamp FROM parameter_versions
               WHERE parameter = 'tactical_plan'
                 AND date(timestamp) = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (today,),
        ).fetchone()

        # Fallback: most recent plan from any day
        if not row:
            row = conn.execute(
                """SELECT new_value, timestamp FROM parameter_versions
                   WHERE parameter = 'tactical_plan'
                   ORDER BY timestamp DESC LIMIT 1""",
            ).fetchone()

        conn.close()

        if row:
            plan = json.loads(row["new_value"])
            plan["_source"] = "parameter_versions"
            plan["_fetched_at"] = datetime.now().isoformat()
            return {"status": "ok", "plan": plan}

        # No plan at all — return defaults
        return {
            "status": "no_plan",
            "plan": {
                "date":              today,
                "mode":              "NORMAL",
                "mode_description":  "No tactical plan generated yet. Run PreMarketBriefing.",
                "risk_multiplier":   1.0,
                "max_daily_risk":    3.0,
                "score_threshold":   5,
                "stop_after_losses": 3,
                "focus_setups":      [],
                "disable_setups":    [],
                "market_condition":  "UNKNOWN",
                "market_regime":     "UNKNOWN",
                "confidence":        50,
                "wr_state":          "UNKNOWN",
                "dd_state":          "UNKNOWN",
                "cl_state":          "UNKNOWN",
            },
        }

    except Exception as e:
        return {"status": "error", "error": str(e), "plan": None}


@router.get("/kite-status")
def kite_status():
    """Return Kite connectivity status without exposing credentials. Token may be in Redis (web login) or env/file."""
    import os
    api_key_set = bool(os.getenv("KITE_API_KEY", "").strip())
    token_set = False
    token_source = None
    kite_last_login_utc = None
    token_expires_in_hours = None
    try:
        from dashboard.backend.kite_auth import (
            get_access_token as get_kite_token,
            get_token_source,
            get_last_login_utc,
            get_access_token_ttl_seconds,
        )
        token_set = bool(get_kite_token())
        token_source = get_token_source()
        kite_last_login_utc = get_last_login_utc()
        ttl = get_access_token_ttl_seconds()
        if ttl is not None and ttl > 0:
            token_expires_in_hours = round(ttl / 3600, 1)
    except Exception:
        pass

    kite_ready = token_set
    kite_error = None
    token_valid = False
    if kite_ready:
        try:
            from dashboard.backend.routes.charts import _get_kite
            k = _get_kite()
            if k is not None:
                k.profile()
                token_valid = True
        except Exception as ve:
            kite_error = str(ve)

    hint = None
    if not api_key_set:
        hint = "Add KITE_API_KEY to Railway Variables and Redeploy."
    elif not token_set:
        hint = "Log in at /api/kite/login or set KITE_ACCESS_TOKEN in Railway."
    elif not token_valid:
        hint = "Token invalid or expired. Log in at /api/kite/login or refresh KITE_ACCESS_TOKEN."

    out = {
        "kite_ready":   kite_ready,
        "token_valid":  token_valid,
        "api_key_set":  api_key_set,
        "token_set":    token_set,
        "error":        kite_error,
        "hint":         hint,
    }
    if token_source is not None:
        out["token_source"] = token_source
    if kite_last_login_utc is not None:
        out["kite_last_login_utc"] = kite_last_login_utc
    if token_expires_in_hours is not None:
        out["token_expires_in_hours"] = token_expires_in_hours
    return out


@router.get("/health/full")
def health_full():
    """Comprehensive health: engine + Redis + snapshot freshness + Kite."""
    result = {"status": "ok", "service": "smc-dashboard"}

    try:
        from dashboard.backend.cache import get_redis_status, is_redis_available
        result["redis"] = get_redis_status()
        result["redis"]["connected"] = is_redis_available()
    except Exception as e:
        result["redis"] = {"connected": False, "error": str(e)}

    try:
        from dashboard.backend.cache import get_engine_heartbeat_ts, get_engine_version, get_engine_started_at
        hb = get_engine_heartbeat_ts()
        started = get_engine_started_at()
        result["engine"] = {
            "heartbeat_age_sec": round(time.time() - hb, 1) if hb else None,
            "running": hb is not None and (time.time() - hb) < 60,
            "version": get_engine_version(),
            "uptime_sec": round(time.time() - started, 1) if started else None,
        }
    except Exception as e:
        result["engine"] = {"running": False, "error": str(e)}

    try:
        from dashboard.backend.state_bridge import get_snapshot_debug
        debug = get_snapshot_debug()
        result["snapshot"] = {
            "live_exists": debug.get("engine_snapshot_exists", False),
            "live_ttl_sec": debug.get("engine_snapshot_ttl_sec"),
            "fallback_exists": debug.get("last_known_good_exists", False),
            "fallback_ttl_sec": debug.get("last_known_good_ttl_sec"),
            "timestamp": debug.get("snapshot_timestamp"),
        }
    except Exception as e:
        result["snapshot"] = {"error": str(e)}

    try:
        from config.kite_auth import is_kite_available
        result["kite"] = {"connected": is_kite_available()}
    except Exception:
        result["kite"] = {"connected": False}

    return result


@router.get("/debug/watchlist-sync")
def debug_watchlist_sync(_unused: None = Depends(verify_ops_key)):
    """
    Watchlist lifecycle diagnostics: Redis key inventory, event traces, bundle revisions,
    LKG vs live divergence, TTL health, per-user symbol counts.
    """
    import json as _json

    try:
        from dashboard.backend.cache import _get_redis
    except Exception as e:
        return {"error": f"Redis unavailable: {e}"}

    r = _get_redis()
    if r is None:
        return {"error": "redis_unavailable"}

    now_ts = time.time()
    out: dict = {"checked_at": datetime.utcnow().isoformat() + "Z"}

    # ── Scan all watchlist-related Redis keys ─────────────────────────────────
    key_inventory: list[dict] = []
    patterns = [
        "snapshot:watchlist_operating:*",
        "snapshot:last_known_good:watchlist_operating:*",
        "snapshot:watchlist_digest:*",
        "watchlist:event_trace:*",
        "watchlist:bundle_ver:*",
        "watchlist:feed:*",
    ]
    for pat in patterns:
        cur = 0
        for _ in range(120):
            cur, batch = r.scan(cur, match=pat, count=48)
            for bk in batch:
                ks = bk.decode("utf-8", errors="replace") if isinstance(bk, bytes) else str(bk)
                try:
                    ttl = r.ttl(ks)
                    size = r.strlen(ks)
                    key_inventory.append({"key": ks, "ttl_sec": int(ttl), "size_bytes": int(size)})
                except Exception:
                    key_inventory.append({"key": ks})
            if cur == 0:
                break
    out["key_inventory"] = key_inventory
    out["total_watchlist_redis_keys"] = len(key_inventory)

    # ── Per-user snapshot health ──────────────────────────────────────────────
    users_health: list[dict] = []
    live_keys = [k["key"] for k in key_inventory if "watchlist_operating:" in k["key"] and "last_known_good" not in k["key"]]
    for lk in live_keys[:20]:
        uid_str = lk.split(":")[-1]
        lkg_key = f"snapshot:last_known_good:watchlist_operating:{uid_str}"
        trace_key = f"watchlist:event_trace:{uid_str}"
        bver_key = f"watchlist:bundle_ver:{uid_str}"

        user_h: dict = {"user_id": uid_str}
        try:
            raw_live = r.get(lk)
            if raw_live:
                pl = _json.loads(raw_live)
                user_h["live_items"] = len(pl.get("items") or [])
                user_h["live_gv"] = pl.get("_global_state_version")
                user_h["live_snapshot_source"] = pl.get("snapshot_source")
                user_h["live_age_sec"] = round(now_ts - float(pl.get("_snapshot_written_at") or now_ts), 1)
                trust = pl.get("_trust") or {}
                user_h["bundle_revision"] = trust.get("bundle_revision")
        except Exception:
            user_h["live_parse_error"] = True

        try:
            raw_lkg = r.get(lkg_key)
            if raw_lkg:
                lkg_pl = _json.loads(raw_lkg)
                user_h["lkg_items"] = len(lkg_pl.get("items") or [])
                user_h["lkg_gv"] = lkg_pl.get("_global_state_version")
                user_h["lkg_age_sec"] = round(now_ts - float(lkg_pl.get("_snapshot_written_at") or now_ts), 1)
            else:
                user_h["lkg_missing"] = True
        except Exception:
            user_h["lkg_parse_error"] = True

        try:
            trace_len = r.llen(trace_key)
            user_h["event_trace_count"] = int(trace_len) if trace_len is not None else 0
            # Sample last 5 events
            if trace_len:
                raw_events = r.lrange(trace_key, -5, -1)
                user_h["event_trace_recent"] = [
                    _json.loads(e.decode("utf-8") if isinstance(e, bytes) else e)
                    for e in raw_events
                ]
        except Exception:
            user_h["event_trace_error"] = True

        try:
            bver = r.get(bver_key)
            user_h["bundle_ver_counter"] = int(bver) if bver is not None else 0
        except Exception:
            pass

        # Check divergence between live and LKG
        live_gv = user_h.get("live_gv")
        lkg_gv = user_h.get("lkg_gv")
        if live_gv is not None and lkg_gv is not None:
            user_h["gv_diverged"] = live_gv != lkg_gv

        users_health.append(user_h)

    out["users_health"] = users_health

    # ── SQLite symbol counts per user ─────────────────────────────────────────
    try:
        from dashboard.backend.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT user_id, COUNT(*) AS cnt FROM user_watchlist GROUP BY user_id"
        ).fetchall()
        conn.close()
        out["db_symbol_counts"] = {str(r["user_id"]): r["cnt"] for r in rows}
    except Exception as e:
        out["db_symbol_counts"] = {"error": str(e)}

    # ── Consistency check: DB users with symbols but no live Redis key ────────
    db_counts = out.get("db_symbol_counts") or {}
    live_uids = {k["key"].split(":")[-1] for k in key_inventory if "watchlist_operating:" in k["key"] and "last_known_good" not in k["key"]}
    missing_redis: list[str] = []
    for uid_s, cnt in db_counts.items():
        if isinstance(cnt, int) and cnt > 0 and uid_s not in live_uids:
            missing_redis.append(uid_s)
    out["users_with_db_symbols_but_no_live_redis_key"] = missing_redis

    # ── Global state version ──────────────────────────────────────────────────
    try:
        from dashboard.backend.global_state_version import read_global_state_version
        out["global_state_version"] = read_global_state_version()
    except Exception:
        out["global_state_version"] = None

    out["diagnosis"] = {
        "empty_live_snapshots": [
            u["user_id"] for u in users_health if u.get("live_items", -1) == 0
        ],
        "lkg_missing": [u["user_id"] for u in users_health if u.get("lkg_missing")],
        "stale_live_gt_60s": [
            u["user_id"] for u in users_health if (u.get("live_age_sec") or 0) > 60
        ],
        "users_missing_redis": missing_redis,
    }

    return out


@router.get("/debug/cache")
def debug_cache(_unused: None = Depends(verify_ops_key)):
    """Observability: Redis key inventory, TTLs, snapshot timestamps, OI interpretation meta."""
    try:
        from dashboard.backend.state_bridge import get_snapshot_debug
        out = get_snapshot_debug()
    except Exception as e:
        out = {"error": str(e)}
    try:
        from dashboard.backend.services.oi_interpretation_engine import get_oi_interpretation_debug

        if isinstance(out, dict):
            out["oi_interpretation"] = get_oi_interpretation_debug()
        else:
            out = {"snapshot_bridge_error": out, "oi_interpretation": get_oi_interpretation_debug()}
    except Exception as e:
        if isinstance(out, dict):
            out["oi_interpretation"] = {"error": str(e)}
        else:
            out = {"oi_interpretation": {"error": str(e)}}
    try:
        from dashboard.backend.services.watchlist_intel_service import (
            FEED_PREFIX,
            FEED_TTL_SEC,
            LAST_PREFIX,
            MAX_FEED_EVENTS,
        )

        if isinstance(out, dict):
            out["watchlist_os"] = {
                "feed_key_pattern": FEED_PREFIX + "{user_id}",
                "last_intel_key_pattern": LAST_PREFIX + "{user_id}",
                "max_feed_events": MAX_FEED_EVENTS,
                "feed_ttl_sec": FEED_TTL_SEC,
                "api": ["/api/watchlist/operating", "/api/watchlist/feed"],
            }
    except Exception as e:
        if isinstance(out, dict):
            out["watchlist_os"] = {"error": str(e)}
    return out


@router.get("/debug/signals")
def debug_signals(_unused: None = Depends(verify_ops_key)):
    """Signal delivery pipeline diagnostics: queue, last sends, failures, heartbeat."""
    try:
        from services.signal_delivery import get_signal_debug
        return get_signal_debug()
    except Exception as e:
        return {"error": str(e), "redis_available": False}


@router.get("/debug/platform")
def debug_platform(_unused: None = Depends(verify_ops_key)):
    """
    Production diagnostics: route latencies, Redis health, snapshot ages,
    WS clients, auth health, engine cycle, memory, stale detection.
    """
    start_ns = time.perf_counter_ns()
    diag: dict = {"checked_at": datetime.utcnow().isoformat() + "Z"}

    # ── Redis health ──────────────────────────────────────────
    redis_ok = False
    redis_latency_ms = None
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is not None:
            t0 = time.perf_counter_ns()
            r.ping()
            redis_latency_ms = round((time.perf_counter_ns() - t0) / 1_000_000, 2)
            redis_ok = True
    except Exception as e:
        diag["redis_error"] = str(e)
    diag["redis"] = {"available": redis_ok, "latency_ms": redis_latency_ms}

    # ── Snapshot ages ─────────────────────────────────────────
    try:
        from dashboard.backend.state_bridge import get_snapshot_debug
        snap_debug = get_snapshot_debug()
        diag["snapshots"] = {
            "engine_snapshot_exists": snap_debug.get("engine_snapshot_exists"),
            "engine_snapshot_ttl_sec": snap_debug.get("engine_snapshot_ttl_sec"),
            "last_known_good_exists": snap_debug.get("last_known_good_exists"),
            "last_known_good_ttl_sec": snap_debug.get("last_known_good_ttl_sec"),
            "engine_last_write_age_sec": snap_debug.get("engine_last_write_age_sec"),
            "snapshot_timestamp": snap_debug.get("snapshot_timestamp"),
            "global_version": snap_debug.get("api_snapshot_global_version"),
        }
        api_snaps = snap_debug.get("api_endpoint_snapshots")
        if isinstance(api_snaps, dict):
            diag["endpoint_snapshots"] = api_snaps
    except Exception as e:
        diag["snapshots"] = {"error": str(e)}

    # ── Engine health ─────────────────────────────────────────
    try:
        from dashboard.backend.cache import (
            get_engine_heartbeat_ts,
            get_engine_version,
            get_engine_started_at,
            get_engine_last_cycle,
        )
        hb = get_engine_heartbeat_ts()
        started = get_engine_started_at()
        cycle = get_engine_last_cycle()
        diag["engine"] = {
            "heartbeat_age_sec": round(time.time() - hb, 1) if hb else None,
            "running": hb is not None and (time.time() - hb) < 60,
            "version": get_engine_version(),
            "uptime_sec": round(time.time() - started, 1) if started else None,
            "last_cycle_age_sec": round(time.time() - cycle, 1) if cycle else None,
        }
    except Exception as e:
        diag["engine"] = {"error": str(e)}

    # ── Auth health (isolated check) ──────────────────────────
    try:
        from dashboard.backend.db import get_connection
        conn = get_connection()
        user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        conn.close()
        diag["auth"] = {"healthy": True, "user_count": user_count}
    except Exception as e:
        diag["auth"] = {"healthy": False, "error": str(e)}

    # ── WebSocket clients ─────────────────────────────────────
    try:
        from dashboard.backend.websocket import manager
        diag["websocket"] = {"clients": manager.client_count}
    except Exception:
        diag["websocket"] = {"clients": 0}

    # ── Discovery snapshot freshness ──────────────────────────
    try:
        from dashboard.backend.redis_endpoint_cache import serve_cached_endpoint
        disc = serve_cached_endpoint("discovery")
        if disc:
            gen_at = disc.get("generated_at")
            diag["discovery"] = {
                "snapshot_available": True,
                "generated_at": gen_at,
                "items_count": len(disc.get("items") or disc.get("final_trades") or []),
            }
        else:
            diag["discovery"] = {"snapshot_available": False}
    except Exception as e:
        diag["discovery"] = {"error": str(e)}

    # ── Memory usage ──────────────────────────────────────────
    try:
        import psutil  # type: ignore[import-untyped]
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        diag["memory"] = {
            "rss_mb": round(mem.rss / 1_048_576, 1),
            "vms_mb": round(mem.vms / 1_048_576, 1),
        }
    except ImportError:
        diag["memory"] = {"note": "psutil not installed"}
    except Exception:
        diag["memory"] = {"rss_mb": None}

    # ── Backend uptime ────────────────────────────────────────
    uptime_s = int(time.time() - _start_time)
    diag["backend_uptime_sec"] = uptime_s

    # ── Stale subsystem detection ─────────────────────────────
    stale_systems = []
    eng = diag.get("engine", {})
    if eng.get("heartbeat_age_sec") and eng["heartbeat_age_sec"] > 120:
        stale_systems.append("engine_heartbeat")
    if eng.get("last_cycle_age_sec") and eng["last_cycle_age_sec"] > 600:
        stale_systems.append("engine_cycle")
    snap_info = diag.get("snapshots", {})
    if not snap_info.get("engine_snapshot_exists"):
        stale_systems.append("engine_snapshot_missing")
    if snap_info.get("engine_last_write_age_sec") and snap_info["engine_last_write_age_sec"] > 300:
        stale_systems.append("engine_write_stale")
    if not diag.get("discovery", {}).get("snapshot_available"):
        stale_systems.append("discovery_snapshot_missing")
    diag["stale_systems"] = stale_systems
    diag["platform_healthy"] = len(stale_systems) == 0 and redis_ok and diag.get("auth", {}).get("healthy", False)

    try:
        from dashboard.backend.snapshot_consistency import (
            estimate_json_bytes,
            read_global_snapshot_version,
            validate_engine_snapshot_structure,
        )
        from dashboard.backend.state_bridge import get_engine_snapshot

        es = get_engine_snapshot()
        ok, issues = validate_engine_snapshot_structure(es)
        diag["engine_snapshot_consistency"] = {
            "ok": ok,
            "issues": issues,
            "payload_estimate_bytes": estimate_json_bytes(es),
            "snapshot_global_version": read_global_snapshot_version(),
        }
    except Exception as e:
        diag["engine_snapshot_consistency"] = {"error": str(e)}

    diag["orchestration"] = {
        "discovery_allow_on_demand_scan": os.getenv("DISCOVERY_ALLOW_ON_DEMAND_SCAN", "false").lower()
        in ("1", "true", "yes"),
        "oi_snapshot_request_generation": os.getenv("OI_SNAPSHOT_REQUEST_GENERATION", "false").lower()
        in ("1", "true", "yes"),
        "ws_full_snapshot_every_ticks": int(os.getenv("WS_FULL_SNAPSHOT_EVERY_TICKS", "6")),
        "ops_api_key_configured": bool(os.getenv("OPS_API_KEY", "").strip()),
        "debug_endpoints_require_ops_header": bool(os.getenv("OPS_API_KEY", "").strip()),
        "research_snapshot_only": os.getenv("RESEARCH_SNAPSHOT_ONLY", "true").lower() in ("1", "true", "yes"),
        "watchlist_os_snapshot_only": os.getenv("WATCHLIST_OS_SNAPSHOT_ONLY", "true").lower() in ("1", "true", "yes"),
    }

    try:
        from dashboard.backend.request_metrics import get_latency_summary, top_slow_paths

        diag["api_latency"] = get_latency_summary()
        diag["api_latency_slow_paths"] = top_slow_paths(15)
    except Exception as e:
        diag["api_latency"] = {"error": str(e)}

    try:
        from dashboard.backend.ws_telemetry import get_ws_telemetry

        diag["websocket_telemetry"] = get_ws_telemetry()
    except Exception as e:
        diag["websocket_telemetry"] = {"error": str(e)}

    # ── WebSocket operational health (server-side; reconnect storms are client-driven) ──
    try:
        from dashboard.backend.websocket import manager
        from dashboard.backend.ws_telemetry import get_ws_telemetry

        _wt = get_ws_telemetry()
        _eng_stale = False
        _eng_reason = None
        try:
            from dashboard.backend.state_bridge import get_engine_snapshot

            _es = get_engine_snapshot()
            if isinstance(_es, dict):
                _eng_stale = bool(_es.get("stale"))
                _eng_reason = _es.get("stale_reason") or _es.get("snapshot_stale_reason")
        except Exception:
            pass
        diag["websocket_health"] = {
            "active_clients": manager.client_count,
            "connections_accepted_since_boot": _wt.get("connections_accepted_since_boot"),
            "avg_broadcast_bytes": _wt.get("avg_broadcast_bytes"),
            "last_broadcast_bytes": _wt.get("last_broadcast_bytes"),
            "last_broadcast_type": _wt.get("last_broadcast_type"),
            "last_broadcast_age_ms": _wt.get("last_broadcast_age_ms"),
            "total_broadcasts_since_boot": _wt.get("total_broadcasts_since_boot"),
            "degraded_stream_reason_engine_snapshot": _eng_reason if _eng_stale else None,
            "note": "Client reconnect counts live in the browser only; server tracks accepts + broadcast telemetry.",
        }
    except Exception as e:
        diag["websocket_health"] = {"error": str(e)}

    # ── WS transport ordering + queue (Phase B) ─────────────────────────────
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot
        from dashboard.backend.websocket import (
            get_ws_event_queue_depth,
            read_ws_stream_sequence,
        )
        from dashboard.backend.ws_telemetry import get_ws_telemetry

        _wt2 = get_ws_telemetry()
        _snap_age = None
        try:
            _es2 = get_engine_snapshot()
            if isinstance(_es2, dict):
                _snap_age = _es2.get("_snapshot_age_ms")
        except Exception:
            pass
        diag["websocket_realtime_health"] = {
            "stream_sequence_current": read_ws_stream_sequence(),
            "last_broadcast_stream_sequence": _wt2.get("last_stream_sequence"),
            "ws_event_queue_depth": get_ws_event_queue_depth(),
            "avg_snapshot_age_ms": _snap_age,
            "reconnect_count": None,
            "stale_event_rejections": None,
            "avg_ltp_delay_ms": None,
            "reconciliation_failures": None,
            "forced_resyncs": None,
            "inactive_tab_resyncs": None,
            "note": "Null fields are browser-side counters (not yet POSTed to server). Server exposes monotonic stream_sequence on every WS frame + Redis→WS queue depth.",
        }
    except Exception as e:
        diag["websocket_realtime_health"] = {"error": str(e)}

    # ── Snapshot route health (research swing/longterm meta ages) ────────────
    try:
        from dashboard.backend.cache import _get_redis
        from dashboard.backend.redis_endpoint_cache import META_PREFIX

        r_meta = _get_redis()
        snap_h: dict = {}
        if r_meta is not None:
            for name in ("swing", "longterm", "discovery"):
                try:
                    raw = r_meta.get(f"{META_PREFIX}{name}")
                    if raw:
                        import json as _json

                        m = _json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                        snap_h[name] = {
                            "size_bytes": m.get("size_bytes"),
                            "written_at": m.get("ts"),
                            "ok": m.get("ok"),
                        }
                    else:
                        snap_h[name] = {"available": False}
                except Exception:
                    snap_h[name] = {"error": True}
        diag["snapshot_route_health"] = snap_h
    except Exception as e:
        diag["snapshot_route_health"] = {"error": str(e)}

    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is not None:
            mem = r.info("memory")
            diag["redis_memory"] = {
                "used_memory": mem.get("used_memory"),
                "used_memory_human": mem.get("used_memory_human"),
                "used_memory_peak": mem.get("used_memory_peak"),
            }
            # Avoid Redis KEYS (blocking); approximate snapshot:* via bounded SCAN
            _cur = 0
            _n = 0
            for _ in range(80):
                _cur, batch = r.scan(_cur, match="snapshot:*", count=64)
                _n += len(batch)
                if _cur == 0:
                    break
            diag["redis_snapshot_keys_scan_approx"] = _n

            _candidates: list[tuple[int, str]] = []
            _scan_cur = 0
            for _ in range(100):
                _scan_cur, batch = r.scan(_scan_cur, match="snapshot:*", count=56)
                for bk in batch:
                    ks = bk.decode("utf-8", errors="replace") if isinstance(bk, bytes) else str(bk)
                    try:
                        ln = int(r.strlen(ks))
                    except Exception:
                        continue
                    _candidates.append((ln, ks))
                if _scan_cur == 0:
                    break
            _candidates.sort(key=lambda x: -x[0])
            _largest: list[dict] = []
            for sz, k in _candidates[:12]:
                try:
                    tt = r.ttl(k)
                    ttl_out = int(tt) if tt is not None and int(tt) >= 0 else None
                except Exception:
                    ttl_out = None
                _largest.append({"key": k, "value_strlen": sz, "ttl_sec": ttl_out})
            diag["redis_largest_snapshot_string_keys"] = _largest

            _dw = 0
            _scan_w = 0
            for _ in range(60):
                _scan_w, batch_w = r.scan(_scan_w, match="snapshot:watchlist_digest:*", count=48)
                _dw += len(batch_w)
                if _scan_w == 0:
                    break
            diag["watchlist_os_redis"] = {
                "digest_keys_scan_approx": _dw,
                "operating_live_key_pattern": "snapshot:watchlist_operating:{user_id}",
                "digest_key_pattern": "snapshot:watchlist_digest:{user_id}",
            }
        else:
            diag["redis_memory"] = {"note": "redis_unavailable"}
    except Exception as e:
        diag["redis_memory"] = {"error": str(e)}

    try:
        from services.signal_delivery import get_signal_debug

        diag["signal_health"] = get_signal_debug()
    except Exception as e:
        diag["signal_health"] = {"error": str(e)}

    # ── Watchlist OS aggregate health (Redis SCAN + JSON samples; bounded cost) ──
    try:
        import json as _json

        from dashboard.backend.cache import _get_redis

        _r = _get_redis()
        _wh: dict = {}
        if _r is not None:
            _now_ts = time.time()
            _digest_sizes: list[int] = []
            _digest_stale = 0
            _max_snap_age = 0.0
            _op_sizes: list[int] = []
            _feed_events = 0
            _feed_invalidations = 0
            _feed_transitions = 0
            _op_keys = 0
            _build_samples: list[float] = []

            _dc = 0
            for _ in range(120):
                _dc, _batch = _r.scan(_dc, match="snapshot:watchlist_digest:*", count=48)
                for _bk in _batch:
                    _ks = _bk if isinstance(_bk, str) else _bk.decode("utf-8", errors="replace")
                    _raw = _r.get(_ks)
                    if not _raw:
                        continue
                    try:
                        _d = _json.loads(_raw)
                        _sc = int(_d.get("symbol_count") or 0)
                        _digest_sizes.append(_sc)
                        _bm = _d.get("build_ms")
                        if _bm is not None:
                            try:
                                _build_samples.append(float(_bm))
                            except (TypeError, ValueError):
                                pass
                        _ua = float(_d.get("updated_at") or 0)
                        if _ua and _now_ts - _ua > 120:
                            _digest_stale += 1
                        if _ua:
                            _max_snap_age = max(_max_snap_age, _now_ts - _ua)
                    except Exception:
                        pass
                if _dc == 0:
                    break

            _oc = 0
            for _ in range(120):
                _oc, _batch = _r.scan(_oc, match="snapshot:watchlist_operating:*", count=48)
                _op_keys += len(_batch)
                for _bk in _batch[:8]:
                    _ks = _bk if isinstance(_bk, str) else _bk.decode("utf-8", errors="replace")
                    _raw = _r.get(_ks)
                    if not _raw:
                        continue
                    try:
                        _pl = _json.loads(_raw)
                        _items = _pl.get("items") if isinstance(_pl.get("items"), list) else []
                        _op_sizes.append(len(_items))
                        _w = float(_pl.get("_snapshot_written_at") or 0)
                        if _w:
                            _max_snap_age = max(_max_snap_age, _now_ts - _w)
                    except Exception:
                        pass
                if _oc == 0:
                    break

            _fc = 0
            for _ in range(100):
                _fc, _batch = _r.scan(_fc, match="watchlist:feed:*", count=40)
                for _bk in _batch:
                    _ks = _bk if isinstance(_bk, str) else _bk.decode("utf-8", errors="replace")
                    _raw = _r.get(_ks)
                    if not _raw:
                        continue
                    try:
                        _hist = _json.loads(_raw)
                        if isinstance(_hist, list):
                            _feed_events += len(_hist)
                            for _ev in _hist[:40]:
                                if not isinstance(_ev, dict):
                                    continue
                                _hl = str(_ev.get("headline") or "").lower()
                                if "invalid" in _hl:
                                    _feed_invalidations += 1
                                if (
                                    "→" in _hl
                                    or "stage" in _hl
                                    or "readiness" in _hl
                                    or "weakening" in _hl
                                    or "near executable" in _hl
                                ):
                                    _feed_transitions += 1
                    except Exception:
                        pass
                if _fc == 0:
                    break

            try:
                _wt = diag.get("websocket_telemetry") or {}
                _bps = float(_wt.get("avg_broadcast_bytes") or 0)
                _bc = int(_wt.get("total_broadcasts_since_boot") or 0)
                _upt = float(diag.get("backend_uptime_sec") or 1)
                _delta_rate = round((_bps * max(_bc, 1)) / max(_upt, 1.0), 2)
            except Exception:
                _delta_rate = None

            _wh = {
                "active_watchlists_operating_keys_approx": _op_keys,
                "avg_watchlist_size_digest": (
                    round(sum(_digest_sizes) / len(_digest_sizes), 2) if _digest_sizes else None
                ),
                "avg_watchlist_size_operating_sample": (
                    round(sum(_op_sizes) / len(_op_sizes), 2) if _op_sizes else None
                ),
                "digest_key_samples": len(_digest_sizes),
                "stale_watchlists_digest_age_gt_120s": _digest_stale,
                "max_snapshot_age_sec_approx": round(_max_snap_age, 1) if _max_snap_age else None,
                "feed_events_total_list_items_scanned": _feed_events,
                "feed_invalidation_headlines_sample": _feed_invalidations,
                "feed_lifecycle_hint_headlines_sample": _feed_transitions,
                "websocket_broadcast_volume_rate_bytes_per_sec_est": _delta_rate,
                "avg_rebuild_duration_ms_digest": (
                    round(sum(_build_samples) / len(_build_samples), 2) if _build_samples else None
                ),
                "watchlist_delta_ws_enabled": True,
                "note": "watchlist_delta WS frames carry user_id only; clients refetch GET /api/watchlist/operating. Feed lists stay Redis-backed.",
            }
        else:
            _wh = {"note": "redis_unavailable"}
        diag["watchlist_health"] = _wh
    except Exception as e:
        diag["watchlist_health"] = {"error": str(e)}

    try:
        import json as _json

        from dashboard.backend.cache import _get_redis
        from dashboard.backend.global_state_version import KEY_LAST_BUMP_META, read_global_state_version

        _r_eng = _get_redis()
        _gsv_dbg = read_global_state_version()
        _leg_dbg = 0
        _bump_meta: dict = {}
        if _r_eng is not None:
            try:
                _gv_raw = _r_eng.get("snapshot:global_version")
                _leg_dbg = int(_gv_raw) if _gv_raw is not None else 0
            except Exception:
                _leg_dbg = 0
            try:
                _mr = _r_eng.get(KEY_LAST_BUMP_META)
                if _mr:
                    _bump_meta = _json.loads(_mr.decode() if isinstance(_mr, bytes) else _mr)
            except Exception:
                _bump_meta = {}
        diag["state_engine"] = {
            "global_version": _gsv_dbg,
            "legacy_global_version": _leg_dbg,
            "versions_aligned": _gsv_dbg == _leg_dbg,
            "last_bump_meta": _bump_meta,
            "redis_keys": {
                "global_state_version": "snapshot:global_state_version",
                "legacy_mirror": "snapshot:global_version",
            },
            "ws_queue_depth": None,
            "stale_entities": None,
            "reconciliation_failures": None,
            "rejected_deltas": None,
            "forced_resyncs": None,
            "entity_registry_size": None,
            "duplicate_entity_keys": None,
            "avg_event_latency_ms": None,
            "stale_snapshot_count": None,
            "notes": "Browser-side queue/reconcile metrics require client instrumentation; Redis holds canonical global_state_version.",
        }
    except Exception as e:
        diag["state_engine"] = {"error": str(e)}

    try:
        from dashboard.backend.services.decision_intelligence_engine import (
            read_decision_engine_debug,
            read_execution_engine_debug,
        )

        diag["decision_engine"] = read_decision_engine_debug()
        diag["execution_engine"] = read_execution_engine_debug()
    except Exception as e:
        diag["decision_engine"] = {"error": str(e)}
        diag["execution_engine"] = {"error": str(e)}

    try:
        from dashboard.backend.routes.user_product import read_product_metrics

        diag["product_metrics"] = read_product_metrics()
    except Exception as e:
        diag["product_metrics"] = {"error": str(e)}

    try:
        from dashboard.backend.services.retention_engine_service import read_retention_engine_metrics

        diag["retention_engine"] = read_retention_engine_metrics()
    except Exception as e:
        diag["retention_engine"] = {"error": str(e)}

    diag["total_latency_ms"] = round((time.perf_counter_ns() - start_ns) / 1_000_000, 2)
    return diag
