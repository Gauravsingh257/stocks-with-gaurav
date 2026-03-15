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
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot
        snap           = get_engine_snapshot()
        engine_live    = snap.get("engine_live", False)
        engine_mode    = snap.get("engine_mode", "UNKNOWN")
        last_snapshot  = snap.get("snapshot_time")
        engine_version = snap.get("engine_version", "v4")
        engine_running = snap.get("engine_running", False)
    except Exception:
        pass

    if engine_running and engine_live:
        engine_status = "running"
    elif engine_live:
        engine_status = "stale"
    else:
        engine_status = "offline"

    # ── Kite connected (token valid) + hint when disconnected ─
    kite_connected = False
    kite_hint = None
    try:
        from config.kite_auth import is_kite_available
        if is_kite_available():
            from dashboard.backend.routes.charts import _get_kite
            k = _get_kite()
            if k is not None:
                k.profile()
                kite_connected = True
        if not kite_connected:
            kite_hint = "Token expired or invalid. Run zerodha_login.py and update KITE_ACCESS_TOKEN."
    except Exception:
        kite_hint = "Token expired or invalid. Run zerodha_login.py and update KITE_ACCESS_TOKEN."

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

    out = {
        "engine_status":    engine_status,
        "kite_connected":   kite_connected,
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
    """Return Kite connectivity status without exposing credentials."""
    import os
    api_key_set    = bool(os.getenv("KITE_API_KEY", "").strip())
    token_set      = bool(os.getenv("KITE_ACCESS_TOKEN", "").strip())
    token_file_ok  = False
    try:
        from pathlib import Path as _Path
        tf = _Path(__file__).resolve().parents[3] / "access_token.txt"
        token_file_ok = tf.exists() and bool(tf.read_text().strip())
    except Exception:
        pass

    kite_ready = False
    kite_error = None
    try:
        from config.kite_auth import is_kite_available
        kite_ready = is_kite_available()
    except Exception as e:
        kite_error = str(e)

    # Attempt a lightweight Kite API call to verify the token is valid
    token_valid = False
    if kite_ready:
        try:
            from dashboard.backend.routes.charts import _get_kite
            k = _get_kite()
            if k is not None:
                k.profile()   # lightweight call — raises on bad token
                token_valid = True
        except Exception as ve:
            kite_error = str(ve)

    hint = None
    if not api_key_set:
        hint = "Add KITE_API_KEY to Railway Variables and Redeploy."
    elif not (token_set or token_file_ok):
        hint = "Add KITE_ACCESS_TOKEN to Railway Variables. Run zerodha_login.py to generate a fresh token."
    elif not token_valid:
        hint = "Token appears invalid or expired. Run zerodha_login.py and update KITE_ACCESS_TOKEN in Railway."

    return {
        "kite_ready":       kite_ready,
        "token_valid":      token_valid,
        "api_key_set":      api_key_set,
        "token_set":        token_set or token_file_ok,
        "error":            kite_error,
        "hint":             hint,
    }
