"""
Regime Controller Module
========================
Translates regime classification into actionable control flags
for the SMC entry model.

Usage:
    controller = RegimeController()
    flags = controller.get_control_flags(classification_result)

    if flags.allow_long:
        # proceed with long setups
    ...
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from smc_trading_engine.regime.premarket_classifier import (
    RegimeType,
    DirectionalBias,
)
from smc_trading_engine.regime.morning_confirmation import (
    OpeningRange,
    ConfirmationResult,
    compute_opening_range,
    confirm_regime,
)

logger = logging.getLogger(__name__)


@dataclass
class RegimeControlFlags:
    """
    Control flags consumed by entry_model and risk management.

    Attributes:
        allow_long:               Whether long entries are permitted
        allow_short:              Whether short entries are permitted
        position_size_multiplier: 1.0 = full size, 0.5 = half size
        regime:                   Current regime classification
        confidence:               Classification confidence (0-100)
        directional_bias:         LONG_ONLY | SHORT_ONLY | BOTH
        call_wall:                If available, nearest call OI wall
        put_wall:                 If available, nearest put OI wall
        max_pain:                 If available, max pain strike
    """
    allow_long: bool = True
    allow_short: bool = True
    position_size_multiplier: float = 1.0
    regime: str = "ROTATIONAL"
    confidence: int = 50
    directional_bias: str = "BOTH"
    call_wall: float = 0.0
    put_wall: float = 0.0
    max_pain: float = 0.0


class RegimeController:
    """
    Converts regime classification into control flags.

    Regime → Control mapping:
        TREND_UP        → longs only, block shorts
        TREND_DOWN      → shorts only, block longs
        ROTATIONAL      → both directions allowed
        HIGH_VOL_EVENT  → both allowed, position size reduced 50%

    Integration:
        The entry_model should call get_control_flags() before 9:15 AM
        and cache the result for the entire trading session.
    """

    def __init__(self, high_vol_size_reduction: float = 0.50):
        """
        Args:
            high_vol_size_reduction: Position size multiplier for
                HIGH_VOL_EVENT regime (default 0.5 = 50%).
        """
        self.high_vol_size_reduction = high_vol_size_reduction

    def get_control_flags(
        self,
        classification: Dict,
    ) -> RegimeControlFlags:
        """
        Translate classification result into control flags.

        Args:
            classification: Output from PremarketClassifier.classify()
                Must contain: regime, directional_bias, confidence
                May contain: components.oi (for wall/max_pain data)

        Returns:
            RegimeControlFlags instance
        """
        regime_str = classification.get("regime", "ROTATIONAL")
        bias_str = classification.get("directional_bias", "BOTH")
        confidence = classification.get("confidence", 50)

        # Parse regime
        try:
            regime = RegimeType(regime_str)
        except ValueError:
            logger.warning(f"Unknown regime '{regime_str}', defaulting to ROTATIONAL")
            regime = RegimeType.ROTATIONAL

        # Extract OI data if available
        components = classification.get("components", {})
        oi_data = components.get("oi", {})
        call_wall = float(oi_data.get("call_wall", 0.0))
        put_wall = float(oi_data.get("put_wall", 0.0))
        max_pain = float(oi_data.get("max_pain", 0.0))

        # ── Build flags ──
        flags = RegimeControlFlags(
            regime=regime.value,
            confidence=confidence,
            directional_bias=bias_str,
            call_wall=call_wall,
            put_wall=put_wall,
            max_pain=max_pain,
        )

        if regime == RegimeType.TREND_UP:
            flags.allow_long = True
            flags.allow_short = False
            flags.position_size_multiplier = 1.0

        elif regime == RegimeType.TREND_DOWN:
            flags.allow_long = False
            flags.allow_short = True
            flags.position_size_multiplier = 1.0

        elif regime == RegimeType.ROTATIONAL:
            flags.allow_long = True
            flags.allow_short = True
            flags.position_size_multiplier = 1.0

        elif regime == RegimeType.HIGH_VOL_EVENT:
            flags.allow_long = True
            flags.allow_short = True
            flags.position_size_multiplier = self.high_vol_size_reduction

        logger.info(
            f"[CTRL] regime={flags.regime} "
            f"long={flags.allow_long} short={flags.allow_short} "
            f"size_mult={flags.position_size_multiplier} "
            f"conf={flags.confidence}"
        )

        return flags

    def should_allow_entry(
        self,
        flags: RegimeControlFlags,
        direction: str,
    ) -> bool:
        """
        Quick check: is this direction allowed by the current regime?

        Args:
            flags:     RegimeControlFlags instance
            direction: "LONG" or "SHORT"

        Returns:
            True if entry is allowed
        """
        direction = direction.upper()
        if direction == "LONG":
            return flags.allow_long
        elif direction == "SHORT":
            return flags.allow_short
        else:
            logger.warning(f"Unknown direction: {direction}")
            return False

    def adjust_position_size(
        self,
        flags: RegimeControlFlags,
        base_qty: int,
    ) -> int:
        """
        Adjust position size based on regime.

        Args:
            flags:    RegimeControlFlags instance
            base_qty: Base position quantity (lots / shares)

        Returns:
            Adjusted quantity (always >= 1 if base > 0)
        """
        adjusted = int(base_qty * flags.position_size_multiplier)
        return max(1, adjusted) if base_qty > 0 else 0

    def apply_morning_confirmation(
        self,
        flags: RegimeControlFlags,
        first_candle_df: Optional[pd.DataFrame] = None,
        confirmation_candle_df: Optional[pd.DataFrame] = None,
        volume_lookback_df: Optional[pd.DataFrame] = None,
    ) -> RegimeControlFlags:
        """
        Apply 9:30–9:45 morning confirmation to an existing set of flags.

        If the confirmation candle contradicts the pre-market regime,
        the flags are downgraded to ROTATIONAL (both directions, full size).

        Args:
            flags:                  Current RegimeControlFlags
            first_candle_df:        DataFrame with the 9:15–9:30 candle
                                    (for opening range extraction)
            confirmation_candle_df: DataFrame with the 9:30–9:45 candle
            volume_lookback_df:     Optional prior candles for avg volume

        Returns:
            Updated RegimeControlFlags (may be same object if not changed)
        """
        if first_candle_df is None or first_candle_df.empty:
            logger.info("[CTRL] No first candle data — skipping morning confirmation")
            return flags

        # Build opening range from the first 15m candle
        opening_range = compute_opening_range(first_candle_df, volume_lookback_df)

        # Run confirmation
        result = confirm_regime(
            premarket_regime=flags.regime,
            premarket_bias=flags.directional_bias,
            opening_range=opening_range,
            confirmation_candle_df=confirmation_candle_df,
        )

        # If regime was downgraded, rebuild flags
        if result.regime_changed:
            flags.regime = result.confirmed_regime
            flags.directional_bias = result.directional_bias
            flags.allow_long = True
            flags.allow_short = True
            # Keep position_size_multiplier unchanged

            logger.warning(
                f"[CTRL] Regime downgraded: {result.original_regime} → "
                f"{result.confirmed_regime} | {result.reason}"
            )
        else:
            logger.info(
                f"[CTRL] Regime confirmed: {flags.regime} | {result.reason}"
            )

        return flags
