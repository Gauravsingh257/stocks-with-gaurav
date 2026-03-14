"""
strategies/base_strategy.py — Abstract base for all trading strategies.

Provides a standard interface so strategies are interchangeable in the
backtest engine, signal scanner, and live execution loop.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Signal:
    """Standardized trading signal output."""
    symbol: str
    direction: str  # "LONG" | "SHORT"
    setup: str
    entry: float
    stop_loss: float
    target: float
    rr: float
    confidence: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)

    @property
    def risk_points(self) -> float:
        return abs(self.entry - self.stop_loss)

    @property
    def reward_points(self) -> float:
        return abs(self.target - self.entry)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "setup": self.setup,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "rr": round(self.rr, 2),
            "confidence": round(self.confidence, 2),
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class BaseStrategy(ABC):
    """Abstract base for all trading strategies."""

    name: str = "BaseStrategy"
    version: str = "1.0.0"
    description: str = ""
    timeframes: list[str] = ["5minute"]
    enabled: bool = True

    @abstractmethod
    def detect(
        self,
        symbol: str,
        ltf_candles: list[dict],
        htf_candles: list[dict],
        current_time: Optional[datetime] = None,
    ) -> Optional[Signal]:
        """
        Analyze candle data and return a Signal if conditions are met,
        or None if no setup is detected.
        """
        ...

    @abstractmethod
    def validate(self, signal: Signal) -> bool:
        """
        Apply additional filters (time of day, volume, regime) to confirm
        whether the signal should be acted upon.
        """
        ...

    def __repr__(self) -> str:
        status = "ON" if self.enabled else "OFF"
        return f"<{self.name} v{self.version} [{status}]>"
