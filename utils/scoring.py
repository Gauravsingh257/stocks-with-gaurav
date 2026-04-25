"""
utils/scoring.py

Composite Opportunity Score (0–100) used across the Research stack.

Single source of truth for blending:
    - trend strength
    - volume conviction
    - price momentum
    - SMC structural quality

Formula (per project spec):

    score = 0.20 * trend
          + 0.20 * volume
          + 0.30 * momentum
          + 0.30 * smc

Each input must already be 0..100. Output is clamped to 0..100.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ScoreBreakdown:
    trend: float
    volume: float
    momentum: float
    smc: float
    composite: float

    def to_dict(self) -> dict:
        return {
            "trend": round(self.trend, 2),
            "volume": round(self.volume, 2),
            "momentum": round(self.momentum, 2),
            "smc": round(self.smc, 2),
            "composite": round(self.composite, 2),
        }


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if v is None:
        return 0.0
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, v))


def composite_score(
    trend: float,
    volume: float,
    momentum: float,
    smc: float,
) -> ScoreBreakdown:
    """
    Compute the project-standard composite opportunity score.

    All inputs are clamped to [0, 100] before blending. A missing component
    (None or non-numeric) is treated as 0 so callers can pass partial info
    without exploding.
    """
    t = _clamp(trend)
    v = _clamp(volume)
    m = _clamp(momentum)
    s = _clamp(smc)
    composite = round(0.20 * t + 0.20 * v + 0.30 * m + 0.30 * s, 2)
    return ScoreBreakdown(trend=t, volume=v, momentum=m, smc=s, composite=composite)


def score_from_discovery(discovery: dict, smc: float = 0.0) -> ScoreBreakdown:
    """
    Convenience: build composite from a `DiscoveryCandidate.to_dict()` payload.

    `discovery` keys used: momentum_score, volume_score, breakout_score.
    `smc` defaults to 0 — pass the SMC quality score (0..100) when available.
    """
    momentum = discovery.get("momentum_score", 0.0)
    volume = discovery.get("volume_score", 0.0)
    # We treat breakout proximity as the "trend" signal — a stock near or at
    # 52W highs is in confirmed uptrend.
    trend = discovery.get("breakout_score", 0.0)
    return composite_score(trend=trend, volume=volume, momentum=momentum, smc=smc)
