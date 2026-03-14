"""
Volatility Model Module
=======================
Classifies the current volatility regime using ATR(14), India VIX,
previous day range, and the gap size.

Deterministic rules — no ML.
"""

import logging
from typing import Dict, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ─── THRESHOLDS ────────────────────────────────────────
VIX_HIGH_THRESHOLD = 20.0       # VIX above this → elevated volatility
VIX_LOW_THRESHOLD = 14.0        # VIX below this → compressed volatility
VIX_RISING_PCT = 10.0           # VIX % increase considered "rising"
GAP_HIGH_ATR_RATIO = 0.8        # Gap > 0.8×ATR → trend probability
GAP_LOW_ATR_RATIO = 0.3         # Gap < 0.3×ATR → rotational


def compute_atr(
    ohlc_df: pd.DataFrame,
    period: int = 14,
) -> float:
    """
    Compute Average True Range from an OHLC DataFrame.

    Expects columns: high, low, close
    Uses the last `period` bars.

    Returns:
        ATR value as float. Returns 0.0 if insufficient data.
    """
    if ohlc_df is None or len(ohlc_df) < period + 1:
        return 0.0

    df = ohlc_df.tail(period + 1).copy()

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    tr_values = []
    for i in range(1, len(df)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        tr_values.append(tr)

    if not tr_values:
        return 0.0

    return round(float(np.mean(tr_values[-period:])), 2)


def compute_previous_day_range(
    daily_df: pd.DataFrame,
) -> float:
    """
    Compute the previous trading day's range (high - low).

    Expects columns: high, low (last row = previous day).

    Returns:
        Range value as float. Returns 0.0 if data is empty.
    """
    if daily_df is None or daily_df.empty:
        return 0.0

    last = daily_df.iloc[-1]
    return round(float(last["high"] - last["low"]), 2)


def compute_volatility_regime(
    atr_14: float = 0.0,
    india_vix: float = 15.0,
    india_vix_prev: float = 15.0,
    gap_points: float = 0.0,
    prev_day_range: float = 0.0,
) -> Dict:
    """
    Classify volatility regime using deterministic rules.

    Inputs:
        atr_14:         ATR(14) in points
        india_vix:      Current India VIX value
        india_vix_prev: Previous day India VIX (for trend detection)
        gap_points:     Absolute gap size in points (open - prev close)
        prev_day_range: Previous day (high - low) in points

    Rules:
        Gap > 0.8 × ATR              → HIGH (trend probability)
        VIX > 20 or VIX rising >10%  → HIGH (expansion likely)
        VIX < 14 AND gap < 0.3 × ATR → LOW  (rotational)
        Otherwise                     → NORMAL

    Score (0-100):
        0   = extremely compressed
        50  = normal
        100 = extremely expanded

    Returns:
        {
            "volatility_regime": "HIGH" | "NORMAL" | "LOW",
            "volatility_score": int (0-100),
            "atr_14": float,
            "india_vix": float,
            "vix_change_pct": float,
            "gap_atr_ratio": float,
            "vix_rising": bool,
        }
    """
    # ── VIX change ──
    if india_vix_prev > 0:
        vix_change_pct = ((india_vix - india_vix_prev) / india_vix_prev) * 100.0
    else:
        vix_change_pct = 0.0
    vix_rising = vix_change_pct >= VIX_RISING_PCT

    # ── Gap / ATR ratio ──
    gap_abs = abs(gap_points)
    gap_atr_ratio = (gap_abs / atr_14) if atr_14 > 0 else 0.0

    # ── Classification ──
    if gap_atr_ratio > GAP_HIGH_ATR_RATIO:
        regime = "HIGH"
    elif india_vix >= VIX_HIGH_THRESHOLD or vix_rising:
        regime = "HIGH"
    elif india_vix <= VIX_LOW_THRESHOLD and gap_atr_ratio < GAP_LOW_ATR_RATIO:
        regime = "LOW"
    else:
        regime = "NORMAL"

    # ── Score (0-100) ──
    # VIX component (40%) — map VIX 8..30 → 0..100
    vix_clamped = max(8.0, min(30.0, india_vix))
    vix_score = ((vix_clamped - 8.0) / (30.0 - 8.0)) * 100.0

    # Gap/ATR component (35%) — map ratio 0..2 → 0..100
    gap_ratio_clamped = max(0.0, min(2.0, gap_atr_ratio))
    gap_score = (gap_ratio_clamped / 2.0) * 100.0

    # VIX momentum component (25%) — map change -20..+20 → 0..100
    vix_mom_clamped = max(-20.0, min(20.0, vix_change_pct))
    vix_mom_score = ((vix_mom_clamped + 20.0) / 40.0) * 100.0

    volatility_score = int(round(
        vix_score * 0.40 + gap_score * 0.35 + vix_mom_score * 0.25
    ))
    volatility_score = max(0, min(100, volatility_score))

    result = {
        "volatility_regime": regime,
        "volatility_score": volatility_score,
        "atr_14": atr_14,
        "india_vix": india_vix,
        "vix_change_pct": round(vix_change_pct, 2),
        "gap_atr_ratio": round(gap_atr_ratio, 4),
        "vix_rising": vix_rising,
    }

    logger.info(
        f"[VOL] regime={regime} score={volatility_score} "
        f"VIX={india_vix} VIX_chg={vix_change_pct:.1f}% "
        f"gap/ATR={gap_atr_ratio:.2f}"
    )

    return result
