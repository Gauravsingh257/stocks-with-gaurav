"""
Market Structure Detection
==========================
Detects swing highs/lows, HH/HL/LH/LL patterns, and classifies
market structure as bullish, bearish, or ranging.

- Internal vs External structure classification
- No repainting — only uses confirmed (closed) candles
- Pandas-based, backtest-friendly
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class TrendState(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"
    UNKNOWN = "UNKNOWN"


class StructureType(Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


@dataclass
class SwingPoint:
    """Represents a swing high or swing low"""
    index: int
    price: float
    timestamp: pd.Timestamp = None
    swing_type: str = ""        # "HH", "HL", "LH", "LL", "SH", "SL"
    structure: str = "EXTERNAL" # "INTERNAL" or "EXTERNAL"
    confirmed: bool = False


@dataclass
class StructureState:
    """Tracks the current market structure state"""
    trend: TrendState = TrendState.UNKNOWN
    last_swing_high: Optional[SwingPoint] = None
    last_swing_low: Optional[SwingPoint] = None
    prev_swing_high: Optional[SwingPoint] = None
    prev_swing_low: Optional[SwingPoint] = None
    swing_points: List[SwingPoint] = field(default_factory=list)


def detect_swing_highs(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    """
    Detect swing highs — a bar whose high is higher than
    the 'lookback' bars on both sides.
    
    Only marks confirmed swing highs (right side candles closed).
    
    Args:
        df: OHLC DataFrame with 'high' column
        lookback: Number of bars to compare on each side
    
    Returns:
        Boolean Series where True = swing high
    """
    highs = df['high']
    swing_highs = pd.Series(False, index=df.index)
    
    for i in range(lookback, len(df) - lookback):
        is_swing = True
        current_high = highs.iloc[i]
        
        for j in range(1, lookback + 1):
            if highs.iloc[i - j] >= current_high or highs.iloc[i + j] >= current_high:
                is_swing = False
                break
        
        if is_swing:
            swing_highs.iloc[i] = True
    
    return swing_highs


def detect_swing_lows(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    """
    Detect swing lows — a bar whose low is lower than
    the 'lookback' bars on both sides.
    
    Args:
        df: OHLC DataFrame with 'low' column
        lookback: Number of bars to compare on each side
    
    Returns:
        Boolean Series where True = swing low
    """
    lows = df['low']
    swing_lows = pd.Series(False, index=df.index)
    
    for i in range(lookback, len(df) - lookback):
        is_swing = True
        current_low = lows.iloc[i]
        
        for j in range(1, lookback + 1):
            if lows.iloc[i - j] <= current_low or lows.iloc[i + j] <= current_low:
                is_swing = False
                break
        
        if is_swing:
            swing_lows.iloc[i] = True
    
    return swing_lows


def classify_swing_points(df: pd.DataFrame, lookback: int = 5) -> List[SwingPoint]:
    """
    Detect and classify all swing points as HH, HL, LH, LL.
    
    Classification Rules:
    - HH (Higher High): swing high > previous swing high
    - LH (Lower High): swing high < previous swing high
    - HL (Higher Low): swing low > previous swing low
    - LL (Lower Low): swing low < previous swing low
    
    Args:
        df: OHLC DataFrame
        lookback: Swing detection lookback
    
    Returns:
        List of classified SwingPoint objects
    """
    if len(df) < lookback * 2 + 1:
        return []
    
    swing_highs = detect_swing_highs(df, lookback)
    swing_lows = detect_swing_lows(df, lookback)
    
    swing_points = []
    last_sh_price = None
    last_sl_price = None
    
    for i in range(len(df)):
        if swing_highs.iloc[i]:
            price = df['high'].iloc[i]
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
            
            if last_sh_price is None:
                swing_type = "SH"  # First swing high (unclassified)
            elif price > last_sh_price:
                swing_type = "HH"
            else:
                swing_type = "LH"
            
            sp = SwingPoint(
                index=i,
                price=price,
                timestamp=timestamp,
                swing_type=swing_type,
                confirmed=True
            )
            swing_points.append(sp)
            last_sh_price = price
        
        if swing_lows.iloc[i]:
            price = df['low'].iloc[i]
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None
            
            if last_sl_price is None:
                swing_type = "SL"  # First swing low (unclassified)
            elif price > last_sl_price:
                swing_type = "HL"
            else:
                swing_type = "LL"
            
            sp = SwingPoint(
                index=i,
                price=price,
                timestamp=timestamp,
                swing_type=swing_type,
                confirmed=True
            )
            swing_points.append(sp)
            last_sl_price = price
    
    return swing_points


def classify_internal_external(
    swing_points: List[SwingPoint],
    atr: float
) -> List[SwingPoint]:
    """
    Classify swing points as INTERNAL or EXTERNAL structure.
    
    External = major swing points (significant displacement)
    Internal = minor pullbacks within the external move
    
    Args:
        swing_points: List of SwingPoint objects
        atr: Average True Range for displacement threshold
    
    Returns:
        Updated list with structure classification
    """
    if len(swing_points) < 3 or atr <= 0:
        return swing_points
    
    min_displacement = atr * 1.5  # Must move 1.5x ATR for external
    
    last_external_high = None
    last_external_low = None
    
    for sp in swing_points:
        if sp.swing_type in ("HH", "LH", "SH"):
            if last_external_high is None:
                sp.structure = StructureType.EXTERNAL.value
                last_external_high = sp.price
            elif abs(sp.price - last_external_high) >= min_displacement:
                sp.structure = StructureType.EXTERNAL.value
                last_external_high = sp.price
            else:
                sp.structure = StructureType.INTERNAL.value
        
        elif sp.swing_type in ("HL", "LL", "SL"):
            if last_external_low is None:
                sp.structure = StructureType.EXTERNAL.value
                last_external_low = sp.price
            elif abs(sp.price - last_external_low) >= min_displacement:
                sp.structure = StructureType.EXTERNAL.value
                last_external_low = sp.price
            else:
                sp.structure = StructureType.INTERNAL.value
    
    return swing_points


def determine_trend(swing_points: List[SwingPoint], min_points: int = 4) -> TrendState:
    """
    Determine current market trend based on swing point sequence.
    
    Rules:
    - BULLISH: HH + HL sequence (higher highs and higher lows)
    - BEARISH: LH + LL sequence (lower highs and lower lows)
    - RANGING: Mixed HH/LL or no clear direction
    
    Args:
        swing_points: Classified swing points
        min_points: Minimum points needed to determine trend
    
    Returns:
        Current TrendState
    """
    if len(swing_points) < min_points:
        return TrendState.UNKNOWN
    
    # Look at last N swing points
    recent = swing_points[-min_points:]
    types = [sp.swing_type for sp in recent]
    
    # Count bullish vs bearish patterns
    bullish_count = types.count("HH") + types.count("HL")
    bearish_count = types.count("LH") + types.count("LL")
    
    if bullish_count >= min_points * 0.6:
        return TrendState.BULLISH
    elif bearish_count >= min_points * 0.6:
        return TrendState.BEARISH
    else:
        return TrendState.RANGING


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Calculate Average True Range.
    
    Args:
        df: OHLC DataFrame
        period: ATR period
    
    Returns:
        ATR value
    """
    if len(df) < period + 1:
        return 0.0
    
    high = df['high']
    low = df['low']
    close = df['close'].shift(1)
    
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs()
    ], axis=1).max(axis=1)
    
    return float(tr.rolling(window=period).mean().iloc[-1])


def is_ranging_market(df: pd.DataFrame, lookback: int = 20, atr_mult: float = 2.0) -> bool:
    """
    Detect if market is in a range (consolidation).
    
    Range = total price range over lookback is less than atr_mult * ATR
    
    Args:
        df: OHLC DataFrame
        lookback: Number of bars to check
        atr_mult: ATR multiplier threshold
    
    Returns:
        True if market is ranging
    """
    if len(df) < lookback + 14:
        return False
    
    recent = df.tail(lookback)
    price_range = recent['high'].max() - recent['low'].min()
    atr = calculate_atr(df)
    
    if atr <= 0:
        return False
    
    return price_range < (atr * atr_mult)


def analyze_structure(
    df: pd.DataFrame,
    swing_lookback: int = 5
) -> StructureState:
    """
    Complete market structure analysis.
    
    Performs full swing detection, classification, internal/external
    separation, and trend determination.
    
    Args:
        df: OHLC DataFrame (must have open, high, low, close columns)
        swing_lookback: Bars on each side for swing detection
    
    Returns:
        StructureState with full analysis
    """
    state = StructureState()
    
    if len(df) < swing_lookback * 2 + 5:
        return state
    
    # 1. Detect and classify swing points
    swing_points = classify_swing_points(df, swing_lookback)
    
    if not swing_points:
        return state
    
    # 2. Calculate ATR for internal/external classification
    atr = calculate_atr(df)
    
    # 3. Classify internal vs external
    swing_points = classify_internal_external(swing_points, atr)
    
    # 4. Determine trend
    state.trend = determine_trend(swing_points)
    state.swing_points = swing_points
    
    # 5. Set latest swing highs/lows
    highs = [sp for sp in swing_points if sp.swing_type in ("HH", "LH", "SH")]
    lows = [sp for sp in swing_points if sp.swing_type in ("HL", "LL", "SL")]
    
    if highs:
        state.last_swing_high = highs[-1]
        if len(highs) >= 2:
            state.prev_swing_high = highs[-2]
    
    if lows:
        state.last_swing_low = lows[-1]
        if len(lows) >= 2:
            state.prev_swing_low = lows[-2]
    
    return state


def get_structure_summary(state: StructureState) -> Dict:
    """
    Get a dictionary summary of the structure state.
    
    Returns:
        Dictionary with trend, swing points, etc.
    """
    return {
        "trend": state.trend.value,
        "last_swing_high": state.last_swing_high.price if state.last_swing_high else None,
        "last_swing_low": state.last_swing_low.price if state.last_swing_low else None,
        "total_swing_points": len(state.swing_points),
        "external_points": len([sp for sp in state.swing_points if sp.structure == "EXTERNAL"]),
        "internal_points": len([sp for sp in state.swing_points if sp.structure == "INTERNAL"]),
    }
