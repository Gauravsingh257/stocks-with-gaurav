"""
SMC Trading Engine — Main Entry Point
======================================
Modes: live, paper, backtest
"""

import argparse
import sys
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("smc_engine")


def run_backtest(args):
    """Run backtest with sample or provided data."""
    from smc_trading_engine.backtest.backtest_engine import BacktestEngine
    from smc_trading_engine.backtest.performance_metrics import (
        compute_metrics, print_report, export_report_csv
    )
    from smc_trading_engine.strategy.risk_management import RiskParams
    import pandas as pd

    logger.info("Starting BACKTEST mode...")

    params = RiskParams(
        account_size=args.account_size,
        risk_pct=args.risk_pct / 100,
        min_rr=args.min_rr,
    )
    engine = BacktestEngine(risk_params=params)

    # Load data
    if args.htf_data and args.ltf_data:
        htf_df = pd.read_csv(args.htf_data, parse_dates=["date"], index_col="date")
        ltf_df = pd.read_csv(args.ltf_data, parse_dates=["date"], index_col="date")
    else:
        logger.info("No data files provided. Generating synthetic sample data...")
        htf_df, ltf_df = _generate_sample_data()

    symbol = args.symbol or "NIFTY50"

    def progress(i, total):
        pct = int(i / total * 100)
        print(f"\r  Backtest progress: {pct}%", end="", flush=True)

    result = engine.run(symbol, htf_df, ltf_df, progress_cb=progress)
    print()  # newline after progress

    # Export trades
    output = args.output or "backtest_results.csv"
    engine.export_csv(output)
    logger.info(f"Trades exported to {output}")

    # Performance report
    trade_dicts = [{
        "pnl": t.pnl, "result": t.result, "rr": t.rr, "bars_held": t.bars_held
    } for t in result.trades]
    report = compute_metrics(trade_dicts)
    print_report(report)

    metrics_path = output.replace(".csv", "_metrics.csv")
    export_report_csv(report, metrics_path)
    logger.info(f"Metrics exported to {metrics_path}")


def run_paper(args):
    """Run paper trading mode."""
    from smc_trading_engine.execution.paper_trading import PaperTrader
    from smc_trading_engine.strategy.signal_generator import SignalGenerator
    from smc_trading_engine.strategy.risk_management import RiskManager

    logger.info("Starting PAPER TRADING mode...")
    logger.info("Paper trading requires live data feed. Connect Kite API and run.")

    risk_mgr = RiskManager()
    sig_gen = SignalGenerator(risk_mgr)
    trader = PaperTrader(risk_mgr)

    logger.info("Paper trader initialized. Waiting for market data...")
    logger.info("Use Ctrl+C to stop.")


def run_live(args):
    """Run live trading mode (DRY_RUN by default)."""
    from smc_trading_engine.execution.live_execution import LiveExecution

    dry_run = not args.go_live
    mode = "LIVE" if not dry_run else "DRY_RUN"
    logger.info(f"Starting {mode} mode...")

    if not dry_run:
        logger.warning("LIVE MODE ENABLED — Real orders will be placed!")

    executor = LiveExecution(
        telegram_token=args.telegram_token or "",
        telegram_chat=args.telegram_chat or "",
        dry_run=dry_run
    )
    logger.info(f"Executor initialized in {mode} mode.")


def _generate_sample_data():
    """Generate synthetic OHLC data for testing."""
    import pandas as pd
    import numpy as np

    np.random.seed(42)
    n_ltf = 2000  # 5m bars (~33 trading days)
    n_htf = n_ltf // 3  # 15m bars

    dates_ltf = pd.date_range("2026-01-01 09:15", periods=n_ltf, freq="5min")
    dates_htf = pd.date_range("2026-01-01 09:15", periods=n_htf, freq="15min")

    def make_ohlc(dates, start=22000):
        prices = [start]
        for _ in range(len(dates) - 1):
            prices.append(prices[-1] + np.random.randn() * 15)
        df = pd.DataFrame(index=dates)
        df["close"] = prices
        df["open"] = df["close"] + np.random.randn(len(dates)) * 5
        df["high"] = df[["open", "close"]].max(axis=1) + abs(np.random.randn(len(dates))) * 10
        df["low"] = df[["open", "close"]].min(axis=1) - abs(np.random.randn(len(dates))) * 10
        df["volume"] = np.random.randint(50000, 500000, len(dates))
        return df

    return make_ohlc(dates_htf), make_ohlc(dates_ltf)


def main():
    parser = argparse.ArgumentParser(
        description="SMC Trading Engine",
        formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="mode", help="Operating mode")

    # Backtest
    bt = sub.add_parser("backtest", help="Run backtesting")
    bt.add_argument("--symbol", default="NIFTY50", help="Symbol to backtest")
    bt.add_argument("--htf-data", help="Path to HTF CSV (15m)")
    bt.add_argument("--ltf-data", help="Path to LTF CSV (5m)")
    bt.add_argument("--output", default="backtest_results.csv", help="Output CSV path")
    bt.add_argument("--account-size", type=float, default=100000)
    bt.add_argument("--risk-pct", type=float, default=1.0, help="Risk per trade %%")
    bt.add_argument("--min-rr", type=float, default=3.0)

    # Paper
    pp = sub.add_parser("paper", help="Paper trading mode")

    # Live
    lv = sub.add_parser("live", help="Live trading mode")
    lv.add_argument("--go-live", action="store_true", help="Enable real orders (DANGEROUS)")
    lv.add_argument("--telegram-token", default="")
    lv.add_argument("--telegram-chat", default="")

    args = parser.parse_args()

    if args.mode == "backtest":
        run_backtest(args)
    elif args.mode == "paper":
        run_paper(args)
    elif args.mode == "live":
        run_live(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
