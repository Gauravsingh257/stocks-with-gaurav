"""
OI / derivatives MARKET INTELLIGENCE layer — Redis-backed deltas + rolling memory.

Extends (never replaces) the raw OI snapshot with:
  • classified OI shifts (support/resistance dynamics, flows)
  • market regime + readable story + institutional-style view (probabilistic)
  • decision guidance (levels, traps, triggers)
  • compact history ring for multi-window comparison (~15m contextual copy)

Cloud-native: Railway Redis holds prior fingerprint, rolling history, and meta for observability.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dashboard.oi_interpretation")

# ── Redis keys (additive — existing OI_SNAPSHOT_KEY unchanged) ─────────────
OI_PRIOR_REDIS_KEY = "oi_intelligence:prior_compact_v1"
OI_HISTORY_REDIS_KEY = "oi_intelligence:history_ring_v1"
OI_INTERP_META_KEY = "oi_intelligence:interp_meta_v1"

PRIOR_TTL_SEC = 7200
HISTORY_TTL_SEC = 7200
META_TTL_SEC = 3600
MAX_HISTORY_FRAMES = 14


def _strike_key(u: str, strike: int) -> str:
    return f"{u}:{strike}"


def _step(u: str) -> float:
    return 100.0 if "BANK" in str(u).upper() else 50.0


def compact_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Minimal fingerprint for cross-request delta + rolling memory (stored in Redis)."""
    hm: Dict[str, Dict[str, Any]] = {}
    for row in snapshot.get("strike_heatmap") or []:
        if not isinstance(row, dict):
            continue
        try:
            u = str(row.get("underlying", ""))
            st = int(row.get("strike", 0))
            k = _strike_key(u, st)
            ce = float(row.get("ce_oi") or 0)
            pe = float(row.get("pe_oi") or 0)
            hm[k] = {
                "ce": ce,
                "pe": pe,
                "ces": str(row.get("ce_status") or ""),
                "pes": str(row.get("pe_status") or ""),
                "cc": float(row.get("ce_change") or 0),
                "pc": float(row.get("pe_change") or 0),
                "spcr": float(row.get("strike_pcr") or ((pe / ce) if ce else 0.0)),
            }
        except (TypeError, ValueError):
            continue

    ul_meta: Dict[str, Any] = {}
    summaries = snapshot.get("underlying_summaries") or {}
    for name, sm in summaries.items():
        if not isinstance(sm, dict):
            continue
        ul_meta[str(name)] = {
            "spot": float(sm.get("spot") or 0),
            "pcr": float(sm.get("pcr") or 0),
            "max_call_strike": float(sm.get("max_call_strike") or 0),
            "max_put_strike": float(sm.get("max_put_strike") or 0),
            "bias": str(sm.get("bias") or "NEUTRAL"),
        }

    ms = snapshot.get("market_state") if isinstance(snapshot.get("market_state"), dict) else {}

    return {
        "ts": snapshot.get("last_update") or snapshot.get("timestamp"),
        "pcr": snapshot.get("pcr"),
        "pcr_trend": snapshot.get("pcr_trend"),
        "overall_bias": snapshot.get("overall_bias"),
        "confidence": snapshot.get("confidence"),
        "bull_score": snapshot.get("bull_score"),
        "bear_score": snapshot.get("bear_score"),
        "dominant_strike": snapshot.get("dominant_strike"),
        "market_state": ms.get("state"),
        "hm": hm,
        "ul": ul_meta,
    }


def _pct_delta(old_v: float, new_v: float) -> float:
    if old_v <= 0:
        return 0.0
    return (new_v - old_v) / old_v


def _oi_dominance(ce: float, pe: float) -> str:
    if ce <= 0 and pe <= 0:
        return "BALANCED"
    if ce > pe * 1.18:
        return "CALL_HEAVY"
    if pe > ce * 1.18:
        return "PUT_HEAVY"
    return "BALANCED"


def _classify_flow(ce_delta_pct: float, pe_delta_pct: float, ces: str, pes: str) -> Optional[str]:
    if ces == "short_cover" or pes == "short_cover":
        return "short_covering"
    if ce_delta_pct > 0.08 and "buildup" in ces:
        return "fresh_call_writing"
    if pe_delta_pct > 0.08 and "buildup" in pes:
        return "fresh_put_writing"
    if ce_delta_pct < -0.06:
        return "call_unwind"
    if pe_delta_pct < -0.06:
        return "put_unwind"
    return None


def _detect_oi_shifts(
    heatmap: List[Any],
    prior_hm: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns (shift_events, strike_cards_seed) — conservative heuristics.
    """
    shifts: List[Dict[str, Any]] = []
    cards: List[Dict[str, Any]] = []

    for row in heatmap:
        if not isinstance(row, dict):
            continue
        try:
            u = str(row["underlying"])
            strike = int(row["strike"])
            k = _strike_key(u, strike)
            ce = float(row.get("ce_oi") or 0)
            pe = float(row.get("pe_oi") or 0)
            ces = str(row.get("ce_status") or "")
            pes = str(row.get("pe_status") or "")
            spot = float(row.get("spot") or 0)
            spcr = float(row.get("strike_pcr") or ((pe / ce) if ce else 0.0))
        except (KeyError, TypeError, ValueError):
            continue

        prev = prior_hm.get(k)
        if not prev:
            continue

        ce_dp = _pct_delta(float(prev.get("ce") or 0), ce)
        pe_dp = _pct_delta(float(prev.get("pe") or 0), pe)
        prev_dom = _oi_dominance(float(prev.get("ce") or 0), float(prev.get("pe") or 0))
        cur_dom = _oi_dominance(ce, pe)

        step = _step(u)
        dist = abs(strike - spot)
        atm = dist <= step * 1.5
        above = strike > spot + step * 0.35
        below = strike < spot - step * 0.35

        # Strike semantic transition (same strike, OI tilt flip)
        shift_label = None
        if prev_dom == "CALL_HEAVY" and cur_dom == "PUT_HEAVY" and (above or atm):
            shift_label = "resistance_to_support_tilt"
        elif prev_dom == "PUT_HEAVY" and cur_dom == "CALL_HEAVY" and (below or atm):
            shift_label = "support_to_resistance_tilt"

        if shift_label:
            cards.append(
                {
                    "underlying": u,
                    "strike": strike,
                    "role": shift_label,
                    "headline": "Strike character shifted",
                    "detail": f"{strike} moved from call-heavy to put-heavy open interest (or reverse) vs prior scan — "
                    f"dealers may be repricing this pocket.",
                    "shift": shift_label,
                    "tone": "structural",
                }
            )

        # Flow classifications
        if ces == "short_cover" or pes == "short_cover":
            shifts.append(
                {
                    "type": "short_covering",
                    "underlying": u,
                    "strike": strike,
                    "confidence": 0.62,
                    "text": f"{u} {strike}: short-cover / gamma-style squeeze risk — call or put open interest compressed abruptly.",
                }
            )
        if pes == "unwind" and pe_dp < -0.05 and (below or atm):
            shifts.append(
                {
                    "type": "long_unwinding",
                    "underlying": u,
                    "strike": strike,
                    "confidence": 0.55,
                    "text": f"{u} {strike}: put open interest deflating — protective bids may be leaving (support vulnerability).",
                }
            )
        if below or atm:
            if pe_dp > 0.035 and ("buildup" in pes or spcr >= 1.05):
                shifts.append(
                    {
                        "type": "support_strengthening",
                        "underlying": u,
                        "strike": strike,
                        "confidence": 0.58,
                        "text": f"{u} {strike}: put writers adding — likely defensive absorption / support reinforcement.",
                    }
                )
            if pe_dp < -0.045 and pes in ("unwind", "normal"):
                shifts.append(
                    {
                        "type": "support_weakening",
                        "underlying": u,
                        "strike": strike,
                        "confidence": 0.52,
                        "text": f"{u} {strike}: put open interest easing — downside cushion may be thinning.",
                    }
                )
        if above or atm:
            if ce_dp > 0.035 and "buildup" in ces and spcr <= 1.02:
                shifts.append(
                    {
                        "type": "resistance_strengthening",
                        "underlying": u,
                        "strike": strike,
                        "confidence": 0.58,
                        "text": f"{u} {strike}: call writers leaning in — overhead supply building.",
                    }
                )
            if ce_dp < -0.045 or ces == "unwind":
                shifts.append(
                    {
                        "type": "resistance_weakening",
                        "underlying": u,
                        "strike": strike,
                        "confidence": 0.56,
                        "text": f"{u} {strike}: call open interest deflating — upside cap may be lifting (not guaranteed).",
                    }
                )

        intensity = abs(ce_dp) + abs(pe_dp)
        if intensity > 0.12:
            cards.append(
                {
                    "underlying": u,
                    "strike": strike,
                    "role": "oi_momentum",
                    "headline": "Large OI delta",
                    "detail": f"Notional positioning moved materially vs prior snapshot at {strike} — worth tracking vs spot.",
                    "shift": None,
                    "tone": "volatile",
                    "intensity": round(intensity, 3),
                }
            )

    # Dedupe shifts by type+strike
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for s in shifts:
        key = f"{s.get('type')}-{s.get('underlying')}-{s.get('strike')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)

    # Rank cards by intensity or keep structural first
    cards.sort(key=lambda c: float(c.get("intensity") or 0), reverse=True)
    return deduped[:18], cards[:10]


def _pick_regime(
    current: Dict[str, Any],
    trap_candidates: int,
    pcr_momentum: str,
) -> Tuple[Dict[str, Any], int]:
    """Regime label + confidence (0–100)."""
    bias = str(current.get("overall_bias") or "NEUTRAL").upper()
    conf = float(current.get("confidence") or 0)
    bull = float(current.get("bull_score") or 0)
    bear = float(current.get("bear_score") or 0)
    ms = current.get("market_state") if isinstance(current.get("market_state"), dict) else {}
    mstate = str(ms.get("state") or "RANGE").upper()

    drivers: List[str] = []

    if conf >= 78:
        drivers.append("Model conviction elevated — treat moves as higher stakes.")
    if trap_candidates >= 2:
        drivers.append("Two-sided ATM writer interest — pin / range-trap risk until a side breaks.")

    code = "range_bound"
    label = "Range / balanced flow"
    rconf = 55

    if mstate in ("VOLATILE", "VOLATILE_EXPANSION", "HIGH_VOL"):
        code = "high_volatility"
        label = "High volatility"
        rconf = min(90, int(conf))
        drivers.append("Market-state engine tagging volatile conditions.")
    elif trap_candidates >= 2:
        code = "trap_zone"
        label = "Trap / pin zone"
        rconf = 62
    elif bias.startswith("BULL") and bull >= bear + 18:
        code = "bullish_expansion"
        label = "Bullish expansion"
        rconf = min(88, int(55 + (bull - bear) / 4))
        drivers.append("Bull score leading bear score — upside participation dominant in OI stack.")
    elif bias.startswith("BEAR") and bear >= bull + 18:
        code = "bearish_expansion"
        label = "Bearish expansion"
        rconf = min(88, int(55 + (bear - bull) / 4))
        drivers.append("Bear score leading — downside hedges / selling flow notable.")
    elif abs(bull - bear) < 12 and conf < 52:
        code = "compression"
        label = "Compression / wait"
        rconf = 48
        drivers.append("Scores compressed — directional edge still forming.")
    elif pcr_momentum in ("bullish_shift", "bearish_shift"):
        code = "breakout_watch"
        label = "Breakout watch"
        rconf = 58
        drivers.append("PCR momentum shifting — positioning adjusting quickly.")

    if mstate in ("TREND", "TREND_UP", "UPTREND") and code == "range_bound":
        code = "bullish_expansion"
        label = "Trend-friendly"
        rconf = max(rconf, 60)

    regime = {
        "code": code,
        "label": label,
        "confidence": min(95, max(35, rconf)),
        "drivers": drivers[:5],
    }
    return regime, regime["confidence"]


def _confidence_pct(current: Dict[str, Any], pcr_momentum: str, regime_conf: int) -> Dict[str, Any]:
    bull = float(current.get("bull_score") or 50)
    bear = float(current.get("bear_score") or 50)
    bias = str(current.get("overall_bias") or "NEUTRAL").upper()

    bullish_pct = int(round(50 + (bull - bear) * 0.35))
    bearish_pct = int(round(50 + (bear - bull) * 0.35))

    if bias.startswith("BULL"):
        bullish_pct = min(92, bullish_pct + 8)
    elif bias.startswith("BEAR"):
        bearish_pct = min(92, bearish_pct + 8)

    if pcr_momentum == "bearish_shift":
        bullish_pct = min(92, bullish_pct + 5)
        bearish_pct = max(8, bearish_pct - 5)
    elif pcr_momentum == "bullish_shift":
        bearish_pct = min(92, bearish_pct + 4)

    s = bullish_pct + bearish_pct
    if s > 0:
        bullish_pct = int(round(bullish_pct / s * 100))
        bearish_pct = 100 - bullish_pct

    bullish_pct = max(5, min(95, bullish_pct))
    bearish_pct = max(5, min(95, bearish_pct))

    return {
        "bullish_pct": bullish_pct,
        "bearish_pct": bearish_pct,
        "regime_pct": min(95, max(30, regime_conf)),
    }


def _institutional_view(
    regime: Dict[str, Any],
    shifts: List[Dict[str, Any]],
    cur_pcr: float,
    trend: str,
) -> Dict[str, Any]:
    tags: List[Dict[str, Any]] = []
    code = regime.get("code", "")

    def add(tag_id: str, label: str, likelihood: float, note: str) -> None:
        tags.append({"id": tag_id, "label": label, "likelihood": likelihood, "note": note})

    if code == "bullish_expansion":
        add("dir_long", "Directional bullish positioning (probable)", 0.58, "Flows skew risk-on; often dealer hedging of upside.")
    if code == "bearish_expansion":
        add("hedge_lift", "Defensive / hedge-heavy positioning (probable)", 0.57, "Put interest often rises into weakness — not automatically bullish for buyers.")

    if cur_pcr >= 1.25:
        add("put_skew", "Crowded put hedges", 0.52, "Elevated PCR can reflect fear OR protective overlays into events.")
    elif cur_pcr <= 0.75:
        add("call_skew", "Call-led positioning", 0.51, "Lower PCR — calls gaining share; can precede squeeze or complacency.")

    if trend == "RISING":
        add("pcr_slope", "PCR building", 0.49, "Rising PCR sometimes precedes mean-reversion chop.")

    stypes = {s.get("type") for s in shifts}
    if "short_covering" in stypes:
        add("gamma", "Gamma / short-cover dynamics possible", 0.54, "Fast OI contraction with price urgency — fragile if liquidity fades.")

    summary = (
        "Institutional-quality positioning cannot be observed directly from retail OI prints alone — "
        "below is a probabilistic read of dealer/customer pressure visible in the chain."
    )

    return {"summary": summary, "tags": tags[:8]}


def _what_changed(
    current_compact: Dict[str, Any],
    prior: Optional[Dict[str, Any]],
    rolling_anchor: Optional[Dict[str, Any]],
    pcr_momentum: str,
    shifts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    bullets: List[Dict[str, Any]] = []

    if prior and prior.get("pcr") is not None:
        try:
            dp = float(current_compact.get("pcr") or 0) - float(prior.get("pcr") or 0)
            if abs(dp) >= 0.03:
                tone = "neutral"
                if dp > 0:
                    tone = "caution"
                else:
                    tone = "positive"
                bullets.append(
                    {
                        "text": f"Index PCR moved {dp:+.2f} vs immediate prior snapshot.",
                        "tone": tone,
                    }
                )
        except (TypeError, ValueError):
            pass

    # Dominant strike migration (top-level compact)
    if prior and rolling_anchor is None:
        ds0 = prior.get("dominant_strike")
        ds1 = current_compact.get("dominant_strike")
        if ds0 is not None and ds1 is not None and ds0 != ds1:
            bullets.append(
                {
                    "text": f"Dominant strike interest migrated toward {ds1} (was {ds0}).",
                    "tone": "neutral",
                }
            )

    for s in shifts[:6]:
        bullets.append({"text": s.get("text") or "", "tone": "neutral"})

    rolling_bullets: List[Dict[str, Any]] = []
    window_label = "vs prior scan"
    if rolling_anchor and rolling_anchor.get("pcr") is not None:
        try:
            dp = float(current_compact.get("pcr") or 0) - float(rolling_anchor.get("pcr") or 0)
            rolling_bullets.append(
                {
                    "text": f"Over recent snapshots, PCR drifted {dp:+.2f} — positioning adjusting across the window.",
                    "tone": "neutral",
                }
            )
            # Wall migration from compact ul
            ul_now = current_compact.get("ul") or {}
            ul_old = rolling_anchor.get("ul") or {}
            if isinstance(ul_now, dict) and isinstance(ul_old, dict):
                for name in ul_now:
                    if name not in ul_old:
                        continue
                    a, b = ul_now[name], ul_old[name]
                    if not isinstance(a, dict) or not isinstance(b, dict):
                        continue
                    mc0, mc1 = float(b.get("max_call_strike") or 0), float(a.get("max_call_strike") or 0)
                    mp0, mp1 = float(b.get("max_put_strike") or 0), float(a.get("max_put_strike") or 0)
                    step = _step(name)
                    if mc0 and mc1 and abs(mc1 - mc0) >= step * 0.85:
                        rolling_bullets.append(
                            {
                                "text": f"{name}: heaviest call-open-interest pocket shifted toward {int(mc1)} vs earlier window.",
                                "tone": "caution",
                            }
                        )
                    if mp0 and mp1 and abs(mp1 - mp0) >= step * 0.85:
                        rolling_bullets.append(
                            {
                                "text": f"{name}: peak put open interest migrated toward {int(mp1)} — support/resistance map repricing.",
                                "tone": "neutral",
                            }
                        )
        except (TypeError, ValueError):
            pass
        window_label = "~15 minute contextual window (snapshot cadence dependent)"

    combined = bullets[:8] + rolling_bullets[:6]
    if not combined and prior:
        combined = [{"text": "Micro-structure unchanged vs prior scan — wait for the next OI refresh for fresh deltas.", "tone": "neutral"}]

    return {
        "window_minutes": 15,
        "window_label": window_label,
        "immediate_vs_prior": bullets[:10],
        "rolling_window": rolling_bullets[:8],
        "bullets": combined[:14],
        "persistence_note": None
        if rolling_anchor
        else "Rolling memory fills as snapshots accumulate — multi-window copy strengthens automatically.",
    }


def _market_story_headline(regime: Dict[str, Any], shifts: List[Dict[str, Any]], bias: str) -> Tuple[str, List[str]]:
    headline = f"{regime.get('label', 'Market')} — {bias.replace('_', ' ')} bias visible in aggregates."
    paras: List[str] = []

    stypes = [s.get("type") for s in shifts]
    if "support_strengthening" in stypes:
        paras.append("Put writers are visibly leaning into downside strikes — often interpreted as protective supply / cushion building.")
    if "resistance_weakening" in stypes:
        paras.append("Several overhead call stacks deflated vs the prior scan — upside friction may be easing (probability read, not certainty).")
    if "short_covering" in stypes:
        paras.append("Short-cover signatures detected — rapid OI contraction consistent with urgency covering / gamma dynamics.")
    if not paras:
        paras.append("Positioning is evolving scan-over-scan — lean on levels & regime rather than any single strike headline.")

    return headline, paras[:5]


def build_interpretation(
    current: Dict[str, Any],
    prior: Optional[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    narrative_lines: List[str] = []
    events: List[Dict[str, Any]] = []
    strike_narratives: List[Dict[str, Any]] = []

    cur_pcr = float(current.get("pcr") or 0)
    trend = str(current.get("pcr_trend") or "FLAT")
    bias = str(current.get("overall_bias") or "NEUTRAL")

    prior_hm: Dict[str, Dict[str, Any]] = (prior or {}).get("hm") or {}
    prior_ul: Dict[str, Any] = (prior or {}).get("ul") or {}

    compact_cur = compact_snapshot(current)

    # Rolling anchor (~15m): oldest frame in ring still within cutoff
    rolling_anchor: Optional[Dict[str, Any]] = None
    now = time.time()
    if history:
        candidates: List[Tuple[float, Dict[str, Any]]] = []
        for frame in history:
            if not isinstance(frame, dict):
                continue
            ts = frame.get("stored_at_epoch")
            if not isinstance(ts, (int, float)):
                continue
            age = now - float(ts)
            if 10 * 60 <= age <= 22 * 60:
                candidates.append((age, frame))
        if candidates:
            candidates.sort(key=lambda x: abs(x[0] - 15 * 60))
            rolling_anchor = candidates[0][1]

    # ── PCR momentum vs prior ────────────────────────────────────────────
    pcr_momentum = "flat"
    if prior and prior.get("pcr") is not None:
        try:
            prev_pcr = float(prior["pcr"])
            d = cur_pcr - prev_pcr
            if d > 0.06:
                pcr_momentum = "bullish_shift"
                narrative_lines.append(
                    f"PCR rose {d:+.2f} vs prior scan — put interest gaining share (often hedging / fear read; context matters)."
                )
            elif d < -0.06:
                pcr_momentum = "bearish_shift"
                narrative_lines.append(f"PCR fell {d:+.2f} vs prior scan — calls gaining share vs puts.")
            else:
                narrative_lines.append("PCR broadly stable vs prior snapshot — no violent repositioning on aggregate.")
        except (TypeError, ValueError):
            narrative_lines.append("PCR delta vs prior unavailable.")
    else:
        narrative_lines.append("Baseline snapshot — next refresh unlocks scan-over-scan intelligence.")

    if trend == "RISING" and cur_pcr >= 1.1:
        narrative_lines.append("PCR slope rising while elevated — hedges may be accumulating into strength.")
    elif trend == "FALLING" and cur_pcr <= 0.85:
        narrative_lines.append("PCR cooling — calls gaining relative share; complacency risk rises if trend persists.")

    trap_candidates = 0
    for row in current.get("strike_heatmap") or []:
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
        if ces == "buildup" and pes == "buildup" and spot and abs(strike - spot) < _step(u) * 1.5:
            trap_candidates += 1

    regime, regime_conf = _pick_regime(current, trap_candidates, pcr_momentum)
    confidence = _confidence_pct(current, pcr_momentum, regime_conf)

    shifts, strike_cards = _detect_oi_shifts(list(current.get("strike_heatmap") or []), prior_hm)
    inst = _institutional_view(regime, shifts, cur_pcr, trend)

    # Re-use shifts for legacy strike narratives
    for s in shifts[:12]:
        if s["type"] in ("support_strengthening", "resistance_strengthening", "support_weakening", "resistance_weakening"):
            strike_narratives.append(
                {
                    "underlying": str(s.get("underlying")),
                    "strike": int(s.get("strike") or 0),
                    "headline": s["type"].replace("_", " ").title(),
                    "detail": s.get("text", ""),
                    "tone": "caution" if "weakening" in s["type"] or "resistance_strengthening" in s["type"] else "constructive",
                }
            )

    # Classic flow narratives (detail)
    for row in current.get("strike_heatmap") or []:
        if not isinstance(row, dict):
            continue
        try:
            u = str(row["underlying"])
            strike = int(row["strike"])
            k = _strike_key(u, strike)
            ce = float(row.get("ce_oi") or 0)
            pe = float(row.get("pe_oi") or 0)
            ces = str(row.get("ce_status") or "")
            pes = str(row.get("pe_status") or "")
            spot = float(row.get("spot") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        prev = prior_hm.get(k)
        if prev:
            ce_dp = _pct_delta(float(prev.get("ce") or 0), ce)
            pe_dp = _pct_delta(float(prev.get("pe") or 0), pe)
            flow = _classify_flow(ce_dp, pe_dp, ces, pes)
            if flow == "fresh_call_writing" and spot and strike > spot:
                strike_narratives.append(
                    {
                        "underlying": u,
                        "strike": strike,
                        "headline": "Aggressive CE writing",
                        "detail": f"{strike} call writer buildup — overhead supply narrative.",
                        "tone": "caution",
                    }
                )
            if flow == "fresh_put_writing" and spot and strike < spot:
                strike_narratives.append(
                    {
                        "underlying": u,
                        "strike": strike,
                        "headline": "PE writing / absorption",
                        "detail": f"{strike} put writers adding — downside absorption story.",
                        "tone": "constructive",
                    }
                )
            if flow == "call_unwind" and strike > spot:
                strike_narratives.append(
                    {
                        "underlying": u,
                        "strike": strike,
                        "headline": "Call wall softening",
                        "detail": f"{strike} CE OI deflated vs prior — upside cap may be lifting.",
                        "tone": "bullish_relief",
                    }
                )

    if trap_candidates >= 2:
        events.append(
            {
                "type": "trap_zone",
                "underlying": "INDEX",
                "strike": None,
                "confidence": 0.45,
                "text": "ATM zone shows two-sided writer buildup — pin / range trap until one side breaks.",
            }
        )

    # Guidance per underlying (extended)
    guidance: Dict[str, Any] = {}
    summaries = current.get("underlying_summaries") or {}
    high_vol = float(current.get("confidence") or 0) >= 75

    for ul_name, sm in summaries.items():
        if not isinstance(sm, dict):
            continue
        spot = float(sm.get("spot") or 0)
        mx_call = float(sm.get("max_call_strike") or 0)
        mx_put = float(sm.get("max_put_strike") or 0)
        step = _step(str(ul_name))

        prev_sm = prior_ul.get(str(ul_name)) if prior_ul else None
        parts: List[str] = []
        if isinstance(prev_sm, dict) and spot > 0:
            p_mc = float(prev_sm.get("max_call_strike") or 0)
            p_mp = float(prev_sm.get("max_put_strike") or 0)
            if mx_call and p_mc and abs(mx_call - p_mc) >= step * 0.9:
                parts.append(f"Heaviest call wall migrated toward {int(mx_call)}.")
            if mx_put and p_mp and abs(mx_put - p_mp) >= step * 0.9:
                parts.append(f"Peak put interest shifted toward {int(mx_put)}.")
        flip_note = " ".join(parts) if parts else None

        bullish_above = round(mx_put + step * 0.5) if mx_put else round(spot + step)
        bearish_below = round(mx_call - step * 0.5) if mx_call else round(spot - step)
        avoid_lo = round(spot - step)
        avoid_hi = round(spot + step)
        breakout_trigger = round(max(spot, mx_call) + step * 0.2) if mx_call else round(spot + step * 1.2)
        momentum_above = round(spot + step * 1.6)
        weak_below = round(min(spot, mx_put) - step * 0.8) if mx_put else round(spot - step * 1.2)

        sweep_risk = "elevated" if high_vol or trap_candidates >= 2 else "moderate" if trap_candidates == 1 else "low"

        guidance[str(ul_name)] = {
            "bullish_above": bullish_above if spot else None,
            "bearish_below": bearish_below if spot else None,
            "avoid_trade_zone": [avoid_lo, avoid_hi] if spot else None,
            "trap_zone": [avoid_lo, avoid_hi] if trap_candidates >= 2 and spot else None,
            "breakout_trigger": breakout_trigger if spot else None,
            "momentum_expansion_above": momentum_above if spot else None,
            "weak_structure_below": weak_below if spot else None,
            "liquidity_sweep_risk": sweep_risk,
            "avoid_aggressive_shorts_above": bullish_above if spot else None,
            "high_volatility_zone": bool(high_vol),
            "structure_note": flip_note,
        }

        ul_bias = str(sm.get("bias") or "NEUTRAL")
        if ul_bias == "BULLISH":
            events.append(
                {
                    "type": "bullish_shift",
                    "underlying": ul_name,
                    "strike": None,
                    "confidence": 0.55,
                    "text": f"{ul_name}: participant skew leaning bullish — operate with long bias above {bullish_above}.",
                }
            )
        elif ul_bias == "BEARISH":
            events.append(
                {
                    "type": "bearish_shift",
                    "underlying": ul_name,
                    "strike": None,
                    "confidence": 0.55,
                    "text": f"{ul_name}: defensive skew — respect supply below {bearish_below}.",
                }
            )

    headline, paragraphs = _market_story_headline(regime, shifts, bias)
    what_changed = _what_changed(compact_cur, prior, rolling_anchor, pcr_momentum, shifts)

    generated_at = datetime.now(timezone.utc).isoformat()

    return {
        "summary_lines": narrative_lines[:8],
        "events": events[:12],
        "strike_narratives": strike_narratives[:24],
        "guidance": guidance,
        "delta_vs_prior": {
            "has_prior": prior is not None,
            "pcr_now": cur_pcr,
            "pcr_momentum": pcr_momentum,
            "bias_snapshot": bias,
            "dominant_strike_stories": strike_narratives[:8],
            "rolling_anchor_age_sec": int(now - float(rolling_anchor["stored_at_epoch"]))
            if rolling_anchor and isinstance(rolling_anchor.get("stored_at_epoch"), (int, float))
            else None,
        },
        "engine_version": "oi_interp_v2",
        "generated_at": generated_at,
        "market_regime": regime,
        "market_story": {"headline": headline, "paragraphs": paragraphs},
        "what_changed": what_changed,
        "institutional_positioning": inst,
        "confidence": confidence,
        "oi_shifts": shifts,
        "strike_intelligence": strike_cards,
    }


def _append_history(compact: Dict[str, Any]) -> None:
    from dashboard.backend.cache import get as cache_get, set as cache_set

    frame = dict(compact)
    frame["stored_at_epoch"] = time.time()
    hist = cache_get(OI_HISTORY_REDIS_KEY)
    if not isinstance(hist, list):
        hist = []
    hist = [frame] + hist[: MAX_HISTORY_FRAMES - 1]
    cache_set(OI_HISTORY_REDIS_KEY, hist, ttl_seconds=HISTORY_TTL_SEC)


def _write_meta_enriched(interp: Dict[str, Any], prior: Optional[Dict[str, Any]], hist_len: int) -> None:
    from dashboard.backend.cache import set as cache_set

    base: Dict[str, Any] = {
        "interpretation_generated_at": interp.get("generated_at"),
        "engine_version": interp.get("engine_version"),
        "narrative_version": interp.get("engine_version"),
        "prior_snapshot_ts": (prior or {}).get("ts"),
        "prior_comparison_available": prior is not None,
        "history_frames": hist_len,
        "regime": (interp.get("market_regime") or {}).get("code"),
        "regime_confidence": (interp.get("market_regime") or {}).get("confidence"),
        "confidence_bullish_pct": (interp.get("confidence") or {}).get("bullish_pct"),
        "confidence_bearish_pct": (interp.get("confidence") or {}).get("bearish_pct"),
        "institutional_tags": [t.get("id") for t in (interp.get("institutional_positioning") or {}).get("tags") or []],
        "oi_shift_types": list({s.get("type") for s in (interp.get("oi_shifts") or []) if s.get("type")}),
        "what_changed_bullet_count": len((interp.get("what_changed") or {}).get("bullets") or []),
    }
    pm = interp.get("phase15_meta") or {}
    base["generation_latency_ms"] = pm.get("generation_latency_ms")
    base["digest_bytes_estimate"] = pm.get("digest_bytes_estimate")
    base["full_interpretation_bytes_estimate"] = pm.get("ws_full_interpretation_bytes_estimate")
    cache_set(OI_INTERP_META_KEY, base, ttl_seconds=META_TTL_SEC)


def enrich_oi_snapshot_with_interpretation(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Attach interpretation + persist compact prior, rolling history, and observability meta."""
    try:
        from dashboard.backend.cache import get as cache_get, set as cache_set
        from dashboard.backend.services.oi_phase15_engine import augment_phase15

        enrich_t0 = time.perf_counter()
        prior = cache_get(OI_PRIOR_REDIS_KEY)
        prior_d = prior if isinstance(prior, dict) else None
        history = cache_get(OI_HISTORY_REDIS_KEY)
        hist_list = history if isinstance(history, list) else None

        interp = build_interpretation(snapshot, prior_d, hist_list)
        interp = augment_phase15(snapshot, interp, prior_d, hist_list, enrich_t0)
        out = dict(snapshot)
        out["interpretation"] = interp
        # Compact digest at root for WS clients that prefer a shallow read (same data as nested).
        if isinstance(interp.get("interpretation_digest"), dict):
            out["interpretation_digest"] = interp["interpretation_digest"]

        compact = compact_snapshot(snapshot)
        cache_set(OI_PRIOR_REDIS_KEY, compact, ttl_seconds=PRIOR_TTL_SEC)
        _append_history(compact)

        fresh_hist = cache_get(OI_HISTORY_REDIS_KEY)
        hist_len = len(fresh_hist) if isinstance(fresh_hist, list) else 0
        _write_meta_enriched(interp, prior_d, hist_len)

        return out
    except Exception as exc:
        logger.debug("OI interpretation enrich failed (non-fatal): %s", exc)
        merged = dict(snapshot)
        merged["interpretation"] = {
            "summary_lines": ["Interpretation layer temporarily unavailable."],
            "events": [],
            "strike_narratives": [],
            "guidance": {},
            "delta_vs_prior": {"has_prior": False, "error": str(exc)},
            "engine_version": "oi_interp_v2_error",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "market_regime": {"code": "unknown", "label": "Unavailable", "confidence": 0, "drivers": []},
            "market_story": {"headline": "", "paragraphs": []},
            "what_changed": {"bullets": [], "window_label": "—"},
            "institutional_positioning": {"summary": "", "tags": []},
            "confidence": {"bullish_pct": 50, "bearish_pct": 50, "regime_pct": 0},
            "oi_shifts": [],
            "strike_intelligence": [],
            "interpretation_digest": {},
            "phase15_meta": {},
            "flow_strength": {},
            "strike_evolution": [],
            "institutional_intent_v2": {},
            "smart_trading_actions": {},
            "session_evolution": {},
        }
        return merged


def get_oi_interpretation_debug() -> Dict[str, Any]:
    """Observability payload for GET /api/system/debug/cache — Redis introspection."""
    try:
        from dashboard.backend.cache import get as cache_get

        meta = cache_get(OI_INTERP_META_KEY)
        prior = cache_get(OI_PRIOR_REDIS_KEY)
        hist = cache_get(OI_HISTORY_REDIS_KEY)
        hist_len = len(hist) if isinstance(hist, list) else 0
        oldest_age_sec = None
        if isinstance(hist, list) and hist:
            tail = hist[-1]
            if isinstance(tail, dict) and isinstance(tail.get("stored_at_epoch"), (int, float)):
                oldest_age_sec = round(time.time() - float(tail["stored_at_epoch"]), 1)

        out: Dict[str, Any] = {
            "redis_keys": {
                "prior": OI_PRIOR_REDIS_KEY,
                "history_ring": OI_HISTORY_REDIS_KEY,
                "meta": OI_INTERP_META_KEY,
            },
            "ttl_hint_sec": {"prior": PRIOR_TTL_SEC, "history": HISTORY_TTL_SEC, "meta": META_TTL_SEC},
            "meta": meta if isinstance(meta, dict) else None,
            "prior_snapshot_ts": (prior or {}).get("ts") if isinstance(prior, dict) else None,
            "history_frames": hist_len,
            "oldest_frame_age_sec": oldest_age_sec,
            "prior_compact_size": len(str(prior)) if prior is not None else 0,
        }
        try:
            from dashboard.backend.services.oi_phase15_engine import get_phase15_debug, SESSION_COMPARE_KEY
            from dashboard.backend.cache import get as cache_get

            out["phase15"] = get_phase15_debug()
            sc = cache_get(SESSION_COMPARE_KEY)
            out["session_compare_cached"] = sc if isinstance(sc, dict) else None
        except Exception as pe:
            out["phase15"] = {"error": str(pe)}
        return out
    except Exception as exc:
        return {"error": str(exc)}
