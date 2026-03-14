"""
Performance Metrics
===================
Analyzes backtest results: win rate, expectancy, drawdown, profit factor, etc.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class PerformanceReport:
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_rr: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_bars_held: float = 0.0


def compute_metrics(trades: List[Dict]) -> PerformanceReport:
    """
    Compute full performance metrics from trade list.
    Each trade dict should have: pnl, result, rr, bars_held
    """
    r = PerformanceReport()
    if not trades:
        return r

    pnls = [t.get("pnl", 0) for t in trades]
    results = [t.get("result", "") for t in trades]
    rrs = [t.get("rr", 0) for t in trades]
    bars = [t.get("bars_held", 0) for t in trades]

    r.total_trades = len(trades)
    r.win_count = results.count("WIN")
    r.loss_count = results.count("LOSS")
    total = r.win_count + r.loss_count
    r.win_rate = round(r.win_count / total * 100, 2) if total else 0
    r.total_pnl = round(sum(pnls), 2)

    wins = [p for p, res in zip(pnls, results) if res == "WIN"]
    losses = [abs(p) for p, res in zip(pnls, results) if res == "LOSS"]

    r.avg_win = round(np.mean(wins), 2) if wins else 0
    r.avg_loss = round(np.mean(losses), 2) if losses else 0
    r.avg_rr = round(np.mean([rr for rr in rrs if rr > 0]), 2) if rrs else 0
    r.best_trade = round(max(pnls), 2) if pnls else 0
    r.worst_trade = round(min(pnls), 2) if pnls else 0
    r.avg_bars_held = round(np.mean(bars), 1) if bars else 0

    # Expectancy
    wr = r.win_rate / 100
    r.expectancy = round(wr * r.avg_win - (1 - wr) * r.avg_loss, 2)

    # Profit factor
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    r.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

    # Sharpe
    if len(pnls) > 1 and np.std(pnls) > 0:
        r.sharpe_ratio = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252), 2)

    # Max drawdown
    r.max_drawdown_pct = _compute_max_drawdown(pnls)

    # Consecutive wins/losses
    r.max_consecutive_wins, r.max_consecutive_losses = _consecutive_streaks(results)

    return r


def _compute_max_drawdown(pnls: List[float]) -> float:
    """Compute max drawdown from PnL series."""
    if not pnls:
        return 0.0
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    max_dd = np.max(drawdown) if len(drawdown) > 0 else 0
    peak_val = np.max(peak) if np.max(peak) > 0 else 1
    return round(max_dd / peak_val * 100, 2)


def _consecutive_streaks(results: List[str]):
    """Compute max consecutive wins and losses."""
    max_w = max_l = cur_w = cur_l = 0
    for r in results:
        if r == "WIN":
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif r == "LOSS":
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = cur_l = 0
    return max_w, max_l


def print_report(report: PerformanceReport):
    """Print formatted performance report."""
    print("\n" + "=" * 50)
    print("  BACKTEST PERFORMANCE REPORT")
    print("=" * 50)
    print(f"  Total Trades:        {report.total_trades}")
    print(f"  Wins:                {report.win_count}")
    print(f"  Losses:              {report.loss_count}")
    print(f"  Win Rate:            {report.win_rate}%")
    print(f"  Average RR:          {report.avg_rr}")
    print(f"  Average Win:         {report.avg_win}")
    print(f"  Average Loss:        {report.avg_loss}")
    print(f"  Best Trade:          {report.best_trade}")
    print(f"  Worst Trade:         {report.worst_trade}")
    print(f"  Total PnL:           {report.total_pnl}")
    print(f"  Max Drawdown:        {report.max_drawdown_pct}%")
    print(f"  Expectancy:          {report.expectancy}")
    print(f"  Profit Factor:       {report.profit_factor}")
    print(f"  Sharpe Ratio:        {report.sharpe_ratio}")
    print(f"  Max Consec Wins:     {report.max_consecutive_wins}")
    print(f"  Max Consec Losses:   {report.max_consecutive_losses}")
    print(f"  Avg Bars Held:       {report.avg_bars_held}")
    print("=" * 50 + "\n")


def export_report_csv(report: PerformanceReport, path: str):
    """Export performance report to CSV."""
    data = {
        "Metric": [
            "Total Trades", "Wins", "Losses", "Win Rate %",
            "Average RR", "Average Win", "Average Loss",
            "Total PnL", "Max Drawdown %", "Expectancy",
            "Profit Factor", "Sharpe Ratio",
            "Max Consec Wins", "Max Consec Losses", "Avg Bars Held"
        ],
        "Value": [
            report.total_trades, report.win_count, report.loss_count,
            report.win_rate, report.avg_rr, report.avg_win, report.avg_loss,
            report.total_pnl, report.max_drawdown_pct, report.expectancy,
            report.profit_factor, report.sharpe_ratio,
            report.max_consecutive_wins, report.max_consecutive_losses,
            report.avg_bars_held
        ]
    }
    pd.DataFrame(data).to_csv(path, index=False)
