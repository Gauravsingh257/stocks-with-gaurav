"""
Phase 5 — Decision intelligence (maturity, temporal readiness, promotion hints, execution quality).

Snapshot-first: all derived from research row + engine snapshot + Redis time series.
Trust language: probabilities and bands, not guarantees.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dashboard.decision_intel")

# Redis: per-user per-symbol capped series of {t, r, m} (readiness, maturity)
HISTORY_PREFIX = "decision:intel_series:"
HISTORY_MAX = 40
HISTORY_TTL_SEC = 8 * 86400
ROLLUP_KEY = "decision:rollup_metrics"
PROMO_STATE_PREFIX = "decision:promo_last:"
CALIB_PREFIX = "decision:calib_v1:"
FIRST_SEEN_PREFIX = "decision:first_seen:"
MATURITY_TTL_SEC = 3 * 86400

# Conservative Beta priors for tier calibration (sparse-data safe)
_CALIB_PRIOR_ALPHA = 2.0
_CALIB_PRIOR_BETA = 8.0


def _now_ts() -> float:
    return time.time()


def _smc(idea: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not idea:
        return {}
    ev = idea.get("smc_evidence")
    return ev if isinstance(ev, dict) else {}


def _history_key(user_id: int, symbol: str) -> str:
    return f"{HISTORY_PREFIX}{int(user_id)}:{symbol}"


def _read_series(user_id: int, symbol: str) -> List[Dict[str, Any]]:
    from dashboard.backend.cache import get as cache_get

    raw = cache_get(_history_key(user_id, symbol))
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def _write_series(user_id: int, symbol: str, series: List[Dict[str, Any]]) -> None:
    from dashboard.backend.cache import set as cache_set

    cache_set(_history_key(user_id, symbol), series[:HISTORY_MAX], ttl_seconds=HISTORY_TTL_SEC)


def _append_history_point(
    user_id: int,
    symbol: str,
    readiness: float,
    maturity_score: float,
) -> List[Dict[str, Any]]:
    series = _read_series(user_id, symbol)
    pt = {"t": _now_ts(), "r": float(readiness), "m": float(maturity_score)}
    series = [p for p in series if _now_ts() - float(p.get("t", 0)) < 4 * 3600]
    series.append(pt)
    series = series[-HISTORY_MAX:]
    _write_series(user_id, symbol, series)
    return series


def _merge_last_history_point(user_id: int, symbol: str, extras: Dict[str, Any]) -> None:
    """Attach exec/calibration fields to latest snapshot point (same Redis series)."""
    series = _read_series(user_id, symbol)
    if not series:
        return
    clean = {k: v for k, v in extras.items() if v is not None}
    if not clean:
        return
    series[-1].update(clean)
    _write_series(user_id, symbol, series)


def _series_readiness_variance(series: List[Dict[str, Any]]) -> float:
    vals = [float(p["r"]) for p in series[-12:] if "r" in p]
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    var = sum((x - mean) ** 2 for x in vals) / len(vals)
    return round(min(100.0, var ** 0.5 * 4), 2)


def _window_mean(series: List[Dict[str, Any]], window_sec: float) -> Optional[float]:
    now = _now_ts()
    vals = [float(p["r"]) for p in series if now - float(p.get("t", 0)) <= window_sec and "r" in p]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def _maturity_stage_and_score(
    idea: Optional[Dict[str, Any]],
    setup_status: str,
    steps: List[Dict[str, Any]],
    smc: Dict[str, Any],
    readiness_w: float,
    deteriorating: bool,
    active_trade: Optional[Dict[str, Any]],
) -> Tuple[str, float]:
    """Return (maturity_stage, maturity_score 0-100)."""
    if deteriorating and setup_status not in ("INVALIDATED", "TARGET_HIT", "FAILED"):
        return "DETERIORATING", max(0.0, min(100.0, readiness_w * 0.65))
    if setup_status == "INVALIDATED":
        return "INVALIDATED", 0.0
    if setup_status == "TARGET_HIT":
        return "TARGET_HIT", 100.0
    if setup_status == "FAILED":
        return "INVALIDATED", 5.0
    if active_trade:
        st = str((active_trade.get("status") or "")).upper()
        if st in ("TRIGGERED", "RUNNING"):
            return "ACTIVE_POSITION", 88.0
        if st == "TARGET_HIT":
            return "TARGET_PROGRESS", 95.0
        return "ACTIVE_POSITION", 85.0

    if not idea:
        return "EARLY_STRUCTURE", 12.0

    smc_l = json.dumps(smc).lower()
    mss = any(x in smc_l for x in ("choch", "mss", "change_of_character"))
    sweep_ok = bool(smc.get("sweep_level"))
    bos = str(smc.get("structure") or "") == "BOS"
    htf_ok = any(s.get("id") == "htf" and s.get("status") == "complete" for s in steps)
    near_ready = setup_status in ("NEAR_ENTRY", "READY")
    mom_high = readiness_w >= 70

    if setup_status == "READY":
        if mom_high:
            return "MOMENTUM_CONFIRMED", min(100.0, 72.0 + readiness_w * 0.28)
        return "ENTRY_READY", min(100.0, 68.0 + readiness_w * 0.32)
    if near_ready:
        return "ENTRY_ZONE_NEAR", min(100.0, 55.0 + readiness_w * 0.35)
    if htf_ok and bos:
        return "HTF_ALIGNMENT", min(100.0, 48.0 + readiness_w * 0.42)
    if mss:
        return "MSS_DETECTED", min(100.0, 38.0 + readiness_w * 0.45)
    if sweep_ok:
        return "LIQUIDITY_FORMING", min(100.0, 28.0 + readiness_w * 0.5)
    return "EARLY_STRUCTURE", min(100.0, 15.0 + readiness_w * 0.55)


def _maturity_velocity(series: List[Dict[str, Any]], maturity_now: float) -> float:
    if len(series) < 2:
        return 0.0
    prev = float(series[-2].get("m", maturity_now))
    return round(maturity_now - prev, 2)


def _readiness_evolution(series: List[Dict[str, Any]], readiness_now: float) -> Dict[str, Any]:
    r5 = _window_mean(series, 300) or readiness_now
    r15 = _window_mean(series, 900) or readiness_now
    r60 = _window_mean(series, 3600) or readiness_now
    accel = round(readiness_now - r15, 2) if series else 0.0
    decay = round(max(0.0, r15 - readiness_now), 2)
    consistency = 100.0 - min(100.0, abs(readiness_now - r60) * 1.2)

    if accel >= 4:
        regime = "explosive expansion"
    elif accel >= 1.5:
        regime = "accelerating"
    elif decay >= 2:
        regime = "weakening"
    elif abs(readiness_now - r15) < 0.8:
        regime = "stalling"
    elif readiness_now < r60 - 3:
        regime = "unstable structure"
    else:
        regime = "stable progression"

    return {
        "readiness_last_5m": r5,
        "readiness_last_15m": r15,
        "readiness_last_1h": r60,
        "readiness_acceleration": accel,
        "readiness_decay": decay,
        "readiness_consistency": round(max(0.0, min(100.0, consistency)), 1),
        "evolution_regime": regime,
    }


def _lifecycle_probability(readiness_w: float, maturity_score: float, rr: float) -> float:
    base = (readiness_w * 0.45 + maturity_score * 0.35 + min(rr, 4.0) * 5.0) / 100.0
    return round(max(0.0, min(100.0, base * 100.0)), 1)


def _deterioration_probability(
    deteriorating: bool,
    readiness_decay: float,
    pillars: Dict[str, float],
    regime: str,
) -> float:
    liq = float(pillars.get("liquidity") or 50)
    vol_ctx = float(pillars.get("volatility_context") or 70)
    p = 8.0
    if deteriorating:
        p += 28.0
    p += min(25.0, readiness_decay * 4.0)
    if liq < 45:
        p += 12.0
    if any(x in regime.upper() for x in ("CHOP", "RISK_OFF", "VOLATILE")):
        p += 10.0
    if vol_ctx < 58:
        p += 8.0
    return round(max(0.0, min(95.0, p)), 1)


def _execution_quality_band(
    pillars: Dict[str, float],
    rr: float,
    idea: Optional[Dict[str, Any]],
    setup_status: str,
) -> Tuple[str, float]:
    struct = float(pillars.get("structure") or 0)
    liq = float(pillars.get("liquidity") or 0)
    vol = float(pillars.get("volatility_context") or 0)
    mom = float(pillars.get("momentum") or 0)
    htf = float(pillars.get("htf") or 0)
    rr_s = min(100.0, max(0.0, min(rr, 4.0) / 4.0 * 100))
    limit_pen = 6.0 if idea and str(idea.get("entry_type") or "").upper() == "LIMIT" else 0.0
    score = (
        struct * 0.18
        + liq * 0.14
        + vol * 0.08
        + mom * 0.16
        + htf * 0.18
        + rr_s * 0.18
        - limit_pen
    )
    if setup_status == "INVALIDATED":
        score *= 0.4
    score = max(0.0, min(100.0, score))
    if score >= 82:
        return "institutional grade", round(score, 1)
    if score >= 68:
        return "high quality", round(score, 1)
    if score >= 48:
        return "acceptable", round(score, 1)
    return "poor", round(score, 1)


def _allocation_band(allocation_score: float) -> str:
    if allocation_score >= 76:
        return "aggressive allocation"
    if allocation_score >= 52:
        return "medium allocation"
    return "low allocation"


def _allocation_priority_score(
    maturity_score: float,
    readiness_w: float,
    exec_score: float,
    rr: float,
    pillars: Dict[str, float],
) -> Tuple[float, str]:
    vol = float(pillars.get("volatility_context") or 70)
    liq = float(pillars.get("liquidity") or 60)
    s = (
        maturity_score * 0.22
        + readiness_w * 0.24
        + exec_score * 0.2
        + min(rr, 4.0) / 4.0 * 18.0
        + liq * 0.08
        + vol * 0.08
    )
    s = max(0.0, min(100.0, s))
    return round(s, 1), _allocation_band(s)


def _holding_period_hint(horizon: Optional[str], maturity_stage: str, setup_status: str) -> str:
    if setup_status == "ACTIVE":
        return "intraday"
    hz = str(horizon or "").upper()
    if hz == "LONGTERM" or maturity_stage in ("TARGET_PROGRESS",):
        return "6-24 month"
    if maturity_stage in ("ENTRY_READY", "MOMENTUM_CONFIRMED", "ENTRY_ZONE_NEAR"):
        return "<1 week"
    if maturity_stage in ("HTF_ALIGNMENT", "MSS_DETECTED"):
        return "1-3 month"
    if maturity_stage in ("LIQUIDITY_FORMING", "EARLY_STRUCTURE"):
        return "1-3 day"
    return "1-3 day"


def _deterioration_detail(
    idea: Optional[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    smc: Dict[str, Any],
    pillars: Dict[str, float],
    rr: float,
    readiness_decay: float,
    market_regime: Optional[str],
) -> Dict[str, Any]:
    flags: List[str] = []
    smc_l = json.dumps(smc).lower()
    if "choch" in smc_l and str(smc.get("structure") or "") != "BOS":
        flags.append("failed_MSS_context")
    if float(pillars.get("liquidity") or 60) < 38:
        flags.append("liquidity_thin")
    if rr > 0 and rr < 1.05:
        flags.append("RR_decay")
    if readiness_decay >= 2:
        flags.append("readiness_slip")
    mr = str(market_regime or "").upper()
    if any(x in mr for x in ("CHOP", "RISK_OFF")):
        flags.append("HTF_or_regime_headwind")

    false_break = round(min(85.0, 18.0 + (100 - float(pillars.get("structure") or 50)) * 0.35), 1)
    trap = round(min(80.0, 15.0 + max(0.0, 55 - float(pillars.get("liquidity") or 55)) * 0.9), 1)

    if len(flags) >= 3:
        overall = "invalidation likely"
    elif len(flags) >= 2:
        overall = "caution"
    elif len(flags) == 1:
        overall = "unstable"
    elif readiness_decay >= 1:
        overall = "weakening"
    else:
        overall = "stable"

    return {
        "flags": flags[:8],
        "false_breakout_probability_pct": false_break,
        "trap_probability_pct": trap,
        "overall_risk_tone": overall,
        "capital_protection_warning": overall in ("invalidation likely", "caution"),
    }


def _promotion_demotion_hints(
    idea: Optional[Dict[str, Any]],
    maturity_score: float,
    readiness_w: float,
    evolution: Dict[str, Any],
    exec_band: str,
    deteriorating: bool,
    rr: float,
    pillars: Dict[str, float],
) -> Dict[str, Any]:
    htf_ok = float(pillars.get("htf") or 0) >= 72
    liq_ok = float(pillars.get("liquidity") or 0) >= 55
    accel = float(evolution.get("readiness_acceleration") or 0)

    promote_dw = bool(
        idea
        and maturity_score >= 58
        and htf_ok
        and accel >= 0.8
        and liq_ok
        and not deteriorating
    )
    promote_wf = bool(
        idea
        and maturity_score >= 72
        and readiness_w >= 68
        and rr >= 1.35
        and exec_band in ("high quality", "institutional grade")
        and float(pillars.get("momentum") or 0) >= 62
        and not deteriorating
    )

    demote_fw = bool(deteriorating or rr < 1.0 or float(pillars.get("structure") or 0) < 42)
    demote_wd = bool(maturity_score < 38 or float(pillars.get("liquidity") or 0) < 36 or accel < -2.5)

    return {
        "eligible_discovery_to_watchlist": promote_dw,
        "eligible_watchlist_to_final": promote_wf,
        "risk_demote_final_to_watchlist": demote_fw,
        "risk_demote_watchlist_to_discovery": demote_wd,
        "rationale": "Heuristic promotion paths — desk still validates against live ledger.",
    }


def _signal_confidence_tier(
    readiness_w: float,
    maturity_score: float,
    exec_band: str,
    deteriorating: bool,
) -> str:
    if deteriorating:
        return "informational"
    if exec_band == "institutional grade" and readiness_w >= 78 and maturity_score >= 80:
        return "institutional grade"
    if readiness_w >= 72 and maturity_score >= 68:
        return "high conviction"
    if readiness_w >= 58 and maturity_score >= 52:
        return "actionable"
    if readiness_w >= 42:
        return "developing"
    return "informational"


def _regime_labels(pillars: Dict[str, float], market_regime: Optional[str]) -> Dict[str, str]:
    liq = float(pillars.get("liquidity") or 55)
    vx = float(pillars.get("volatility_context") or 65)
    mr = str(market_regime or "").upper()
    liq_lab = "thin" if liq < 45 else "moderate" if liq < 62 else "adequate"
    vol_lab = "compressed" if vx > 72 else "elevated" if vx < 52 else "normal"
    return {"liquidity_regime": liq_lab, "volatility_regime": vol_lab, "macro_regime_hint": mr or "unknown"}


def _touch_calibration_redis(tier: str, det_p: float, det_detail: Dict[str, Any]) -> None:
    """Accumulate sparse tier outcomes for empirical shrinkage (stress / false-break proxies)."""
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        day = date.today().isoformat()
        key = f"{CALIB_PREFIX}{day}"
        slug = (tier or "informational").replace(" ", "_").lower()
        stress_hit = 1 if det_p >= 52 else 0
        fb_hit = 1 if float(det_detail.get("false_breakout_probability_pct") or 0) >= 45 else 0
        pipe = r.pipeline()
        pipe.hincrby(key, f"{slug}_n", 1)
        pipe.hincrby(key, f"{slug}_stress", stress_hit)
        pipe.hincrby(key, f"{slug}_fb", fb_hit)
        pipe.expire(key, 14 * 86400)
        pipe.execute()
    except Exception as exc:
        logger.debug("calibration touch skipped: %s", exc)


def _calibrated_tier_probabilities(tier: str, det_p: float, det_detail: Dict[str, Any]) -> Dict[str, Any]:
    """Beta-smoothed marginal probabilities — conservative when samples sparse."""
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        slug = (tier or "informational").replace(" ", "_").lower()
        n = stress = fb = 0
        if r is not None:
            day = date.today().isoformat()
            key = f"{CALIB_PREFIX}{day}"
            raw = r.hgetall(key)
            if isinstance(raw, dict):
                def _g(k: str) -> int:
                    try:
                        return int(raw.get(k, 0) or 0)
                    except (TypeError, ValueError):
                        return 0

                n = _g(f"{slug}_n")
                stress = _g(f"{slug}_stress")
                fb = _g(f"{slug}_fb")
    except Exception:
        n = stress = fb = 0

    # Posterior stress rate ~ "failure / regret" proxy for this tier
    a_s = _CALIB_PRIOR_ALPHA + stress
    b_s = _CALIB_PRIOR_BETA + max(0, n - stress)
    fail_pct = round(100.0 * a_s / max(a_s + b_s, 1e-6), 1)
    a_fb = _CALIB_PRIOR_ALPHA + fb
    b_fb = _CALIB_PRIOR_BETA + max(0, n - fb)
    fb_pct = round(100.0 * a_fb / max(a_fb + b_fb, 1e-6), 1)

    # Tier-conditioned upward bands (shrink toward conservative priors)
    tier_boost = {"institutional grade": 0.12, "high conviction": 0.09, "actionable": 0.06}.get(
        (tier or "").lower(), 0.02
    )
    inst_pct = round(max(5.0, min(55.0, 22.0 + tier_boost * 100 - fail_pct * 0.35)), 1)
    high_pct = round(max(8.0, min(62.0, 28.0 + tier_boost * 90 - fail_pct * 0.28)), 1)

    return {
        "inst_probability_pct": inst_pct,
        "high_probability_pct": high_pct,
        "failure_probability_pct": min(92.0, fail_pct),
        "false_break_probability_pct": min(90.0, fb_pct),
        "calibration_samples_for_tier": n,
        "note": "Empirical-Beta blend from tier-day aggregates — widens when samples sparse; not win-rate.",
    }


def _execution_realism_layer(
    *,
    setup_status: str,
    quote_ltp: Optional[float],
    entry_px: Optional[float],
    stop_px: Optional[float],
    target_px: Optional[float],
    rr_plan: float,
    pillars: Dict[str, float],
    evolution: Dict[str, Any],
    exec_score: float,
    det_p: float,
    det_detail: Dict[str, Any],
    deteriorating: bool,
    readiness_w: float,
) -> Dict[str, Any]:
    struct = float(pillars.get("structure") or 50)
    liq = float(pillars.get("liquidity") or 55)
    vx = float(pillars.get("volatility_context") or 60)
    accel = float(evolution.get("readiness_acceleration") or 0)
    decay = float(evolution.get("readiness_decay") or 0)
    regime = str(evolution.get("evolution_regime") or "")

    dist_pct: Optional[float] = None
    rr_effective = rr_plan
    if quote_ltp is not None and entry_px and float(entry_px) > 0:
        try:
            dist_pct = round(abs(float(quote_ltp) - float(entry_px)) / float(entry_px) * 100.0, 2)
            if dist_pct > 1.2 and rr_plan > 0:
                rr_effective = round(max(0.5, rr_plan * (1.0 - min(0.35, (dist_pct - 1.2) * 0.04))), 2)
        except (TypeError, ValueError):
            dist_pct = None

    slip_risk = round(min(95.0, 25.0 + max(0.0, 65 - liq) * 0.9 + (dist_pct or 0) * 1.4), 1)
    liq_risk = round(min(95.0, 20.0 + max(0.0, 55 - liq) * 1.1), 1)
    delay_risk = round(min(90.0, 18.0 + max(0.0, 10 - struct) * 0.8), 1)
    vol_exp_risk = round(min(92.0, 15.0 + max(0.0, 58 - vx) * 1.2 + (5.0 if regime == "unstable structure" else 0)), 1)

    rr_deg = round(max(0.0, rr_plan - rr_effective), 2) if rr_plan > 0 else 0.0

    label = "waiting confirmation"
    if setup_status == "ACTIVE":
        label = "actionable now"
    elif deteriorating or det_p >= 58:
        label = "entry deteriorating"
    elif exec_score < 48 or liq < 40:
        label = "execution risky"
    elif dist_pct is not None and dist_pct > 2.8:
        label = "late entry risk"
    elif setup_status == "READY" and exec_score >= 72 and det_p <= 48 and liq >= 52:
        label = "actionable now"
    elif setup_status == "READY" and exec_score >= 58:
        label = "early positioning"
    elif readiness_w >= 52 and accel >= 1.0:
        label = "prepare"
    elif decay >= 2.5:
        label = "monitor"

    return {
        "distance_from_entry_pct": dist_pct,
        "rr_planned": round(rr_plan, 2) if rr_plan else None,
        "rr_effective_estimate": rr_effective if rr_plan > 0 else None,
        "rr_degradation": rr_deg,
        "slippage_risk_pct": slip_risk,
        "liquidity_risk_pct": liq_risk,
        "execution_delay_risk_pct": delay_risk,
        "volatility_expansion_risk_pct": vol_exp_risk,
        "execution_posture": label,
        "trust_note": "Posture uses price distance + pillar stress — not a fill guarantee.",
    }


def _deterioration_validation_layer(
    det_detail: Dict[str, Any],
    evolution: Dict[str, Any],
    pillars: Dict[str, float],
    smc: Dict[str, Any],
    market_regime: Optional[str],
    det_p: float,
) -> Dict[str, Any]:
    flags = list(det_detail.get("flags") or [])
    reg = str(evolution.get("evolution_regime") or "").lower()
    mr = str(market_regime or "").upper()
    struct = float(pillars.get("structure") or 50)
    mom = float(pillars.get("momentum") or 50)
    liq = float(pillars.get("liquidity") or 55)

    failed_cont = "failed_MSS_context" in flags or ("readiness_slip" in flags and reg == "weakening")
    weak_ft = reg in ("weakening", "stalling") and mom < 48
    fake_bos = struct < 44 and "RR_decay" not in flags
    liq_exhaust = liq < 36
    mom_collapse = mom < 42 and reg == "weakening"
    htf_div = any(x in mr for x in ("CHOP", "RISK_OFF", "VOLATILE"))
    vol_against = float(det_detail.get("false_breakout_probability_pct") or 0) >= 52 and mom < 50

    signals_score = 0
    if failed_cont:
        signals_score += 3
    if weak_ft:
        signals_score += 2
    if fake_bos:
        signals_score += 2
    if liq_exhaust:
        signals_score += 2
    if mom_collapse:
        signals_score += 2
    if htf_div:
        signals_score += 1
    if vol_against:
        signals_score += 2

    if det_p >= 62 or signals_score >= 7:
        validated = "likely_invalidation"
    elif det_p >= 52 or signals_score >= 5:
        validated = "capital_protection"
    elif signals_score >= 4 or liq_exhaust:
        validated = "avoid_entry"
    elif signals_score >= 2:
        validated = "unstable"
    elif signals_score >= 1:
        validated = "weakening"
    else:
        validated = "tracking"

    smc_l = json.dumps(smc).lower()
    failed_mss = "choch" in smc_l and str(smc.get("structure") or "") != "BOS"

    return {
        "validated_overall": validated,
        "failed_continuation": failed_cont,
        "weak_follow_through": weak_ft,
        "fake_bos_context": fake_bos,
        "liquidity_exhaustion": liq_exhaust,
        "momentum_collapse": mom_collapse,
        "htf_divergence": htf_div,
        "failed_mss_context": failed_mss,
        "volatility_against_setup": vol_against,
        "validation_score": signals_score,
        "eject_from_portfolio": validated in ("likely_invalidation", "capital_protection", "avoid_entry")
            and det_p >= 48,
    }


def _user_action_layer(
    exec_posture: str,
    det_val: Dict[str, Any],
    det_p: float,
    promo: Dict[str, Any],
    readiness_w: float,
    setup_status: str,
    deteriorating: bool,
) -> Dict[str, Any]:
    val = det_val.get("validated_overall")
    primary = "Monitor"
    if val in ("likely_invalidation", "capital_protection"):
        primary = "Capital protection mode"
    elif val == "avoid_entry":
        primary = "Avoid now"
    elif exec_posture == "late entry risk":
        primary = "Momentum chasing risk"
    elif exec_posture == "execution risky":
        primary = "Avoid now"
    elif exec_posture == "actionable now" and setup_status == "READY":
        primary = "Entry ready"
    elif exec_posture == "early positioning":
        primary = "Partial positioning"
    elif exec_posture == "prepare":
        primary = "Prepare"
    elif exec_posture == "waiting confirmation":
        primary = "Wait"
    elif deteriorating or det_p >= 55:
        primary = "Monitor"
    elif promo.get("eligible_watchlist_to_final"):
        primary = "Prepare"

    return {
        "primary_action": primary,
        "rationale": "Rule blend from validation + execution posture — discretionary desk still leads.",
    }


def _time_decay_layer(
    user_id: Optional[int],
    symbol: str,
    series: List[Dict[str, Any]],
    evolution: Dict[str, Any],
    readiness_w: float,
    setup_status: str,
) -> Dict[str, Any]:
    stagnation_min = 0.0
    if len(series) >= 3:
        t0 = float(series[0].get("t", 0))
        t1 = float(series[-1].get("t", 0))
        stagnation_min = max(0.0, round((t1 - t0) / 60.0, 1))
    reg = str(evolution.get("evolution_regime") or "")
    stalled = reg == "stalling" and readiness_w < 58
    setup_expired_hint = stagnation_min > 180 and stalled and setup_status not in ("ACTIVE", "TARGET_HIT", "INVALIDATED")

    first_ts: Optional[float] = None
    if user_id is not None:
        try:
            from dashboard.backend.cache import get as cache_get, set as cache_set

            k = f"{FIRST_SEEN_PREFIX}{int(user_id)}:{symbol}"
            raw = cache_get(k)
            now = _now_ts()
            if raw is None:
                cache_set(k, now, ttl_seconds=20 * 86400)
                first_ts = now
            else:
                try:
                    first_ts = float(raw)
                except (TypeError, ValueError):
                    first_ts = now
        except Exception:
            first_ts = None

    watchlist_age_min: Optional[float] = None
    if first_ts is not None:
        watchlist_age_min = round((_now_ts() - first_ts) / 60.0, 1)

    mom_age_note = "Momentum aging flagged if readiness stalled while time accrues." if stalled else None

    return {
        "stagnation_window_min": stagnation_min,
        "readiness_stagnation": stalled,
        "watchlist_tenure_min": watchlist_age_min,
        "setup_expired_hint": setup_expired_hint,
        "momentum_aging_note": mom_age_note,
    }


def _portfolio_competition_score(
    alloc_score: float,
    momentum_pillar: float,
    readiness_consistency: float,
    det_p: float,
    det_val: Dict[str, Any],
    stability_sigma: float,
) -> float:
    if det_val.get("eject_from_portfolio"):
        return -999.0
    pen = det_p * 0.38
    boost = momentum_pillar * 0.22 + readiness_consistency * 0.12 + max(0.0, 28 - stability_sigma) * 0.15
    return round(alloc_score * 0.42 + boost - pen, 2)


def _trade_levels_allowed(
    setup_status: str,
    idea: Optional[Dict[str, Any]],
    exec_score: float,
    rr: float,
    det_p: float,
    exec_realism: Dict[str, Any],
    pillars: Dict[str, float],
    det_val: Dict[str, Any],
) -> Tuple[bool, str]:
    if setup_status not in ("READY", "ACTIVE") or idea is None:
        return False, "monitoring_zone"
    if det_val.get("eject_from_portfolio"):
        return False, "not_actionable_yet"
    if float(pillars.get("liquidity") or 0) < 46:
        return False, "liquidity_not_valid"
    if rr < 1.12:
        return False, "rr_below_desk_minimum"
    if exec_score < 56:
        return False, "execution_quality_insufficient"
    if det_p > 54:
        return False, "deterioration_elevated"
    posture = str(exec_realism.get("execution_posture") or "")
    if posture in ("execution risky", "late entry risk", "entry deteriorating"):
        return False, "execution_not_realistic"
    return True, "actionable_levels_ok"


def build_decision_package(
    user_id: Optional[int],
    symbol: str,
    idea: Optional[Dict[str, Any]],
    horizon: Optional[str],
    setup_status: str,
    steps: List[Dict[str, Any]],
    pillars: Dict[str, float],
    readiness_w: float,
    readiness_prog: float,
    deteriorating: bool,
    active_trade: Optional[Dict[str, Any]],
    engine_snap: Dict[str, Any],
    quote_ltp: Optional[float] = None,
    entry_px: Optional[float] = None,
    stop_px: Optional[float] = None,
    target_px: Optional[float] = None,
) -> Dict[str, Any]:
    smc = _smc(idea)
    rr = float((idea or {}).get("risk_reward") or 0)
    market_regime = engine_snap.get("market_regime")
    regimes = _regime_labels(pillars, str(market_regime) if market_regime is not None else None)

    stage, maturity_score = _maturity_stage_and_score(
        idea, setup_status, steps, smc, readiness_w, deteriorating, active_trade
    )

    series: List[Dict[str, Any]] = []
    if user_id is not None:
        series = _append_history_point(user_id, symbol, readiness_w, maturity_score)

    maturity_delta = 0.0
    if len(series) >= 2:
        maturity_delta = round(float(series[-1].get("m", maturity_score)) - float(series[-2].get("m", 0)), 2)

    velocity = _maturity_velocity(series, maturity_score)
    evolution = _readiness_evolution(series, readiness_w)

    lifecycle_p = _lifecycle_probability(readiness_w, maturity_score, rr)
    det_p = _deterioration_probability(
        deteriorating,
        float(evolution.get("readiness_decay") or 0),
        pillars,
        str(market_regime or ""),
    )

    exec_band, exec_score = _execution_quality_band(pillars, rr, idea, setup_status)
    alloc_score, alloc_label = _allocation_priority_score(maturity_score, readiness_w, exec_score, rr, pillars)

    det_detail = _deterioration_detail(
        idea, steps, smc, pillars, rr, float(evolution.get("readiness_decay") or 0), market_regime
    )

    promo = _promotion_demotion_hints(
        idea, maturity_score, readiness_w, evolution, exec_band, deteriorating, rr, pillars
    )

    tier = _signal_confidence_tier(readiness_w, maturity_score, exec_band, deteriorating)

    holding = _holding_period_hint(horizon, stage, setup_status)

    det_val = _deterioration_validation_layer(det_detail, evolution, pillars, smc, market_regime, det_p)
    exec_realism = _execution_realism_layer(
        setup_status=setup_status,
        quote_ltp=quote_ltp,
        entry_px=entry_px,
        stop_px=stop_px,
        target_px=target_px,
        rr_plan=rr,
        pillars=pillars,
        evolution=evolution,
        exec_score=exec_score,
        det_p=det_p,
        det_detail=det_detail,
        deteriorating=deteriorating,
        readiness_w=readiness_w,
    )
    user_act = _user_action_layer(
        str(exec_realism.get("execution_posture") or ""),
        det_val,
        det_p,
        promo,
        readiness_w,
        setup_status,
        deteriorating,
    )
    time_decay = _time_decay_layer(user_id, symbol, series, evolution, readiness_w, setup_status)
    stab_sigma = _series_readiness_variance(series)
    comp_score = _portfolio_competition_score(
        float(alloc_score),
        float(pillars.get("momentum") or 0),
        float(evolution.get("readiness_consistency") or 50),
        float(det_p),
        det_val,
        stab_sigma,
    )
    levels_ok, zone_reason = _trade_levels_allowed(
        setup_status, idea, exec_score, rr, det_p, exec_realism, pillars, det_val
    )

    calib = _calibrated_tier_probabilities(tier, det_p, det_detail)
    if user_id is not None:
        _touch_calibration_redis(tier, det_p, det_detail)
        _merge_last_history_point(
            user_id,
            symbol,
            {"eq": exec_score, "dp": det_p, "tier": tier, "stage": stage},
        )

    snap_note = {
        "lifecycle_stage_at_snapshot": stage,
        "volatility_regime": regimes.get("volatility_regime"),
        "liquidity_regime": regimes.get("liquidity_regime"),
        "readiness_acceleration_snapshot": evolution.get("readiness_acceleration"),
        "execution_quality_snapshot": exec_score,
        "deterioration_probability_snapshot": det_p,
        "outcome_tracking_note": "Outcome labels require ledger linkage — snapshots record context only.",
    }

    return {
        "setup_maturity_stage": stage,
        "setup_maturity_score": round(maturity_score, 1),
        "maturity_delta": maturity_delta,
        "maturity_velocity": velocity,
        "lifecycle_probability_pct": lifecycle_p,
        "deterioration_probability_pct": det_p,
        "readiness_evolution": evolution,
        "execution_quality": {"band": exec_band, "score": exec_score},
        "allocation": {"priority_score": alloc_score, "label": alloc_label},
        "deterioration": det_detail,
        "deterioration_validation": det_val,
        "promotion": promo,
        "signal_tier": tier,
        "holding_period_hint": holding,
        "confidence_calibration": calib,
        "regimes": regimes,
        "execution_realism": exec_realism,
        "trade_levels": {
            "show_actionable_levels": levels_ok,
            "zone_reason": zone_reason,
            "monitoring_copy": (
                "Monitoring zone — levels withheld until liquidity + RR + execution quality align."
                if not levels_ok
                else None
            ),
        },
        "user_action": user_act,
        "time_decay": time_decay,
        "portfolio_competition_score": comp_score,
        "calibration_snapshot_meta": snap_note,
        "confidence_language": {
            "lifecycle": f"Modelled continuation ~{lifecycle_p}% vs current desk inputs — not a forecast.",
            "deterioration": f"Stress probability ~{det_p}% given structure/liquidity/vol mix.",
            "tiers": "Tier probabilities blend sparse Redis aggregates with conservative shrinkage — not historical win-rate.",
        },
    }


def build_virtual_portfolios(enriched: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Four competitive desks — max 30 slots; weakest candidates lose slots when overcrowded.
    Ranked by portfolio_competition_score (fallback allocation.priority_score).
    """
    rows: List[Tuple[float, str, Dict[str, Any]]] = []
    for item in enriched:
        dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        alloc = dec.get("allocation") if isinstance(dec.get("allocation"), dict) else {}
        cr = dec.get("portfolio_competition_score")
        if cr is None:
            try:
                score = float(alloc.get("priority_score") or 0)
            except (TypeError, ValueError):
                score = 0.0
        else:
            try:
                score = float(cr)
            except (TypeError, ValueError):
                score = 0.0
        if score <= -900:
            continue
        sym = str(item.get("symbol") or "")
        if sym:
            rows.append((score, sym, item))

    rows.sort(key=lambda x: (-x[0], x[1]))

    def take(pred, limit: int = 30) -> List[str]:
        picked: List[str] = []
        for _sc, sy, it in rows:
            if len(picked) >= limit:
                break
            if pred(it):
                picked.append(sy)
        return picked

    intraday = take(
        lambda it: it.get("setup_status") == "ACTIVE"
        or str((it.get("decision") or {}).get("holding_period_hint") or "") == "intraday",
    )
    swing = take(lambda it: str(it.get("horizon") or "").upper() == "SWING")
    growth = take(
        lambda it: str(it.get("horizon") or "").upper() == "SWING"
        and float((it.get("decision") or {}).get("setup_maturity_score") or 0) >= 55
    )
    lt = take(lambda it: str(it.get("horizon") or "").upper() == "LONGTERM")

    return {
        "intraday_momentum": intraday,
        "mtf_swing": swing,
        "short_term_growth": growth,
        "long_term_compounders": lt,
        "slot_cap": 30,
        "note": "Competitive desk ranking — replaces weak slots when scores deteriorate; not executed allocation.",
    }


def promotion_transition_touch(user_id: int, enriched: List[Dict[str, Any]]) -> None:
    """Edge-detect promotion hint flips; accumulate daily counters in Redis."""
    from dashboard.backend.cache import get as cache_get, set as cache_set

    uid_s = str(int(user_id))
    prev_raw = cache_get(PROMO_STATE_PREFIX + uid_s)
    prev = prev_raw if isinstance(prev_raw, dict) else {}

    transitions = {
        "promotions_discovery_to_watchlist": 0,
        "promotions_watchlist_to_final": 0,
        "demotions_final_to_watchlist": 0,
        "demotions_watchlist_to_discovery": 0,
    }
    cur: Dict[str, Any] = {}
    for item in enriched:
        sym = str(item.get("symbol") or "")
        if not sym:
            continue
        dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        promo = dec.get("promotion") if isinstance(dec.get("promotion"), dict) else {}
        flags = {
            "dw": bool(promo.get("eligible_discovery_to_watchlist")),
            "wf": bool(promo.get("eligible_watchlist_to_final")),
            "rfw": bool(promo.get("risk_demote_final_to_watchlist")),
            "rwd": bool(promo.get("risk_demote_watchlist_to_discovery")),
        }
        cur[sym] = flags
        old = prev.get(sym)
        if isinstance(old, dict):
            if not old.get("dw") and flags["dw"]:
                transitions["promotions_discovery_to_watchlist"] += 1
            if not old.get("wf") and flags["wf"]:
                transitions["promotions_watchlist_to_final"] += 1
            if not old.get("rfw") and flags["rfw"]:
                transitions["demotions_final_to_watchlist"] += 1
            if not old.get("rwd") and flags["rwd"]:
                transitions["demotions_watchlist_to_discovery"] += 1

    cache_set(PROMO_STATE_PREFIX + uid_s, cur, ttl_seconds=3 * 86400)

    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        day = date.today().isoformat()
        key = f"{ROLLUP_KEY}:{day}"
        for name, v in transitions.items():
            if v:
                r.hincrby(key, name, int(v))
        r.expire(key, 8 * 86400)
    except Exception as exc:
        logger.debug("promotion_transition_touch skipped: %s", exc)


def rollup_touch(user_id: int, enriched: List[Dict[str, Any]]) -> None:
    """Overwrite daily gauge fields for observability (last refresh wins per field)."""
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        today = date.today().isoformat()
        key = f"{ROLLUP_KEY}:{today}"
        n = len(enriched)
        if n == 0:
            return

        mat_sum = 0.0
        exec_sum = 0.0
        rv_sum = 0.0
        det_weak = 0
        decay_cnt = 0
        strongest: List[Tuple[float, str]] = []
        weakest: List[Tuple[float, str]] = []
        tier_hist: Dict[str, int] = {}
        posture_hist: Dict[str, int] = {}
        failed_continuations = 0
        false_break_events = 0
        decayed_setups = 0

        for x in enriched:
            dec = x.get("decision") if isinstance(x.get("decision"), dict) else {}
            sym = str(x.get("symbol") or "")
            try:
                m = float(dec.get("setup_maturity_score") or 0)
            except (TypeError, ValueError):
                m = 0.0
            try:
                eq = float((dec.get("execution_quality") or {}).get("score") or 0)
            except (TypeError, ValueError):
                eq = 0.0
            rev = dec.get("readiness_evolution") if isinstance(dec.get("readiness_evolution"), dict) else {}
            try:
                accel = float(rev.get("readiness_acceleration") or 0)
            except (TypeError, ValueError):
                accel = 0.0
            rv_sum += abs(accel)
            mat_sum += m
            exec_sum += eq
            try:
                dp = float(dec.get("deterioration_probability_pct") or 0)
            except (TypeError, ValueError):
                dp = 0.0
            if dp >= 45:
                det_weak += 1
            reg = str(rev.get("evolution_regime") or "").lower()
            if reg in ("weakening", "unstable structure"):
                decay_cnt += 1
            strongest.append((m, sym))
            weakest.append((m, sym))

            tier = str(dec.get("signal_tier") or "unknown").replace(" ", "_")
            tier_hist[tier] = tier_hist.get(tier, 0) + 1
            post = str((dec.get("execution_realism") or {}).get("execution_posture") or "unknown")
            posture_hist[post] = posture_hist.get(post, 0) + 1
            dv = dec.get("deterioration_validation") if isinstance(dec.get("deterioration_validation"), dict) else {}
            if dv.get("failed_continuation"):
                failed_continuations += 1
            try:
                if float((dec.get("deterioration") or {}).get("false_breakout_probability_pct") or 0) >= 52:
                    false_break_events += 1
            except (TypeError, ValueError):
                pass
            td = dec.get("time_decay") if isinstance(dec.get("time_decay"), dict) else {}
            if td.get("setup_expired_hint"):
                decayed_setups += 1

        strongest.sort(key=lambda t: -t[0])
        weakest.sort(key=lambda t: t[0])

        pipe = r.pipeline()
        pipe.hset(key, "gauge_last_user_id", int(user_id))
        pipe.hset(key, "gauge_last_symbol_count", n)
        pipe.hset(key, "gauge_avg_maturity", round(mat_sum / n, 2))
        pipe.hset(key, "gauge_avg_execution_quality", round(exec_sum / n, 2))
        pipe.hset(key, "gauge_avg_readiness_velocity", round(rv_sum / n, 3))
        pipe.hset(key, "gauge_deteriorated_setups", det_weak)
        pipe.hset(key, "gauge_setup_decay_count", decay_cnt)
        pipe.hset(key, "gauge_failed_continuations", failed_continuations)
        pipe.hset(key, "gauge_false_break_events", false_break_events)
        pipe.hset(key, "gauge_decayed_setups", decayed_setups)
        pipe.hset(key, "gauge_confidence_distribution_json", json.dumps(tier_hist))
        pipe.hset(key, "gauge_execution_risk_distribution_json", json.dumps(posture_hist))
        pipe.hset(key, "gauge_strongest_setups", ",".join(s for _, s in strongest[:8] if s))
        pipe.hset(key, "gauge_weakest_setups", ",".join(s for _, s in weakest[:8] if s))
        pipe.hset(key, "gauge_updated_at", time.time())
        pipe.expire(key, 8 * 86400)
        pipe.execute()
    except Exception as exc:
        logger.debug("rollup_touch skipped: %s", exc)


def read_decision_engine_debug() -> Dict[str, Any]:
    """Ops snapshot: Redis rollup + approximate intel key cardinality."""
    out: Dict[str, Any] = {"date": date.today().isoformat()}
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            out["redis_available"] = False
            return out
        out["redis_available"] = True
        day = date.today().isoformat()
        key = f"{ROLLUP_KEY}:{day}"
        out["rollup_key"] = key
        raw = r.hgetall(key)
        out["rollup_hash"] = dict(raw) if isinstance(raw, dict) else {}

        try:
            d = out["rollup_hash"]
            pd = int(d.get("promotions_discovery_to_watchlist") or 0)
            pw = int(d.get("promotions_watchlist_to_final") or 0)
            df = int(d.get("demotions_final_to_watchlist") or 0)
            dw = int(d.get("demotions_watchlist_to_discovery") or 0)
            out["promotions_today"] = pd + pw
            out["demotions_today"] = df + dw
            out["portfolio_turnover_events"] = pd + pw + df + dw
        except (TypeError, ValueError):
            pass

        out["deteriorated_setups_last_gauge"] = out["rollup_hash"].get("gauge_deteriorated_setups")
        out["strongest_setups"] = [
            x for x in (out["rollup_hash"].get("gauge_strongest_setups") or "").split(",") if x
        ][:12]
        out["weakest_setups"] = [
            x for x in (out["rollup_hash"].get("gauge_weakest_setups") or "").split(",") if x
        ][:12]
        out["avg_maturity"] = out["rollup_hash"].get("gauge_avg_maturity")
        out["avg_execution_quality"] = out["rollup_hash"].get("gauge_avg_execution_quality")
        out["readiness_velocity"] = out["rollup_hash"].get("gauge_avg_readiness_velocity")
        out["setup_decay_count"] = out["rollup_hash"].get("gauge_setup_decay_count")

        cur = 0
        approx = 0
        for _ in range(80):
            cur, batch = r.scan(cur, match="decision:intel_series:*", count=64)
            approx += len(batch)
            if cur == 0:
                break
        out["intel_series_keys_approx"] = approx
    except Exception as exc:
        out["error"] = str(exc)
    return out


def read_execution_engine_debug() -> Dict[str, Any]:
    """Phase 5.5 execution realism + calibration observability (Redis rollup-derived)."""
    base = read_decision_engine_debug()
    out: Dict[str, Any] = {"date": base.get("date"), "rollup_key": base.get("rollup_key")}
    if base.get("error"):
        out["error"] = base["error"]
        return out
    d = base.get("rollup_hash") or {}
    out["avg_execution_quality"] = d.get("gauge_avg_execution_quality")
    out["deteriorated_today"] = d.get("gauge_deteriorated_setups")
    out["promoted_today"] = base.get("promotions_today")
    out["portfolio_turnover"] = base.get("portfolio_turnover_events")
    out["decayed_setups"] = d.get("gauge_decayed_setups")
    out["avg_readiness_velocity"] = d.get("gauge_avg_readiness_velocity")
    out["failed_continuations"] = d.get("gauge_failed_continuations")
    out["false_breakout_events"] = d.get("gauge_false_break_events")
    out["setup_decay_count"] = d.get("gauge_setup_decay_count")
    try:
        out["confidence_distribution"] = json.loads(d.get("gauge_confidence_distribution_json") or "{}")
    except Exception:
        out["confidence_distribution"] = {}
    try:
        out["execution_risk_distribution"] = json.loads(d.get("gauge_execution_risk_distribution_json") or "{}")
    except Exception:
        out["execution_risk_distribution"] = {}
    calib_key = f"{CALIB_PREFIX}{date.today().isoformat()}"
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is not None:
            _raw_c = r.hgetall(calib_key)
            out["calibration_day_key"] = calib_key
            if _raw_c:
                out["calibration_sample_raw"] = dict(_raw_c)
    except Exception:
        pass
    return out


def format_telegram_signal_prefix(tier: str) -> str:
    """Prefix line for outbound Telegram formatting (import from signal_delivery)."""
    labels = {
        "institutional grade": "[INST]",
        "high conviction": "[HIGH]",
        "actionable": "[ACT]",
        "developing": "[DEV]",
        "informational": "[INFO]",
    }
    return labels.get((tier or "").lower(), "[SIG]")
