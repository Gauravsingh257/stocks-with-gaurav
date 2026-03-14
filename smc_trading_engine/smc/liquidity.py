"""
Liquidity Detection
===================
Detects liquidity pools: equal highs/lows, PDH/PDL sweeps.
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


class LiquidityType(Enum):
    EQUAL_HIGHS = "EQUAL_HIGHS"
    EQUAL_LOWS = "EQUAL_LOWS"
    PDH = "PDH"  # Previous Day High
    PDL = "PDL"  # Previous Day Low
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"


class SweepStatus(Enum):
    PENDING = "PENDING"      # Liquidity present, not yet swept
    SWEPT = "SWEPT"          # Liquidity has been taken
    REJECTED = "REJECTED"    # Swept and price reversed (strong signal)


@dataclass
class LiquidityPool:
    pool_type: str
    price: float
    index: int
    timestamp: pd.Timestamp = None
    status: str = "PENDING"
    sweep_index: int = -1
    sweep_depth: float = 0.0     # How far price went beyond
    atr_ratio: float = 0.0
    quality_score: float = 0.0   # 0-10

    @property
    def is_swept(self) -> bool:
        return self.status in ("SWEPT", "REJECTED")


def detect_equal_highs(df: pd.DataFrame, tolerance_pct: float = 0.001,
                       min_touches: int = 2, lookback: int = 50) -> List[LiquidityPool]:
    """Detect equal highs (buy-side liquidity resting above)."""
    if len(df) < lookback:
        return []
    atr = calculate_atr(df)
    pools = []
    recent = df.tail(lookback)

    for i in range(len(recent) - 1):
        h1 = recent['high'].iloc[i]
        touches = 1
        for j in range(i + 1, len(recent)):
            h2 = recent['high'].iloc[j]
            if abs(h2 - h1) / h1 <= tolerance_pct:
                touches += 1
        if touches >= min_touches:
            idx = len(df) - lookback + i
            ts = df.index[idx] if isinstance(df.index, pd.DatetimeIndex) else None
            q = min(10.0, touches * 2.5)
            pools.append(LiquidityPool(
                LiquidityType.EQUAL_HIGHS.value, h1, idx, ts,
                quality_score=round(q, 1)))
    # Deduplicate close levels
    return _deduplicate_pools(pools, tolerance_pct)


def detect_equal_lows(df: pd.DataFrame, tolerance_pct: float = 0.001,
                      min_touches: int = 2, lookback: int = 50) -> List[LiquidityPool]:
    """Detect equal lows (sell-side liquidity resting below)."""
    if len(df) < lookback:
        return []
    pools = []
    recent = df.tail(lookback)

    for i in range(len(recent) - 1):
        l1 = recent['low'].iloc[i]
        touches = 1
        for j in range(i + 1, len(recent)):
            l2 = recent['low'].iloc[j]
            if abs(l2 - l1) / l1 <= tolerance_pct:
                touches += 1
        if touches >= min_touches:
            idx = len(df) - lookback + i
            ts = df.index[idx] if isinstance(df.index, pd.DatetimeIndex) else None
            q = min(10.0, touches * 2.5)
            pools.append(LiquidityPool(
                LiquidityType.EQUAL_LOWS.value, l1, idx, ts,
                quality_score=round(q, 1)))
    return _deduplicate_pools(pools, tolerance_pct)


def detect_pdh_pdl(df: pd.DataFrame) -> Dict[str, Optional[LiquidityPool]]:
    """Detect Previous Day High and Previous Day Low as liquidity targets."""
    if len(df) < 2:
        return {"PDH": None, "PDL": None}

    result = {"PDH": None, "PDL": None}
    if not isinstance(df.index, pd.DatetimeIndex):
        return result

    dates = df.index.normalize().unique()
    if len(dates) < 2:
        return result

    prev_day = dates[-2]
    prev_data = df[df.index.normalize() == prev_day]
    if len(prev_data) == 0:
        return result

    pdh = prev_data['high'].max()
    pdl = prev_data['low'].min()
    last_idx = len(df) - 1
    ts = df.index[-1]

    result["PDH"] = LiquidityPool(LiquidityType.PDH.value, pdh, last_idx, ts, quality_score=7.0)
    result["PDL"] = LiquidityPool(LiquidityType.PDL.value, pdl, last_idx, ts, quality_score=7.0)
    return result


def detect_liquidity_sweep(df: pd.DataFrame, pool: LiquidityPool,
                           min_sweep_atr: float = 0.1) -> LiquidityPool:
    """Check if a liquidity pool has been swept by recent price action."""
    if pool.is_swept:
        return pool
    atr = calculate_atr(df)
    if atr <= 0:
        return pool

    start = max(pool.index + 1, 0)
    for i in range(start, len(df)):
        if pool.pool_type in (LiquidityType.EQUAL_HIGHS.value, LiquidityType.PDH.value,
                              LiquidityType.SESSION_HIGH.value):
            if df['high'].iloc[i] > pool.price:
                depth = df['high'].iloc[i] - pool.price
                pool.sweep_depth = depth
                pool.atr_ratio = depth / atr
                pool.sweep_index = i
                # If price closed back below — rejection (strongest signal)
                if df['close'].iloc[i] < pool.price:
                    pool.status = SweepStatus.REJECTED.value
                else:
                    pool.status = SweepStatus.SWEPT.value
                break
        elif pool.pool_type in (LiquidityType.EQUAL_LOWS.value, LiquidityType.PDL.value,
                                LiquidityType.SESSION_LOW.value):
            if df['low'].iloc[i] < pool.price:
                depth = pool.price - df['low'].iloc[i]
                pool.sweep_depth = depth
                pool.atr_ratio = depth / atr
                pool.sweep_index = i
                if df['close'].iloc[i] > pool.price:
                    pool.status = SweepStatus.REJECTED.value
                else:
                    pool.status = SweepStatus.SWEPT.value
                break
    return pool


def detect_all_liquidity(df: pd.DataFrame, lookback: int = 50) -> List[LiquidityPool]:
    """Detect all liquidity pools: equal highs/lows + PDH/PDL."""
    pools = []
    pools.extend(detect_equal_highs(df, lookback=lookback))
    pools.extend(detect_equal_lows(df, lookback=lookback))
    pdh_pdl = detect_pdh_pdl(df)
    if pdh_pdl["PDH"]:
        pools.append(pdh_pdl["PDH"])
    if pdh_pdl["PDL"]:
        pools.append(pdh_pdl["PDL"])
    # Check sweeps
    for pool in pools:
        detect_liquidity_sweep(df, pool)
    return pools


def get_sweep_quality(pool: LiquidityPool) -> float:
    """Score sweep quality 0-10. Rejected sweeps are highest quality."""
    if pool.status == SweepStatus.REJECTED.value:
        return min(10.0, pool.quality_score + 3.0)
    elif pool.status == SweepStatus.SWEPT.value:
        return min(10.0, pool.quality_score + 1.0)
    return 0.0


def _deduplicate_pools(pools: List[LiquidityPool], tol: float) -> List[LiquidityPool]:
    """Remove duplicate liquidity pools at similar price levels."""
    if not pools:
        return []
    unique = [pools[0]]
    for p in pools[1:]:
        is_dup = False
        for u in unique:
            if abs(p.price - u.price) / u.price <= tol * 2:
                if p.quality_score > u.quality_score:
                    unique.remove(u)
                    unique.append(p)
                is_dup = True
                break
        if not is_dup:
            unique.append(p)
    return unique
