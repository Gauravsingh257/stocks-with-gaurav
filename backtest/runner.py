"""
backtest/runner.py — Walk-Forward Validation & Reporting (F3.5 / F3.6)
======================================================================
Main entry point for running backtests with:
  - Walk-forward validation (70/30 split)
  - Comprehensive metrics (WR, PF, Expectancy, Max DD, Sharpe)
  - Per-setup breakdown
  - CSV export of all trades
  - Console report

Usage:
    python -m backtest.runner
    python -m backtest.runner --symbols "NSE:NIFTY 50,NSE:NIFTY BANK" --split 0.7
    python -m backtest.runner --no-costs --output results.csv
"""

import os
import sys
import csv
import json
import logging
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest.data_store import DataStore
from backtest.engine import BacktestEngine, BacktestConfig, Trade

logger = logging.getLogger(__name__)


# =====================================================
# METRICS CALCULATION
# =====================================================

def calculate_metrics(trades: List[Trade]) -> dict:
    """
    Calculate comprehensive backtest metrics from a list of trades.

    Returns dict with:
      - total_trades, winners, losers
      - win_rate, profit_factor, expectancy_r
      - avg_winner_r, avg_loser_r
      - max_drawdown_r, max_consecutive_losses
      - sharpe_ratio (daily R basis)
      - avg_rr, best_trade_r, worst_trade_r
      - total_r, per_setup breakdown
    """
    if not trades:
        return {"total_trades": 0, "error": "No trades"}

    r_values = [t.r_multiple for t in trades]
    winners = [t for t in trades if t.r_multiple > 0]
    losers = [t for t in trades if t.r_multiple <= 0]

    total = len(trades)
    n_win = len(winners)
    n_loss = len(losers)
    win_rate = n_win / total if total > 0 else 0.0

    # Profit Factor
    gross_profit = sum(t.r_multiple for t in winners) if winners else 0.0
    gross_loss = abs(sum(t.r_multiple for t in losers)) if losers else 0.001
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Expectancy (average R per trade)
    total_r = sum(r_values)
    expectancy = total_r / total if total > 0 else 0.0

    # Average winner / loser
    avg_winner = sum(t.r_multiple for t in winners) / n_win if n_win else 0.0
    avg_loser = sum(t.r_multiple for t in losers) / n_loss if n_loss else 0.0

    # Max drawdown (in R)
    peak = 0.0
    cumulative = 0.0
    max_dd = 0.0
    for r in r_values:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Max consecutive losses
    max_consec_loss = 0
    current_streak = 0
    for r in r_values:
        if r <= 0:
            current_streak += 1
            max_consec_loss = max(max_consec_loss, current_streak)
        else:
            current_streak = 0

    # Sharpe ratio (using daily R)
    daily_r = defaultdict(float)
    for t in trades:
        try:
            day = (t.exit_time or t.entry_time)[:10]
        except Exception:
            day = "unknown"
        daily_r[day] += t.r_multiple

    daily_returns = list(daily_r.values())
    if len(daily_returns) > 1:
        import statistics
        mean_r = statistics.mean(daily_returns)
        std_r = statistics.stdev(daily_returns)
        sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Per-setup breakdown
    setup_metrics = {}
    setups = set(t.setup for t in trades)
    for setup in setups:
        st = [t for t in trades if t.setup == setup]
        sw = [t for t in st if t.r_multiple > 0]
        sl = [t for t in st if t.r_multiple <= 0]
        gp = sum(t.r_multiple for t in sw) if sw else 0.0
        gl = abs(sum(t.r_multiple for t in sl)) if sl else 0.001
        setup_metrics[setup] = {
            "trades": len(st),
            "win_rate": len(sw) / len(st) if st else 0.0,
            "profit_factor": gp / gl if gl > 0 else float("inf"),
            "expectancy_r": sum(t.r_multiple for t in st) / len(st) if st else 0.0,
            "total_r": sum(t.r_multiple for t in st),
            "avg_rr": sum(t.rr for t in st) / len(st) if st else 0.0,
        }

    # Per-direction breakdown
    long_trades = [t for t in trades if t.direction == "LONG"]
    short_trades = [t for t in trades if t.direction == "SHORT"]

    return {
        "total_trades": total,
        "winners": n_win,
        "losers": n_loss,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 3),
        "expectancy_r": round(expectancy, 4),
        "total_r": round(total_r, 2),
        "avg_winner_r": round(avg_winner, 3),
        "avg_loser_r": round(avg_loser, 3),
        "max_drawdown_r": round(max_dd, 2),
        "max_consecutive_losses": max_consec_loss,
        "sharpe_ratio": round(sharpe, 3),
        "best_trade_r": round(max(r_values), 3) if r_values else 0,
        "worst_trade_r": round(min(r_values), 3) if r_values else 0,
        "avg_rr_taken": round(sum(t.rr for t in trades) / total, 2) if total else 0,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_rate": round(
            len([t for t in long_trades if t.r_multiple > 0]) / len(long_trades), 4
        ) if long_trades else 0.0,
        "short_win_rate": round(
            len([t for t in short_trades if t.r_multiple > 0]) / len(short_trades), 4
        ) if short_trades else 0.0,
        "per_setup": setup_metrics,
        "trading_days": len(daily_r),
        "avg_trades_per_day": round(total / max(len(daily_r), 1), 2),
    }


# =====================================================
# WALK-FORWARD SPLIT
# =====================================================

def split_candles(candles: list, train_ratio: float = 0.7) -> Tuple[list, list]:
    """Split a candle list into train/test by date ratio."""
    if not candles:
        return [], []

    split_idx = int(len(candles) * train_ratio)
    return candles[:split_idx], candles[split_idx:]


def walk_forward_split(data: Dict[str, dict],
                       train_ratio: float = 0.7) -> Tuple[dict, dict]:
    """
    Split multi-symbol data into train/test sets.

    Args:
        data: {symbol: {"5m": [...], "1h": [...]}}
        train_ratio: fraction for training (default 0.7)

    Returns:
        (train_data, test_data) with same structure
    """
    train = {}
    test = {}

    for symbol, tf_data in data.items():
        train[symbol] = {}
        test[symbol] = {}
        for tf_key, candles in tf_data.items():
            tr, te = split_candles(candles, train_ratio)
            train[symbol][tf_key] = tr
            test[symbol][tf_key] = te

    return train, test


# =====================================================
# REPORTING
# =====================================================

def print_report(metrics: dict, label: str = "BACKTEST"):
    """Print a formatted metrics report to console."""
    print("\n" + "=" * 70)
    print(f"  {label} RESULTS")
    print("=" * 70)

    if metrics.get("total_trades", 0) == 0:
        print("  No trades generated.")
        return

    print(f"  Total Trades:        {metrics['total_trades']}")
    print(f"  Winners/Losers:      {metrics['winners']}/{metrics['losers']}")
    print(f"  Win Rate:            {metrics['win_rate']*100:.1f}%")
    print(f"  Profit Factor:       {metrics['profit_factor']:.3f}")
    print(f"  Expectancy (R):      {metrics['expectancy_r']:.4f}")
    print(f"  Total R:             {metrics['total_r']:.2f}")
    print("")
    print(f"  Avg Winner (R):      {metrics['avg_winner_r']:.3f}")
    print(f"  Avg Loser (R):       {metrics['avg_loser_r']:.3f}")
    print(f"  Best Trade (R):      {metrics['best_trade_r']:.3f}")
    print(f"  Worst Trade (R):     {metrics['worst_trade_r']:.3f}")
    print("")
    print(f"  Max Drawdown (R):    {metrics['max_drawdown_r']:.2f}")
    print(f"  Max Consec. Losses:  {metrics['max_consecutive_losses']}")
    print(f"  Sharpe Ratio:        {metrics['sharpe_ratio']:.3f}")
    print("")
    print(f"  Direction Split:     {metrics['long_trades']}L / {metrics['short_trades']}S")
    print(f"  Long Win Rate:       {metrics.get('long_win_rate', 0)*100:.1f}%")
    print(f"  Short Win Rate:      {metrics.get('short_win_rate', 0)*100:.1f}%")
    print(f"  Trading Days:        {metrics.get('trading_days', 0)}")
    print(f"  Avg Trades/Day:      {metrics.get('avg_trades_per_day', 0):.1f}")
    print("")

    # Per-setup breakdown
    per_setup = metrics.get("per_setup", {})
    if per_setup:
        print("  ┌─────────────┬────────┬────────┬─────────┬──────────┬─────────┐")
        print("  │ Setup       │ Trades │ WR     │ PF      │ Expect.R │ Total R │")
        print("  ├─────────────┼────────┼────────┼─────────┼──────────┼─────────┤")
        for setup, sm in sorted(per_setup.items()):
            print(f"  │ {setup:<11} │ {sm['trades']:>6} │ "
                  f"{sm['win_rate']*100:>5.1f}% │ {sm['profit_factor']:>7.3f} │ "
                  f"{sm['expectancy_r']:>8.4f} │ {sm['total_r']:>7.2f} │")
        print("  └─────────────┴────────┴────────┴─────────┴──────────┴─────────┘")

    print("=" * 70)

    # Target check
    print("\n  TARGET CHECK:")
    targets = {
        "Win Rate ≥ 40%": metrics["win_rate"] >= 0.40,
        "Profit Factor ≥ 1.5": metrics["profit_factor"] >= 1.5,
        "Expectancy ≥ 0.3R": metrics["expectancy_r"] >= 0.3,
        "Max DD ≤ 10R": metrics["max_drawdown_r"] <= 10.0,
        "Total Trades ≥ 500": metrics["total_trades"] >= 500,
    }
    for label, passed in targets.items():
        status = "PASS" if passed else "FAIL"
        icon = "[+]" if passed else "[-]"
        print(f"    {icon} {label}: {status}")
    print("")


def export_trades_csv(trades: List[Trade], filepath: str):
    """Export all trades to a CSV file."""
    if not trades:
        return

    fieldnames = [
        "trade_id", "symbol", "setup", "direction",
        "entry_price", "sl", "target", "rr",
        "smc_score", "entry_time", "exit_price",
        "exit_time", "exit_reason",
        "gross_pnl_pts", "net_pnl_pts", "r_multiple", "cost_pts"
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(t.to_dict())

    logger.info(f"Exported {len(trades)} trades to {filepath}")


def export_metrics_json(metrics: dict, filepath: str):
    """Export metrics to a JSON file."""

    def _clean(obj):
        if isinstance(obj, float) and (obj == float("inf") or obj == float("-inf")):
            return str(obj)
        return obj

    clean_metrics = json.loads(json.dumps(metrics, default=_clean))
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(clean_metrics, f, indent=2)


# =====================================================
# MAIN RUNNER
# =====================================================

def load_data_from_store(store: DataStore,
                         symbols: Optional[List[str]] = None,
                         timeframes: Optional[List[str]] = None) -> dict:
    """
    Load candle data from DataStore into the format expected by BacktestEngine.

    Returns:
        {symbol: {"5m": [...], "1h": [...]}}
    """
    if symbols is None:
        symbols = store.get_symbols(timeframe="5minute")
    if not symbols:
        logger.error("No symbols found in data store!")
        return {}

    tf_map = {"5minute": "5m", "60minute": "1h", "15minute": "15m", "day": "day"}
    required_tfs = timeframes or ["5minute", "60minute"]

    data = {}
    for symbol in symbols:
        tf_data = {}
        for tf in required_tfs:
            candles = store.get_candles(symbol, tf)
            mapped_key = tf_map.get(tf, tf)
            tf_data[mapped_key] = candles
        if tf_data.get("5m"):
            data[symbol] = tf_data
            logger.info(f"Loaded {symbol}: {len(tf_data.get('5m', []))} 5m candles, "
                        f"{len(tf_data.get('1h', []))} 1h candles")
        else:
            logger.warning(f"Skipping {symbol} — no 5m data")

    return data


def run_backtest(data: dict,
                 config: Optional[BacktestConfig] = None,
                 train_ratio: float = 0.7,
                 walk_forward: bool = True,
                 output_dir: Optional[str] = None) -> dict:
    """
    Run a full backtest with optional walk-forward validation.

    Args:
        data: {symbol: {"5m": [...], "1h": [...]}}
        config: BacktestConfig (default used if None)
        train_ratio: train/test split ratio
        walk_forward: whether to do walk-forward validation
        output_dir: directory for CSV/JSON output files

    Returns:
        dict with "train_metrics", "test_metrics", "full_metrics", "trades"
    """
    config = config or BacktestConfig()

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    results = {}

    if walk_forward:
        train_data, test_data = walk_forward_split(data, train_ratio)

        # Train phase
        engine_train = BacktestEngine(config)
        train_trades = engine_train.run_multi(train_data)
        train_metrics = calculate_metrics(train_trades)
        print_report(train_metrics, "TRAIN SET (In-Sample)")
        results["train_metrics"] = train_metrics
        results["train_trades"] = train_trades

        if output_dir:
            export_trades_csv(train_trades,
                              os.path.join(output_dir, "train_trades.csv"))

        # Test phase
        engine_test = BacktestEngine(config)
        test_trades = engine_test.run_multi(test_data)
        test_metrics = calculate_metrics(test_trades)
        print_report(test_metrics, "TEST SET (Out-of-Sample)")
        results["test_metrics"] = test_metrics
        results["test_trades"] = test_trades

        if output_dir:
            export_trades_csv(test_trades,
                              os.path.join(output_dir, "test_trades.csv"))

        # Degradation check
        if train_metrics["total_trades"] > 0 and test_metrics["total_trades"] > 0:
            print("\n  WALK-FORWARD DEGRADATION CHECK:")
            wr_deg = (test_metrics["win_rate"] - train_metrics["win_rate"]) / max(train_metrics["win_rate"], 0.01) * 100
            pf_deg = (test_metrics["profit_factor"] - train_metrics["profit_factor"]) / max(train_metrics["profit_factor"], 0.01) * 100
            exp_deg = (test_metrics["expectancy_r"] - train_metrics["expectancy_r"]) / max(abs(train_metrics["expectancy_r"]), 0.01) * 100

            for label, deg in [("Win Rate", wr_deg), ("Profit Factor", pf_deg), ("Expectancy", exp_deg)]:
                status = "OK" if deg > -20 else "WARN" if deg > -40 else "FAIL"
                print(f"    [{status}] {label}: {deg:+.1f}% change (train → test)")

    # Full dataset
    engine_full = BacktestEngine(config)
    all_trades = engine_full.run_multi(data)
    full_metrics = calculate_metrics(all_trades)
    print_report(full_metrics, "FULL DATASET")
    results["full_metrics"] = full_metrics
    results["trades"] = all_trades

    if output_dir:
        export_trades_csv(all_trades,
                          os.path.join(output_dir, "all_trades.csv"))
        export_metrics_json(full_metrics,
                            os.path.join(output_dir, "metrics.json"))

    return results


# =====================================================
# SYNTHETIC DATA GENERATOR (for testing without Kite)
# =====================================================

def generate_synthetic_candles(base_price: float = 22000.0,
                               days: int = 120,
                               candles_per_day: int = 75,
                               volatility: float = 0.002,
                               trend_bias: float = 0.0001) -> list:
    """
    Generate synthetic 5-minute candles for testing the backtester.
    Creates realistic-looking price action with trends, ranges, and breakouts.

    Args:
        base_price: starting price
        days: number of trading days
        candles_per_day: 5-min candles per session (75 = 9:15-15:30)
        volatility: per-candle volatility factor
        trend_bias: slight upward/downward bias

    Returns:
        list of candle dicts with date, open, high, low, close, volume
    """
    import random
    random.seed(42)  # Reproducible

    candles = []
    price = base_price
    start_date = datetime(2025, 8, 1, 9, 15)
    current_date = start_date
    day_count = 0

    # Create trend phases
    phases = []
    remaining = days
    while remaining > 0:
        phase_len = random.randint(5, 25)
        phase_len = min(phase_len, remaining)
        phase_type = random.choice(["TREND_UP", "TREND_DOWN", "RANGE"])
        phases.append((phase_type, phase_len))
        remaining -= phase_len

    phase_idx = 0
    days_in_phase = 0
    current_phase = phases[0] if phases else ("RANGE", days)

    for day in range(days):
        # Skip weekends
        while current_date.weekday() >= 5:
            current_date += timedelta(days=1)

        # Phase transitions
        if days_in_phase >= current_phase[1]:
            phase_idx += 1
            if phase_idx < len(phases):
                current_phase = phases[phase_idx]
            days_in_phase = 0
        days_in_phase += 1

        phase_type = current_phase[0]
        day_bias = {
            "TREND_UP": volatility * 0.3,
            "TREND_DOWN": -volatility * 0.3,
            "RANGE": 0.0,
        }[phase_type]

        # Generate intraday candles
        session_start = current_date.replace(hour=9, minute=15, second=0)
        day_open = price

        for j in range(candles_per_day):
            dt = session_start + timedelta(minutes=5 * j)

            # Add time-of-day patterns
            hour = dt.hour + dt.minute / 60.0
            if 9.25 <= hour < 10.0:
                # Opening volatility
                vol_mult = 1.8
            elif 11.0 <= hour < 13.0:
                # Prime hours — trending
                vol_mult = 1.2
            elif 14.0 <= hour < 15.5:
                # Late session — mean reversion
                vol_mult = 0.8
            else:
                vol_mult = 1.0

            move = price * volatility * vol_mult
            bias = day_bias * price

            open_p = price
            change = random.gauss(bias, move)
            close_p = open_p + change

            # Generate realistic wicks
            if close_p > open_p:  # bullish
                high_p = close_p + abs(random.gauss(0, move * 0.3))
                low_p = open_p - abs(random.gauss(0, move * 0.5))
            else:  # bearish
                high_p = open_p + abs(random.gauss(0, move * 0.5))
                low_p = close_p - abs(random.gauss(0, move * 0.3))

            # Sometimes create impulse candles (SMC displacement)
            if random.random() < 0.03:  # 3% chance of impulse
                impulse_dir = 1 if random.random() > 0.5 else -1
                impulse_size = move * random.uniform(3, 6)
                close_p = open_p + impulse_dir * impulse_size
                if impulse_dir > 0:
                    high_p = close_p + abs(random.gauss(0, move * 0.2))
                    low_p = open_p - abs(random.gauss(0, move * 0.2))
                else:
                    high_p = open_p + abs(random.gauss(0, move * 0.2))
                    low_p = close_p - abs(random.gauss(0, move * 0.2))

            # Volume (higher at open/close, lower midday)
            base_vol = 100000
            if hour < 10:
                vol = int(base_vol * random.uniform(1.5, 3.0))
            elif hour > 14.5:
                vol = int(base_vol * random.uniform(1.2, 2.0))
            else:
                vol = int(base_vol * random.uniform(0.5, 1.5))

            candles.append({
                "date": dt.isoformat(),
                "open": round(open_p, 2),
                "high": round(max(high_p, max(open_p, close_p)), 2),
                "low": round(min(low_p, min(open_p, close_p)), 2),
                "close": round(close_p, 2),
                "volume": vol,
            })

            price = close_p

        current_date += timedelta(days=1)

    return candles


# =====================================================
# CLI ENTRY POINT
# =====================================================

def main():
    parser = argparse.ArgumentParser(description="SMC Backtest Runner")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols (default: all in store)")
    parser.add_argument("--split", type=float, default=0.7,
                        help="Train/test split ratio (default: 0.7)")
    parser.add_argument("--no-walk-forward", action="store_true",
                        help="Skip walk-forward validation")
    parser.add_argument("--no-costs", action="store_true",
                        help="Disable transaction cost modelling")
    parser.add_argument("--output", type=str, default="backtest_results",
                        help="Output directory (default: backtest_results)")
    parser.add_argument("--synthetic", action="store_true",
                        help="Run on synthetic data (for testing)")
    parser.add_argument("--synthetic-days", type=int, default=120,
                        help="Days of synthetic data (default: 120)")
    parser.add_argument("--db", type=str, default=None,
                        help="Path to data store DB")
    parser.add_argument("--min-score", type=int, default=5,
                        help="Minimum SMC confluence score (default: 5)")
    parser.add_argument("--setup-d", action="store_true",
                        help="Enable Setup-D (disabled by default)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    config = BacktestConfig(
        apply_costs=not args.no_costs,
        min_smc_score=args.min_score,
        enable_setup_d=args.setup_d,
    )

    if args.synthetic:
        # Run on synthetic data
        print("Generating synthetic data...")
        candles = generate_synthetic_candles(days=args.synthetic_days)
        data = {"SYNTHETIC:INDEX": {"5m": candles}}
        print(f"Generated {len(candles)} candles ({args.synthetic_days} days)")
    else:
        # Load from data store
        store = DataStore(args.db) if args.db else DataStore()
        symbols = args.symbols.split(",") if args.symbols else None
        data = load_data_from_store(store, symbols)
        store.close()

        if not data:
            print("ERROR: No data available. Options:")
            print("  1. Run data_fetcher.py to populate the store from Kite API")
            print("  2. Use --synthetic flag to test with synthetic data")
            print("  3. Import CSV data via DataStore.import_csv()")
            sys.exit(1)

    # Output directory
    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), args.output)

    # Run
    results = run_backtest(
        data, config,
        train_ratio=args.split,
        walk_forward=not args.no_walk_forward,
        output_dir=output_dir,
    )

    print(f"\nResults exported to: {output_dir}/")

    return results


if __name__ == "__main__":
    main()
