from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StockRecommendation:
    id: int | None
    symbol: str
    agent_type: str
    entry_price: float
    stop_loss: float | None
    targets: list[float] = field(default_factory=list)
    confidence_score: float = 0.0
    reasoning: str = ""
    created_at: str | None = None
    setup: str | None = None
    expected_holding_period: str | None = None
    technical_signals: dict[str, Any] = field(default_factory=dict)
    fundamental_signals: dict[str, Any] = field(default_factory=dict)
    sentiment_signals: dict[str, Any] = field(default_factory=dict)
    technical_factors: dict[str, Any] = field(default_factory=dict)
    fundamental_factors: dict[str, Any] = field(default_factory=dict)
    sentiment_factors: dict[str, Any] = field(default_factory=dict)
    fair_value_estimate: float | None = None
    entry_zone: list[float] = field(default_factory=list)
    long_term_target: float | None = None
    risk_factors: list[str] = field(default_factory=list)
