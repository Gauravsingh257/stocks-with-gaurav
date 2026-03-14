"""
Break of Structure (BOS) & Change of Character (CHoCH) Detection
================================================================
Detects structural breaks and character changes on any timeframe.

BOS  = Price breaks a swing point IN the direction of the trend (continuation)
CHoCH = Price breaks a swing point AGAINST the trend (reversal signal)

Features:
- Internal BOS vs External BOS classification
- Weak BOS rejection filter (insufficient displacement)
- No repainting — only uses confirmed candles
- Pandas-based, backtest-friendly
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

from smc_trading_engine.smc.market_structure import (
    SwingPoint, StructureState, TrendState,
    detect_swing_highs, detect_swing_lows,
    calculate_atr, classify_swing_points
)

logger = logging.getLogger(__name__)


class BreakType(Enum):
    BOS = "BOS"       # Break of Structure (continuation)
    CHOCH = "CHoCH"   # Change of Character (reversal)


class BreakStrength(Enum):
    STRONG = "STRONG"   # Full body close beyond level + displacement
    MODERATE = "MODERATE"  # Body close beyond level, weak displacement
    WEAK = "WEAK"       # Only wick beyond level


@dataclass
class StructureBreak:
    """Represents a BOS or CHoCH event"""
    index: int                          # Bar index where break occurred
    timestamp: pd.Timestamp = None
    break_type: str = ""                # "BOS" or "CHoCH"
    direction: str = ""                 # "BULLISH" or "BEARISH"
    broken_level: float = 0.0          # The swing level that was broken
    close_price: float = 0.0           # Close of the breaking candle
    displacement: float = 0.0          # How far price closed beyond level
    strength: str = "MODERATE"         # "STRONG", "MODERATE", "WEAK"
    structure_type: str = "EXTERNAL"   # "INTERNAL" or "EXTERNAL"
    atr_ratio: float = 0.0            # displacement / ATR (quality metric)
    valid: bool = True                 # Passes quality filters


def detect_bos(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    min_displacement_atr: float = 0.05,
    lookback_bars: int = 50
) -> List[StructureBreak]:
    """
    Detect all Break of Structure (BOS) events.
    
    A BOS occurs when price closes beyond a swing high (bullish BOS)
    or below a swing low (bearish BOS) in the direction of the prevailing trend.
    
    Args:
        df: OHLC DataFrame
        swing_lookback: Bars on each side for swing detection
        min_displacement_atr: Minimum displacement as ATR multiple to validate
        lookback_bars: How many bars back to scan
    
    Returns:
        List of StructureBreak events
    """
    if len(df) < swing_lookback * 2 + 10:
        return []
    
    atr = calculate_atr(df)
    if atr <= 0:
        return []
    
    # Get swing points
    swing_highs_mask = detect_swing_highs(df, swing_lookback)
    swing_lows_mask = detect_swing_lows(df, swing_lookback)
    
    # Collect swing levels
    swing_high_levels = []  # (index, price)
    swing_low_levels = []
    
    start_idx = max(0, len(df) - lookback_bars)
    
    for i in range(start_idx, len(df)):
        if swing_highs_mask.iloc[i]:
            swing_high_levels.append((i, df['high'].iloc[i]))
        if swing_lows_mask.iloc[i]:
            swing_low_levels.append((i, df['low'].iloc[i]))
    
    breaks = []
    
    # Check for bullish BOS (price closes above a swing high)
    for sh_idx, sh_price in swing_high_levels:
        # Look for candles after this swing high that close above it
        for i in range(sh_idx + swing_lookback, len(df)):
            close = df['close'].iloc[i]
            
            if close > sh_price:
                displacement = close - sh_price
                atr_ratio = displacement / atr if atr > 0 else 0
                
                # Classify strength
                if atr_ratio >= 0.5:
                    strength = BreakStrength.STRONG.value
                elif atr_ratio >= 0.05:
                    strength = BreakStrength.MODERATE.value
                else:
                    strength = BreakStrength.WEAK.value
                
                # Check if only wick (not body)
                body_close = close
                body_open = df['open'].iloc[i]
                if min(body_close, body_open) > sh_price:
                    # Full body above — strong
                    pass
                elif max(body_close, body_open) > sh_price:
                    # Only partial body
                    strength = BreakStrength.WEAK.value
                
                timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
                
                brk = StructureBreak(
                    index=i,
                    timestamp=timestamp,
                    break_type=BreakType.BOS.value,
                    direction="BULLISH",
                    broken_level=sh_price,
                    close_price=close,
                    displacement=displacement,
                    strength=strength,
                    atr_ratio=atr_ratio,
                    valid=atr_ratio >= min_displacement_atr
                )
                breaks.append(brk)
                break  # Only count first break of each level
    
    # Check for bearish BOS (price closes below a swing low)
    for sl_idx, sl_price in swing_low_levels:
        for i in range(sl_idx + swing_lookback, len(df)):
            close = df['close'].iloc[i]
            
            if close < sl_price:
                displacement = sl_price - close
                atr_ratio = displacement / atr if atr > 0 else 0
                
                if atr_ratio >= 0.5:
                    strength = BreakStrength.STRONG.value
                elif atr_ratio >= 0.05:
                    strength = BreakStrength.MODERATE.value
                else:
                    strength = BreakStrength.WEAK.value
                
                body_close = close
                body_open = df['open'].iloc[i]
                if max(body_close, body_open) < sl_price:
                    pass
                elif min(body_close, body_open) < sl_price:
                    strength = BreakStrength.WEAK.value
                
                timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
                
                brk = StructureBreak(
                    index=i,
                    timestamp=timestamp,
                    break_type=BreakType.BOS.value,
                    direction="BEARISH",
                    broken_level=sl_price,
                    close_price=close,
                    displacement=displacement,
                    strength=strength,
                    atr_ratio=atr_ratio,
                    valid=atr_ratio >= min_displacement_atr
                )
                breaks.append(brk)
                break
    
    # Sort by index
    breaks.sort(key=lambda b: b.index)
    return breaks


def detect_choch(
    df: pd.DataFrame,
    current_trend: TrendState = TrendState.UNKNOWN,
    swing_lookback: int = 5,
    min_displacement_atr: float = 0.5,
    lookback_bars: int = 30
) -> List[StructureBreak]:
    """
    Detect Change of Character (CHoCH) events.
    
    A CHoCH occurs when price breaks structure AGAINST the prevailing trend.
    
    In a BULLISH trend: CHoCH = price closes below the last swing low (HL broken)
    In a BEARISH trend: CHoCH = price closes above the last swing high (LH broken)
    
    CHoCH requires stronger displacement than BOS (more confirmation needed).
    
    Args:
        df: OHLC DataFrame
        current_trend: Current market trend (needed to identify reversal)
        swing_lookback: Bars for swing detection
        min_displacement_atr: Minimum ATR displacement for valid CHoCH
        lookback_bars: Bars to scan
    
    Returns:
        List of CHoCH StructureBreak events
    """
    if len(df) < swing_lookback * 2 + 10:
        return []
    
    if current_trend == TrendState.UNKNOWN or current_trend == TrendState.RANGING:
        return []
    
    atr = calculate_atr(df)
    if atr <= 0:
        return []
    
    swing_highs_mask = detect_swing_highs(df, swing_lookback)
    swing_lows_mask = detect_swing_lows(df, swing_lookback)
    
    breaks = []
    start_idx = max(0, len(df) - lookback_bars)
    
    if current_trend == TrendState.BULLISH:
        # In bullish trend, CHoCH = break below swing low
        # Find the last swing low
        last_sl_idx = None
        last_sl_price = None
        
        for i in range(len(df) - 1, start_idx - 1, -1):
            if swing_lows_mask.iloc[i]:
                last_sl_idx = i
                last_sl_price = df['low'].iloc[i]
                break
        
        if last_sl_idx is not None:
            for i in range(last_sl_idx + 1, len(df)):
                close = df['close'].iloc[i]
                if close < last_sl_price:
                    displacement = last_sl_price - close
                    atr_ratio = displacement / atr
                    
                    strength = BreakStrength.STRONG.value if atr_ratio >= 0.8 else \
                               BreakStrength.MODERATE.value if atr_ratio >= min_displacement_atr else \
                               BreakStrength.WEAK.value
                    
                    timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
                    
                    brk = StructureBreak(
                        index=i,
                        timestamp=timestamp,
                        break_type=BreakType.CHOCH.value,
                        direction="BEARISH",
                        broken_level=last_sl_price,
                        close_price=close,
                        displacement=displacement,
                        strength=strength,
                        atr_ratio=atr_ratio,
                        valid=atr_ratio >= min_displacement_atr
                    )
                    breaks.append(brk)
                    break
    
    elif current_trend == TrendState.BEARISH:
        # In bearish trend, CHoCH = break above swing high
        last_sh_idx = None
        last_sh_price = None
        
        for i in range(len(df) - 1, start_idx - 1, -1):
            if swing_highs_mask.iloc[i]:
                last_sh_idx = i
                last_sh_price = df['high'].iloc[i]
                break
        
        if last_sh_idx is not None:
            for i in range(last_sh_idx + 1, len(df)):
                close = df['close'].iloc[i]
                if close > last_sh_price:
                    displacement = close - last_sh_price
                    atr_ratio = displacement / atr
                    
                    strength = BreakStrength.STRONG.value if atr_ratio >= 0.8 else \
                               BreakStrength.MODERATE.value if atr_ratio >= min_displacement_atr else \
                               BreakStrength.WEAK.value
                    
                    timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
                    
                    brk = StructureBreak(
                        index=i,
                        timestamp=timestamp,
                        break_type=BreakType.CHOCH.value,
                        direction="BULLISH",
                        broken_level=last_sh_price,
                        close_price=close,
                        displacement=displacement,
                        strength=strength,
                        atr_ratio=atr_ratio,
                        valid=atr_ratio >= min_displacement_atr
                    )
                    breaks.append(brk)
                    break
    
    return breaks


def get_latest_bos(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    min_displacement_atr: float = 0.05
) -> Optional[StructureBreak]:
    """
    Get the most recent valid BOS event.
    
    Convenience function for strategy modules that need the latest
    confirmed BOS for entry logic.
    
    Args:
        df: OHLC DataFrame
        swing_lookback: Swing detection parameter
        min_displacement_atr: Minimum quality threshold
    
    Returns:
        Latest valid StructureBreak or None
    """
    all_bos = detect_bos(df, swing_lookback, min_displacement_atr)
    valid_bos = [b for b in all_bos if b.valid]
    
    return valid_bos[-1] if valid_bos else None


def get_latest_choch(
    df: pd.DataFrame,
    current_trend: TrendState,
    swing_lookback: int = 5
) -> Optional[StructureBreak]:
    """
    Get the most recent valid CHoCH event.
    
    Args:
        df: OHLC DataFrame
        current_trend: Current TrendState
        swing_lookback: Swing detection parameter
    
    Returns:
        Latest valid CHoCH or None
    """
    all_choch = detect_choch(df, current_trend, swing_lookback)
    valid_choch = [c for c in all_choch if c.valid]
    
    return valid_choch[-1] if valid_choch else None


def is_weak_bos(bos: StructureBreak, min_atr_ratio: float = 0.05) -> bool:
    """
    Filter to reject weak BOS events.
    
    A weak BOS has insufficient displacement — often noise or
    internal structure that doesn't indicate real momentum.
    
    Args:
        bos: The BOS event to check
        min_atr_ratio: Minimum displacement/ATR ratio (default 0.05)
    
    Returns:
        True if the BOS is weak (should be rejected)
    """
    if bos.strength == BreakStrength.WEAK.value:
        return True
    if bos.atr_ratio < min_atr_ratio:
        return True
    return False


def detect_bias(df: pd.DataFrame, swing_lookback: int = 5) -> Optional[str]:
    """
    Determine directional bias based on the latest BOS.
    Lenient: even weak BOS gives bias direction.
    Uses a short lookback (30 bars) to avoid stale bias from old BOS events.
    
    Returns:
        "LONG" if last BOS was bullish
        "SHORT" if last BOS was bearish
        None if no BOS found
    """
    # Use only the last 30 bars for bias detection — prevents stale bias
    recent_df = df.tail(60) if len(df) > 60 else df
    latest_bos = get_latest_bos(recent_df, swing_lookback)
    
    if latest_bos is None:
        return None
    
    if latest_bos.direction == "BULLISH":
        return "LONG"
    elif latest_bos.direction == "BEARISH":
        return "SHORT"
    
    return None
