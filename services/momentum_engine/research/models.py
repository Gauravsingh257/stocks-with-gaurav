"""
services/momentum_engine/research/models.py
============================================
Schemas for the Momentum research + backtesting framework.

The centrepiece is `ExperimentRecord` — the "gold" data model. EVERY simulated
signal stores WHY it qualified / ranked / entered / exited plus its full feature
vector and realised outcome. Two years of these become a labelled dataset the
engine can be *re-fit* on, instead of tuned by intuition.

All records are plain, serialisable dataclasses. No behaviour, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(slots=True, frozen=True)
class SimConfig:
    """One fully-specified experiment configuration — the independent variables
    a sweep varies. Every field is a knob; `config_id` is a stable hash for
    grouping results."""
    entry_models: tuple[str, ...] = ("vcp", "breakout", "shallow_pullback")
    stop_method: str = "structural"
    stop_params: dict[str, Any] = field(default_factory=dict)
    trail_method: str = "atr_chandelier"
    trail_params: dict[str, Any] = field(default_factory=dict)
    breakeven_at_r: float = 1.0          # move stop to breakeven after +NR
    max_hold_bars: int = 40
    max_arm_bars: int = 10               # cancel if the trigger isn't tapped within N bars
    ranking_weights: dict[str, float] = field(default_factory=dict)  # overrides MOM_W_* (empty = config default)
    regime_filter: tuple[str, ...] = ()  # empty = all regimes
    label: str = ""

    def config_id(self) -> str:
        import hashlib, json
        blob = json.dumps({
            "e": sorted(self.entry_models), "s": self.stop_method, "sp": self.stop_params,
            "t": self.trail_method, "tp": self.trail_params, "be": self.breakeven_at_r,
            "mh": self.max_hold_bars, "ma": self.max_arm_bars, "rw": self.ranking_weights,
            "rf": sorted(self.regime_filter),
        }, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class SimTrade:
    """Outcome of simulating one entry plan forward on real OHLC."""
    symbol: str
    entered: bool
    entry_price: float | None
    exit_price: float | None
    exit_reason: str          # STOP | TRAIL | BREAKEVEN | FAILED_BREAKOUT | TARGET | TIME | NOT_TRIGGERED
    r_multiple: float | None  # (exit-entry)/(entry-initial_stop)
    hold_bars: int
    mfe_r: float | None       # max favourable excursion in R
    mae_r: float | None       # max adverse excursion in R
    initial_stop: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class ExperimentRecord:
    """The persistent, queryable record of ONE momentum experiment — features +
    every 'why' + outcome. This is the training dataset."""
    run_id: str
    config_id: str
    symbol: str
    horizon: str
    scan_date: str
    # context
    regime: str | None
    sector: str | None
    # features (the model inputs)
    rs_20d: float | None
    atr_pct: float | None
    extension_atr: float | None
    trend_quality: float | None
    base_atr_pct: float | None
    breakout_score: float | None
    volume_ratio: float | None
    quality_score: float | None
    entry_model: str | None
    stop_method: str | None
    trail_method: str | None
    # explanations (WHY)
    why_qualified: dict[str, Any]
    why_ranked: dict[str, Any]
    why_entered: str | None
    why_exited: str | None
    # outcome (the label)
    entered: bool
    r_multiple: float | None
    hold_bars: int | None
    mfe_r: float | None
    mae_r: float | None
    outcome: str              # WIN | LOSS | SCRATCH | NO_ENTRY
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class BacktestResult:
    config_id: str
    label: str
    metrics: dict[str, Any]
    n_candidates: int
    n_entered: int
    by_regime: dict[str, Any] = field(default_factory=dict)
    by_sector: dict[str, Any] = field(default_factory=dict)
    by_entry_model: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
