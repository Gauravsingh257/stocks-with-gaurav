"""
Risk Management
===============
Position sizing, SL/TP placement, RR enforcement, and daily loss limits.
"""

import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ─── DEFAULTS ────────────────────────────────────────
MIN_RR_RATIO = 2.0  # Aligned with grid search optimal RR
DEFAULT_RISK_PCT = 0.01        # 1% per trade
MAX_DAILY_LOSS_PCT = 0.05      # 5% daily loss limit
DEFAULT_ACCOUNT_SIZE = 100_000
SLIPPAGE_PCT = 0.001           # 0.1% slippage
BROKERAGE_PER_TRADE = 40.0     # INR per side


@dataclass
class RiskParams:
    account_size: float = DEFAULT_ACCOUNT_SIZE
    risk_pct: float = DEFAULT_RISK_PCT
    min_rr: float = MIN_RR_RATIO
    max_daily_loss_pct: float = MAX_DAILY_LOSS_PCT
    slippage_pct: float = SLIPPAGE_PCT
    brokerage: float = BROKERAGE_PER_TRADE

    @property
    def risk_amount(self) -> float:
        return self.account_size * self.risk_pct

    @property
    def max_daily_loss(self) -> float:
        return self.account_size * self.max_daily_loss_pct


class RiskManager:
    """Centralized risk management for the trading engine."""

    def __init__(self, params: RiskParams = None):
        self.params = params or RiskParams()
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.max_trades_per_day = 100 # [V4] Limit Removed (User Request)

    def calculate_position_size(self, entry: float, stop_loss: float) -> int:
        """Dynamic position sizing based on % risk and SL distance."""
        risk_per_unit = abs(entry - stop_loss)
        if risk_per_unit <= 0:
            return 0
        raw_qty = self.params.risk_amount / risk_per_unit
        return max(1, int(raw_qty))

    def compute_sl_target(self, direction: str, entry: float,
                          ob_low: float, ob_high: float,
                          atr: float) -> Tuple[float, float]:
        """
        Compute SL and Target enforcing minimum RR.
        SL = beyond OB edge + ATR buffer.
        Target = entry + risk * min_rr.
        """
        buffer = atr * 0.3
        if direction == "LONG":
            sl = ob_low - buffer
            risk = entry - sl
            target = entry + (risk * self.params.min_rr)
        else:
            sl = ob_high + buffer
            risk = sl - entry
            target = entry - (risk * self.params.min_rr)
        return round(sl, 2), round(target, 2)

    def calculate_rr(self, entry: float, sl: float,
                     target: float, direction: str) -> float:
        """Calculate actual risk-reward ratio."""
        if direction == "LONG":
            risk = entry - sl
            reward = target - entry
        else:
            risk = sl - entry
            reward = entry - target
        if risk <= 0:
            return 0.0
        return round(reward / risk, 2)

    def passes_rr_filter(self, entry: float, sl: float,
                         target: float, direction: str) -> bool:
        """Check if trade meets minimum RR requirement."""
        return self.calculate_rr(entry, sl, target, direction) >= self.params.min_rr

    def can_take_trade(self) -> Tuple[bool, str]:
        """Check daily risk limits."""
        if self.daily_pnl <= -self.params.max_daily_loss:
            return False, "MAX_DAILY_LOSS_HIT"
        if self.trades_today >= self.max_trades_per_day:
            return False, "MAX_TRADES_HIT"
        return True, "OK"

    def record_trade_result(self, pnl: float):
        self.daily_pnl += pnl
        self.trades_today += 1

    def apply_slippage(self, price: float, direction: str, is_entry: bool) -> float:
        """Apply slippage to a price (adverse fill)."""
        slip = price * self.params.slippage_pct
        if is_entry:
            return price + slip if direction == "LONG" else price - slip
        else:
            return price - slip if direction == "LONG" else price + slip

    def total_costs(self) -> float:
        """Total brokerage for entry + exit."""
        return self.params.brokerage * 2

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.trades_today = 0

    def get_summary(self) -> Dict:
        return {
            "account_size": self.params.account_size,
            "risk_pct": self.params.risk_pct,
            "risk_amount": self.params.risk_amount,
            "min_rr": self.params.min_rr,
            "daily_pnl": self.daily_pnl,
            "trades_today": self.trades_today,
            "can_trade": self.can_take_trade()[0],
        }
