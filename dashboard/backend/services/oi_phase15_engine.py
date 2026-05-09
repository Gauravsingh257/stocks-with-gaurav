"""
Phase 1.5 — continuous reasoning layer on top of oi_interp_v2.

Adds (Redis-safe, capped payloads):
  • session story timeline (chronological narrative memory)
  • flow strength / momentum / persistence heuristics
  • strike evolution map (migration language)
  • institutional intent probabilities (capped, disclaimers)
  • smart trading action hints
  • cross-session vs last saved session anchor (EOD-style compact)
  • compact interpretation_digest for WS/mobile-friendly clients

Cloud-native only — extends enrich path; does not replace OI snapshot keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dashboard.backend.services.oi_interpretation_engine import _pct_delta, _step as oi_step

logger = logging.getLogger("dashboard.oi_phase15")

IST = timezone(timedelta(hours=5, minutes=30))

STORY_TIMELINE_KEY = "oi_intelligence:story_timeline_v1"
EOD_MEMORY_KEY = "oi_intelligence:eod_memory_v1"
SESSION_COMPARE_KEY = "oi_intelligence:session_compare_v1"

TIMELINE_MAX_EVENTS = 120
TIMELINE_TTL_SEC = 20 * 3600  # ≥ one cash session + buffer
EOD_TTL_SEC = 10 * 86400


def _ist_now() -> datetime:
    return datetime.now(IST)


def _iso_date(d: datetime) -> str:
    return d.date().isoformat()


def _session_date_from_snapshot(snapshot: Dict[str, Any]) -> str:
    raw = snapshot.get("last_update") or snapshot.get("timestamp") or ""
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return raw[:10]
        except Exception:
            pass
    return _iso_date(_ist_now())


def _format_ist_time_short(iso_ts: str) -> str:
    try:
        if isinstance(iso_ts, str) and "T" in iso_ts:
            dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_ist = dt.astimezone(IST)
            return dt_ist.strftime("%H:%M")
    except Exception:
        pass
    return _ist_now().strftime("%H:%M")


def _dominant_shift_type(shifts: List[Dict[str, Any]]) -> Optional[str]:
    if not shifts:
        return None
    return str(shifts[0].get("type") or "")


def _migration_notes(prior_ul: Dict[str, Any], cur_ul: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    support_txt: Optional[str] = None
    resist_txt: Optional[str] = None
    for name in cur_ul:
        if name not in prior_ul:
            continue
        a, b = cur_ul[name], prior_ul[name]
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        step = 100.0 if "BANK" in name.upper() else 50.0
        mc0, mc1 = float(b.get("max_call_strike") or 0), float(a.get("max_call_strike") or 0)
        mp0, mp1 = float(b.get("max_put_strike") or 0), float(a.get("max_put_strike") or 0)
        if mc0 and mc1 and abs(mc1 - mc0) >= step * 0.85:
            resist_txt = f"{name} call-OI peak → {int(mc1)} (was {int(mc0)})"
        if mp0 and mp1 and abs(mp1 - mp0) >= step * 0.85:
            support_txt = f"{name} put-OI peak → {int(mp1)} (was {int(mp0)})"
    return support_txt, resist_txt


def _compute_flow_strength(
    current: Dict[str, Any],
    prior: Optional[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]],
    shifts: List[Dict[str, Any]],
    trap_candidates: int,
) -> Dict[str, Any]:
    bull = float(current.get("bull_score") or 50)
    bear = float(current.get("bear_score") or 50)
    conf = float(current.get("confidence") or 50)
    pcr = float(current.get("pcr") or 1.0)

    prev_bull = prev_bear = prev_pcr = None
    if prior:
        prev_bull = float(prior.get("bull_score") or bull)
        prev_bear = float(prior.get("bear_score") or bear)
        prev_pcr = float(prior.get("pcr") or pcr)

    d_bull = (bull - prev_bull) if prev_bull is not None else 0.0
    d_bear = (bear - prev_bear) if prev_bear is not None else 0.0
    d_pcr = (pcr - prev_pcr) if prev_pcr is not None else 0.0

    # Rolling second derivative proxy from history ring (if ≥3 frames)
    accel = 0.0
    if history and len(history) >= 3:
        try:
            b0 = float(history[0].get("bull_score") or bull)
            b2 = float(history[2].get("bull_score") or b0)
            accel = (bull - b0) - (b0 - b2)
        except (TypeError, ValueError, IndexError):
            accel = 0.0

    flow_acceleration = max(-1.0, min(1.0, (d_bull - d_bear) / 40.0 + accel / 60.0))
    flow_persistence = max(0.0, min(1.0, (abs(d_bull) + abs(d_bear)) / 30.0 + conf / 200.0))
    flow_decay = max(0.0, min(1.0, -min(0, d_bull) / 25.0 + trap_candidates * 0.08))

    # Pressure strength bucket
    spread = bull - bear
    abs_sp = abs(spread)
    if abs_sp < 10:
        pressure = "weak"
    elif abs_sp < 22:
        pressure = "moderate"
    elif abs_sp < 38:
        pressure = "strong"
    else:
        pressure = "aggressive"

    # Narrative direction of bullish pressure
    if spread > 5 and d_bull >= 0:
        bp_trend = "increasing_bullish_pressure"
    elif spread > 5 and d_bull < -2:
        bp_trend = "decreasing_bullish_pressure"
    elif spread < -5 and d_bear >= 0:
        bp_trend = "increasing_bearish_pressure"
    elif spread < -5 and d_bear < -2:
        bp_trend = "decreasing_bearish_pressure"
    else:
        bp_trend = "balanced_chop"

    narratives: List[str] = []
    if flow_acceleration > 0.25:
        narratives.append("Bullish pressure appears to be accelerating vs recent scans (heuristic).")
    elif flow_acceleration < -0.25:
        narratives.append("Bearish pressure may be building faster than prior intervals.")
    if flow_persistence > 0.55:
        narratives.append("Flow bias looks persistent — structure may not mean-revert immediately.")
    if flow_decay > 0.45:
        narratives.append("Evidence of fading participation / decay risk on the dominant side.")
    if trap_candidates >= 2:
        narratives.append("Trapped / two-sided writer positioning possible near ATM — breakout quality uncertain.")

    stypes = {s.get("type") for s in shifts}
    if "resistance_weakening" in stypes:
        narratives.append("Overhead call stacks softening — resistance may be decaying faster than support.")

    return {
        "pressure_strength": pressure,
        "bullish_pressure_trend": bp_trend,
        "flow_acceleration": round(flow_acceleration, 3),
        "flow_persistence": round(flow_persistence, 3),
        "flow_decay": round(flow_decay, 3),
        "pressure_strength_score": min(100, int(abs_sp * 1.2 + conf * 0.35)),
        "scores": {
            "acceleration": int(max(0, min(100, 50 + flow_acceleration * 45))),
            "persistence": int(max(0, min(100, flow_persistence * 100))),
            "decay_risk": int(max(0, min(100, flow_decay * 100))),
        },
        "narratives": narratives[:6],
    }


def _strike_evolution_map(
    current: Dict[str, Any],
    prior: Optional[Dict[str, Any]],
    shifts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    prior_hm = (prior or {}).get("hm") or {}
    prior_ul = (prior or {}).get("ul") or {}

    # Wall migration rows
    ul_cur = compact_ul_meta(current)
    s_txt, r_txt = _migration_notes(prior_ul, ul_cur)
    if s_txt:
        out.append(
            {
                "underlying": "INDEX",
                "strike": None,
                "evolution": "support_migration",
                "arrow": "up",
                "label": "Support migration",
                "short": s_txt,
                "strength_delta": "repricing",
            }
        )
    if r_txt:
        out.append(
            {
                "underlying": "INDEX",
                "strike": None,
                "evolution": "resistance_migration",
                "arrow": "side",
                "label": "Resistance migration",
                "short": r_txt,
                "strength_delta": "repricing",
            }
        )

    for row in current.get("strike_heatmap") or []:
        if not isinstance(row, dict):
            continue
        try:
            u = str(row["underlying"])
            strike = int(row["strike"])
            k = f"{u}:{strike}"
            ce = float(row.get("ce_oi") or 0)
            pe = float(row.get("pe_oi") or 0)
            spot = float(row.get("spot") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        prev = prior_hm.get(k)
        if not prev:
            continue
        ce_dp = _pct_delta(float(prev.get("ce") or 0), ce)
        pe_dp = _pct_delta(float(prev.get("pe") or 0), pe)
        step = oi_step(u)
        label = None
        arrow = "flat"
        if pe_dp > 0.05 and strike <= spot + step:
            label = "strengthening support"
            arrow = "up"
        elif pe_dp < -0.05 and strike <= spot + step:
            label = "weakening support"
            arrow = "down"
        elif ce_dp > 0.05 and strike >= spot - step:
            label = "strengthening resistance"
            arrow = "down"
        elif ce_dp < -0.05 and strike >= spot - step:
            label = "collapsing resistance"
            arrow = "up"
        if label:
            out.append(
                {
                    "underlying": u,
                    "strike": strike,
                    "evolution": label.replace(" ", "_"),
                    "arrow": arrow,
                    "label": label,
                    "short": f"{strike} — {label.replace('_', ' ')}",
                    "strength_delta": f"ΔOI CE {ce_dp:+.0%} PE {pe_dp:+.0%}",
                }
            )

    # Pin unstable strikes (high two-sided from shifts)
    for s in shifts:
        if s.get("type") == "trap_zone":
            out.append(
                {
                    "underlying": s.get("underlying"),
                    "strike": s.get("strike"),
                    "evolution": "trapped_positioning",
                    "arrow": "trap",
                    "label": "Unstable / pin risk",
                    "short": s.get("text", "")[:160],
                    "strength_delta": "two_sided",
                }
            )
            break

    return out[:16]


def compact_ul_meta(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    summaries = snapshot.get("underlying_summaries") or {}
    for name, sm in summaries.items():
        if not isinstance(sm, dict):
            continue
        out[str(name)] = {
            "spot": float(sm.get("spot") or 0),
            "pcr": float(sm.get("pcr") or 0),
            "max_call_strike": float(sm.get("max_call_strike") or 0),
            "max_put_strike": float(sm.get("max_put_strike") or 0),
        }
    return out


def _cap_prob(x: float) -> float:
    return max(5.0, min(85.0, round(x, 1)))


def institutional_intent_v2(
    regime: Dict[str, Any],
    flow: Dict[str, Any],
    shifts: List[Dict[str, Any]],
    cur_pcr: float,
    trend: str,
) -> Dict[str, Any]:
    code = str(regime.get("code") or "range_bound")
    spread_accel = float(flow.get("flow_acceleration") or 0)
    intents: List[Dict[str, Any]] = []

    def add(iid: str, label: str, base: float, note: str) -> None:
        intents.append(
            {
                "id": iid,
                "label": label,
                "probability_pct": _cap_prob(base + spread_accel * 12.0),
                "confidence_cap": 72,
                "note": note,
            }
        )

    if code in ("bullish_expansion",):
        add("bull_acc", "Bullish accumulation probability", 52.0, "Positioning suggests upside participation may be building — not definitive.")
    if code in ("bearish_expansion",):
        add("bear_hedge", "Bearish hedging probability", 54.0, "Defensive overlays often rise when downside scenarios dominate discourse.")
    add("defensive", "Defensive positioning probability", 45.0 + (1.2 if cur_pcr > 1.15 else 0), "Elevated PCR can reflect hedging regardless of spot direction.")
    if abs(spread_accel) > 0.2:
        add("break_prep", "Breakout preparation probability", 48.0, "Fast-changing score spreads can precede expansion — also common in chop.")
    if code in ("compression", "range_bound"):
        add("vol_comp", "Volatility compression probability", 50.0, "Tight score spreads sometimes precede expansion — timing is uncertain.")
    if code in ("high_volatility", "trap_zone"):
        add("vol_exp", "Volatility expansion probability", 58.0, "Pin / trap contexts can resolve sharply — path risk is elevated.")
    stypes = {s.get("type") for s in shifts}
    if "short_covering" in stypes:
        add("gamma", "Gamma / dealer hedging stress probability", 56.0, "Rapid OI contraction can force dealer hedging adjustments.")

    disclaimer = (
        "Probabilities are stylistic confidence bands derived from public OI aggregates — "
        "not order-flow certainty and not investment advice."
    )

    return {"intents": intents[:10], "disclaimer": disclaimer}


def smart_trading_actions(
    regime: Dict[str, Any],
    guidance: Dict[str, Any],
    flow: Dict[str, Any],
    trap_risk: bool,
) -> Dict[str, Any]:
    code = str(regime.get("code") or "")
    first_key = next(iter(guidance.keys()), "INDEX")
    g0 = guidance.get(first_key) or {}
    ba = g0.get("bullish_above")
    bb = g0.get("bearish_below")
    mom = g0.get("momentum_expansion_above")
    weak = g0.get("weak_structure_below")

    preferred: List[str] = []
    avoid: List[str] = []
    risks: List[str] = []

    if code in ("bullish_expansion", "trend_friendly") and ba is not None:
        preferred.append(f"Consider dip participation above {ba} with reduced size until momentum confirms.")
    elif code in ("bearish_expansion",) and bb is not None:
        preferred.append(f"Respect sell/bias below {bb}; fades require explicit invalidation rules.")

    if trap_risk:
        avoid.append("Avoid aggressive breakout chasing inside ATM two-sided writer zones.")
    if float(flow.get("flow_decay") or 0) > 0.5:
        avoid.append("Participation decay signals — avoid oversized directional bets.")

    if g0.get("liquidity_sweep_risk") == "elevated":
        risks.append("Liquidity sweep risk is elevated near intraday pivots — widen risk awareness.")
    if mom is not None:
        risks.append(f"Momentum confirmation more credible above {mom} (quality gate).")
    if weak is not None:
        risks.append(f"Structure fragile below {weak} — invalidation speed may increase.")

    qual_break = "high" if float(flow.get("flow_persistence") or 0) > 0.55 else "moderate"
    qual_momo = "high" if code in ("bullish_expansion", "bearish_expansion") else "mixed"

    return {
        "preferred_strategy_lines": preferred[:4],
        "avoid_lines": avoid[:4],
        "risk_context_lines": risks[:6],
        "breakout_quality": qual_break,
        "momentum_quality": qual_momo,
        "trap_probability_hint": "elevated" if trap_risk else "moderate",
    }


def cross_session_compare(snapshot: Dict[str, Any], compact_cur: Dict[str, Any]) -> Dict[str, Any]:
    from dashboard.backend.cache import get as cache_get

    eod = cache_get(EOD_MEMORY_KEY)
    if not isinstance(eod, dict):
        return {"available": False, "note": "No saved session anchor yet."}

    session_date = str(eod.get("session_date") or "")
    today = _session_date_from_snapshot(snapshot)
    old_c = eod.get("compact")
    if not isinstance(old_c, dict):
        return {"available": False, "note": "Invalid session anchor."}

    if session_date == today:
        return {"available": False, "note": "Intraday — cross-session compare uses prior session anchor after hours."}

    bullets: List[str] = []
    try:
        p0, p1 = float(old_c.get("pcr") or 0), float(compact_cur.get("pcr") or 0)
        if abs(p1 - p0) >= 0.04:
            bullets.append(f"Index PCR differs vs last saved session ({p0:.2f} → {p1:.2f}) — overnight/participant mix shifted.")
        b0, b1 = float(old_c.get("bull_score") or 0), float(compact_cur.get("bull_score") or 0)
        if abs(b1 - b0) >= 8:
            bullets.append("Bull score regime changed materially vs prior session snapshot.")
    except (TypeError, ValueError):
        pass

    ul0, ul1 = old_c.get("ul") or {}, compact_cur.get("ul") or {}
    if isinstance(ul0, dict) and isinstance(ul1, dict):
        for n in ul1:
            if n not in ul0:
                continue
            a, b = ul1[n], ul0[n]
            if not isinstance(a, dict) or not isinstance(b, dict):
                continue
            mco, mc1 = float(b.get("max_call_strike") or 0), float(a.get("max_call_strike") or 0)
            if mco and mc1 and mco != mc1:
                bullets.append(f"{n}: call-OI peak relocated vs prior session ({int(mco)} → {int(mc1)}).")

    return {
        "available": True,
        "vs_session_date": session_date,
        "bullets": bullets[:8],
        "compared_at": datetime.now(timezone.utc).isoformat(),
    }


def build_interpretation_digest(interp: Dict[str, Any]) -> Dict[str, Any]:
    regime = interp.get("market_regime") or {}
    story = interp.get("market_story") or {}
    shifts = interp.get("oi_shifts") or []
    g = interp.get("guidance") or {}
    first = next(iter(g.keys()), None)
    top_g = g.get(first) if first else {}
    strongest = shifts[0] if shifts else {}
    conf = interp.get("confidence") or {}

    digest = {
        "regime": regime.get("code"),
        "regime_label": regime.get("label"),
        "bias": (interp.get("delta_vs_prior") or {}).get("bias_snapshot"),
        "top_story": (story.get("headline") or "")[:180],
        "strongest_shift": (strongest.get("type") or "")[:48],
        "strongest_shift_text": (strongest.get("text") or "")[:140],
        "top_guidance_underlying": first,
        "bullish_above": top_g.get("bullish_above"),
        "bearish_below": top_g.get("bearish_below"),
        "confidence_bullish_pct": conf.get("bullish_pct"),
        "confidence_bearish_pct": conf.get("bearish_pct"),
        "engine_version": interp.get("engine_version"),
        "generated_at": interp.get("generated_at"),
    }
    return digest


def _timeline_fingerprint(ev: Dict[str, Any]) -> str:
    raw = f"{ev.get('regime')}|{ev.get('headline')}|{ev.get('dominant_shift')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def append_story_timeline_event(
    snapshot: Dict[str, Any],
    interp: Dict[str, Any],
    prior: Optional[Dict[str, Any]],
) -> None:
    from dashboard.backend.cache import get as cache_get, set as cache_set

    regime = (interp.get("market_regime") or {}).get("code") or "unknown"
    story = interp.get("market_story") or {}
    headline = str(story.get("headline") or "")[:200]
    shifts = interp.get("oi_shifts") or []
    dom = _dominant_shift_type(shifts)
    conf = interp.get("confidence") or {}
    inst = interp.get("institutional_positioning") or {}
    tags = inst.get("tags") or []
    inst_hint = str(tags[0].get("label") if tags else "")[:120]

    prior_ul = (prior or {}).get("ul") or {}
    cur_ul = compact_ul_meta(snapshot)
    s_mig, r_mig = _migration_notes(prior_ul, cur_ul)

    ts_iso = snapshot.get("last_update") or interp.get("generated_at") or datetime.now(timezone.utc).isoformat()
    ev = {
        "ts": ts_iso,
        "ts_ist": _format_ist_time_short(ts_iso),
        "regime": regime,
        "dominant_shift": dom,
        "support_migration": s_mig,
        "resistance_migration": r_mig,
        "institutional_hint": inst_hint,
        "confidence_bull": conf.get("bullish_pct"),
        "headline": headline,
    }
    fp = _timeline_fingerprint(ev)

    timeline = cache_get(STORY_TIMELINE_KEY)
    if not isinstance(timeline, list):
        timeline = []
    if timeline and isinstance(timeline[0], dict):
        prev_fp = timeline[0].get("_fp")
        if prev_fp == fp:
            return

    ev["_fp"] = fp
    timeline = [ev] + timeline[: TIMELINE_MAX_EVENTS - 1]
    cache_set(STORY_TIMELINE_KEY, timeline, ttl_seconds=TIMELINE_TTL_SEC)


def maybe_persist_eod_memory(snapshot: Dict[str, Any], compact: Dict[str, Any]) -> None:
    """After hours: persist latest compact as session anchor for next-day compare."""
    from dashboard.backend.cache import set as cache_set

    if snapshot.get("market_hours", True):
        return
    sd = _session_date_from_snapshot(snapshot)
    payload = {"session_date": sd, "compact": compact}
    cache_set(EOD_MEMORY_KEY, payload, ttl_seconds=EOD_TTL_SEC)


def persist_session_compare(compare: Dict[str, Any]) -> None:
    from dashboard.backend.cache import set as cache_set

    cache_set(SESSION_COMPARE_KEY, compare, ttl_seconds=86400)


def _trap_candidate_count(snapshot: Dict[str, Any]) -> int:
    n = 0
    for row in snapshot.get("strike_heatmap") or []:
        if not isinstance(row, dict):
            continue
        try:
            u = str(row["underlying"])
            strike = int(row["strike"])
            ces = str(row.get("ce_status") or "")
            pes = str(row.get("pe_status") or "")
            spot = float(row.get("spot") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if ces == "buildup" and pes == "buildup" and spot and abs(strike - spot) < oi_step(u) * 1.5:
            n += 1
    return n


def augment_phase15(
    snapshot: Dict[str, Any],
    interp: Dict[str, Any],
    prior: Optional[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]],
    enrich_t0: float,
) -> Dict[str, Any]:
    compact_cur: Dict[str, Any] = {}
    try:
        from dashboard.backend.services.oi_interpretation_engine import compact_snapshot

        compact_cur = compact_snapshot(snapshot)
    except Exception:
        compact_cur = {}

    shifts = list(interp.get("oi_shifts") or [])
    regime = interp.get("market_regime") or {}
    guidance = interp.get("guidance") or {}
    trend = str(snapshot.get("pcr_trend") or "FLAT")
    cur_pcr = float(snapshot.get("pcr") or 0)
    trap_n = _trap_candidate_count(snapshot)

    flow = _compute_flow_strength(snapshot, prior, history, shifts, trap_n)
    strike_evo = _strike_evolution_map(snapshot, prior, shifts)
    intent_v2 = institutional_intent_v2(regime, flow, shifts, cur_pcr, trend)
    trap_risk = any((s.get("type") == "trap_zone") for s in interp.get("events") or []) or trap_n >= 2
    actions = smart_trading_actions(regime, guidance, flow, trap_risk)

    compare = cross_session_compare(snapshot, compact_cur)

    out = dict(interp)
    out["engine_version"] = "oi_interp_v2p5"
    out["flow_strength"] = flow
    out["strike_evolution"] = strike_evo
    out["institutional_intent_v2"] = intent_v2
    out["smart_trading_actions"] = actions

    # Timeline: append then preview (event visible same response when Redis succeeds)
    try:
        append_story_timeline_event(snapshot, out, prior)
    except Exception as exc:
        logger.debug("timeline append skipped: %s", exc)

    timeline_preview: List[Dict[str, Any]] = []
    try:
        from dashboard.backend.cache import get as cache_get

        tl = cache_get(STORY_TIMELINE_KEY)
        if isinstance(tl, list):
            timeline_preview = []
            for e in tl[:40]:
                if not isinstance(e, dict):
                    continue
                timeline_preview.append({k: v for k, v in e.items() if not str(k).startswith("_")})
    except Exception:
        pass

    try:
        persist_session_compare(compare)
    except Exception:
        pass

    try:
        if isinstance(compact_cur, dict) and compact_cur:
            maybe_persist_eod_memory(snapshot, compact_cur)
    except Exception as exc:
        logger.debug("eod memory skipped: %s", exc)

    out["session_evolution"] = {
        "cross_session": compare,
        "story_timeline": timeline_preview,
        "session_date_ist": _session_date_from_snapshot(snapshot),
    }

    gen_ms = round((time.perf_counter() - enrich_t0) * 1000, 2)
    out["interpretation_digest"] = build_interpretation_digest(out)
    out["phase15_meta"] = {
        "generation_latency_ms": gen_ms,
        "digest_bytes_estimate": len(json.dumps(out.get("interpretation_digest") or {}, default=str)),
        "timeline_events_loaded": len(timeline_preview),
        "ws_full_interpretation_bytes_estimate": len(json.dumps(out, default=str)),
    }

    return out


def get_phase15_debug() -> Dict[str, Any]:
    from dashboard.backend.cache import get as cache_get

    tl = cache_get(STORY_TIMELINE_KEY)
    eod = cache_get(EOD_MEMORY_KEY)
    sc = cache_get(SESSION_COMPARE_KEY)
    tl_len = len(tl) if isinstance(tl, list) else 0
    return {
        "story_timeline_key": STORY_TIMELINE_KEY,
        "timeline_depth": tl_len,
        "eod_memory_date": (eod or {}).get("session_date") if isinstance(eod, dict) else None,
        "session_compare_available": isinstance(sc, dict) and sc.get("available"),
        "keys_ttl_hint_sec": {"timeline": TIMELINE_TTL_SEC, "eod": EOD_TTL_SEC},
    }
