"""
dashboard/backend/state_bridge.py

Read-only bridge between the SMC engine's in-memory globals and the dashboard.

TWO MODES:
  LIVE MODE  — when FastAPI is launched as part of the engine process
               (e.g. via run_dashboard.bat that starts both together).
               Reads globals directly from smc_mtf_engine_v4 module.

  STANDALONE MODE — when FastAPI is run independently for development
               (engine not imported). Falls back to reading from:
               • dashboard.db  (agent_logs, regime_history)
               • smc_engine_state.db  (ACTIVE_TRADES persisted via state_db)
               • JSON fallback files (active_setups.json etc.)

SAFETY RULE: This module NEVER writes to engine globals.
             All returned objects are deep-copied or constructed fresh.
"""

import copy
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# ATTEMPT LIVE ENGINE IMPORT (safe — won't crash if unavailable)
# ─────────────────────────────────────────────────────────────
_ENGINE = None
_ENGINE_AVAILABLE = False

try:
    import importlib
    _ENGINE = importlib.import_module("smc_mtf_engine_v4")
    _ENGINE_AVAILABLE = True
    logger.info("[StateBridge] Live engine connection established")
except Exception as e:
    logger.info(f"[StateBridge] Engine not imported — STANDALONE mode ({e})")

# ─────────────────────────────────────────────────────────────
# FALLBACK: state_db for ACTIVE_TRADES when engine not imported
# ─────────────────────────────────────────────────────────────
_STATE_DB = None
try:
    import sys
    _WORKSPACE_ROOT = str(Path(__file__).resolve().parents[2])
    if _WORKSPACE_ROOT not in sys.path:
        sys.path.insert(0, _WORKSPACE_ROOT)
    from utils.state_db import db as _STATE_DB
    logger.info("[StateBridge] state_db fallback available")
except Exception as e:
    logger.warning(f"[StateBridge] state_db fallback unavailable: {e}")


# ─────────────────────────────────────────────────────────────
# SAFE READERS — never raise, always return a sane default
# ─────────────────────────────────────────────────────────────

def _safe_read(attr: str, default: Any) -> Any:
    """Read an attribute from the live engine module without mutating it."""
    if _ENGINE_AVAILABLE and _ENGINE is not None:
        try:
            return getattr(_ENGINE, attr, default)
        except Exception:
            return default
    return default


def _get_active_trades_live() -> List[Dict]:
    """Deep-copy ACTIVE_TRADES from engine (live mode)."""
    raw = _safe_read("ACTIVE_TRADES", [])
    try:
        # Deep copy; strip non-serialisable objects (DataFrames, datetimes→str)
        cleaned = []
        for t in raw:
            entry = {}
            for k, v in t.items():
                if hasattr(v, "isoformat"):          # datetime
                    entry[k] = v.isoformat()
                elif hasattr(v, "to_dict"):           # pandas DataFrame
                    entry[k] = None                   # omit raw OHLC data
                else:
                    entry[k] = copy.deepcopy(v)
            cleaned.append(entry)
        return cleaned
    except Exception as e:
        logger.error(f"[StateBridge] Failed to copy ACTIVE_TRADES: {e}")
        return []


def _get_active_trades_fallback() -> List[Dict]:
    """Read ACTIVE_TRADES from state_db (standalone/fallback mode)."""
    if _STATE_DB is None:
        return []
    try:
        data = _STATE_DB.get_value("engine_state", "active_trades", default=[])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _get_zone_state_live() -> Dict:
    """Deep-copy ZONE_STATE from engine."""
    raw = _safe_read("ZONE_STATE", {})
    try:
        result = {}
        for symbol, zones in raw.items():
            result[symbol] = {}
            for direction, zone in zones.items():
                if zone is None:
                    result[symbol][direction] = None
                else:
                    z = {}
                    for k, v in zone.items():
                        if hasattr(v, "isoformat"):
                            z[k] = v.isoformat()
                        elif hasattr(v, "to_dict"):
                            z[k] = None
                        else:
                            z[k] = copy.deepcopy(v)
                    result[symbol][direction] = z
        return result
    except Exception as e:
        logger.error(f"[StateBridge] Failed to copy ZONE_STATE: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# PUBLIC API — single function consumed by all routes + WebSocket
# ─────────────────────────────────────────────────────────────

def get_engine_snapshot() -> Dict:
    """
    Returns a SAFE, READ-ONLY snapshot of the engine's current state.
    Deep-copied — callers cannot mutate engine globals.
    Safe to call at any frequency.
    """
    if _ENGINE_AVAILABLE:
        active_trades = _get_active_trades_live()
        zone_state    = _get_zone_state_live()
        market_regime = str(_safe_read("MARKET_REGIME", "NEUTRAL"))
        daily_pnl_r   = float(_safe_read("DAILY_PNL_R", 0.0))
        cb_active     = bool(_safe_read("CIRCUIT_BREAKER_ACTIVE", False))
        consec_losses = int(_safe_read("CONSECUTIVE_LOSSES", 0))
        traded_today  = list(_safe_read("TRADED_TODAY", set()))
        daily_signal_count = int(_safe_read("DAILY_SIGNAL_COUNT", len(traded_today)))
        engine_mode   = str(_safe_read("ENGINE_MODE", "UNKNOWN"))
        active_strats = dict(_safe_read("ACTIVE_STRATEGIES", {}))
        max_daily_loss= float(_safe_read("MAX_DAILY_LOSS_R", -3.0))
        max_signals   = int(_safe_read("MAX_DAILY_SIGNALS", 5))
        index_only    = bool(_safe_read("INDEX_ONLY", True))
        paper_mode    = bool(_safe_read("PAPER_MODE", False))
        engine_last_loop_at = _safe_read("ENGINE_LAST_LOOP_AT", None)
        engine_running = False
        engine_live = True  # in-process engine is always "live"
        heartbeat_age_sec = None
        if hasattr(engine_last_loop_at, "isoformat"):
            try:
                heartbeat_age_sec = round((datetime.now(_IST).replace(tzinfo=None) - engine_last_loop_at).total_seconds(), 2)
                # Engine considered running if loop heartbeat was seen in the last 120s.
                engine_running = heartbeat_age_sec <= 120
            except Exception:
                heartbeat_age_sec = None
                engine_running = False
        # Setup-D live state (shows BOS_WAIT / WAIT / TAPPED stages + gap-day flag)
        raw_sds = _safe_read("SETUP_D_STATE", {})
        setup_d_state: Dict = {}
        try:
            for k, v in raw_sds.items():
                entry = {}
                for sk, sv in v.items():
                    if hasattr(sv, "isoformat"):
                        entry[sk] = sv.isoformat()
                    elif isinstance(sv, bool):
                        entry[sk] = sv
                    elif isinstance(sv, (int, float, str)) or sv is None:
                        entry[sk] = sv
                    elif isinstance(sv, tuple):
                        entry[sk] = list(sv)
                    else:
                        entry[sk] = copy.deepcopy(sv)
                setup_d_state[k] = entry
        except Exception:
            setup_d_state = {}

        # Tier 3: adaptive intelligence snapshot
        adaptive_intel = {
            "setup_multipliers": {},
            "recent_blocks": [],
            "recent_ai_scores": [],
        }
        try:
            setup_mult = {}
            setup_keys = list(active_strats.keys())
            multiplier_fn = getattr(_ENGINE, "_get_adaptive_setup_multiplier", None) if _ENGINE else None
            if callable(multiplier_fn):
                for setup_name in setup_keys:
                    setup_mult[setup_name] = float(multiplier_fn(setup_name))
            adaptive_intel["setup_multipliers"] = setup_mult
        except Exception:
            adaptive_intel["setup_multipliers"] = {}

        try:
            raw_blocks = list(_safe_read("ADAPTIVE_BLOCK_LOG", []))
            blocks = []
            for b in raw_blocks[-10:]:
                row = dict(b)
                if hasattr(row.get("ts"), "isoformat"):
                    row["ts"] = row["ts"].isoformat()
                blocks.append(row)
            adaptive_intel["recent_blocks"] = blocks
        except Exception:
            adaptive_intel["recent_blocks"] = []

        try:
            raw_scores = list(_safe_read("ADAPTIVE_SCORE_LOG", []))
            scores = []
            for s in raw_scores[-10:]:
                row = dict(s)
                if hasattr(row.get("ts"), "isoformat"):
                    row["ts"] = row["ts"].isoformat()
                scores.append(row)
            adaptive_intel["recent_ai_scores"] = scores
        except Exception:
            adaptive_intel["recent_ai_scores"] = []
    else:
        active_trades = _get_active_trades_fallback()
        zone_state    = {}
        market_regime = "NEUTRAL"
        daily_pnl_r   = 0.0
        cb_active     = False
        consec_losses = 0
        traded_today  = []
        daily_signal_count = 0
        engine_mode   = "STANDALONE_DEV"
        active_strats = {}
        max_daily_loss= -3.0
        max_signals   = 5
        index_only    = True
        paper_mode    = False
        setup_d_state = {}
        adaptive_intel = {
            "setup_multipliers": {},
            "recent_blocks": [],
            "recent_ai_scores": [],
        }
        # Railway 24/7: engine runs as separate service — derive status from Redis heartbeat
        try:
            from dashboard.backend.cache import get_engine_heartbeat_ts, ENGINE_HEARTBEAT_STALE_SEC, ENGINE_HEARTBEAT_OFFLINE_SEC
            import time as _time
            ts = get_engine_heartbeat_ts()
            if ts is not None:
                heartbeat_age_sec = round(_time.time() - ts, 2)
                engine_running = heartbeat_age_sec <= ENGINE_HEARTBEAT_STALE_SEC   # <= 60s = LIVE
                engine_live = heartbeat_age_sec <= ENGINE_HEARTBEAT_OFFLINE_SEC  # <= 120s = at least STALE
            else:
                heartbeat_age_sec = None
                engine_running = False
                engine_live = False
        except Exception:
            heartbeat_age_sec = None
            engine_running = False
            engine_live = False

    _engine_version = _get_engine_version_from_cache()
    if _engine_version is None:
        _engine_version = str(_safe_read("ENGINE_VERSION", "v4")) if _ENGINE_AVAILABLE else "v4"
    _last_cycle_ts, _last_cycle_age = _get_engine_last_cycle_from_cache()

    return {
        # ── Core trade state
        "active_trades":       active_trades,
        "active_trade_count":  len(active_trades),
        "zone_state":          zone_state,

        # ── Daily metrics
        "daily_pnl_r":         daily_pnl_r,
        "consecutive_losses":  consec_losses,
        "signals_today":       daily_signal_count,
        "traded_today":        traded_today,

        # ── Risk state
        "circuit_breaker_active": cb_active,
        "market_regime":       market_regime,
        "max_daily_loss_r":    max_daily_loss,
        "max_daily_signals":   max_signals,

        # ── Engine config (read-only info)
        "engine_mode":         engine_mode,
        "active_strategies":   active_strats,
        "index_only":          index_only,
        "paper_mode":          paper_mode,

        # ── Setup-D live state (stages + gap-day flag)
        "setup_d_state":       setup_d_state,
        "adaptive_intel":      adaptive_intel,

        # ── Meta (engine_live: True when in-process; when standalone, from Redis heartbeat)
        "engine_live":         engine_live,
        "engine_running":      engine_running,
        "engine_heartbeat_age_sec": heartbeat_age_sec,
        "engine_started_at":   _get_engine_started_at_from_cache(),
        "engine_version":      _engine_version,
        "engine_last_cycle":   _last_cycle_ts,
        "engine_last_cycle_age_sec": _last_cycle_age,
        "snapshot_time":       datetime.now(_IST).isoformat(),

        # ── Index LTP from cache (for real-time command bar / sparklines)
        "index_ltp":           _get_index_ltp_from_cache(),
    }


def _get_engine_started_at_from_cache() -> Optional[float]:
    """Return engine start timestamp (epoch seconds) from Redis for uptime display. None if not set."""
    try:
        from dashboard.backend.cache import get_engine_started_at
        return get_engine_started_at()
    except Exception:
        return None


def _get_engine_version_from_cache() -> Optional[str]:
    """Return running engine version from Redis (e.g. v4.2.1). None if not set."""
    try:
        from dashboard.backend.cache import get_engine_version
        return get_engine_version()
    except Exception:
        return None


def _get_engine_last_cycle_from_cache() -> tuple[Optional[float], Optional[float]]:
    """Return (engine_last_cycle_ts, engine_last_cycle_age_sec) from Redis. (None, None) if not set."""
    try:
        import time as _time
        from dashboard.backend.cache import get_engine_last_cycle
        ts = get_engine_last_cycle()
        if ts is None:
            return (None, None)
        age = round(_time.time() - ts, 2)
        return (ts, age)
    except Exception:
        return (None, None)


def _get_index_ltp_from_cache() -> Dict[str, float]:
    """Read LTP from Redis (realtime tick stream) first; fallback to OHLC last close (worker)."""
    out: Dict[str, float] = {}
    try:
        from dashboard.backend.cache import get_ltp, get as cache_get, ohlc_key
        for label, redis_sym in (("NIFTY 50", "NIFTY"), ("NIFTY BANK", "BANKNIFTY")):
            ltp = get_ltp(redis_sym)
            if ltp is not None:
                out[label] = float(ltp)
                continue
            # Fallback: last close from OHLC cache (worker or historical)
            kite_sym = "NSE:NIFTY 50" if redis_sym == "NIFTY" else "NSE:NIFTY BANK"
            key = ohlc_key(kite_sym, "15minute")
            candles = cache_get(key)
            if isinstance(candles, list) and len(candles) > 0:
                last = candles[-1]
                if isinstance(last, dict) and isinstance(last.get("close"), (int, float)):
                    out[label] = float(last["close"])
    except Exception:
        pass
    return out


def is_engine_live() -> bool:
    return _ENGINE_AVAILABLE


def get_market_regime() -> str:
    return str(_safe_read("MARKET_REGIME", "NEUTRAL"))
