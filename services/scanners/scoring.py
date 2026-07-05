"""
services/scanners/scoring.py — Confluence quality score + tiering.

Turns a scanner's raw per-symbol metrics into a single transparent
`quality_score` (0–100) and a tier label, so every scanner is ranked by the
same probability-stacking logic that drives "high-potential" picks:

  - momentum (already moving)          - 52-week positioning (near high = breakout)
  - trend stack (close>EMA20>EMA50)    - volume confirmation on the flip bar
  - liquidity (tradeable size)         - proximity to 52-week high (runway/continuation)

The weights live here (one place to tune), and the backtest harness in Phase 4
validates that higher tiers really do out-hit lower tiers before any number is
shown to users. NOTHING here fetches data; it is pure arithmetic on metrics.
"""

from __future__ import annotations

# Liquidity gate (hard): below this 20-bar avg volume we drop the row entirely.
# Tradeable-universe floor — keeps penny/illiquid noise out of results.
MIN_AVG_VOLUME = 100_000

# Score weights (sum = 100). Tuned conservatively; Phase 4 backtest may revise.
_W_MOMENTUM = 25.0
_W_POS52 = 20.0
_W_STACK = 12.0
_W_FLIPVOL = 13.0
_W_LIQUIDITY = 15.0
_W_NEAR_HIGH = 15.0

# Saturation caps so a single huge value can't dominate.
_MOM_CAP = 40.0          # % move that maxes the momentum component
_FLIPVOL_CAP = 3.0       # 3x avg volume maxes the confirmation component
_LIQ_CAP = 5_000_000.0   # avg volume that maxes the liquidity component
_NEAR_HIGH_BAND = 15.0   # within 15% of 52w high earns full proximity credit

# ── Sector Rotation bonus (additive, only for rows flagged is_sector_rotation) ──
# A stock in a strongly-leading sector with hot, positive news is a higher-odds
# continuation than the same setup in a flat sector. This is an ADD-ON: rows
# without the marker (e.g. the Supertrend scanner) get exactly zero, so their
# scores are byte-identical to before.
_W_SECTOR_LEAD = 8.0     # sector relative strength vs NIFTY
_W_NEWS_HEAT = 4.0       # coverage intensity (GDELT article volume)
_W_NEWS_TONE = 4.0       # positive news tone
_SECTOR_REL_CAP = 6.0    # sector rel20 (%) that maxes the lead component
_NEWS_TONE_CAP = 4.0     # GDELT tone that maxes the tone component


def passes_gates(metrics: dict) -> bool:
    """Hard gates: reject illiquid / structurally invalid rows."""
    if not isinstance(metrics, dict):
        return False
    if float(metrics.get("avg_vol") or 0) < MIN_AVG_VOLUME:
        return False
    if float(metrics.get("close") or 0) <= 0:
        return False
    return True


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sector_rotation_bonus(metrics: dict) -> float:
    """Additive bonus for Sector Rotation rows. Zero for every other scanner."""
    if not metrics.get("is_sector_rotation"):
        return 0.0
    b = 0.0
    rel20 = float(metrics.get("sector_rel20_pct") or 0.0)   # sector vs NIFTY (%)
    b += _clamp(rel20, 0.0, _SECTOR_REL_CAP) / _SECTOR_REL_CAP * _W_SECTOR_LEAD
    heat = metrics.get("news_heat")
    if heat is not None:
        b += _clamp(float(heat), 0.0, 1.0) * _W_NEWS_HEAT
    tone = metrics.get("news_tone")
    if tone is not None:
        b += _clamp(float(tone), 0.0, _NEWS_TONE_CAP) / _NEWS_TONE_CAP * _W_NEWS_TONE
    return b


def quality_score(metrics: dict) -> float:
    """Composite 0–100 score from raw metrics. Higher = stronger continuation odds."""
    mom = float(metrics.get("mom_long_pct") or 0.0)          # ~1-month momentum
    pos52 = float(metrics.get("pos52") or 0.0)
    stack = bool(metrics.get("stack"))
    flipvol = float(metrics.get("flip_vol_x") or 0.0)
    avgvol = float(metrics.get("avg_vol") or 0.0)
    from_high = abs(float(metrics.get("from_high_pct") or 0.0))

    s = 0.0
    s += _clamp(mom, 0.0, _MOM_CAP) / _MOM_CAP * _W_MOMENTUM
    s += _clamp(pos52, 0.0, 100.0) / 100.0 * _W_POS52
    s += (_W_STACK if stack else 0.0)
    s += _clamp(flipvol, 0.0, _FLIPVOL_CAP) / _FLIPVOL_CAP * _W_FLIPVOL
    s += _clamp(avgvol, 0.0, _LIQ_CAP) / _LIQ_CAP * _W_LIQUIDITY
    s += _clamp(_NEAR_HIGH_BAND - from_high, 0.0, _NEAR_HIGH_BAND) / _NEAR_HIGH_BAND * _W_NEAR_HIGH
    s += _sector_rotation_bonus(metrics)
    return round(_clamp(s, 0.0, 100.0), 1)


def tier_of(score: float, metrics: dict) -> str:
    """Bucket a row for the UI. Quality = breakout-grade; Momentum = runway;
    Speculative = weaker structure / far from high."""
    near_high = abs(float(metrics.get("from_high_pct") or 0.0)) <= 8.0
    stack = bool(metrics.get("stack"))
    if score >= 70 and stack and near_high:
        return "quality"
    if score >= 55:
        return "momentum"
    return "speculative"


def score_and_tier(metrics: dict) -> dict:
    """Return a copy of metrics enriched with quality_score + tier."""
    out = dict(metrics)
    sc = quality_score(metrics)
    out["quality_score"] = sc
    out["tier"] = tier_of(sc, metrics)
    return out
