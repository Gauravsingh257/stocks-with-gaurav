"""
services/idea_selector.py

High-conviction stock selection engine with separate SWING and LONGTERM scoring.

SWING criteria  — HTF trend aligned, fresh breakout/BOS, OB/FVG proximity,
                  ATR expansion, target potential 10-20%, RR ≥ 2.5
LONGTERM criteria — weekly/daily bullish structure, accumulation/base breakout,
                    institutional zones, volume accumulation, target ≥ 50%

Scoring model uses technical_factors + fundamental_factors from ranking_engine.
Only top-10 scored candidates are promoted (never exceeds portfolio cap).
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("services.idea_selector")


# ──────────────────────────────────────────────────────────────────────────────
# Scoring weights
# ──────────────────────────────────────────────────────────────────────────────

SWING_WEIGHTS = {
    "trend":            0.20,   # HTF trend strength (1D+4H aligned)
    "momentum":         0.20,   # Fresh breakout / BOS strength
    "breakout":         0.15,   # Proximity to OB/FVG entry
    "mtf_alignment":    0.15,   # Multi-timeframe alignment
    "liquidity":        0.10,   # Liquidity sweep / clean levels
    "volume_expansion": 0.10,   # ATR / volume expansion
    "rr_bonus":         0.10,   # Risk-reward quality bonus
}

LONGTERM_WEIGHTS = {
    "trend":                       0.10,
    "momentum":                    0.10,
    "growth":                      0.20,   # Fundamental growth
    "quality":                     0.15,   # Balance sheet quality
    "institutional_accumulation":  0.15,   # Institutional buying
    "volume_expansion":            0.10,   # Volume accumulation
    "rr_bonus":                    0.10,   # Target upside bonus
    "breakout":                    0.10,   # Base breakout strength
}


# ──────────────────────────────────────────────────────────────────────────────
# Helper — parse JSON fields safely
# ──────────────────────────────────────────────────────────────────────────────

def _parse_json_field(val) -> dict:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _parse_targets(raw) -> list[float]:
    targets = _parse_json_field(raw)
    if isinstance(targets, list):
        return [float(t) for t in targets if t]
    return []


# ──────────────────────────────────────────────────────────────────────────────
# ADAPTIVE WEIGHT COMPUTATION
# ──────────────────────────────────────────────────────────────────────────────

def _get_adaptive_weights(base_weights: dict[str, float], horizon: str) -> dict[str, float]:
    """
    Compute adaptive weights by layering:
      1. Base (static) weights
      2. + Feedback adjustments (from historical win/loss analysis)
      3. + Market regime adjustments (trending/sideways)

    All adjustments are clamped so no weight goes below 0.02 or above 0.40.
    Weights are re-normalized to sum to 1.0 after adjustments.
    """
    from services.feedback_analyzer import get_weight_adjustments
    from services.market_regime import get_regime_adjustments

    weights = dict(base_weights)

    # Layer 1: feedback from historical trades
    feedback_adj = get_weight_adjustments(horizon)
    for k, delta in feedback_adj.items():
        if k in weights:
            weights[k] += delta

    # Layer 2: market regime
    regime_adj = get_regime_adjustments(horizon)
    for k, delta in regime_adj.items():
        if k in weights:
            weights[k] += delta

    # Clamp individual weights
    for k in weights:
        weights[k] = max(0.02, min(0.40, weights[k]))

    # Re-normalize to sum to 1.0
    total = sum(weights.values())
    if total > 0:
        weights = {k: round(v / total, 4) for k, v in weights.items()}

    return weights


# ──────────────────────────────────────────────────────────────────────────────
# SWING SCORING
# ──────────────────────────────────────────────────────────────────────────────

def _score_swing(rec: dict) -> float | None:
    """
    Score a swing recommendation 0-100. Returns None if hard filters fail.

    Hard filters (must pass ALL):
      - confidence ≥ 55
      - RR ≥ 2.5
      - target upside ≥ 10%
      - entry > 0, sl > 0

    Score = adaptive_weighted_factors + rr_bonus + historical_success_bonus
    """
    entry = float(rec.get("entry_price", 0))
    sl = float(rec.get("stop_loss") or 0)
    if entry <= 0 or sl <= 0:
        return None

    confidence = float(rec.get("confidence_score", 0))
    if confidence < 55:
        log.debug("Swing skip %s: confidence %.1f < 55", rec["symbol"], confidence)
        return None

    targets = _parse_targets(rec.get("targets"))
    t_final = targets[-1] if targets else entry * 1.10
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    reward = t_final - entry
    rr = reward / risk

    if rr < 2.5:
        log.debug("Swing skip %s: RR %.2f < 2.5", rec["symbol"], rr)
        return None

    upside_pct = (t_final - entry) / entry * 100
    if upside_pct < 10:
        log.debug("Swing skip %s: upside %.1f%% < 10%%", rec["symbol"], upside_pct)
        return None

    # Adaptive weights (feedback + regime adjusted)
    weights = _get_adaptive_weights(SWING_WEIGHTS, "SWING")

    # Extract factor scores (0-1 range from ranking_engine)
    tf = _parse_json_field(rec.get("technical_factors"))

    score = 0.0
    score += weights.get("trend", 0.20)            * tf.get("trend", 0)            * 100
    score += weights.get("momentum", 0.20)         * tf.get("momentum", 0)         * 100
    score += weights.get("breakout", 0.15)         * tf.get("breakout", 0)         * 100
    score += weights.get("mtf_alignment", 0.15)    * tf.get("mtf_alignment", 0)    * 100
    score += weights.get("liquidity", 0.10)        * tf.get("liquidity", 0)        * 100
    score += weights.get("volume_expansion", 0.10) * tf.get("volume_expansion", 0) * 100

    # RR bonus — scaled: RR 2.5 → 0.5, RR 5+ → 1.0
    rr_norm = min((rr - 2.5) / 2.5, 1.0) * 0.5 + 0.5
    score += weights.get("rr_bonus", 0.10) * rr_norm * 100

    # Historical success bonus (±5 points)
    from services.feedback_analyzer import get_historical_success_bonus
    score += get_historical_success_bonus(rec, "SWING")

    return round(max(0, score), 2)


# ──────────────────────────────────────────────────────────────────────────────
# LONGTERM SCORING
# ──────────────────────────────────────────────────────────────────────────────

def _score_longterm(rec: dict) -> float | None:
    """
    Score a longterm recommendation 0-100. Returns None if hard filters fail.

    Hard filters (must pass ALL):
      - confidence ≥ 50
      - target upside ≥ 50% (from long_term_target or final target vs entry)
      - RR ≥ 2.0
      - entry > 0, sl > 0
    """
    entry = float(rec.get("entry_price", 0))
    sl = float(rec.get("stop_loss") or 0)
    if entry <= 0 or sl <= 0:
        return None

    confidence = float(rec.get("confidence_score", 0))
    if confidence < 50:
        log.debug("LT skip %s: confidence %.1f < 50", rec["symbol"], confidence)
        return None

    # Use long_term_target if available, else fallback to last target
    lt_target = float(rec.get("long_term_target") or 0)
    targets = _parse_targets(rec.get("targets"))
    t_final = lt_target if lt_target > 0 else (targets[-1] if targets else entry * 1.20)

    risk = abs(entry - sl)
    if risk <= 0:
        return None
    reward = t_final - entry
    rr = reward / risk

    if rr < 2.0:
        log.debug("LT skip %s: RR %.2f < 2.0", rec["symbol"], rr)
        return None

    upside_pct = (t_final - entry) / entry * 100
    if upside_pct < 50:
        log.debug("LT skip %s: upside %.1f%% < 50%%", rec["symbol"], upside_pct)
        return None

    # Adaptive weights (feedback + regime adjusted)
    weights = _get_adaptive_weights(LONGTERM_WEIGHTS, "LONGTERM")

    tf = _parse_json_field(rec.get("technical_factors"))
    ff = _parse_json_field(rec.get("fundamental_factors"))

    score = 0.0
    score += weights.get("trend", 0.10)                       * tf.get("trend", 0)                       * 100
    score += weights.get("momentum", 0.10)                    * tf.get("momentum", 0)                    * 100
    score += weights.get("breakout", 0.10)                    * tf.get("breakout", 0)                    * 100
    score += weights.get("volume_expansion", 0.10)            * tf.get("volume_expansion", 0)            * 100
    score += weights.get("growth", 0.20)                      * ff.get("growth", 0)                      * 100
    score += weights.get("quality", 0.15)                     * ff.get("quality", 0)                     * 100
    score += weights.get("institutional_accumulation", 0.15)  * ff.get("institutional_accumulation", 0)  * 100

    # RR bonus — scaled: RR 2 → 0.4, RR 6+ → 1.0
    rr_norm = min((rr - 2.0) / 4.0, 1.0) * 0.6 + 0.4
    score += weights.get("rr_bonus", 0.10) * rr_norm * 100

    # Historical success bonus (±5 points)
    from services.feedback_analyzer import get_historical_success_bonus
    score += get_historical_success_bonus(rec, "LONGTERM")

    return round(max(0, score), 2)


# ──────────────────────────────────────────────────────────────────────────────
# Public API — select_swing_ideas / select_longterm_ideas
# ──────────────────────────────────────────────────────────────────────────────

def _build_idea(rec: dict, horizon: str, score: float) -> dict:
    """Build a portfolio-ready idea dict from a recommendation row."""
    entry = float(rec["entry_price"])
    sl = float(rec.get("stop_loss") or entry * 0.95)
    targets = _parse_targets(rec.get("targets"))
    t1 = targets[0] if targets else None
    t2 = targets[-1] if len(targets) > 1 else t1

    return {
        "symbol": rec["symbol"],
        "horizon": horizon,
        "entry_price": entry,
        "stop_loss": sl,
        "target_1": t1,
        "target_2": t2,
        "confidence_score": float(rec.get("confidence_score", 0)),
        "selection_score": score,
        "reasoning": rec.get("reasoning", ""),
        "recommendation_id": rec.get("id"),
        "scan_cmp": rec.get("scan_cmp"),
    }


def select_swing_ideas(max_picks: int | None = None) -> list[dict]:
    """
    Select top SWING ideas using the swing scoring engine.
    Returns at most max_picks ideas, ranked by score descending.
    """
    from dashboard.backend.db import get_stock_recommendations
    from dashboard.backend.db.portfolio import get_portfolio_counts, get_active_position_by_symbol

    counts = get_portfolio_counts()
    if max_picks is None:
        current = counts.get("swing", 0)
        max_cap = counts.get("swing_max", 10)
        max_picks = max_cap - current

    if max_picks is None or max_picks <= 0:
        log.info("[SwingSelector] Portfolio full — no picks needed")
        return []

    recs = get_stock_recommendations("SWING", limit=50)

    from services.portfolio_risk import pre_promotion_risk_check

    scored: list[tuple[float, dict]] = []
    for rec in recs:
        if get_active_position_by_symbol(rec["symbol"]):
            continue
        s = _score_swing(rec)
        if s is not None:
            scored.append((s, rec))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Apply risk filters on top candidates
    top: list[tuple[float, dict]] = []
    for sc, rec in scored:
        if len(top) >= max_picks:
            break
        ok, reason = pre_promotion_risk_check(rec["symbol"], "SWING")
        if not ok:
            log.info("[SwingSelector] RISK BLOCK %s: %s", rec["symbol"], reason)
            continue
        top.append((sc, rec))

    result = [_build_idea(rec, "SWING", sc) for sc, rec in top]
    log.info("[SwingSelector] %d candidates → %d scored → %d selected (need %d)",
             len(recs), len(scored), len(result), max_picks)
    return result


def select_longterm_ideas(max_picks: int | None = None) -> list[dict]:
    """
    Select top LONGTERM ideas using the longterm scoring engine.
    Filters out noisy intraday SMC signals — only weekly/daily setups pass.
    Returns at most max_picks ideas, ranked by score descending.
    """
    from dashboard.backend.db import get_stock_recommendations
    from dashboard.backend.db.portfolio import get_portfolio_counts, get_active_position_by_symbol

    counts = get_portfolio_counts()
    if max_picks is None:
        current = counts.get("longterm", 0)
        max_cap = counts.get("longterm_max", 10)
        max_picks = max_cap - current

    if max_picks is None or max_picks <= 0:
        log.info("[LongtermSelector] Portfolio full — no picks needed")
        return []

    recs = get_stock_recommendations("LONGTERM", limit=50)

    # Filter out noisy intraday setups — require weekly/daily setup names
    _INTRADAY_NOISE = {"SMC_INTRADAY", "SCALP", "5M_", "15M_"}

    from services.portfolio_risk import pre_promotion_risk_check

    scored: list[tuple[float, dict]] = []
    for rec in recs:
        if get_active_position_by_symbol(rec["symbol"]):
            continue

        setup = (rec.get("setup") or "").upper()
        if any(noise in setup for noise in _INTRADAY_NOISE):
            log.debug("LT skip %s: intraday setup '%s'", rec["symbol"], setup)
            continue

        s = _score_longterm(rec)
        if s is not None:
            scored.append((s, rec))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Apply risk filters on top candidates
    top: list[tuple[float, dict]] = []
    for sc, rec in scored:
        if len(top) >= max_picks:
            break
        ok, reason = pre_promotion_risk_check(rec["symbol"], "LONGTERM")
        if not ok:
            log.info("[LongtermSelector] RISK BLOCK %s: %s", rec["symbol"], reason)
            continue
        top.append((sc, rec))

    result = [_build_idea(rec, "LONGTERM", sc) for sc, rec in top]
    log.info("[LongtermSelector] %d candidates → %d scored → %d selected (need %d)",
             len(recs), len(scored), len(result), max_picks)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Backward-compat wrapper
# ──────────────────────────────────────────────────────────────────────────────

def select_top_ideas(horizon: str, max_picks: int | None = None) -> list[dict]:
    """Dispatch to the appropriate selector based on horizon."""
    horizon = horizon.upper()
    if horizon == "SWING":
        return select_swing_ideas(max_picks)
    elif horizon == "LONGTERM":
        return select_longterm_ideas(max_picks)
    else:
        log.warning("Unknown horizon '%s', defaulting to swing", horizon)
        return select_swing_ideas(max_picks)


def select_and_promote(horizon: str) -> int:
    """Select top ideas and auto-promote them into the portfolio. Returns count promoted."""
    from services.portfolio_manager import promote_to_portfolio

    ideas = select_top_ideas(horizon)
    promoted = 0

    for idea in ideas:
        try:
            promote_to_portfolio(
                symbol=idea["symbol"],
                horizon=idea["horizon"],
                entry_price=idea["entry_price"],
                stop_loss=idea["stop_loss"],
                target_1=idea.get("target_1"),
                target_2=idea.get("target_2"),
                confidence_score=idea.get("confidence_score", 0),
                reasoning=idea.get("reasoning", ""),
                recommendation_id=idea.get("recommendation_id"),
                current_price=idea.get("scan_cmp"),
            )
            promoted += 1
        except ValueError as exc:
            log.debug("Skip promote %s: %s", idea["symbol"], exc)

    log.info("[IdeaSelector] Promoted %d %s ideas to portfolio", promoted, horizon)
    return promoted
