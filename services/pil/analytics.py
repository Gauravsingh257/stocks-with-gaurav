"""
services/pil/analytics.py
=========================
Combined-portfolio analytics (Part 4). Answers the cross-engine questions:

  * Which engine contributed the most return / lowest drawdown / best expectancy?
  * How correlated are the engines? (reuses services/pil/exposure)
  * How much diversification benefit does the mix provide?
  * What-if: recompute combined risk/return under arbitrary engine weights.
  * What is the optimal allocation (max-Sharpe / min-vol over history)?
  * Historical playback of the aligned equity curves.

All derived from the reconstructed daily equity curves — descriptive only.
"""

from __future__ import annotations

import math
from itertools import product
from statistics import mean, pstdev
from typing import Any

from services.pil import config as pil_config

_TRADING_DAYS = 252
_ENGINES = ("SWING", "LONGTERM", "MOMENTUM")


def _return_series(books: dict[str, dict]) -> dict[str, dict[str, float]]:
    """Per-engine {date: daily_return}."""
    out: dict[str, dict[str, float]] = {}
    for b in _ENGINES:
        curve = books.get(b, {}).get("equity_curve", [])
        series: dict[str, float] = {}
        for i in range(1, len(curve)):
            prev = curve[i - 1]["value"]
            if prev:
                series[curve[i]["date"]] = curve[i]["value"] / prev - 1.0
        out[b] = series
    return out


def _stats(returns: list[float], rf: float) -> dict[str, float]:
    if len(returns) < 2:
        return {"ann_return_pct": 0.0, "ann_vol_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0}
    mu = mean(returns); sd = pstdev(returns)
    ann_ret = mu * _TRADING_DAYS
    ann_vol = sd * math.sqrt(_TRADING_DAYS)
    # drawdown of the cumulative curve
    cum = 1.0; peak = 1.0; maxdd = 0.0
    for r in returns:
        cum *= (1 + r); peak = max(peak, cum); maxdd = min(maxdd, cum / peak - 1)
    return {
        "ann_return_pct": round(ann_ret * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round((ann_ret - rf) / ann_vol, 2) if ann_vol > 0 else 0.0,
        "max_drawdown_pct": round(maxdd * 100, 2),
    }


def _weighted_returns(series: dict[str, dict[str, float]], weights: dict[str, float]) -> list[float]:
    """Portfolio daily returns on the common date set under `weights`."""
    common = None
    for b in _ENGINES:
        s = set(series[b])
        common = s if common is None else (common & s)
    if not common:
        return []
    dates = sorted(common)
    return [sum(weights.get(b, 0.0) * series[b][d] for b in _ENGINES) for d in dates]


def contribution(books: dict[str, dict]) -> dict[str, Any]:
    """P&L contribution of each engine to the combined book."""
    rows = []
    total_pnl = sum(books.get(b, {}).get("total_pnl", 0.0) for b in _ENGINES) or 1.0
    combined_initial = books.get("COMBINED", {}).get("initial_capital", 0.0) or 1.0
    for b in _ENGINES:
        pnl = books.get(b, {}).get("total_pnl", 0.0)
        rows.append({
            "book": b,
            "pnl": round(pnl, 2),
            "contribution_pct": round(pnl / total_pnl * 100, 2),
            "return_contribution_pct": round(pnl / combined_initial * 100, 2),
        })
    rows.sort(key=lambda x: x["pnl"], reverse=True)
    return {"rows": rows, "top_contributor": rows[0]["book"] if rows else None}


def diversification_benefit(books: dict[str, dict], rf: float) -> dict[str, Any]:
    """Combined vol vs the capital-weighted average of the standalone vols. A
    lower combined vol => real diversification benefit."""
    series = _return_series(books)
    caps = pil_config.all_book_capital()
    total_cap = sum(caps.values()) or 1.0
    weights = {b: caps[b] / total_cap for b in _ENGINES}

    per_book_vol = {}
    weighted_vol = 0.0
    for b in _ENGINES:
        st = _stats(list(series[b].values()), rf)
        per_book_vol[b] = st["ann_vol_pct"]
        weighted_vol += weights[b] * st["ann_vol_pct"]

    combined_ret = _weighted_returns(series, weights)
    combined_vol = _stats(combined_ret, rf)["ann_vol_pct"]
    benefit = round(weighted_vol - combined_vol, 2)
    return {
        "weights": {b: round(weights[b], 4) for b in _ENGINES},
        "per_book_vol_pct": per_book_vol,
        "weighted_avg_vol_pct": round(weighted_vol, 2),
        "combined_vol_pct": combined_vol,
        "diversification_benefit_pct": benefit,
        "diversification_ratio": round(combined_vol / weighted_vol, 3) if weighted_vol else 1.0,
    }


def what_if(books: dict[str, dict], weights: dict[str, float], rf: float | None = None) -> dict[str, Any]:
    """Recompute combined risk/return under arbitrary engine weights."""
    rf = pil_config.risk_free_rate() if rf is None else rf
    total = sum(max(0.0, weights.get(b, 0.0)) for b in _ENGINES) or 1.0
    w = {b: max(0.0, weights.get(b, 0.0)) / total for b in _ENGINES}
    series = _return_series(books)
    rets = _weighted_returns(series, w)
    return {"weights": {b: round(w[b], 4) for b in _ENGINES}, **_stats(rets, rf)}


def optimal_allocation(books: dict[str, dict], rf: float | None = None, step: float = 0.05) -> dict[str, Any]:
    """Grid-search engine weights (summing to 1) for max Sharpe and min vol over
    the historical daily returns. Coarse but robust; no external optimiser."""
    rf = pil_config.risk_free_rate() if rf is None else rf
    series = _return_series(books)
    grid = [round(i * step, 4) for i in range(int(1 / step) + 1)]
    best_sharpe = {"sharpe": -1e9}
    best_minvol = {"ann_vol_pct": 1e9}
    for ws, wl in product(grid, grid):
        wm = round(1 - ws - wl, 4)
        if wm < -1e-9 or wm > 1 + 1e-9:
            continue
        w = {"SWING": ws, "LONGTERM": wl, "MOMENTUM": max(0.0, wm)}
        rets = _weighted_returns(series, w)
        if len(rets) < 2:
            continue
        st = _stats(rets, rf)
        if st["sharpe"] > best_sharpe["sharpe"]:
            best_sharpe = {"weights": w, **st}
        if 0 < st["ann_vol_pct"] < best_minvol["ann_vol_pct"]:
            best_minvol = {"weights": w, **st}
    return {
        "max_sharpe": best_sharpe if "weights" in best_sharpe else None,
        "min_vol": best_minvol if "weights" in best_minvol else None,
        "current": what_if(books, {b: pil_config.all_book_capital()[b] for b in _ENGINES}, rf),
    }


def playback(books: dict[str, dict], start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Aligned per-engine + combined equity curves for historical playback."""
    def _filt(curve):
        return [p for p in curve if (not start or p["date"] >= start) and (not end or p["date"] <= end)]
    return {
        "engines": {b: _filt(books.get(b, {}).get("equity_curve", [])) for b in _ENGINES},
        "combined": _filt(books.get("COMBINED", {}).get("equity_curve", [])),
    }


def compute(books: dict[str, dict]) -> dict[str, Any]:
    """Full Part-4 analytics payload."""
    from services.pil import exposure, metrics
    rf = pil_config.risk_free_rate()
    met = metrics.metrics_all(books)
    # engine leaderboard
    leaderboard = {
        "highest_return": max(_ENGINES, key=lambda b: met[b]["total_return_pct"]),
        "lowest_drawdown": max(_ENGINES, key=lambda b: met[b]["max_drawdown_pct"]),  # closest to 0
        "highest_expectancy": max(_ENGINES, key=lambda b: met[b]["expectancy_pct"]),
        "highest_sharpe": max(_ENGINES, key=lambda b: met[b]["sharpe"]),
    }
    return {
        "contribution": contribution(books),
        "correlation": exposure.correlation_matrix(books),
        "diversification": diversification_benefit(books, rf),
        "optimal": optimal_allocation(books, rf),
        "leaderboard": leaderboard,
        "per_engine": {b: {"total_return_pct": met[b]["total_return_pct"],
                           "max_drawdown_pct": met[b]["max_drawdown_pct"],
                           "sharpe": met[b]["sharpe"],
                           "expectancy_pct": met[b]["expectancy_pct"]} for b in _ENGINES},
    }
