"""
services/momentum_engine/research/backtest.py
==============================================
The backtest harness. Runs a SimConfig over a set of prepared samples using the
REAL engine logic (eligibility → entry models → ranking), simulates each accepted
signal forward, records an ExperimentRecord for every one, and returns a
BacktestResult with metrics + attribution.

Also provides walk-forward / out-of-sample splitting, parameter sensitivity, and
multi-config comparison. Pure research tooling — never touches the portfolio.

A "prepared sample" is a dict:
    {symbol, scan_date, regime, sector, history:[candles<=scan], forward:[candles>scan], nifty:[...]}
"""

from __future__ import annotations

import contextlib
import logging
import os
from datetime import datetime, timezone, timedelta
from statistics import mean
from typing import Any

from .. import eligibility, entry_models, ranking
from ..metrics import compute_metrics
from . import simulator
from .models import BacktestResult, ExperimentRecord, SimConfig

log = logging.getLogger("services.momentum_engine.research.backtest")
_IST = timezone(timedelta(hours=5, minutes=30))


@contextlib.contextmanager
def _config_env(config: SimConfig):
    """Temporarily apply a config's entry-model list + ranking weights to the
    environment so the REAL engine functions honour them, then restore. This is
    what lets a sweep vary those knobs without reimplementing the engine."""
    saved: dict[str, str | None] = {}

    def _set(k: str, v: str):
        saved[k] = os.environ.get(k)
        os.environ[k] = v

    _set("MOM_ENTRY_MODELS", ",".join(config.entry_models))
    for name, val in (config.ranking_weights or {}).items():
        _set(f"MOM_W_{name.upper()}", str(val))
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _outcome(trade) -> str:
    if not trade.entered:
        return "NO_ENTRY"
    if trade.r_multiple is None:
        return "NO_ENTRY"
    if trade.r_multiple > 0.1:
        return "WIN"
    if trade.r_multiple < -0.1:
        return "LOSS"
    return "SCRATCH"


def run_backtest(samples: list[dict], config: SimConfig, run_id: str | None = None,
                 sector_score_provider=None) -> tuple[BacktestResult, list[ExperimentRecord]]:
    run_id = run_id or f"bt_{datetime.now(_IST).strftime('%Y%m%d%H%M%S')}_{config.config_id()}"
    records: list[ExperimentRecord] = []
    trades = []

    with _config_env(config):
        for s in samples:
            regime = s.get("regime")
            if config.regime_filter and (regime or "").upper() not in {r.upper() for r in config.regime_filter}:
                continue
            history = s.get("history") or []
            m = compute_metrics(history, s.get("nifty"))
            if m is None:
                continue
            elig = eligibility.evaluate(m)
            if not elig.passed:
                continue
            sig = entry_models.detect_entry(m, history)
            if sig is None:
                continue
            ss = sector_score_provider(s["symbol"]) if sector_score_provider else 0.5
            q = ranking.score(m, sig, discovery_breakout_score=s.get("breakout_score"), sector_score=ss)

            trade = simulator.simulate_trade(
                s["symbol"], sig.trigger, sig.base_low, m.get("atr") or 0.0,
                s.get("forward") or [], config)
            trades.append((trade, regime, s.get("sector"), sig.model))

            records.append(ExperimentRecord(
                run_id=run_id, config_id=config.config_id(), symbol=s["symbol"],
                horizon=s.get("horizon", "SWING"), scan_date=s.get("scan_date", ""),
                regime=regime, sector=s.get("sector"),
                rs_20d=m.get("rs_20d"), atr_pct=m.get("atr_pct"),
                extension_atr=m.get("extension_atr"), trend_quality=m.get("trend_quality"),
                base_atr_pct=m.get("base_atr_pct"), breakout_score=s.get("breakout_score"),
                volume_ratio=m.get("volume_ratio"), quality_score=q.score,
                entry_model=sig.model, stop_method=config.stop_method, trail_method=config.trail_method,
                why_qualified=elig.to_dict(), why_ranked=q.to_dict(),
                why_entered=sig.reason, why_exited=trade.exit_reason,
                entered=trade.entered, r_multiple=trade.r_multiple, hold_bars=trade.hold_bars,
                mfe_r=trade.mfe_r, mae_r=trade.mae_r, outcome=_outcome(trade),
            ))

    from .metrics import performance
    plain = [t[0] for t in trades]
    result = BacktestResult(
        config_id=config.config_id(), label=config.label or config.config_id(),
        metrics=performance(plain), n_candidates=len(samples),
        n_entered=sum(1 for t in plain if t.entered),
        by_regime=_attr(trades, 1), by_sector=_attr(trades, 2), by_entry_model=_attr(trades, 3),
    )
    log.info("[Backtest] %s: cand=%d entered=%d exp_r=%s PF=%s DD=%s",
             config.label or config.config_id(), result.n_candidates, result.n_entered,
             result.metrics.get("expectancy_r"), result.metrics.get("profit_factor"),
             result.metrics.get("max_drawdown_r"))
    return result, records


def _attr(trades: list[tuple], dim_idx: int) -> dict[str, Any]:
    """Per-group (regime/sector/model) n / win_rate / expectancy from R."""
    groups: dict[str, list[float]] = {}
    for t in trades:
        entered = t[0].entered and t[0].r_multiple is not None
        if not entered:
            continue
        groups.setdefault(str(t[dim_idx] or "unknown"), []).append(t[0].r_multiple)
    return {
        k: {"n": len(v), "win_rate": round(sum(1 for r in v if r > 0) / len(v) * 100, 1),
            "expectancy_r": round(mean(v), 3), "total_r": round(sum(v), 2)}
        for k, v in sorted(groups.items())
    }


# ── Validation utilities ─────────────────────────────────────────────────────
def time_split(samples: list[dict], train_frac: float = 0.7) -> tuple[list[dict], list[dict]]:
    """Out-of-sample split by scan_date (chronological, no leakage)."""
    ordered = sorted(samples, key=lambda s: s.get("scan_date") or "")
    cut = int(len(ordered) * train_frac)
    return ordered[:cut], ordered[cut:]


def walk_forward_folds(samples: list[dict], k: int = 4) -> list[tuple[list[dict], list[dict]]]:
    """k sequential expanding-window folds: train on all data before a fold,
    test on the fold. Chronological, leakage-free."""
    ordered = sorted(samples, key=lambda s: s.get("scan_date") or "")
    n = len(ordered)
    if n < k or k < 2:
        return []
    size = n // k
    folds = []
    for i in range(1, k):
        train = ordered[: i * size]
        test = ordered[i * size: (i + 1) * size]
        if train and test:
            folds.append((train, test))
    return folds


def compare_configs(samples: list[dict], configs: list[SimConfig],
                    run_id: str | None = None) -> list[BacktestResult]:
    """Run many configs over the same samples; sort best-first by expectancy then
    profit factor. The core of parameter/methodology optimisation."""
    results = [run_backtest(samples, c, run_id=run_id)[0] for c in configs]
    results.sort(key=lambda r: (r.metrics.get("expectancy_r", 0), r.metrics.get("profit_factor", 0)),
                 reverse=True)
    return results


def sensitivity(samples: list[dict], base: SimConfig, param_path: str,
                values: list[Any]) -> list[dict]:
    """One-factor-at-a-time sensitivity: vary `param_path` (e.g. 'stop_params.k'
    or 'breakeven_at_r') across `values`, hold everything else, report metrics.
    Reveals whether an edge is robust or a knife-edge fit."""
    import dataclasses
    out = []
    for v in values:
        kwargs: dict[str, Any] = {}
        if "." in param_path:
            top, sub = param_path.split(".", 1)
            d = dict(getattr(base, top) or {}); d[sub] = v
            kwargs[top] = d
        else:
            kwargs[param_path] = v
        cfg = dataclasses.replace(base, label=f"{param_path}={v}", **kwargs)
        res, _ = run_backtest(samples, cfg)
        out.append({"value": v, "metrics": res.metrics, "n_entered": res.n_entered})
    return out
