"""
Paper Trading
=============
Simulated order execution with position tracking and PnL logging.
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import dataclass, field

from smc_trading_engine.strategy.risk_management import RiskManager

logger = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    target: float
    quantity: int
    entry_time: datetime = None
    exit_time: datetime = None
    exit_price: float = 0.0
    pnl: float = 0.0
    status: str = "OPEN"  # "OPEN", "CLOSED_WIN", "CLOSED_LOSS", "CLOSED_MANUAL"


class PaperTrader:
    """Simulated order execution for testing strategies without risk."""

    def __init__(self, risk_mgr: RiskManager = None):
        self.risk_mgr = risk_mgr or RiskManager()
        self.positions: List[PaperPosition] = []
        self.closed_positions: List[PaperPosition] = []
        self.trade_log: List[Dict] = []

    def place_order(self, signal: Dict) -> Optional[PaperPosition]:
        """Place a simulated order from a signal dict."""
        if not signal:
            return None

        qty = signal.get("position_size", 1)
        pos = PaperPosition(
            symbol=signal["symbol"],
            direction=signal["direction"],
            entry_price=signal["entry"],
            stop_loss=signal["stop_loss"],
            target=signal["target"],
            quantity=qty,
            entry_time=datetime.now()
        )
        self.positions.append(pos)
        logger.info(f"[PAPER] Opened {pos.direction} {pos.symbol} @ {pos.entry_price} qty={qty}")
        return pos

    def update_positions(self, price_data: Dict[str, float]):
        """
        Check all open positions against current prices.
        price_data: {symbol: current_price}
        """
        for pos in self.positions[:]:
            if pos.status != "OPEN":
                continue
            price = price_data.get(pos.symbol)
            if price is None:
                continue

            if pos.direction == "LONG":
                if price <= pos.stop_loss:
                    self._close(pos, pos.stop_loss, "CLOSED_LOSS")
                elif price >= pos.target:
                    self._close(pos, pos.target, "CLOSED_WIN")
            else:
                if price >= pos.stop_loss:
                    self._close(pos, pos.stop_loss, "CLOSED_LOSS")
                elif price <= pos.target:
                    self._close(pos, pos.target, "CLOSED_WIN")

    def _close(self, pos: PaperPosition, exit_price: float, status: str):
        if pos.direction == "LONG":
            pos.pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pos.pnl = (pos.entry_price - exit_price) * pos.quantity

        pos.exit_price = exit_price
        pos.exit_time = datetime.now()
        pos.status = status
        self.positions.remove(pos)
        self.closed_positions.append(pos)
        self.risk_mgr.record_trade_result(pos.pnl)
        self.trade_log.append(self._to_dict(pos))
        result = "WIN" if "WIN" in status else "LOSS"
        logger.info(f"[PAPER] Closed {pos.symbol} {result} PnL={pos.pnl:.2f}")

    def close_all(self, price_data: Dict[str, float]):
        for pos in self.positions[:]:
            price = price_data.get(pos.symbol, pos.entry_price)
            self._close(pos, price, "CLOSED_MANUAL")

    def get_open_positions(self) -> List[Dict]:
        return [self._to_dict(p) for p in self.positions]

    def get_trade_log(self) -> List[Dict]:
        return self.trade_log

    def get_total_pnl(self) -> float:
        return sum(p.pnl for p in self.closed_positions)

    def _to_dict(self, pos: PaperPosition) -> Dict:
        return {
            "symbol": pos.symbol, "direction": pos.direction,
            "entry": pos.entry_price, "exit": pos.exit_price,
            "sl": pos.stop_loss, "tp": pos.target,
            "qty": pos.quantity, "pnl": round(pos.pnl, 2),
            "status": pos.status,
            "entry_time": str(pos.entry_time),
            "exit_time": str(pos.exit_time),
        }
