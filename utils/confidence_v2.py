"""
utils/confidence_v2.py

Multi-dimensional confidence (PR3). The legacy confidence is dominated by
collinear momentum signals (trend ≈ momentum ≈ breakout all measure "has gone
up"), so a 62%% number over-weights recent price strength. This blends EIGHT
independent dimensions so the score reflects overall setup quality:

    trend · momentum · smc · volume · sector · regime · risk · freshness

Additive and flag-gated (`CONFIDENCE_V2_ENABLED`, default OFF): callers compute
`confidence_v2` ALONGSIDE the existing `confidence_score` and surface it as an
extra field + breakdown. It does NOT replace selection until you choose to flip
the flag — so shipping it is byte-identical. All weights are env-tunable.
"""

from __future__ import annotations

import os

# The eight independent dimensions and their default weights (sum = 1.0).
_DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 0.15,       # HTF trend quality / structure
    "momentum": 0.15,    # price momentum
    "smc": 0.20,         # SMC structural confluence (OB/FVG/BOS)
    "volume": 0.10,      # volume conviction
    "sector": 0.12,      # sector leadership (relative strength)
    "regime": 0.10,      # alignment with the market regime
    "risk": 0.10,        # risk quality (RR, stop distance)
    "freshness": 0.08,   # entry freshness (not already extended)
}

DIMENSIONS = tuple(_DEFAULT_WEIGHTS.keys())


def confidence_v2_enabled() -> bool:
    return str(os.getenv("CONFIDENCE_V2_ENABLED", "0")).strip().lower() in ("1", "true", "yes", "on")


def _clamp(v, lo: float = 0.0, hi: float = 100.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, v))


def _weight(dim: str) -> float:
    return _envf(f"CONF_W_{dim.upper()}", _DEFAULT_WEIGHTS[dim])


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def compute_confidence_v2(dimensions: dict[str, float]) -> dict:
    """Blend the eight dimension scores (each 0..100) into a composite 0..100.

    Missing dimensions are treated as 0. Weights are re-normalised over the
    dimensions actually provided, so a partial set still yields a sane score
    rather than being silently penalised for absent inputs.

    Returns {composite, breakdown{dim: {score, weight, contribution}}, weights}.
    """
    provided = {d: _clamp(dimensions.get(d)) for d in DIMENSIONS if d in (dimensions or {})}
    if not provided:
        return {"composite": 0.0, "breakdown": {}, "weights": {}}

    raw_w = {d: _weight(d) for d in provided}
    total_w = sum(raw_w.values()) or 1.0
    weights = {d: raw_w[d] / total_w for d in provided}

    breakdown: dict[str, dict] = {}
    composite = 0.0
    for d, score in provided.items():
        contribution = weights[d] * score
        composite += contribution
        breakdown[d] = {
            "score": round(score, 1),
            "weight": round(weights[d], 3),
            "contribution": round(contribution, 2),
        }
    return {
        "composite": round(composite, 2),
        "breakdown": breakdown,
        "weights": {d: round(w, 3) for d, w in weights.items()},
    }


def risk_quality_score(rr: float | None, *, target_rr: float = 3.0) -> float:
    """Map a realised risk-reward into a 0..100 'risk quality' dimension.
    RR 1 → ~20, RR = target → ~90, capped 100. Never raises."""
    try:
        rr = float(rr) if rr is not None else 0.0
    except (TypeError, ValueError):
        rr = 0.0
    if rr <= 0:
        return 0.0
    return round(min(100.0, 20.0 + (rr / max(target_rr, 0.1)) * 70.0), 1)
