"""
scripts/evaluate_performance.py — Strategy performance evaluator.

Reads backtest results or live trade logs and generates comprehensive
performance metrics, equity curves, and drawdown analysis.

Usage:
    python scripts/evaluate_performance.py
    python scripts/evaluate_performance.py --input backtest_results/all_trades.csv
    python scripts/evaluate_performance.py --input trade_ledger_2026.csv --live
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger("performance")

REPORTS_DIR = Path(__file__).parent.parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate Strategy Performance")
    parser.add_argument("--input", type=str, default="backtest_results/all_trades.csv")
    parser.add_argument("--live", action="store_true", help="Parse as live trade ledger")
    parser.add_argument("--initial-capital", type=float, default=100_000)
    parser.add_argument("--risk-per-trade", type=float, default=0.02, help="Fraction of capital risked")
    return parser.parse_args()


def load_trades(input_path: str, is_live: bool) -> pd.DataFrame:
    df = pd.read_csv(input_path)
    logger.info("Loaded %d trades from %s", len(df), input_path)
    return df


def calculate_metrics(df: pd.DataFrame, initial_capital: float, risk_pct: float) -> dict:
    if df.empty:
        return {"error": "No trades to evaluate"}

    r_col = None
    for candidate in ["r_multiple", "pnl_r", "R"]:
        if candidate in df.columns:
            r_col = candidate
            break

    if r_col is None:
        logger.error("No R-multiple column found in data")
        return {"error": "No R-multiple column"}

    r_values = pd.to_numeric(df[r_col], errors="coerce").dropna()

    total_r = r_values.sum()
    winners = r_values[r_values > 0]
    losers = r_values[r_values <= 0]

    cumulative_r = r_values.cumsum()
    peak = cumulative_r.cummax()
    drawdown = cumulative_r - peak
    max_drawdown_r = drawdown.min()

    risk_per_trade = initial_capital * risk_pct
    equity_curve = initial_capital + (cumulative_r * risk_per_trade)
    equity_peak = equity_curve.cummax()
    dd_pct = ((equity_curve - equity_peak) / equity_peak * 100)

    streak_max_win = 0
    streak_max_loss = 0
    current_streak = 0
    for r in r_values:
        if r > 0:
            current_streak = max(current_streak, 0) + 1
            streak_max_win = max(streak_max_win, current_streak)
        else:
            current_streak = min(current_streak, 0) - 1
            streak_max_loss = max(streak_max_loss, abs(current_streak))

    gross_profit = winners.sum()
    gross_loss = abs(losers.sum())

    metrics = {
        "total_trades": len(r_values),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate_pct": round(len(winners) / len(r_values) * 100, 1) if len(r_values) > 0 else 0,
        "total_r": round(float(total_r), 2),
        "avg_r_per_trade": round(float(r_values.mean()), 3),
        "median_r": round(float(r_values.median()), 3),
        "std_r": round(float(r_values.std()), 3),
        "profit_factor": round(float(gross_profit / gross_loss), 2) if gross_loss > 0 else float("inf"),
        "expectancy_r": round(float(r_values.mean()), 3),
        "max_win_r": round(float(winners.max()), 2) if len(winners) > 0 else 0,
        "max_loss_r": round(float(losers.min()), 2) if len(losers) > 0 else 0,
        "avg_winner_r": round(float(winners.mean()), 2) if len(winners) > 0 else 0,
        "avg_loser_r": round(float(losers.mean()), 2) if len(losers) > 0 else 0,
        "max_drawdown_r": round(float(max_drawdown_r), 2),
        "max_drawdown_pct": round(float(dd_pct.min()), 2),
        "max_consecutive_wins": streak_max_win,
        "max_consecutive_losses": streak_max_loss,
        "sharpe_ratio": round(float(r_values.mean() / r_values.std()), 2) if r_values.std() > 0 else 0,
        "calmar_ratio": round(float(total_r / abs(max_drawdown_r)), 2) if max_drawdown_r < 0 else float("inf"),
        "final_equity": round(float(equity_curve.iloc[-1]), 0) if len(equity_curve) > 0 else initial_capital,
        "return_pct": round(float((equity_curve.iloc[-1] - initial_capital) / initial_capital * 100), 1) if len(equity_curve) > 0 else 0,
        "evaluation_date": datetime.now().isoformat(),
    }

    if "setup" in df.columns:
        setup_stats = {}
        for setup, group in df.groupby("setup"):
            s_r = pd.to_numeric(group[r_col], errors="coerce").dropna()
            setup_stats[str(setup)] = {
                "trades": len(s_r),
                "win_rate": round(len(s_r[s_r > 0]) / len(s_r) * 100, 1) if len(s_r) > 0 else 0,
                "total_r": round(float(s_r.sum()), 2),
                "avg_r": round(float(s_r.mean()), 3) if len(s_r) > 0 else 0,
            }
        metrics["by_setup"] = setup_stats

    return metrics


def print_report(metrics: dict):
    print("\n" + "=" * 60)
    print("         STRATEGY PERFORMANCE REPORT")
    print("=" * 60)
    print(f"  Evaluation Date:     {metrics.get('evaluation_date', 'N/A')}")
    print(f"  Total Trades:        {metrics.get('total_trades', 0)}")
    print(f"  Win Rate:            {metrics.get('win_rate_pct', 0)}%")
    print(f"  Total R:             {metrics.get('total_r', 0)}")
    print(f"  Avg R/Trade:         {metrics.get('avg_r_per_trade', 0)}")
    print(f"  Profit Factor:       {metrics.get('profit_factor', 0)}")
    print(f"  Sharpe Ratio:        {metrics.get('sharpe_ratio', 0)}")
    print(f"  Max Drawdown (R):    {metrics.get('max_drawdown_r', 0)}")
    print(f"  Max Drawdown (%):    {metrics.get('max_drawdown_pct', 0)}%")
    print(f"  Final Equity:        ₹{metrics.get('final_equity', 0):,.0f}")
    print(f"  Return:              {metrics.get('return_pct', 0)}%")
    print("-" * 60)
    print(f"  Best Trade:          {metrics.get('max_win_r', 0)}R")
    print(f"  Worst Trade:         {metrics.get('max_loss_r', 0)}R")
    print(f"  Max Win Streak:      {metrics.get('max_consecutive_wins', 0)}")
    print(f"  Max Loss Streak:     {metrics.get('max_consecutive_losses', 0)}")

    if "by_setup" in metrics:
        print("\n  --- By Setup ---")
        for setup, stats in metrics["by_setup"].items():
            print(f"  {setup:12s}  Trades={stats['trades']:3d}  "
                  f"WR={stats['win_rate']:5.1f}%  "
                  f"TotalR={stats['total_r']:+7.2f}  "
                  f"AvgR={stats['avg_r']:+6.3f}")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    args = parse_args()
    df = load_trades(args.input, args.live)
    metrics = calculate_metrics(df, args.initial_capital, args.risk_per_trade)

    report_file = REPORTS_DIR / f"perf_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, "w") as f:
        json.dump(metrics, f, indent=2)
    logger.info("Report saved to %s", report_file)

    print_report(metrics)
