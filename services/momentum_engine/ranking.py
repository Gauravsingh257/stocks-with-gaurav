"""
services/momentum_engine/ranking.py
====================================
Phase 5 — the Momentum Quality Score (0..100). One transparent, configurable
number so the strongest leaders fill momentum slots first, with an EXPLAINABLE
component + penalty breakdown attached to every decision.

Weights are config-driven (MOM_W_*) and normalised at use, so tuning never
requires code changes. Two hard penalties (extension) can pull a high scorer
down — the anti-chase discipline.
"""

from __future__ import annotations

from typing import Any

from .config import cfg
from .models import EntrySignal, MomentumQualityScore


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def score(metrics: dict[str, Any], entry: EntrySignal | None,
          discovery_breakout_score: float | None = None,
          sector_score: float = 0.5) -> MomentumQualityScore:
    c = cfg()

    # ── Component signals, each mapped to 0..1 ──
    rs = metrics.get("rs_20d") or 0.0
    c_rs = _clamp01(rs / 20.0)                                   # +20% RS = full marks

    if discovery_breakout_score is not None:
        c_breakout = _clamp01(discovery_breakout_score / 100.0)
    else:
        c_breakout = _clamp01(metrics.get("pos52", 0) / 100.0)  # 52w positioning proxy

    vr = metrics.get("volume_ratio", 0.0)
    c_volume = _clamp01((vr - 1.0) / 2.0)                        # 1x→0, 3x→1

    c_trend = _clamp01(metrics.get("trend_quality", 0.0))

    base_atr = metrics.get("base_atr_pct")
    # Tighter base (lower ATR%) = higher; a 6%+ base scores 0, a ~0% base ~1.
    c_vcp = _clamp01(1.0 - (base_atr / 6.0)) if base_atr is not None else 0.0

    c_sector = _clamp01(sector_score)

    weights = {
        "rs": c["MOM_W_RS"], "breakout": c["MOM_W_BREAKOUT"], "volume": c["MOM_W_VOLUME"],
        "trend_quality": c["MOM_W_TREND_QUALITY"], "vcp_tightness": c["MOM_W_VCP_TIGHTNESS"],
        "sector": c["MOM_W_SECTOR"],
    }
    signals = {
        "rs": c_rs, "breakout": c_breakout, "volume": c_volume,
        "trend_quality": c_trend, "vcp_tightness": c_vcp, "sector": c_sector,
    }
    wsum = sum(weights.values()) or 1.0
    raw = sum(weights[k] / wsum * signals[k] for k in weights) * 100.0

    # ── Penalties (anti-chase) ──
    ext_atr = max(0.0, metrics.get("extension_atr", 0.0))
    ext_penalty = ext_atr * c["MOM_EXTENSION_PENALTY"] if ext_atr > 1.0 else 0.0
    penalties = {"extension": round(ext_penalty, 2)}

    final = max(0.0, min(100.0, raw - ext_penalty))
    components = {k: round(signals[k] * 100.0, 1) for k in signals}
    components["_weighted_raw"] = round(raw, 1)
    return MomentumQualityScore(score=round(final, 1), components=components, penalties=penalties)
