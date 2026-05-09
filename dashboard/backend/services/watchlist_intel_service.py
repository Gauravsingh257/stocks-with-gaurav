"""
Watchlist Operating System — backend-driven setup intelligence.

Sources (single source of truth, no fake frontend levels):
  • Research recommendations (SWING / LONGTERM) when symbol matches
  • Redis engine snapshot active_trades for ACTIVE / live levels
  • smc_evidence for progression steps (sweep, BOS, structure)

Redis:
  watchlist:feed:{user_id}     — capped event list (TTL 7d)
  watchlist:last_intel:{user_id} — last snapshot hash per symbol for feed diff
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dashboard.watchlist_intel")

FEED_PREFIX = "watchlist:feed:"
LAST_PREFIX = "watchlist:last_intel:"
MAX_FEED_EVENTS = 80
FEED_TTL_SEC = 7 * 86400


def _norm_symbol(sym: str) -> str:
    return str(sym).replace("NSE:", "").strip().upper()


def _smc(idea: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not idea:
        return {}
    ev = idea.get("smc_evidence")
    return ev if isinstance(ev, dict) else {}


def _trend_state(idea: Optional[Dict[str, Any]]) -> str:
    smc = _smc(idea)
    d = str(smc.get("structure_dir") or "").upper()
    st = str(smc.get("structure") or "")
    if "DISTRIB" in str(idea.get("setup") or "").upper():
        return "distribution"
    if "ACCUM" in str(idea.get("setup") or "").upper():
        return "accumulation"
    if d == "BEARISH":
        return "bearish"
    if d == "BULLISH":
        return "bullish"
    if st == "NONE" or not st:
        return "range"
    return "range"


def _progression(idea: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], float]:
    """Steps with status: complete | pending | waiting."""
    smc = _smc(idea)
    steps: List[Dict[str, Any]] = []
    weight = 0.0
    n = 0

    sweep_ok = bool(smc.get("sweep_level"))
    steps.append(
        {
            "id": "liquidity_sweep",
            "label": "Liquidity sweep",
            "status": "complete" if sweep_ok else "pending",
        }
    )
    weight += 1.0 if sweep_ok else 0.35
    n += 1

    st = str(smc.get("structure") or "")
    bos_ok = st == "BOS"
    steps.append(
        {
            "id": "bos",
            "label": "BOS",
            "status": "complete" if bos_ok else "pending",
        }
    )
    weight += 1.0 if bos_ok else 0.4
    n += 1

    ob_ok = bool(smc.get("ob_zone")) or "order_block" in json.dumps(smc).lower() or "ob" in json.dumps(smc).lower()
    steps.append(
        {
            "id": "ob_alignment",
            "label": "OB alignment",
            "status": "complete" if ob_ok else "pending",
        }
    )
    weight += 1.0 if ob_ok else 0.45
    n += 1

    tech = (idea or {}).get("technical_signals") or {}
    htf_ok = any("HTF" in str(k).upper() or "WEEK" in str(k).upper() for k in tech.keys())
    steps.append(
        {
            "id": "htf",
            "label": "HTF confirmation",
            "status": "complete" if htf_ok else "pending",
        }
    )
    weight += 1.0 if htf_ok else 0.35
    n += 1

    exec_ready = str((idea or {}).get("action_tag") or "") == "EXECUTE_NOW"
    steps.append(
        {
            "id": "entry_trigger",
            "label": "Entry trigger",
            "status": "complete" if exec_ready else "waiting",
        }
    )
    weight += 1.0 if exec_ready else 0.2
    n += 1

    readiness_pct = round(min(100.0, (weight / max(n, 1)) * 100), 1)
    return steps, readiness_pct


def lifecycle_stage_from_setup_status(setup_status: str) -> str:
    """Trade-preparation lifecycle label (institutional OS vocabulary)."""
    return {
        "EARLY": "DISCOVERY",
        "FORMING": "BUILDING",
        "NEAR_ENTRY": "NEAR_ENTRY",
        "READY": "ENTRY_READY",
        "ACTIVE": "ACTIVE",
        "INVALIDATED": "INVALIDATED",
    }.get((setup_status or "").upper(), "MONITORING")


def _derive_setup_status(
    idea: Optional[Dict[str, Any]],
    active_trade: Optional[Dict[str, Any]],
    confidence: float,
    readiness_pct: float,
) -> str:
    if active_trade:
        return "ACTIVE"
    if not idea:
        return "EARLY"
    st = str(idea.get("status") or "ACTIVE").upper()
    if st in ("CLOSED", "INVALIDATED", "EXPIRED"):
        return "INVALIDATED"
    if confidence >= 76 and readiness_pct >= 72:
        return "READY"
    if confidence >= 62 and readiness_pct >= 52:
        return "NEAR_ENTRY"
    if confidence >= 48:
        return "FORMING"
    return "EARLY"


def _blocking_reasons_summary(
    idea: Optional[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    readiness_pct: float,
) -> str:
    """Trust-first copy: explain what is missing before entry/SL/target are shown."""
    if not idea:
        return "No published research match yet — monitoring symbol until a setup is logged."
    parts: List[str] = []
    for st in steps:
        if st.get("status") == "complete":
            continue
        lid = str(st.get("id") or "")
        if lid == "bos":
            parts.append("Awaiting BOS")
        elif lid == "liquidity_sweep":
            parts.append("Liquidity not swept")
        elif lid == "ob_alignment":
            parts.append("Demand/supply zone not confirmed")
        elif lid == "htf":
            parts.append("HTF not aligned")
        elif lid == "entry_trigger":
            parts.append("Executable trigger not active")
    if readiness_pct < 45:
        parts.append("Setup quality still building")
    try:
        rr = float((idea or {}).get("risk_reward") or 0)
        if rr > 0 and rr < 1.2:
            parts.append("R:R insufficient vs risk budget")
    except (TypeError, ValueError):
        pass
    if not parts:
        parts.append("Confirmation pending")
    return " · ".join(parts[:6])


def _composite_score(confidence: float, readiness_pct: float, rr: float, idea: Optional[Dict[str, Any]]) -> int:
    rr_clamped = min(4.0, max(0.0, rr))
    base = confidence * 0.45 + readiness_pct * 0.35 + min(20.0, rr_clamped * 5.0)
    smc = _smc(idea)
    if smc.get("sweep_level") and str(smc.get("structure") or "") == "BOS":
        base += 5.0
    return int(max(0, min(100, round(base))))


def build_symbol_intel(
    symbol: str,
    idea: Optional[Dict[str, Any]],
    horizon: Optional[str],
    active_trade: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    sym = _norm_symbol(symbol)
    confidence = float((idea or {}).get("confidence_score") or 0)
    rr = float((idea or {}).get("risk_reward") or 0)
    steps, readiness_pct = _progression(idea)
    setup_status = _derive_setup_status(idea, active_trade, confidence, readiness_pct)
    trend = _trend_state(idea)
    ai_score = _composite_score(confidence, readiness_pct, rr, idea)

    show_levels = setup_status in ("READY", "ACTIVE") and idea is not None

    entry = sl = tgt = None
    rationale = None
    if show_levels and idea:
        entry = idea.get("entry_price")
        sl = idea.get("stop_loss")
        if horizon == "LONGTERM":
            tgt = idea.get("long_term_target")
        else:
            tgt = idea.get("target_1") or idea.get("target_2")
        rationale = (idea.get("reasoning_summary") or "")[:400] or None

    nearest_trigger = None
    if idea:
        at = str(idea.get("action_tag") or "")
        if at == "WAIT_FOR_RETEST":
            nearest_trigger = "Pullback toward entry zone"
        elif at == "EXECUTE_NOW":
            nearest_trigger = "Price at executable entry window"
        elif at == "IN_MOTION":
            nearest_trigger = "Trade already in motion vs plan"

    if show_levels:
        monitoring_message = None
    else:
        monitoring_message = _blocking_reasons_summary(idea, steps, readiness_pct)

    risk_notes: List[str] = []
    if idea and str(idea.get("entry_type") or "") == "LIMIT":
        risk_notes.append("Limit entry — fill risk if spot overshoots.")
    if setup_status == "INVALIDATED":
        risk_notes.append("Recorded setup no longer active in research ledger.")

    return {
        "symbol": sym,
        "horizon": horizon,
        "trend_state": trend,
        "setup_status": setup_status,
        "lifecycle_stage": lifecycle_stage_from_setup_status(setup_status),
        "current_stage": lifecycle_stage_from_setup_status(setup_status),
        "progression": steps,
        "readiness_pct": readiness_pct,
        "conviction_pct": round(confidence, 1),
        "ai_setup_score": ai_score,
        "entry_ready": bool(show_levels),
        "risk": {
            "rr": round(rr, 2) if rr else None,
            "volatility_hint": "elevated" if confidence < 55 else "moderate",
            "liquidity_quality": "ok",
            "notes": risk_notes[:4],
        },
        "recommendation": {
            "show_trade_levels": show_levels,
            "entry_ready": bool(show_levels),
            "entry": entry,
            "stop_loss": sl,
            "target": tgt,
            "rationale": rationale,
            "monitoring_message": monitoring_message,
            "nearest_trigger": nearest_trigger,
            "invalidation_reason": "Research status closed or confidence collapsed."
            if setup_status == "INVALIDATED"
            else None,
        },
        "meta": {
            "has_research_row": idea is not None,
            "in_active_trade": active_trade is not None,
        },
    }


def _active_trade_for_symbol(snap: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    sym = _norm_symbol(symbol)
    for t in snap.get("active_trades") or []:
        if not isinstance(t, dict):
            continue
        if _norm_symbol(str(t.get("symbol") or "")) == sym:
            return t
    return None


def merge_idea_maps(sw_items: List[dict], lt_items: List[dict]) -> Dict[str, Tuple[dict, str]]:
    out: Dict[str, Tuple[dict, str]] = {}
    for it in sw_items:
        s = _norm_symbol(str(it.get("symbol") or ""))
        if s:
            out[s] = (it, "SWING")
    for it in lt_items:
        s = _norm_symbol(str(it.get("symbol") or ""))
        if s and s not in out:
            out[s] = (it, "LONGTERM")
    return out


def build_operating_payload(
    symbols: List[str],
    research_map: Dict[str, Tuple[dict, str]],
    engine_snap: Dict[str, Any],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in symbols:
        sym = _norm_symbol(raw)
        idea_h = research_map.get(sym)
        idea, hz = (idea_h[0], idea_h[1]) if idea_h else (None, None)
        at = _active_trade_for_symbol(engine_snap, sym)
        out.append(build_symbol_intel(sym, idea, hz, at))
    # strongest first
    out.sort(key=lambda x: (-(x.get("ai_setup_score") or 0), x["symbol"]))
    return out


def _intel_hash(item: Dict[str, Any]) -> str:
    key = f"{item.get('setup_status')}|{item.get('ai_setup_score')}|{item.get('readiness_pct')}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def append_feed_diff(user_id: int, enriched: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Persist diff-based feed events; returns newest events (cap 12 for API)."""
    from dashboard.backend.cache import get as cache_get, set as cache_set

    uid = str(int(user_id))
    last_raw = cache_get(LAST_PREFIX + uid)
    last_map: Dict[str, str] = last_raw if isinstance(last_raw, dict) else {}

    events: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    for item in enriched:
        sym = item["symbol"]
        h = _intel_hash(item)
        prev = last_map.get(sym)
        if prev == h:
            continue
        last_map[sym] = h
        status = item.get("setup_status")
        msg = f"{sym}: setup → {status}"
        if status == "READY":
            msg = f"{sym}: approaching executable window (readiness {item.get('readiness_pct')}%)"
        elif status == "INVALIDATED":
            msg = f"{sym}: setup invalidated / stale vs research"
        elif status == "ACTIVE":
            msg = f"{sym}: active position context detected"
        events.append(
            {
                "ts": now,
                "symbol": sym,
                "type": "setup_shift",
                "headline": msg,
                "setup_status": status,
            }
        )

    if events:
        cache_set(LAST_PREFIX + uid, last_map, ttl_seconds=FEED_TTL_SEC)

    key = FEED_PREFIX + uid
    cur = cache_get(key)
    hist: List[Dict[str, Any]] = cur if isinstance(cur, list) else []
    hist = events + hist[: MAX_FEED_EVENTS - len(events)]
    cache_set(key, hist, ttl_seconds=FEED_TTL_SEC)

    return hist[:12]


def load_feed_only(user_id: int, limit: int = 40) -> List[Dict[str, Any]]:
    from dashboard.backend.cache import get as cache_get

    raw = cache_get(FEED_PREFIX + str(int(user_id)))
    if not isinstance(raw, list):
        return []
    return raw[:limit]


def retention_hints(enriched: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Lightweight habit-loop summaries (deterministic sorting)."""
    near = [x for x in enriched if x.get("setup_status") in ("NEAR_ENTRY", "READY")]
    near.sort(key=lambda x: -(x.get("readiness_pct") or 0))
    improving = [x for x in enriched if (x.get("ai_setup_score") or 0) >= 70]
    risk_up = [x for x in enriched if x.get("setup_status") == "INVALIDATED"]
    best_rr = sorted(
        [x for x in enriched if (x.get("risk") or {}).get("rr")],
        key=lambda x: float((x.get("risk") or {}).get("rr") or 0),
        reverse=True,
    )
    return {
        "closest_to_trigger": [x["symbol"] for x in near[:6]],
        "strongest_scores": [x["symbol"] for x in improving[:6]],
        "invalidated": [x["symbol"] for x in risk_up[:6]],
        "best_rr": [x["symbol"] for x in best_rr[:6]],
    }


def market_alignment(engine_snap: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "market_regime": engine_snap.get("market_regime"),
        "engine_live": engine_snap.get("engine_live"),
        "signals_today": engine_snap.get("signals_today"),
        "snapshot_stale": bool(engine_snap.get("stale")),
    }
