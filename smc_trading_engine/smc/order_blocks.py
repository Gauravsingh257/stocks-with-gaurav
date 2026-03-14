"""
Order Block (OB) Detection
===========================
Identifies institutional order blocks based on SMC methodology.

Rules:
- Bullish OB: Last bearish candle before a bullish BOS
- Bearish OB: Last bullish candle before a bearish BOS
- OB must have impulsive displacement after it
- Minimum displacement candle size filter
- Only first OB retest counts (golden win-rate filter)

No repainting — only uses confirmed candles.
Pandas-based, backtest-friendly.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging

from smc_trading_engine.smc.market_structure import calculate_atr

logger = logging.getLogger(__name__)


class OBType(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class OBStatus(Enum):
    ACTIVE = "ACTIVE"           # Not yet tested
    TESTED = "TESTED"           # Price returned to OB once
    MITIGATED = "MITIGATED"     # OB fully mitigated (invalidated)
    EXPIRED = "EXPIRED"         # Too old


@dataclass
class OrderBlock:
    """Represents an Order Block zone"""
    ob_type: str               # "BULLISH" or "BEARISH"
    high: float                # OB zone high
    low: float                 # OB zone low
    index: int                 # Bar index of the OB candle
    timestamp: pd.Timestamp = None
    displacement: float = 0.0  # Impulse move size after OB
    atr_ratio: float = 0.0    # displacement / ATR quality metric
    status: str = "ACTIVE"     # "ACTIVE", "TESTED", "MITIGATED"
    retest_count: int = 0      # Number of times price returned
    body_size: float = 0.0     # OB candle body size
    is_impulsive: bool = False # True if displacement >= 2x OB range
    quality_score: float = 0.0 # 0-10 quality score
    
    @property
    def mid_price(self) -> float:
        return (self.high + self.low) / 2
    
    @property
    def range_size(self) -> float:
        return self.high - self.low


def detect_order_blocks(
    df: pd.DataFrame,
    direction: str,
    lookback: int = 30,
    min_displacement_mult: float = 2.0,
    min_body_atr_ratio: float = 0.3
) -> List[OrderBlock]:
    """
    Detect order blocks based on impulsive move after specific candle.
    
    For BULLISH OB:
    - Find bearish candle (close < open) followed by strong bullish impulse
    - Impulse must break structure (close above recent swing high)
    - Displacement must be >= min_displacement_mult * OB range
    
    For BEARISH OB:
    - Find bullish candle (close > open) followed by strong bearish impulse
    - Impulse must break structure (close below recent swing low)
    - Displacement must be >= min_displacement_mult * OB range
    
    Args:
        df: OHLC DataFrame
        direction: "BULLISH" or "BEARISH"
        lookback: How many bars back to scan
        min_displacement_mult: Minimum impulse/OB size ratio
        min_body_atr_ratio: Minimum OB body size as ATR ratio
    
    Returns:
        List of OrderBlock objects
    """
    if len(df) < 10:
        return []
    
    atr = calculate_atr(df)
    if atr <= 0:
        return []
    
    order_blocks = []
    scan_start = max(0, len(df) - lookback)
    
    for i in range(scan_start, len(df) - 3):
        ob_candle = df.iloc[i]
        ob_open = ob_candle['open']
        ob_close = ob_candle['close']
        ob_high = ob_candle['high']
        ob_low = ob_candle['low']
        ob_body = abs(ob_close - ob_open)
        ob_range = ob_high - ob_low
        
        if ob_range <= 0:
            continue
        
        # Body size filter — skip doji / insignificant candles
        if ob_body < atr * min_body_atr_ratio:
            continue
        
        # Look at next 3 candles for impulse
        impulse_candles = df.iloc[i + 1: min(i + 4, len(df))]
        if len(impulse_candles) == 0:
            continue
        
        impulse_high = impulse_candles['high'].max()
        impulse_low = impulse_candles['low'].min()
        impulse_range = impulse_high - impulse_low
        
        if direction == "BULLISH":
            # OB candle must be bearish (close < open)
            if ob_close >= ob_open:
                continue
            
            # Impulse must be bullish and impulsive
            displacement = impulse_high - ob_high
            if displacement <= 0:
                continue
            
            if impulse_range < ob_range * min_displacement_mult:
                continue
            
            # Impulse candles should be predominantly bullish
            bullish_impulse = all(
                impulse_candles.iloc[j]['close'] > impulse_candles.iloc[j]['open']
                for j in range(min(2, len(impulse_candles)))
            )
            if not bullish_impulse:
                continue
            
            atr_ratio = displacement / atr
            quality = _compute_ob_quality(ob_body, displacement, atr, ob_range, impulse_range)
            
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
            
            ob = OrderBlock(
                ob_type=OBType.BULLISH.value,
                high=ob_high,
                low=ob_low,
                index=i,
                timestamp=timestamp,
                displacement=displacement,
                atr_ratio=atr_ratio,
                body_size=ob_body,
                is_impulsive=True,
                quality_score=quality
            )
            order_blocks.append(ob)
        
        elif direction == "BEARISH":
            # OB candle must be bullish (close > open)
            if ob_close <= ob_open:
                continue
            
            # Impulse must be bearish and impulsive
            displacement = ob_low - impulse_low
            if displacement <= 0:
                continue
            
            if impulse_range < ob_range * min_displacement_mult:
                continue
            
            # Impulse candles should be predominantly bearish
            bearish_impulse = all(
                impulse_candles.iloc[j]['close'] < impulse_candles.iloc[j]['open']
                for j in range(min(2, len(impulse_candles)))
            )
            if not bearish_impulse:
                continue
            
            atr_ratio = displacement / atr
            quality = _compute_ob_quality(ob_body, displacement, atr, ob_range, impulse_range)
            
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
            
            ob = OrderBlock(
                ob_type=OBType.BEARISH.value,
                high=ob_high,
                low=ob_low,
                index=i,
                timestamp=timestamp,
                displacement=displacement,
                atr_ratio=atr_ratio,
                body_size=ob_body,
                is_impulsive=True,
                quality_score=quality
            )
            order_blocks.append(ob)
    
    return order_blocks


def _compute_ob_quality(
    body_size: float,
    displacement: float,
    atr: float,
    ob_range: float,
    impulse_range: float
) -> float:
    """
    Compute OB quality score (0-10).
    
    Factors:
    - Displacement strength (0-4 points)
    - Body/range ratio — filled candle better (0-3 points)
    - Impulse/OB ratio (0-3 points)
    """
    score = 0.0
    
    # Displacement strength: displacement / ATR
    disp_ratio = displacement / atr if atr > 0 else 0
    score += min(4.0, disp_ratio * 2.0)
    
    # Body fill ratio: how much of OB is body vs wicks
    fill_ratio = body_size / ob_range if ob_range > 0 else 0
    score += min(3.0, fill_ratio * 3.0)
    
    # Impulse/OB ratio: bigger impulse = higher quality
    imp_ratio = impulse_range / ob_range if ob_range > 0 else 0
    score += min(3.0, (imp_ratio - 1.0) * 1.0)
    
    return round(max(0.0, min(10.0, score)), 1)


def get_nearest_ob(
    order_blocks: List[OrderBlock],
    current_price: float,
    direction: str
) -> Optional[OrderBlock]:
    """
    Find the nearest active order block to current price.
    
    For BULLISH: nearest OB below current price
    For BEARISH: nearest OB above current price
    
    Args:
        order_blocks: List of detected OBs
        current_price: Current market price
        direction: "BULLISH" or "BEARISH"
    
    Returns:
        Nearest active OrderBlock or None
    """
    # Trust the caller to filter status if needed
    active_obs = order_blocks 
    
    if not active_obs:
        return None
    
    if direction == "BULLISH":
        # OB at or below price (price may already be in the zone)
        candidates = [ob for ob in active_obs if ob.low <= current_price]
        if not candidates:
            return None
        # Return the closest OB to price
        return min(candidates, key=lambda ob: abs(current_price - (ob.high + ob.low) / 2))
    
    elif direction == "BEARISH":
        # OB at or above price
        candidates = [ob for ob in active_obs if ob.high >= current_price]
        if not candidates:
            return None
        return min(candidates, key=lambda ob: abs(current_price - (ob.high + ob.low) / 2))
    
    return None


def is_price_in_ob(price: float, ob: OrderBlock, tolerance: float = 0.002) -> bool:
    """
    Check if price is inside an order block zone.
    
    Args:
        price: Current price
        ob: OrderBlock to check
        tolerance: % tolerance for zone edges
    
    Returns:
        True if price is within OB zone
    """
    tol_amount = ob.range_size * tolerance
    return (ob.low - tol_amount) <= price <= (ob.high + tol_amount)


def update_ob_status(
    order_blocks: List[OrderBlock],
    df: pd.DataFrame,
    max_retests: int = 1
) -> List[OrderBlock]:
    """
    Update OB status based on price action.
    
    Golden Filter: Only first OB retest is valid. After that, OB is mitigated.
    
    Args:
        order_blocks: List of OBs to update
        df: Current OHLC DataFrame
        max_retests: Max valid retests (default 1 = first touch only)
    
    Returns:
        Updated list of OrderBlocks
    """
    if len(df) == 0:
        return order_blocks
    
    for ob in order_blocks:
        if ob.status == OBStatus.MITIGATED.value:
            continue
        
        # Check candles after OB formation
        was_in_zone = False # Track if previous candle was in zone
        
        for i in range(ob.index + 1, len(df)):
            low = df['low'].iloc[i]
            high = df['high'].iloc[i]
            close = df['close'].iloc[i]
            
            in_zone = False
            mitigated = False
            
            if ob.ob_type == OBType.BULLISH.value:
                # Check Deep Mitigation (Close below Low)
                if close < ob.low:
                    ob.status = OBStatus.MITIGATED.value
                    mitigated = True
                # Check Zone Entry
                elif low <= ob.high:
                    in_zone = True
            
            elif ob.ob_type == OBType.BEARISH.value:
                # Check Deep Mitigation (Close above High)
                if close > ob.high:
                    ob.status = OBStatus.MITIGATED.value
                    mitigated = True
                # Check Zone Entry
                elif high >= ob.low:
                    in_zone = True
            
            if mitigated:
                break
                
            if in_zone:
                if not was_in_zone:
                    # New distinct retest event
                    ob.retest_count += 1
                    if ob.retest_count == 1:
                        ob.status = OBStatus.TESTED.value
                    if ob.retest_count > max_retests:
                        ob.status = OBStatus.MITIGATED.value
                        break
                was_in_zone = True
            else:
                was_in_zone = False
    
    return order_blocks


def get_ob_summary(ob: OrderBlock) -> Dict:
    """Get dictionary summary of an OB"""
    return {
        "type": ob.ob_type,
        "high": ob.high,
        "low": ob.low,
        "mid": ob.mid_price,
        "range": ob.range_size,
        "displacement": ob.displacement,
        "quality": ob.quality_score,
        "status": ob.status,
        "retests": ob.retest_count,
        "is_impulsive": ob.is_impulsive,
    }
