"""
scripts/run_backtest.py — CLI backtest runner.

Usage:
    python scripts/run_backtest.py
    python scripts/run_backtest.py --symbol "NSE:NIFTY 50" --days 90
    python scripts/run_backtest.py --setup-a --setup-c --rr 2.5
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BACKTEST_MODE"] = "1"

from backtest.engine import BacktestEngine, BacktestConfig
from backtest.data_store import DataStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger("backtest_runner")

RESULTS_DIR = Path(__file__).parent.parent / "backtest_results"
RESULTS_DIR.mkdir(exist_ok=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Run SMC Backtest")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol to backtest")
    parser.add_argument("--days", type=int, default=60, help="Days of history")
    parser.add_argument("--setup-a", action="store_true", default=True)
    parser.add_argument("--setup-b", action="store_true", default=False)
    parser.add_argument("--setup-c", action="store_true", default=True)
    parser.add_argument("--setup-d", action="store_true", default=False)
    parser.add_argument("--rr", type=float, default=2.0, help="Risk:Reward ratio")
    parser.add_argument("--min-score", type=int, default=5, help="Min confluence score")
    parser.add_argument("--output", type=str, default=None, help="Output directory")
    return parser.parse_args()


def run_backtest(args):
    config = BacktestConfig(
        enable_setup_a=args.setup_a,
        enable_setup_b=args.setup_b,
        enable_setup_c=args.setup_c,
        enable_setup_d=args.setup_d,
        default_rr_a=args.rr,
        default_rr_c=args.rr,
        min_smc_score=args.min_score,
    )

    engine = BacktestEngine(config)
    store = DataStore()

    logger.info("=" * 60)
    logger.info("BACKTEST RUN — %s", datetime.now().isoformat())
    logger.info("Config: Setups A=%s B=%s C=%s D=%s | RR=%.1f | MinScore=%d",
                config.enable_setup_a, config.enable_setup_b,
                config.enable_setup_c, config.enable_setup_d,
                args.rr, args.min_score)
    logger.info("=" * 60)

    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = store.get_symbols(timeframe="5minute")

    from datetime import timedelta
    end_date = datetime.now().isoformat()
    start_date = (datetime.now() - timedelta(days=args.days)).isoformat()

    data = {}
    for sym in symbols:
        candles = store.get_candles(sym, "5minute", start=start_date, end=end_date)
        if candles:
            data[sym] = {"5m": candles}
            logger.info("Loaded %d candles for %s", len(candles), sym)

    if not data:
        logger.error("No data loaded. Check DataStore configuration.")
        return

    trades = engine.run_multi(data)
    logger.info("Backtest complete: %d trades", len(trades))

    output_dir = Path(args.output) if args.output else RESULTS_DIR
    output_dir.mkdir(exist_ok=True)

    import csv
    trades_file = output_dir / "all_trades.csv"
    if trades:
        with open(trades_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trades[0].to_dict().keys())
            writer.writeheader()
            for t in trades:
                writer.writerow(t.to_dict())
        logger.info("Trades saved to %s", trades_file)

    metrics = compute_metrics(trades)
    metrics_file = output_dir / "metrics.json"
    with open(metrics_file, "w") as f:
        json.dump(metrics, f, indent=2)
    logger.info("Metrics saved to %s", metrics_file)

    print_summary(metrics)


def compute_metrics(trades) -> dict:
    if not trades:
        return {"total_trades": 0}

    winners = [t for t in trades if t.r_multiple > 0]
    losers = [t for t in trades if t.r_multiple <= 0]
    total_r = sum(t.r_multiple for t in trades)
    gross_profit = sum(t.r_multiple for t in winners)
    gross_loss = abs(sum(t.r_multiple for t in losers))

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / len(trades) * 100, 1),
        "total_r": round(total_r, 2),
        "avg_r": round(total_r / len(trades), 3),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "max_win_r": round(max((t.r_multiple for t in trades), default=0), 2),
        "max_loss_r": round(min((t.r_multiple for t in trades), default=0), 2),
        "avg_winner_r": round(gross_profit / len(winners), 2) if winners else 0,
        "avg_loser_r": round(-gross_loss / len(losers), 2) if losers else 0,
        "run_date": datetime.now().isoformat(),
    }


def print_summary(metrics: dict):
    print("\n" + "=" * 50)
    print("        BACKTEST RESULTS SUMMARY")
    print("=" * 50)
    print(f"  Total Trades:    {metrics.get('total_trades', 0)}")
    print(f"  Win Rate:        {metrics.get('win_rate', 0)}%")
    print(f"  Total R:         {metrics.get('total_r', 0)}")
    print(f"  Avg R/Trade:     {metrics.get('avg_r', 0)}")
    print(f"  Profit Factor:   {metrics.get('profit_factor', 0)}")
    print(f"  Best Trade:      {metrics.get('max_win_r', 0)}R")
    print(f"  Worst Trade:     {metrics.get('max_loss_r', 0)}R")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    args = parse_args()
    run_backtest(args)
