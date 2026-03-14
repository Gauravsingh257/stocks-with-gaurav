from dataclasses import dataclass, field


@dataclass(slots=True)
class RunningTrade:
    id: int | None
    symbol: str
    entry_price: float
    stop_loss: float
    targets: list[float] = field(default_factory=list)
    current_price: float = 0.0
    profit_loss: float = 0.0
    drawdown: float = 0.0
    distance_to_target: float | None = None
    distance_to_stop_loss: float | None = None
    status: str = "RUNNING"
    created_at: str | None = None
    updated_at: str | None = None
    recommendation_id: int | None = None
