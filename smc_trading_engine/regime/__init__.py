"""
Regime Classification Package
==============================
Pre-market regime classification for the SMC Trading Engine.

Classifies trading days into:
- TREND_UP
- TREND_DOWN
- ROTATIONAL
- HIGH_VOL_EVENT

Includes morning confirmation (9:30–9:45) to validate or downgrade
the pre-market regime based on actual price action.

All modules are deterministic, backtest-compatible, and fully testable.
"""

from smc_trading_engine.regime.premarket_classifier import (
    PremarketClassifier,
    RegimeType,
    DirectionalBias,
)
from smc_trading_engine.regime.regime_controller import (
    RegimeController,
    RegimeControlFlags,
)
from smc_trading_engine.regime.morning_confirmation import (
    OpeningRange,
    ConfirmationResult,
    confirm_regime,
    compute_opening_range,
)

__all__ = [
    "PremarketClassifier",
    "RegimeController",
    "RegimeControlFlags",
    "RegimeType",
    "DirectionalBias",
    "OpeningRange",
    "ConfirmationResult",
    "confirm_regime",
    "compute_opening_range",
]
