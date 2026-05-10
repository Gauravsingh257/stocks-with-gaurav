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
FEED_DECISION_PREFIX = "watchlist:feed_decision_state:"
METRICS_PREFIX = "watchlist:last_metrics:"
MAX_FEED_EVENTS = 80
FEED_TTL_SEC = 7 * 86400


def _norm_symbol(sym: str) -> str:
    return str(sym).replace("NSE:", "").strip().upper()


def _try_quote_ltp(sym: str) -> Optional[float]:
    """Best-effort last traded price from Redis LTP cache (may be absent)."""
    try:
        from dashboard.backend.cache import get as cache_get, ltp_key

        raw = cache_get(ltp_key(sym))
        if raw is None:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


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
        "TARGET_HIT": "TARGET_HIT",
        "FAILED": "FAILED",
        "MONITORING": "MONITORING",
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
    if st == "INVALIDATED":
        return "INVALIDATED"
    if st in ("CLOSED", "EXPIRED", "ARCHIVED"):
        res = str(idea.get("result") or idea.get("trade_outcome") or "").upper()
        exit_reason = str(idea.get("exit_reason") or "").upper()
        if res in ("WIN", "TARGET_HIT") or "TARGET" in exit_reason:
            return "TARGET_HIT"
        if res in ("LOSS", "STOP_HIT") or "STOP" in exit_reason:
            return "FAILED"
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


def _regime_volatility_score(market_regime: Optional[str]) -> float:
    r = str(market_regime or "").upper()
    if any(x in r for x in ("VOLATILE", "CHOP", "RISK_OFF")):
        return 55.0
    if any(x in r for x in ("TREND", "NORMAL", "RISK_ON")):
        return 88.0
    return 72.0


def _compute_pillar_scores(
    idea: Optional[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    smc: Dict[str, Any],
    confidence: float,
    rr: float,
    market_regime: Optional[str],
) -> Dict[str, float]:
    """0–100 pillar scores (transparent heuristics; engine-grade refinement comes later)."""
    complete_n = sum(1 for s in steps if s.get("status") == "complete")
    total_n = max(len(steps), 1)
    structure = min(100.0, (complete_n / total_n) * 100)
    smc_blob = json.dumps(smc).lower()
    if any(x in smc_blob for x in ("choch", "mss", "change_of_character")):
        structure = min(100.0, structure + 8.0)
    exec_boost = 12.0 if str((idea or {}).get("action_tag") or "") == "EXECUTE_NOW" else 0.0
    momentum = min(100.0, confidence * 0.88 + exec_boost)
    liquidity = 78.0 if smc.get("sweep_level") else 36.0
    volume = min(100.0, 52.0 + complete_n * 9.0)
    rr_score = min(100.0, max(0.0, min(rr, 4.0) / 4.0 * 100))
    htf = 86.0 if any(s.get("id") == "htf" and s.get("status") == "complete" for s in steps) else 41.0
    vol_ctx = _regime_volatility_score(market_regime)
    return {
        "structure": round(structure, 1),
        "momentum": round(momentum, 1),
        "liquidity": round(liquidity, 1),
        "volume": round(volume, 1),
        "rr": round(rr_score, 1),
        "htf": round(htf, 1),
        "volatility_context": round(vol_ctx, 1),
    }


def _weighted_readiness(pillars: Dict[str, float]) -> float:
    weights = {
        "structure": 0.22,
        "momentum": 0.14,
        "liquidity": 0.14,
        "volume": 0.10,
        "rr": 0.18,
        "htf": 0.14,
        "volatility_context": 0.08,
    }
    total = sum(float(pillars.get(k, 50.0)) * weights[k] for k in weights)
    return round(min(100.0, max(0.0, total)), 1)


def _risk_grade(ai_score: int) -> str:
    if ai_score >= 80:
        return "A"
    if ai_score >= 60:
        return "B"
    return "C"


def _quality_band(score: float) -> str:
    if score >= 75:
        return "strong"
    if score >= 55:
        return "moderate"
    return "weak"


def _setup_deteriorating(
    idea: Optional[Dict[str, Any]],
    readiness_prog: float,
    rr: float,
    confidence: float,
    setup_status: str,
) -> bool:
    if setup_status in ("INVALIDATED", "TARGET_HIT", "FAILED") or not idea:
        return False
    if readiness_prog < 36 and confidence < 50:
        return True
    if 0 < rr < 1.08 and confidence < 56:
        return True
    return False


def _stage_reason(
    setup_status: str,
    steps: List[Dict[str, Any]],
    idea: Optional[Dict[str, Any]],
    deteriorating: bool,
) -> str:
    if setup_status == "TARGET_HIT":
        return "Objective achieved vs published plan — book keeping."
    if setup_status == "FAILED":
        return "Stopped out or thesis broken — stand down until structure resets."
    if setup_status == "INVALIDATED":
        return "Setup no longer meets live retention rules."
    if deteriorating:
        return "Quality softening — reduce urgency until structure re-confirms."
    pend = [str(s.get("label") or "") for s in steps if s.get("status") != "complete"]
    if pend:
        return f"Next focus: {pend[0]}" + (f"; then {pend[1]}" if len(pend) > 1 else "")
    if idea and str(idea.get("action_tag")) == "EXECUTE_NOW":
        return "Trigger zone active vs published research plan."
    return "Tracking progression vs desk template."


def _invalidation_copy(idea: Optional[Dict[str, Any]], setup_status: str) -> Optional[str]:
    if setup_status == "FAILED":
        return "Plan violated — stopped or thesis broken vs research template."
    if setup_status != "INVALIDATED":
        return None
    st = str((idea or {}).get("status") or "").upper()
    if st in ("CLOSED", "EXPIRED", "ARCHIVED"):
        return "Recommendation closed in research ledger."
    if not _smc(idea).get("structure") and not _smc(idea).get("sweep_level"):
        return "Structure / liquidity evidence fell below minimum bar."
    return "Score or structure dropped below watchlist retention threshold."


def build_symbol_intel(
    symbol: str,
    idea: Optional[Dict[str, Any]],
    horizon: Optional[str],
    active_trade: Optional[Dict[str, Any]],
    engine_snap: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    sym = _norm_symbol(symbol)
    es = engine_snap if isinstance(engine_snap, dict) else {}
    market_regime = es.get("market_regime")
    confidence = float((idea or {}).get("confidence_score") or 0)
    rr = float((idea or {}).get("risk_reward") or 0)
    steps, readiness_pct = _progression(idea)
    setup_status = _derive_setup_status(idea, active_trade, confidence, readiness_pct)
    trend = _trend_state(idea)
    ai_score = _composite_score(confidence, readiness_pct, rr, idea)
    smc = _smc(idea)
    pillars = _compute_pillar_scores(idea, steps, smc, confidence, rr, str(market_regime) if market_regime is not None else None)
    readiness_weighted = _weighted_readiness(pillars)
    deteriorating = _setup_deteriorating(idea, readiness_pct, rr, confidence, setup_status)
    stage_reason = _stage_reason(setup_status, steps, idea, deteriorating)
    structure_q = _quality_band(float(pillars.get("structure") or 0))
    momentum_q = _quality_band(float(pillars.get("momentum") or 0))

    show_levels = setup_status in ("READY", "ACTIVE") and idea is not None

    entry = sl = tgt = None
    rationale = None
    if idea:
        entry = idea.get("entry_price")
        sl = idea.get("stop_loss")
        if horizon == "LONGTERM":
            tgt = idea.get("long_term_target")
        else:
            tgt = idea.get("target_1") or idea.get("target_2")
    if show_levels and idea:
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
    if setup_status in ("TARGET_HIT", "FAILED"):
        monitoring_message = stage_reason

    risk_notes: List[str] = []
    if idea and str(idea.get("entry_type") or "") == "LIMIT":
        risk_notes.append("Limit entry — fill risk if spot overshoots.")
    if setup_status == "INVALIDATED":
        risk_notes.append("Recorded setup no longer active in research ledger.")

    inv_detail = _invalidation_copy(idea, setup_status)

    out: Dict[str, Any] = {
        "symbol": sym,
        "quote_ltp": _try_quote_ltp(sym),
        "horizon": horizon,
        "trend_state": trend,
        "setup_status": setup_status,
        "lifecycle_stage": lifecycle_stage_from_setup_status(setup_status),
        "current_stage": lifecycle_stage_from_setup_status(setup_status),
        "progression": steps,
        "readiness_pct": readiness_weighted,
        "readiness_progression_pct": readiness_pct,
        "conviction_pct": round(confidence, 1),
        "ai_setup_score": ai_score,
        "entry_ready": bool(show_levels),
        "intelligence": {
            "pillar_scores": pillars,
            "readiness_weighted_pct": readiness_weighted,
            "readiness_delta_hint": None,
            "structure_quality": structure_q,
            "momentum_quality": momentum_q,
            "setup_quality": (
                "strong" if ai_score >= 75 else "moderate" if ai_score >= 55 else "developing"
            ),
            "risk_grade": _risk_grade(ai_score),
            "setup_deteriorating": deteriorating,
            "stage_reason": stage_reason,
            "next_trigger": nearest_trigger,
            "invalidation_reason": inv_detail,
            "market_regime": market_regime,
        },
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
            "invalidation_reason": inv_detail
            or (
                "Research status closed or confidence collapsed."
                if setup_status == "INVALIDATED"
                else None
            ),
        },
        "meta": {
            "has_research_row": idea is not None,
            "in_active_trade": active_trade is not None,
            "engine_tick_ts": es.get("snapshot_time"),
        },
    }

    try:
        from dashboard.backend.services.decision_intelligence_engine import build_decision_package

        out["decision"] = build_decision_package(
            user_id,
            sym,
            idea,
            horizon,
            setup_status,
            steps,
            pillars,
            readiness_weighted,
            readiness_pct,
            deteriorating,
            active_trade,
            es,
            quote_ltp=out.get("quote_ltp"),
            entry_px=entry,
            stop_px=sl,
            target_px=tgt,
        )
    except Exception:
        out["decision"] = {"note": "decision_intel_unavailable"}

    dec = out.get("decision") if isinstance(out.get("decision"), dict) else {}
    tl = dec.get("trade_levels") if isinstance(dec.get("trade_levels"), dict) else {}
    if tl and not tl.get("show_actionable_levels"):
        out["recommendation"]["show_trade_levels"] = False
        out["recommendation"]["entry_ready"] = False
        out["entry_ready"] = False
        out["recommendation"]["entry"] = None
        out["recommendation"]["stop_loss"] = None
        out["recommendation"]["target"] = None
        mc = tl.get("monitoring_copy") or (
            "Monitoring zone — levels withheld until liquidity, RR, and execution realism align."
        )
        if out.get("setup_status") not in ("TARGET_HIT", "FAILED", "INVALIDATED"):
            out["recommendation"]["monitoring_message"] = mc
        out["recommendation"]["rationale"] = None

    return out


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
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in symbols:
        sym = _norm_symbol(raw)
        idea_h = research_map.get(sym)
        idea, hz = (idea_h[0], idea_h[1]) if idea_h else (None, None)
        at = _active_trade_for_symbol(engine_snap, sym)
        out.append(build_symbol_intel(sym, idea, hz, at, engine_snap, user_id))
    # strongest first
    out.sort(key=lambda x: (-(x.get("ai_setup_score") or 0), x["symbol"]))
    try:
        from dashboard.backend.services.retention_engine_service import attach_desk_os_fields

        attach_desk_os_fields(out)
    except Exception:
        pass
    return out


def _load_prev_metrics(user_id: int) -> Dict[str, Any]:
    from dashboard.backend.cache import get as cache_get

    raw = cache_get(METRICS_PREFIX + str(int(user_id)))
    return raw if isinstance(raw, dict) else {}


def _save_current_metrics(user_id: int, enriched: List[Dict[str, Any]]) -> None:
    from dashboard.backend.cache import set as cache_set

    store: Dict[str, Any] = {}
    for item in enriched:
        sym = str(item.get("symbol") or "")
        if not sym:
            continue
        pm = 0.0
        intel = item.get("intelligence")
        if isinstance(intel, dict):
            ps = intel.get("pillar_scores")
            if isinstance(ps, dict):
                try:
                    pm = float(ps.get("momentum") or 0)
                except (TypeError, ValueError):
                    pm = 0.0
        store[sym] = {
            "readiness_pct": item.get("readiness_pct"),
            "setup_status": item.get("setup_status"),
            "lifecycle_stage": item.get("lifecycle_stage"),
            "momentum_pillar": pm,
        }
    cache_set(METRICS_PREFIX + str(int(user_id)), store, ttl_seconds=FEED_TTL_SEC)


def _stamp_readiness_deltas(user_id: int, enriched: List[Dict[str, Any]]) -> Dict[str, Any]:
    prev = _load_prev_metrics(user_id)
    for item in enriched:
        sym = str(item.get("symbol") or "")
        if not sym:
            continue
        before = prev.get(sym)
        cur_rp = item.get("readiness_pct")
        if isinstance(before, dict) and before.get("readiness_pct") is not None and cur_rp is not None:
            try:
                delta = round(float(cur_rp) - float(before["readiness_pct"]), 1)
                item["readiness_delta_pct"] = delta
                intel = item.get("intelligence")
                if isinstance(intel, dict) and delta != 0:
                    intel["readiness_delta_hint"] = f"{delta:+.1f} pts vs prior snapshot"
            except (TypeError, ValueError):
                item["readiness_delta_pct"] = None
        else:
            item["readiness_delta_pct"] = None
    return prev


def _intel_hash(item: Dict[str, Any]) -> str:
    intel = item.get("intelligence") if isinstance(item.get("intelligence"), dict) else {}
    dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    rev = dec.get("readiness_evolution") if isinstance(dec.get("readiness_evolution"), dict) else {}
    tl = dec.get("trade_levels") if isinstance(dec.get("trade_levels"), dict) else {}
    key = "|".join(
        str(x)
        for x in (
            item.get("setup_status"),
            item.get("ai_setup_score"),
            item.get("readiness_pct"),
            intel.get("readiness_weighted_pct"),
            intel.get("setup_deteriorating"),
            intel.get("stage_reason"),
            dec.get("setup_maturity_stage"),
            dec.get("signal_tier"),
            rev.get("evolution_regime"),
            round(float(dec.get("deterioration_probability_pct") or 0), 1),
            tl.get("show_actionable_levels"),
            (dec.get("user_action") or {}).get("primary_action"),
        )
    )
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def append_feed_diff(user_id: int, enriched: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Persist diff-based feed events; returns newest events (cap 12 for API)."""
    from dashboard.backend.cache import get as cache_get, set as cache_set

    uid = str(int(user_id))
    metrics_prev = _stamp_readiness_deltas(int(user_id), enriched)

    last_raw = cache_get(LAST_PREFIX + uid)
    last_map: Dict[str, str] = last_raw if isinstance(last_raw, dict) else {}
    feed_prev_raw = cache_get(FEED_DECISION_PREFIX + uid)
    feed_prev: Dict[str, Any] = feed_prev_raw if isinstance(feed_prev_raw, dict) else {}
    new_feed_state: Dict[str, Any] = {}

    events: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    for item in enriched:
        sym = item["symbol"]
        dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        prom = dec.get("promotion") if isinstance(dec.get("promotion"), dict) else {}
        eq_band = (dec.get("execution_quality") or {}).get("band") if isinstance(dec.get("execution_quality"), dict) else None
        dv = dec.get("deterioration_validation") if isinstance(dec.get("deterioration_validation"), dict) else {}
        new_feed_state[sym] = {
            "readiness": item.get("readiness_pct"),
            "eq_band": eq_band,
            "tier": dec.get("signal_tier"),
            "wf": bool(prom.get("eligible_watchlist_to_final")),
            "det_val": dv.get("validated_overall"),
        }

    for item in enriched:
        sym = item["symbol"]
        h = _intel_hash(item)
        prev_h = last_map.get(sym)
        if prev_h == h:
            continue
        last_map[sym] = h

        status = item.get("setup_status")
        intel = item.get("intelligence") if isinstance(item.get("intelligence"), dict) else {}
        stage = item.get("lifecycle_stage") or status
        prev_m = metrics_prev.get(sym) if isinstance(metrics_prev.get(sym), dict) else {}
        old_rp = prev_m.get("readiness_pct")
        cur_rp = item.get("readiness_pct")
        old_ss = prev_m.get("setup_status")
        cur_ss = item.get("setup_status")
        old_ls = prev_m.get("lifecycle_stage")
        cur_ls = item.get("lifecycle_stage")

        msg: Optional[str] = None
        fp_sym = feed_prev.get(sym) if isinstance(feed_prev.get(sym), dict) else {}
        dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        prom = dec.get("promotion") if isinstance(dec.get("promotion"), dict) else {}
        eq_band = (dec.get("execution_quality") or {}).get("band") if isinstance(dec.get("execution_quality"), dict) else None
        try:
            if (
                old_rp is not None
                and cur_rp is not None
                and abs(float(cur_rp) - float(old_rp)) >= 0.5
            ):
                if fp_sym.get("readiness") is not None and abs(float(cur_rp) - float(old_rp)) >= 3:
                    msg = f"{sym} readiness improved {float(old_rp):.0f} → {float(cur_rp):.0f} (desk snapshots)"
                else:
                    msg = f"{sym} readiness {float(old_rp):.0f} → {float(cur_rp):.0f}"
        except (TypeError, ValueError):
            pass
        if msg is None and fp_sym and not fp_sym.get("wf") and prom.get("eligible_watchlist_to_final"):
            msg = f"{sym}: promoted toward Final review posture — gates still apply"
        if msg is None and fp_sym and fp_sym.get("eq_band") and eq_band and fp_sym.get("eq_band") != eq_band:
            msg = f"{sym}: execution quality band shifted ({fp_sym.get('eq_band')} → {eq_band})"
        dv = dec.get("deterioration_validation") if isinstance(dec.get("deterioration_validation"), dict) else {}
        rev = dec.get("readiness_evolution") if isinstance(dec.get("readiness_evolution"), dict) else {}
        det = dec.get("deterioration") if isinstance(dec.get("deterioration"), dict) else {}
        if msg is None and fp_sym and fp_sym.get("det_val") and dv.get("validated_overall"):
            if fp_sym.get("det_val") not in ("likely_invalidation", "capital_protection") and dv.get("validated_overall") in (
                "likely_invalidation",
                "capital_protection",
            ):
                msg = f"{sym}: deterioration risk accelerating — {dv.get('validated_overall')}"
        if msg is None and dv.get("failed_continuation"):
            msg = f"{sym}: failed continuation vs prior MSS / structure context"
        if msg is None and old_ss and cur_ss and str(old_ss) != str(cur_ss):
            msg = f"{sym} entered {cur_ls or cur_ss} stage (was {old_ss})"
        if msg is None and old_ls and cur_ls and str(old_ls) != str(cur_ls):
            msg = f"{sym} lifecycle {old_ls} → {cur_ls}"
        if msg is None and intel.get("setup_deteriorating"):
            msg = f"{sym}: setup weakening (readiness {item.get('readiness_pct')}%)"
        if msg is None and status == "READY":
            msg = f"{sym}: NEAR executable window — readiness {item.get('readiness_pct')}%"
        if msg is None and status == "INVALIDATED":
            msg = f"{sym}: invalidated — {intel.get('invalidation_reason') or 'research / structure'}"

        if msg is None:
            try:
                if float(det.get("trap_probability_pct") or 0) >= 55:
                    msg = f"{sym}: liquidity / trap-style risk elevated — exercise patience near triggers"
            except (TypeError, ValueError):
                pass
        if msg is None:
            try:
                if float(dec.get("deterioration_probability_pct") or 0) >= 55:
                    msg = f"{sym}: deterioration probability elevated — defensive posture sensible"
            except (TypeError, ValueError):
                pass
        if msg is None and rev.get("evolution_regime") == "accelerating":
            msg = f"{sym}: readiness trajectory strengthening vs recent snapshots"
        if msg is None and rev.get("evolution_regime") == "weakening":
            msg = f"{sym}: readiness softening — wait for structure to re-align"
        if msg is None and rev.get("evolution_regime") == "explosive expansion":
            msg = f"{sym}: rapid readiness expansion — verify liquidity before sizing"
        if msg is None and prom.get("eligible_watchlist_to_final"):
            msg = f"{sym}: quality / RR stack aligning toward final-review posture"
        if msg is None and prom.get("eligible_discovery_to_watchlist"):
            msg = f"{sym}: maturity / HTF context improving — worth closer monitoring"

        if msg is None and status == "ACTIVE":
            msg = f"{sym}: ACTIVE — engine trade context"

        if msg is None and status == "TARGET_HIT":
            msg = f"{sym}: target / objective achieved — book keeping"
        if msg is None and status == "FAILED":
            msg = f"{sym}: setup failed vs plan — review ledger"
        if msg is None:
            msg = f"{sym}: {stage}"

        events.append(
            {
                "ts": now,
                "symbol": sym,
                "type": "setup_shift",
                "headline": msg,
                "setup_status": status,
                "lifecycle_stage": item.get("lifecycle_stage"),
            }
        )

    if events:
        cache_set(LAST_PREFIX + uid, last_map, ttl_seconds=FEED_TTL_SEC)

    try:
        cache_set(FEED_DECISION_PREFIX + uid, new_feed_state, ttl_seconds=FEED_TTL_SEC)
    except Exception:
        pass

    _save_current_metrics(int(user_id), enriched)

    if events:
        try:
            from dashboard.backend.services.retention_engine_service import (
                maybe_enqueue_high_signal_alerts,
                push_evolution_timeline_events,
            )

            push_evolution_timeline_events(int(user_id), events)
            maybe_enqueue_high_signal_alerts(int(user_id), events)
        except Exception:
            pass

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
    weakening = [
        x
        for x in enriched
        if isinstance(x.get("intelligence"), dict) and x["intelligence"].get("setup_deteriorating")
    ]
    risk_up = [x for x in enriched if x.get("setup_status") == "INVALIDATED"]
    best_rr = sorted(
        [x for x in enriched if (x.get("risk") or {}).get("rr")],
        key=lambda x: float((x.get("risk") or {}).get("rr") or 0),
        reverse=True,
    )
    active_syms = [x["symbol"] for x in enriched if x.get("setup_status") == "ACTIVE"]
    improving_rd = sorted(
        [x for x in enriched if (x.get("readiness_delta_pct") or 0) >= 1.0],
        key=lambda x: -(x.get("readiness_delta_pct") or 0),
    )

    def _mom(item: Dict[str, Any]) -> float:
        intel = item.get("intelligence")
        if not isinstance(intel, dict):
            return 0.0
        ps = intel.get("pillar_scores")
        if not isinstance(ps, dict):
            return 0.0
        try:
            return float(ps.get("momentum") or 0)
        except (TypeError, ValueError):
            return 0.0

    momentum_exp = [
        x["symbol"]
        for x in enriched
        if _mom(x) >= 72.0 and (x.get("readiness_delta_pct") or 0) > 0
    ]
    return {
        "closest_to_trigger": [x["symbol"] for x in near[:6]],
        "strongest_scores": [x["symbol"] for x in improving[:6]],
        "improving_readiness": [x["symbol"] for x in improving_rd[:6]],
        "momentum_expansion": momentum_exp[:6],
        "weakening": [x["symbol"] for x in weakening[:6]],
        "invalidated": [x["symbol"] for x in risk_up[:6]],
        "best_rr": [x["symbol"] for x in best_rr[:6]],
        "active": active_syms[:6],
    }


def market_alignment(engine_snap: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "market_regime": engine_snap.get("market_regime"),
        "engine_live": engine_snap.get("engine_live"),
        "signals_today": engine_snap.get("signals_today"),
        "snapshot_stale": bool(engine_snap.get("stale")),
    }
