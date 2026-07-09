"""
services/momentum_engine/research
=================================
Quantitative research + backtesting framework for the Momentum Engine.

Proves / disproves / optimises / parameterises every component BEFORE any
production integration. Isolated research tooling — reads history and simulates;
never touches the live portfolio or the SMC engine.

Capabilities:
  * multiple entry models (reuses the engine registry)
  * multiple stop + trailing methodologies (pluggable registries)
  * config-driven backtest with per-experiment recording (the "gold" dataset)
  * ranking-weight + allocation + parameter sensitivity sweeps
  * regime / sector / entry-model attribution
  * walk-forward + out-of-sample validation
  * durable ExperimentRecord store for continuous, evidence-based improvement

Entry points:
    from services.momentum_engine.research import (
        SimConfig, run_backtest, compare_configs, walk_forward_folds,
        time_split, sensitivity, experiment_store, stops, trailing,
    )
"""

from __future__ import annotations

from . import experiment_store, metrics, simulator, stops, trailing
from .backtest import (
    run_backtest, compare_configs, walk_forward_folds, time_split, sensitivity,
)
from .models import SimConfig, SimTrade, ExperimentRecord, BacktestResult

__all__ = [
    "SimConfig", "SimTrade", "ExperimentRecord", "BacktestResult",
    "run_backtest", "compare_configs", "walk_forward_folds", "time_split", "sensitivity",
    "experiment_store", "metrics", "simulator", "stops", "trailing",
]
