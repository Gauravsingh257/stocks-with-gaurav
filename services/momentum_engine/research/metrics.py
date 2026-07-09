"""
services/momentum_engine/research/metrics.py
=============================================
Performance metrics + attribution from a list of SimTrades. Everything is in
R-multiples (risk-normalised) so configs with different stop widths compare
fairly. Pure functions.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Callable

from .models import SimTrade


def performance(trades: list[SimTrade]) -> dict[str, Any]:
    """Core metric block for a set of trades. Only entered trades count toward
    performance; `n_candidates`/`entry_rate` are tracked by the caller."""
    entered = [t for t in trades if t.entered and t.r_multiple is not None]
    n = len(entered)
    if n == 0:
        return {"n_trades": 0, "win_rate": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0,
                "avg_win_r": 0.0, "avg_loss_r": 0.0, "avg_hold": 0.0, "max_drawdown_r": 0.0,
                "total_r": 0.0, "avg_mfe_r": 0.0, "avg_mae_r": 0.0}
    rs = [t.r_multiple for t in entered]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    # Max drawdown of the cumulative-R equity curve.
    cum = 0.0; peak = 0.0; max_dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    return {
        "n_trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "expectancy_r": round(mean(rs), 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "avg_win_r": round(mean(wins), 3) if wins else 0.0,
        "avg_loss_r": round(mean(losses), 3) if losses else 0.0,
        "avg_hold": round(mean([t.hold_bars for t in entered]), 1),
        "max_drawdown_r": round(max_dd, 2),
        "total_r": round(sum(rs), 2),
        "avg_mfe_r": round(mean([t.mfe_r for t in entered if t.mfe_r is not None]), 2) if entered else 0.0,
        "avg_mae_r": round(mean([t.mae_r for t in entered if t.mae_r is not None]), 2) if entered else 0.0,
    }


def attribute(trades: list[SimTrade], key: Callable[[SimTrade], str | None]) -> dict[str, Any]:
    """Group trades by a dimension (regime/sector/entry-model — supplied via
    `key`) and compute performance per group."""
    groups: dict[str, list[SimTrade]] = {}
    for t in trades:
        k = key(t) or "unknown"
        groups.setdefault(k, []).append(t)
    return {k: performance(v) for k, v in sorted(groups.items())}


def exit_reason_breakdown(trades: list[SimTrade]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in trades:
        out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
    return out
