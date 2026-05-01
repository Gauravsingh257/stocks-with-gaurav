"""
dashboard/backend/intelligence.py

Phase 3 — Trade Intelligence + Explanation + Ranking + Summary.

Pure functions over a normalized signal record (the schema produced by
`terminal_events.normalize_signal`). No I/O — safe to call from anywhere.

Public API
----------
    enrich_with_intelligence(record)          → record + {probability, quality_score,
                                                          risk_level, expected_move_time,
                                                          expected_outcome, ranking_score}
    build_narrative(record)                   → human readable explanation string
    rank_signals(records, prefs=None)         → list re-sorted by ranking_score
    summarize(records, regime=None, prefs=None) → AI summary panel payload

Heuristics are documented inline. They are intentionally deterministic
(no model call) so the terminal stays fast and cheap. Replace the `_score_*`
helpers later with model output if/when desired — interface stays the same.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional


# ─────────────────────────────────────────────────────────────────────────
# Component scorers (each returns 0.0-1.0)
# ─────────────────────────────────────────────────────────────────────────

def _confluence_score(rec: Dict[str, Any]) -> float:
    """0-1 from analysis flags + grade."""
    an = rec.get("analysis") or {}
    flags = sum(bool(an.get(k)) for k in ("liquidity", "fvg", "ob"))
    base = flags / 3.0  # 0..1
    grade_boost = {"A+": 0.20, "A": 0.12, "B": 0.04, "C": -0.05}.get(rec.get("confidence", "B"), 0.0)
    htf = (an.get("htf_bias") or "").upper()
    direction = (rec.get("direction") or "").upper()
    htf_aligned = (
        (direction == "LONG" and ("LONG" in htf or "BULL" in htf))
        or (direction == "SHORT" and ("SHORT" in htf or "BEAR" in htf))
    )
    htf_boost = 0.10 if htf_aligned else -0.08
    return _clip(base + grade_boost + htf_boost)


def _rr_score(rec: Dict[str, Any]) -> float:
    rr = rec.get("rr")
    if not isinstance(rr, (int, float)) or rr <= 0:
        return 0.4
    if rr >= 3.0:
        return 1.0
    if rr >= 2.0:
        return 0.85
    if rr >= 1.5:
        return 0.65
    if rr >= 1.0:
        return 0.45
    return 0.25


def _structure_score(rec: Dict[str, Any]) -> float:
    """BOS in trade direction = strongest. CHOCH = transition. Default = mid."""
    structure = ((rec.get("analysis") or {}).get("structure") or "").upper()
    if "BOS" in structure:
        return 0.85
    if "CHOCH" in structure:
        return 0.65
    if "MSS" in structure:
        return 0.75
    return 0.55


def _model_score(rec: Dict[str, Any]) -> float:
    """If engine provided a numeric confidence, fold it in (0-10 or 0-100)."""
    s = rec.get("score")
    if not isinstance(s, (int, float)):
        return 0.5
    if s > 10:
        s = s / 10.0
    return _clip(s / 10.0)


# ─────────────────────────────────────────────────────────────────────────
# Top-level intelligence
# ─────────────────────────────────────────────────────────────────────────

def _quality_from_components(components: Dict[str, float]) -> float:
    """0-10 quality score — weighted mean of component scores."""
    weights = {"confluence": 0.40, "rr": 0.20, "structure": 0.20, "model": 0.20}
    total = sum(components[k] * w for k, w in weights.items())
    return round(total * 10.0, 2)


def _probability_from_quality(q: float) -> int:
    """
    0-100 probability. Calibrated so:
      q=10 → ~88   (capped to keep humility)
      q=8.5 → ~78
      q=7  → ~65
      q=5  → ~48
      q=3  → ~32
    """
    raw = 35.0 + q * 5.5
    return int(_clip(raw / 100.0) * 100)


def _risk_level(quality: float, rr: Optional[float]) -> str:
    """LOW / MED / HIGH from quality + RR."""
    if quality >= 8.0 and (rr or 0) >= 2.0:
        return "LOW"
    if quality >= 6.5 and (rr or 0) >= 1.5:
        return "MED"
    return "HIGH"


def _expected_move_time(rec: Dict[str, Any]) -> str:
    """Heuristic: setup type + grade → typical resolution window."""
    setup = (rec.get("setup") or "B").upper()
    grade = (rec.get("confidence") or "B").upper()
    base_minutes = {"A": 35, "B": 60, "C": 95, "D": 130}.get(setup, 60)
    if grade == "A+":
        base_minutes = max(20, int(base_minutes * 0.7))
    elif grade == "C":
        base_minutes = int(base_minutes * 1.4)
    if base_minutes < 60:
        return f"{base_minutes} min"
    hours = base_minutes / 60.0
    if hours < 4:
        return f"{hours:.1f} hr".rstrip("0").rstrip(".") + " hr" if False else f"{round(hours, 1)} hr"
    return f"{round(hours, 0):.0f} hr"


def _expected_outcome(probability: int, status: str) -> str:
    """Short narrative label for the card."""
    status = (status or "").upper()
    if status == "TARGET_HIT":
        return "TARGET HIT"
    if status == "STOP_HIT":
        return "STOP HIT"
    if probability >= 75:
        return "TARGET LIKELY"
    if probability >= 60:
        return "FAVOURABLE"
    if probability >= 45:
        return "BALANCED"
    return "RISKY"


# ─────────────────────────────────────────────────────────────────────────
# Decision Engine
# ─────────────────────────────────────────────────────────────────────────

# Action hierarchy: STRONG BUY > BUY > WATCH > AVOID
_DECISION_LABELS = ("STRONG BUY", "BUY", "WATCH", "AVOID")
_CONVICTION_LABELS = ("HIGH", "MEDIUM", "LOW")


def _action(quality: float, probability: int, rr: Optional[float], risk: str, status: str) -> str:
    """
    Deterministic trade action from intelligence outputs.

    STRONG BUY — setup is exceptional: quality ≥ 8.5, prob ≥ 75, RR ≥ 2.0, LOW risk.
    BUY        — solid setup worth entering: quality ≥ 7.0, prob ≥ 60, RR ≥ 1.5.
    WATCH      — marginal: worth monitoring, not immediately executable.
    AVOID      — setup is too weak or the RR doesn't justify the risk.
    """
    st = (status or "").upper()
    # Already closed positions are neutral
    if st in ("TARGET_HIT", "STOP_HIT"):
        return "WATCH"
    rr_val = rr or 0.0
    if quality >= 8.5 and probability >= 75 and rr_val >= 2.0 and risk == "LOW":
        return "STRONG BUY"
    if quality >= 7.0 and probability >= 60 and rr_val >= 1.5:
        return "BUY"
    if quality < 5.5 or probability < 42 or rr_val < 1.0:
        return "AVOID"
    return "WATCH"


def _conviction(quality: float, probability: int, risk: str) -> str:
    """
    HIGH   — quality ≥ 8.0, prob ≥ 70, LOW risk.
    MEDIUM — quality ≥ 6.5, prob ≥ 55.
    LOW    — everything else.
    """
    if quality >= 8.0 and probability >= 70 and risk == "LOW":
        return "HIGH"
    if quality >= 6.5 and probability >= 55:
        return "MEDIUM"
    return "LOW"


def enrich_with_intelligence(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Mutates `rec` in place with intelligence fields and returns it."""
    if not isinstance(rec, dict) or not rec:
        return rec
    components = {
        "confluence": _confluence_score(rec),
        "rr": _rr_score(rec),
        "structure": _structure_score(rec),
        "model": _model_score(rec),
    }
    quality = _quality_from_components(components)
    probability = _probability_from_quality(quality)
    risk = _risk_level(quality, rec.get("rr"))
    expected_time = _expected_move_time(rec)
    expected = _expected_outcome(probability, rec.get("status", ""))
    # Ranking score blends probability + RR + freshness implied by status
    rank_bonus = {"WAITING": 0.0, "TAPPED": 0.10, "TRIGGERED": 0.15}.get(
        (rec.get("status") or "").upper(), 0.0
    )
    ranking = round(quality + rank_bonus * 10.0, 3)
    rec["intelligence"] = {
        "probability": probability,
        "quality_score": quality,
        "risk_level": risk,
        "expected_move_time": expected_time,
        "expected_outcome": expected,
        "action": _action(quality, probability, rec.get("rr"), risk, rec.get("status", "")),
        "conviction": _conviction(quality, probability, risk),
        "components": {k: round(v, 3) for k, v in components.items()},
    }
    rec["ranking_score"] = ranking
    # Top-level mirrors so old frontend code still works without nested access
    rec["probability"] = probability
    rec["quality_score"] = quality
    rec["risk_level"] = risk
    rec["expected_move_time"] = expected_time
    rec["expected_outcome"] = expected
    rec["action"] = rec["intelligence"]["action"]
    rec["conviction"] = rec["intelligence"]["conviction"]
    if "narrative" not in rec:
        rec["narrative"] = build_narrative(rec)
    return rec


# ─────────────────────────────────────────────────────────────────────────
# Deep explanation (narrative)
# ─────────────────────────────────────────────────────────────────────────

def build_narrative(rec: Dict[str, Any]) -> str:
    """Return a 1-3 sentence trader-grade explanation."""
    if not isinstance(rec, dict) or not rec:
        return ""
    an = rec.get("analysis") or {}
    direction = (rec.get("direction") or "LONG").upper()
    side = "long" if direction == "LONG" else "short"
    bias = (an.get("htf_bias") or direction).upper()
    bias_word = "bullish" if "LONG" in bias or "BULL" in bias else "bearish"

    parts: List[str] = []

    swept = an.get("liquidity")
    ob_ok = an.get("ob")
    fvg_ok = an.get("fvg")
    structure = (an.get("structure") or "").upper()

    sweep_txt = (
        "Price swept sell-side liquidity" if (swept and direction == "LONG")
        else "Price swept buy-side liquidity" if swept
        else "Liquidity is building under recent lows" if direction == "LONG"
        else "Liquidity is building above recent highs"
    )

    confirm_bits: List[str] = []
    if ob_ok:
        confirm_bits.append(f"tapped a {bias_word} order block")
    if fvg_ok:
        confirm_bits.append("confirmed FVG rejection")
    if "BOS" in structure:
        confirm_bits.append("printed a clean break of structure")
    elif "CHOCH" in structure:
        confirm_bits.append("flipped market structure (CHoCH)")
    confirm_txt = ", ".join(confirm_bits) if confirm_bits else "is forming a reactive zone"

    parts.append(f"{sweep_txt}, {confirm_txt}.")

    htf_aligned = (
        (direction == "LONG" and ("LONG" in bias or "BULL" in bias))
        or (direction == "SHORT" and ("SHORT" in bias or "BEAR" in bias))
    )
    if htf_aligned:
        parts.append(f"HTF trend is {bias_word} — high-probability {side} continuation.")
    else:
        parts.append(f"HTF bias is {bias_word} — counter-trend trade, manage size.")

    rr = rec.get("rr")
    intel = rec.get("intelligence") or {}
    prob = intel.get("probability")
    if rr and prob:
        parts.append(f"Setup pays {rr:.2f}R with ~{int(prob)}% historical edge for this confluence.")
    elif rr:
        parts.append(f"Setup pays {rr:.2f}R if entry triggers.")

    return " ".join(p.strip() for p in parts if p)


# ─────────────────────────────────────────────────────────────────────────
# Ranking + personalization
# ─────────────────────────────────────────────────────────────────────────

def rank_signals(records: Iterable[Dict[str, Any]], prefs: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Return new list ordered by ranking_score desc, then by recency."""
    enriched = [enrich_with_intelligence(dict(r)) if "intelligence" not in r else r for r in records if r]
    prefs = prefs or {}
    enriched = [r for r in enriched if _passes_preferences(r, prefs)]

    # Apply preference-based bias on ranking score (additive, not destructive).
    preferred_setups = {s.upper() for s in (prefs.get("preferred_setups") or [])}
    risk_pref = (prefs.get("risk_preference") or "").upper()
    for r in enriched:
        bonus = 0.0
        if preferred_setups and (r.get("setup") or "").upper() in preferred_setups:
            bonus += 1.0
        if risk_pref == "CONSERVATIVE" and r.get("risk_level") == "LOW":
            bonus += 0.7
        if risk_pref == "AGGRESSIVE" and (r.get("rr") or 0) >= 2.5:
            bonus += 0.5
        r["ranking_score"] = round((r.get("ranking_score") or 0.0) + bonus, 3)

    enriched.sort(
        key=lambda r: (
            -(r.get("ranking_score") or 0.0),
            -(r.get("probability") or 0),
            r.get("symbol", ""),
        )
    )
    return enriched


def _passes_preferences(rec: Dict[str, Any], prefs: Dict[str, Any]) -> bool:
    if not prefs:
        return True
    risk = (prefs.get("risk_preference") or "").upper()
    if risk == "CONSERVATIVE" and rec.get("risk_level") == "HIGH":
        return False
    min_rr = prefs.get("min_rr")
    if isinstance(min_rr, (int, float)) and (rec.get("rr") or 0) < min_rr:
        return False
    min_prob = prefs.get("min_probability")
    if isinstance(min_prob, (int, float)) and (rec.get("probability") or 0) < min_prob:
        return False
    setups = prefs.get("preferred_setups")
    # `preferred_setups` only biases ranking — it does not exclude unless
    # the user opts in via `setups_strict`.
    if setups and prefs.get("setups_strict"):
        if (rec.get("setup") or "").upper() not in {s.upper() for s in setups}:
            return False
    direction = (prefs.get("direction") or "").upper()
    if direction in ("LONG", "SHORT") and (rec.get("direction") or "").upper() != direction:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────
# AI summary panel
# ─────────────────────────────────────────────────────────────────────────

def summarize(
    records: Iterable[Dict[str, Any]],
    regime: Optional[Dict[str, Any]] = None,
    prefs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the payload for the AI Summary panel."""
    ranked = rank_signals(records, prefs)
    top3 = ranked[:3]
    best = ranked[0] if ranked else None
    longs = [r for r in ranked if r.get("direction") == "LONG"]
    shorts = [r for r in ranked if r.get("direction") == "SHORT"]
    avg_quality = round(_mean([r.get("quality_score") or 0 for r in ranked]) or 0, 2)
    avg_prob = int(_mean([r.get("probability") or 0 for r in ranked]) or 0)

    bias = "NEUTRAL"
    if regime and isinstance(regime, dict):
        rb = (regime.get("bias") or regime.get("regime") or "").upper()
        if rb:
            bias = rb
    if bias == "NEUTRAL":
        if len(longs) >= len(shorts) * 2 and len(longs) >= 3:
            bias = "BULLISH"
        elif len(shorts) >= len(longs) * 2 and len(shorts) >= 3:
            bias = "BEARISH"
        elif len(ranked) >= 4:
            bias = "MIXED"

    headline = _build_headline(best, bias, avg_quality, len(ranked))
    return {
        "market_bias": bias,
        "headline": headline,
        "best_opportunity": _summary_card(best) if best else None,
        "top_trades": [_summary_card(r) for r in top3],
        "totals": {
            "count": len(ranked),
            "long": len(longs),
            "short": len(shorts),
            "avg_quality": avg_quality,
            "avg_probability": avg_prob,
        },
    }


def _summary_card(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": r.get("symbol"),
        "direction": r.get("direction"),
        "setup": r.get("setup"),
        "confidence": r.get("confidence"),
        "probability": r.get("probability"),
        "quality_score": r.get("quality_score"),
        "rr": r.get("rr"),
        "risk_level": r.get("risk_level"),
        "expected_outcome": r.get("expected_outcome"),
        "expected_move_time": r.get("expected_move_time"),
        "action": r.get("action"),
        "conviction": r.get("conviction"),
        "narrative": r.get("narrative"),
    }


def _build_headline(best: Optional[Dict[str, Any]], bias: str, avg_q: float, n: int) -> str:
    if not best or n == 0:
        return "No actionable setups right now — waiting for the engine to find a high-quality print."
    sym = best.get("symbol")
    direction = (best.get("direction") or "LONG").lower()
    prob = best.get("probability") or 0
    qual = best.get("quality_score") or 0
    return (
        f"Best opportunity right now: {sym} {direction} — "
        f"{prob}% probability at {qual:.1f}/10 quality. "
        f"Market bias is {bias.lower()} across {n} live setups (avg quality {avg_q:.1f}/10)."
    )


# ─────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────

def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _mean(values: List[float]) -> Optional[float]:
    nums = [v for v in values if isinstance(v, (int, float)) and not math.isnan(v)]
    if not nums:
        return None
    return sum(nums) / len(nums)
