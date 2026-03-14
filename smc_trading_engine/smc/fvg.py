"""
Fair Value Gap (FVG) Detection
==============================
Detects 3-candle imbalance zones per SMC methodology.
No repainting. Pandas-based, backtest-friendly.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum
import logging

from smc_trading_engine.smc.market_structure import calculate_atr

logger = logging.getLogger(__name__)


class FVGType(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class FVGStatus(Enum):
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"


@dataclass
class FairValueGap:
    fvg_type: str
    high: float
    low: float
    index: int
    timestamp: pd.Timestamp = None
    gap_size: float = 0.0
    atr_ratio: float = 0.0
    status: str = "ACTIVE"
    middle_body: float = 0.0
    is_strong: bool = False
    htf_aligned: bool = False
    quality_score: float = 0.0

    @property
    def mid_price(self) -> float:
        return (self.high + self.low) / 2

    @property
    def range_size(self) -> float:
        return self.high - self.low


def detect_fvg(df: pd.DataFrame, direction: str = None, lookback: int = 30,
               min_gap_atr_ratio: float = 0.1, min_body_atr_ratio: float = 0.5) -> List[FairValueGap]:
    """Detect Fair Value Gaps. Bullish: C1.high < C3.low. Bearish: C1.low > C3.high."""
    if len(df) < 5:
        return []
    atr = calculate_atr(df)
    if atr <= 0:
        return []

    fvgs = []
    scan_start = max(0, len(df) - lookback)

    for i in range(scan_start, len(df) - 2):
        c1, c2, c3 = df.iloc[i], df.iloc[i + 1], df.iloc[i + 2]
        c2_body = abs(c2['close'] - c2['open'])
        if c2_body < atr * min_body_atr_ratio:
            continue
        ts = df.index[i + 1] if isinstance(df.index, pd.DatetimeIndex) else None

        # Bullish FVG
        if (direction is None or direction == "BULLISH") and c3['low'] > c1['high']:
            gap = c3['low'] - c1['high']
            ratio = gap / atr
            if ratio >= min_gap_atr_ratio:
                strong = (c2['close'] > c2['open']) and (c2_body > atr * 0.8)
                q = _fvg_quality(gap, c2_body, atr, strong)
                fvgs.append(FairValueGap(FVGType.BULLISH.value, c3['low'], c1['high'],
                                         i + 1, ts, gap, ratio, middle_body=c2_body,
                                         is_strong=strong, quality_score=q))

        # Bearish FVG
        if (direction is None or direction == "BEARISH") and c1['low'] > c3['high']:
            gap = c1['low'] - c3['high']
            ratio = gap / atr
            if ratio >= min_gap_atr_ratio:
                strong = (c2['close'] < c2['open']) and (c2_body > atr * 0.8)
                q = _fvg_quality(gap, c2_body, atr, strong)
                fvgs.append(FairValueGap(FVGType.BEARISH.value, c1['low'], c3['high'],
                                         i + 1, ts, gap, ratio, middle_body=c2_body,
                                         is_strong=strong, quality_score=q))
    return fvgs


def _fvg_quality(gap: float, body: float, atr: float, strong: bool) -> float:
    s = min(4.0, (gap / atr) * 4.0) + min(3.0, (body / atr) * 2.0)
    if strong:
        s += 3.0
    return round(max(0.0, min(10.0, s)), 1)


def is_price_in_fvg(price: float, fvg: FairValueGap) -> bool:
    return fvg.low <= price <= fvg.high


def update_fvg_status(fvgs: List[FairValueGap], df: pd.DataFrame) -> List[FairValueGap]:
    """Update FVG fill status based on price action."""
    for fvg in fvgs:
        if fvg.status == FVGStatus.FILLED.value:
            continue
        for i in range(fvg.index + 2, len(df)):
            if fvg.fvg_type == FVGType.BULLISH.value:
                if df['low'].iloc[i] <= fvg.low:
                    fvg.status = FVGStatus.FILLED.value
                    break
                elif df['low'].iloc[i] <= fvg.mid_price:
                    fvg.status = FVGStatus.PARTIAL.value
            else:
                if df['high'].iloc[i] >= fvg.high:
                    fvg.status = FVGStatus.FILLED.value
                    break
                elif df['high'].iloc[i] >= fvg.mid_price:
                    fvg.status = FVGStatus.PARTIAL.value
    return fvgs


def get_nearest_fvg(fvgs: List[FairValueGap], price: float, direction: str) -> Optional[FairValueGap]:
    """Find nearest active FVG to current price (including price inside zone)."""
    active = [f for f in fvgs if f.status in ("ACTIVE", "PARTIAL")]
    if direction == "BULLISH":
        c = [f for f in active if f.fvg_type == "BULLISH" and f.low <= price]
        return min(c, key=lambda f: abs(price - (f.high + f.low) / 2)) if c else None
    elif direction == "BEARISH":
        c = [f for f in active if f.fvg_type == "BEARISH" and f.high >= price]
        return min(c, key=lambda f: abs(price - (f.high + f.low) / 2)) if c else None
    return None
