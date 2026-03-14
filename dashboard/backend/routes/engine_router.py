"""
dashboard/backend/routes/engine_router.py
Phase 7 + Phase 2 Upgrade: Decision Trace API + Displacement Events API

GET /api/engine/decision-trace          — Full Setup-D audit log
GET /api/engine/decision-trace/{symbol} — Per-symbol trace
GET /api/engine/setup-d-state           — Current live SETUP_D_STATE dict
GET /api/engine/displacement-events     — Recent displacement events (Early SMC)
GET /api/engine/early-warning           — EARLY_SMART_MONEY_ACTIVITY states
"""

import copy
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/engine", tags=["engine"])
log = logging.getLogger("engine_router")

# Maximum trace entries to return per symbol (avoid huge payloads)
_MAX_ENTRIES = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_engine_read(attr: str, default: Any) -> Any:
    """Read an attribute from the live engine module without mutation."""
    try:
        from dashboard.backend.state_bridge import _ENGINE, _ENGINE_AVAILABLE
        if _ENGINE_AVAILABLE and _ENGINE is not None:
            return copy.deepcopy(getattr(_ENGINE, attr, default))
    except Exception as e:
        log.warning(f"[engine_router] Could not read '{attr}' from engine: {e}")
    return default


def _serialise_trace_entry(entry: dict) -> dict:
    """Convert datetime objects to ISO strings for JSON serialisation."""
    out = {}
    for k, v in entry.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, tuple):
            out[k] = list(v)
        else:
            out[k] = v
    return out


def _serialise_trace(trace: dict) -> dict:
    """Convert the full SETUP_D_STRUCTURE_TRACE dict for JSON output."""
    result = {}
    for symbol, entries in trace.items():
        if isinstance(entries, list):
            result[symbol] = [_serialise_trace_entry(e) for e in entries[-_MAX_ENTRIES:]]
        else:
            result[symbol] = entries
    return result


def _serialise_state(state: dict) -> dict:
    """Convert SETUP_D_STATE dict for JSON output."""
    result = {}
    for key, val in state.items():
        if isinstance(val, dict):
            result[key] = _serialise_trace_entry(val)
        else:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/decision-trace")
def get_decision_trace(limit: int = 50):
    """
    Returns the full Setup-D decision audit log (SETUP_D_STRUCTURE_TRACE).

    Each entry records:
      - timestamp     : when the signal was evaluated
      - symbol        : trading symbol
      - direction     : LONG / SHORT
      - choch_time    : when CHoCH was first detected
      - bos_confirmed : whether BOS stage completed before entry
      - sweep_detected: whether a liquidity sweep was present at CHoCH
      - ob            : [low, high] of the Order Block
      - fvg           : [low, high] of the Fair Value Gap
      - score         : SMC confluence score (Setup-D scoring)
      - score_breakdown: score component breakdown
      - signal_fired  : True if a trade signal was emitted
      - block_reason  : why the signal was blocked (if not fired)
      - entry / sl / target / rr: levels when fired
    """
    trace = _safe_engine_read("SETUP_D_STRUCTURE_TRACE", {})
    payload = _serialise_trace(trace)

    # Flatten to a list sorted by timestamp (newest first)
    all_entries = []
    for symbol, entries in payload.items():
        for e in entries:
            e.setdefault("symbol", symbol)
            all_entries.append(e)

    all_entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return {
        "total": len(all_entries),
        "limit": limit,
        "entries": all_entries[:limit],
        "symbols_tracked": list(payload.keys()),
    }


@router.get("/decision-trace/{symbol}")
def get_decision_trace_symbol(symbol: str, limit: int = 20):
    """
    Returns the Setup-D decision trace for a single symbol.
    Symbol should be URL-encoded if it contains spaces or colons.
    """
    trace = _safe_engine_read("SETUP_D_STRUCTURE_TRACE", {})

    # Try exact match, then partial
    entries = trace.get(symbol)
    if entries is None:
        # Partial match (e.g. 'NIFTY' matches 'NSE:NIFTY 50')
        for key, val in trace.items():
            if symbol.upper() in key.upper():
                entries = val
                break

    if not entries:
        raise HTTPException(status_code=404, detail=f"No trace found for symbol: {symbol}")

    serialised = [_serialise_trace_entry(e) for e in entries[-limit:]]
    serialised.reverse()  # newest first

    return {
        "symbol": symbol,
        "total": len(serialised),
        "entries": serialised,
    }


@router.get("/setup-d-state")
def get_setup_d_state():
    """
    Returns the current live SETUP_D_STATE dict — shows what symbols are
    in BOS_WAIT / WAIT / TAPPED stages right now.
    """
    state = _safe_engine_read("SETUP_D_STATE", {})
    return {
        "active_count": len(state),
        "states": _serialise_state(state),
    }


@router.get("/setup-d-config")
def get_setup_d_config():
    """
    Returns the current Setup-D configuration parameters for transparency.
    """
    active_strategies = _safe_engine_read("ACTIVE_STRATEGIES", {})
    disabled_setups = _safe_engine_read("_DISABLED_SETUPS", set())

    return {
        "setup_d_enabled": active_strategies.get("SETUP_D", False),
        "index_only": True,  # Phase 1: always index-only
        "disabled_setups": list(disabled_setups),
        "phases_active": {
            "phase1_index_only_gate": True,
            "phase2_bos_confirmation": True,
            "phase2_displacement_detection": True,
            "phase3_4h_expiry_for_indices": True,
            "phase4_liquidity_sweep_htf_bypass": True,
            "phase4b_gap_day_sweep_override": True,      # gap > 0.3% counts as sweep
            "phase5_liquidity_engine": True,
            "phase6_early_smart_money_state": True,
            "phase6_scored_confluences": True,
            "phase7_decision_trace_api": True,
            "phase8_pipeline_wired": True,
            "phase9_backward_compat_fallback": True,
            "phase10_opening_gap_choch_detector": True,  # same-day CHoCH on gap days
        },
        "gap_day_config": {
            "threshold_pct": 0.3,            # gap > 0.3% triggers dedicated detector
            "detector": "detect_choch_opening_gap",
            "bar0_spike_skip": True,         # opening wick excluded from range
            "open_window_bars": 5,           # bars 1-4 define opening range
            "displacement_skip": True,       # gap itself IS displacement
            "bos_level": "choch_close * 1.001",
            "sweep_override": True,          # gap overrides sweep detector for HTF bypass
        },
    }


# ---------------------------------------------------------------------------
# NEW: Displacement Events Endpoint (Phase 2 — Early SMC Activity)
# ---------------------------------------------------------------------------

@router.get("/displacement-events")
def get_displacement_events(
    symbol: str = Query(None, description="Filter by symbol (partial match)"),
    limit: int  = Query(50,   description="Max events to return"),
):
    """
    Returns recent displacement events detected by engine/displacement_detector.py.

    Each event represents an institutional momentum candle detected BEFORE CHoCH
    fires — 30–40 min earlier than traditional CHOCH-based signals.

    Fields:
      timestamp        : when the displacement candle closed
      symbol           : instrument
      direction        : "bullish" | "bearish"
      strength         : "weak" | "medium" | "strong"
      created_fvg      : bool — whether a 3-candle FVG imbalance was created
      atr_ratio        : candle range / ATR (e.g. 2.3 = 2.3x ATR)
      body_ratio       : body / full range (0.0–1.0)
      confidence       : "low" | "medium" | "high"
      price            : close of the displacement candle
      liquidity_context: "sweep_present" | "no_sweep"
    """
    try:
        from engine.displacement_detector import get_recent_displacement_events
        raw = get_recent_displacement_events(symbol=symbol, limit=limit)
    except ImportError:
        # Fallback: read from engine module if accessible
        raw = _safe_engine_read("DISPLACEMENT_EVENTS", [])
        if symbol:
            raw = [e for e in raw if symbol.upper() in str(e.get("symbol", "")).upper()]
        raw = list(raw)[:limit]

    # Serialise datetime objects
    events = []
    for e in raw:
        entry = {}
        for k, v in e.items():
            if isinstance(v, datetime):
                entry[k] = v.isoformat()
            else:
                entry[k] = v
        events.append(entry)

    return {
        "total"           : len(events),
        "limit"           : limit,
        "symbol_filter"   : symbol,
        "events"          : events,
        "description"     : (
            "Institutional displacement candles detected before CHoCH. "
            "High confidence + sweep_present = strongest early warning."
        ),
    }


# ---------------------------------------------------------------------------
# NEW: Early Warning State Endpoint (Phase 6 — EARLY_SMART_MONEY_ACTIVITY)
# ---------------------------------------------------------------------------

@router.get("/early-warning")
def get_early_warning_state():
    """
    Returns the EARLY_SMART_MONEY_ACTIVITY state per symbol.

    This state is set when:
      - A high/medium confidence displacement candle is detected
      - AND optionally a liquidity sweep is nearby

    It prepares the engine for an imminent CHoCH — the signal that
    a large player has started accumulating/distributing BEFORE structure
    formally breaks.

    Fields per symbol:
      type        : always "EARLY_SMART_MONEY_ACTIVITY"
      direction   : "bullish" | "bearish"
      confidence  : "low" | "medium" | "high"
      displacement: raw displacement event dict
      timestamp   : when first detected
      liquidity   : "sweep_present" | "no_sweep"
    """
    state = _safe_engine_read("EARLY_WARNING_STATE", {})

    serialised = {}
    for sym, val in state.items():
        entry = {}
        for k, v in val.items():
            if isinstance(v, datetime):
                entry[k] = v.isoformat()
            elif isinstance(v, dict):
                inner = {}
                for ik, iv in v.items():
                    inner[ik] = iv.isoformat() if isinstance(iv, datetime) else iv
                entry[k] = inner
            else:
                entry[k] = v
        serialised[sym] = entry

    active_high   = [s for s, v in serialised.items() if v.get("confidence") == "high"]
    active_medium = [s for s, v in serialised.items() if v.get("confidence") == "medium"]

    return {
        "active_count"   : len(serialised),
        "high_confidence": active_high,
        "med_confidence" : active_medium,
        "states"         : serialised,
    }

