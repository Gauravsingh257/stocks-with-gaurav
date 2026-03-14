"""
Morning Confirmation Module
============================
Post-open confirmation logic (9:30–9:45 AM) that validates or
downgrades the pre-market regime based on actual price action.

If price contradicts the pre-market classification during the
first 15-minute candle, the regime is downgraded to ROTATIONAL
to prevent fighting the tape.

Deterministic. No repainting. Backtest-compatible.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd
import numpy as np

from smc_trading_engine.regime.premarket_classifier import RegimeType, DirectionalBias

logger = logging.getLogger(__name__)


# ─── THRESHOLDS ────────────────────────────────────────
VOLUME_EXPANSION_THRESHOLD = 1.5   # First candle vol > 1.5× avg → strong conviction
BULLISH_BODY_RATIO = 0.60          # Close in top 60% of range → bullish candle
BEARISH_BODY_RATIO = 0.40          # Close in bottom 40% of range → bearish candle


@dataclass
class OpeningRange:
    """
    The opening range captured from the first 15m candle (9:15–9:30)
    or the first tradeable candle (9:30–9:45).

    Attributes:
        high:          Opening range high
        low:           Opening range low
        open_price:    Session open price
        close_price:   First candle close
        volume:        First candle volume
        avg_volume:    Average volume (lookback) for expansion check
    """
    high: float = 0.0
    low: float = 0.0
    open_price: float = 0.0
    close_price: float = 0.0
    volume: float = 0.0
    avg_volume: float = 0.0


@dataclass
class ConfirmationResult:
    """
    Result of the morning confirmation check.

    Attributes:
        original_regime:     Regime before confirmation
        confirmed_regime:    Regime after confirmation (may be downgraded)
        regime_changed:      True if regime was downgraded
        directional_bias:    Updated bias
        reason:              Human-readable reason for change (or "CONFIRMED")
        opening_range:       The opening range used
        volume_expansion:    Whether volume expansion was detected
        structure_break:     Direction of structure break ("BULLISH" / "BEARISH" / None)
    """
    original_regime: str = "ROTATIONAL"
    confirmed_regime: str = "ROTATIONAL"
    regime_changed: bool = False
    directional_bias: str = "BOTH"
    reason: str = "CONFIRMED"
    opening_range: Optional[OpeningRange] = None
    volume_expansion: bool = False
    structure_break: Optional[str] = None


def compute_opening_range(
    first_candle_df: pd.DataFrame,
    volume_lookback_df: Optional[pd.DataFrame] = None,
) -> OpeningRange:
    """
    Extract the opening range from the first 15m candle data.

    Args:
        first_candle_df: DataFrame with at least 1 row containing the
                         first 15m candle (columns: open, high, low, close, volume)
        volume_lookback_df: Optional DataFrame of prior candles for avg volume.
                           If None, avg_volume defaults to first candle's volume.

    Returns:
        OpeningRange dataclass
    """
    if first_candle_df is None or first_candle_df.empty:
        return OpeningRange()

    candle = first_candle_df.iloc[-1]

    avg_vol = float(candle.get("volume", 0))
    if volume_lookback_df is not None and not volume_lookback_df.empty:
        avg_vol = float(volume_lookback_df["volume"].mean())

    return OpeningRange(
        high=float(candle["high"]),
        low=float(candle["low"]),
        open_price=float(candle["open"]),
        close_price=float(candle["close"]),
        volume=float(candle.get("volume", 0)),
        avg_volume=avg_vol,
    )


def detect_volume_expansion(opening_range: OpeningRange) -> bool:
    """
    Check if the first candle has volume expansion (> threshold × average).

    Returns:
        True if volume is significantly above average.
    """
    if opening_range.avg_volume <= 0:
        return False
    ratio = opening_range.volume / opening_range.avg_volume
    return ratio >= VOLUME_EXPANSION_THRESHOLD


def detect_structure_break(
    opening_range: OpeningRange,
    confirmation_candle_df: pd.DataFrame,
) -> Optional[str]:
    """
    Detect if the 9:30–9:45 candle broke the opening range.

    A structure break occurs when the confirmation candle's close
    exceeds the opening range boundary:
        - Close > opening_range.high → BULLISH break
        - Close < opening_range.low  → BEARISH break

    Args:
        opening_range:          The 9:15–9:30 opening range
        confirmation_candle_df: DataFrame with the 9:30–9:45 candle
                                (last row used)

    Returns:
        "BULLISH", "BEARISH", or None if no break
    """
    if confirmation_candle_df is None or confirmation_candle_df.empty:
        return None
    if opening_range.high == 0 and opening_range.low == 0:
        return None

    candle = confirmation_candle_df.iloc[-1]
    close = float(candle["close"])

    if close > opening_range.high:
        return "BULLISH"
    elif close < opening_range.low:
        return "BEARISH"

    return None


def is_strong_bullish_candle(candle_series: pd.Series) -> bool:
    """
    Check if a candle is a strong bullish candle.
    Close must be in the upper portion of the range (body ratio > threshold).
    """
    high = float(candle_series["high"])
    low = float(candle_series["low"])
    close = float(candle_series["close"])
    candle_range = high - low

    if candle_range <= 0:
        return False

    # Position of close within the range (0=low, 1=high)
    position = (close - low) / candle_range
    return position >= BULLISH_BODY_RATIO


def is_strong_bearish_candle(candle_series: pd.Series) -> bool:
    """
    Check if a candle is a strong bearish candle.
    Close must be in the lower portion of the range (body ratio < threshold).
    """
    high = float(candle_series["high"])
    low = float(candle_series["low"])
    close = float(candle_series["close"])
    candle_range = high - low

    if candle_range <= 0:
        return False

    position = (close - low) / candle_range
    return position <= BEARISH_BODY_RATIO


def confirm_regime(
    premarket_regime: str,
    premarket_bias: str,
    opening_range: OpeningRange,
    confirmation_candle_df: pd.DataFrame,
) -> ConfirmationResult:
    """
    Run morning confirmation logic.

    Rules:
        If premarket TREND_DOWN but:
            price breaks opening range HIGH
            AND strong bullish 15m close (confirmation candle)
            → downgrade to ROTATIONAL

        If premarket TREND_UP but:
            price breaks opening range LOW
            → downgrade to ROTATIONAL

        If HIGH_VOL_EVENT:
            never downgrade (event days are inherently volatile)

        Otherwise:
            regime is CONFIRMED (no change)

    Args:
        premarket_regime:       Regime string from PremarketClassifier
        premarket_bias:         DirectionalBias string
        opening_range:          OpeningRange from first 15m candle
        confirmation_candle_df: DataFrame with the 9:30–9:45 candle

    Returns:
        ConfirmationResult with original and potentially updated regime
    """
    result = ConfirmationResult(
        original_regime=premarket_regime,
        confirmed_regime=premarket_regime,
        directional_bias=premarket_bias,
        opening_range=opening_range,
    )

    # Skip confirmation for HIGH_VOL_EVENT — don't second-guess event days
    if premarket_regime == RegimeType.HIGH_VOL_EVENT.value:
        result.reason = "CONFIRMED_EVENT_DAY"
        logger.info("[CONFIRM] HIGH_VOL_EVENT — skipping confirmation, event day")
        return result

    # Skip if no confirmation candle data
    if confirmation_candle_df is None or confirmation_candle_df.empty:
        result.reason = "CONFIRMED_NO_DATA"
        logger.info("[CONFIRM] No confirmation candle data — regime unchanged")
        return result

    # Detect volume and structure
    vol_expansion = detect_volume_expansion(opening_range)
    structure_break = detect_structure_break(opening_range, confirmation_candle_df)
    result.volume_expansion = vol_expansion
    result.structure_break = structure_break

    confirm_candle = confirmation_candle_df.iloc[-1]

    # ── Rule 1: TREND_DOWN contradicted by bullish breakout ──
    if premarket_regime == RegimeType.TREND_DOWN.value:
        if structure_break == "BULLISH" and is_strong_bullish_candle(confirm_candle):
            result.confirmed_regime = RegimeType.ROTATIONAL.value
            result.directional_bias = DirectionalBias.BOTH.value
            result.regime_changed = True
            result.reason = (
                "DOWNGRADE: TREND_DOWN → ROTATIONAL | "
                "Price broke opening range HIGH with strong bullish candle"
            )
            logger.warning(
                f"[CONFIRM] {result.reason} | "
                f"OR_high={opening_range.high} close={confirm_candle['close']}"
            )
            return result

    # ── Rule 2: TREND_UP contradicted by bearish breakdown ──
    if premarket_regime == RegimeType.TREND_UP.value:
        if structure_break == "BEARISH":
            result.confirmed_regime = RegimeType.ROTATIONAL.value
            result.directional_bias = DirectionalBias.BOTH.value
            result.regime_changed = True
            result.reason = (
                "DOWNGRADE: TREND_UP → ROTATIONAL | "
                "Price broke opening range LOW"
            )
            logger.warning(
                f"[CONFIRM] {result.reason} | "
                f"OR_low={opening_range.low} close={confirm_candle['close']}"
            )
            return result

    # ── No contradiction — regime confirmed ──
    result.reason = "CONFIRMED"
    logger.info(
        f"[CONFIRM] Regime {premarket_regime} CONFIRMED | "
        f"structure_break={structure_break} vol_expansion={vol_expansion}"
    )
    return result
