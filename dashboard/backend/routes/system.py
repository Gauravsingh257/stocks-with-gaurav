"""
dashboard/backend/routes/system.py
System health, version, diagnostics, and tactical plan endpoints.

GET /api/system/health         — DB, WS, engine live status, uptime, market_status, worker heartbeat
GET /api/system/version        — backend + engine + agent version strings
GET /api/system/tactical-plan  — today's tactical plan from PreMarketBriefing
"""

import json
import time
from datetime import datetime, time as dtime
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["system"])

BACKEND_VERSION = "1.1.0"
AGENT_VERSION   = "2.0.0"
_start_time     = time.time()

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
        import logging
        _log = logging.getLogger("dashboard.system")
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
            from dashboard.backend.routes.charts import _get_kite, _reset_kite
            k = _get_kite()
            if k is not None:
                try:
                    k.profile()
                    kite_connected = True
                except Exception:
                    _log.warning("Kite session expired")
                    kite_disconnect_reason = "token_expired"
                    _reset_kite()
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
                        engine_status = "stale"  # so health shows stale when worker died
                    else:
                        worker_status = "running"
                except (TypeError, ValueError):
                    worker_status = "stale"
                    engine_status = "stale"
            else:
                worker_status = "stale"
                engine_status = "stale"
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
