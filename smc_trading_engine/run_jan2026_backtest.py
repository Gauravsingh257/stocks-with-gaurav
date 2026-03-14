"""
Backtest Runner — January 2026
================================
Fetches real OHLC data for NIFTY 50, NIFTY BANK, NIFTY FIN SERVICE
via Kite API and runs the new SMC Trading Engine backtest.
"""

import os
import sys
# Add parent dir (Trading Algo/) to sys.path for kite_credentials and smc_trading_engine
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
from kite_credentials import API_KEY

from smc_trading_engine.backtest.backtest_engine import BacktestEngine
from smc_trading_engine.backtest.performance_metrics import (
    compute_metrics, print_report, export_report_csv
)
from smc_trading_engine.strategy.risk_management import RiskParams


# ─── KITE CONNECTION ─────────────────────────────
def connect_kite():
    if not os.path.exists("access_token.txt"):
        print("[ERROR] access_token.txt not found. Run zerodha_login.py first.")
        sys.exit(1)
    with open("access_token.txt", "r") as f:
        token = f.read().strip()
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    print("[OK] Kite connected")
    return kite


# ─── FETCH HISTORICAL DATA ──────────────────────
def fetch_ohlc_df(kite, symbol, interval, from_date, to_date):
    """Fetch OHLC and return as DataFrame."""
    try:
        # Get instrument token
        ltp = kite.ltp(symbol)
        token = list(ltp.values())[0]["instrument_token"]

        data = kite.historical_data(token, from_date, to_date, interval)
        if not data:
            print(f"  [WARN] No data for {symbol} {interval}")
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        # Remove timezone for clean processing
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        print(f"  [OK] {symbol} {interval}: {len(df)} bars")
        return df

    except Exception as e:
        print(f"  [ERROR] {symbol} {interval}: {e}")
        return pd.DataFrame()


# ─── MAIN ────────────────────────────────────────
def main():
    print("=" * 60)
    print("  SMC TRADING ENGINE — JAN 2026 BACKTEST")
    print("=" * 60)

    kite = connect_kite()

    # Date range: full January 2026
    from_date = datetime(2026, 1, 1)
    to_date = datetime(2026, 1, 31)

    symbols = [
        "NSE:NIFTY 50",
        "NSE:NIFTY BANK",
        "NSE:NIFTY FIN SERVICE",
    ]

    risk_params = RiskParams(
        account_size=100_000,
        risk_pct=0.01,    # 1%
        min_rr=3.0,
    )

    all_trades = []
    all_results = []

    for symbol in symbols:
        clean = symbol.replace("NSE:", "").replace(" ", "_")
        print(f"\n--- Fetching {symbol} ---")

        # Fetch HTF (15m) and LTF (5m)
        import time
        htf_df = fetch_ohlc_df(kite, symbol, "15minute", from_date, to_date)
        time.sleep(0.5)
        ltf_df = fetch_ohlc_df(kite, symbol, "5minute", from_date, to_date)
        time.sleep(0.5)

        if htf_df.empty or ltf_df.empty:
            print(f"  [SKIP] {symbol} — insufficient data")
            continue

        # Run backtest
        print(f"\n--- Running backtest: {symbol} ---")
        engine = BacktestEngine(risk_params=risk_params)

        def progress(i, total):
            pct = int(i / total * 100)
            print(f"\r  Progress: {pct}%", end="", flush=True)

        result = engine.run(symbol, htf_df, ltf_df, progress_cb=progress)
        print()

        # Export per-symbol CSV
        csv_path = f"smc_trading_engine/backtest_{clean}_jan2026.csv"
        engine.export_csv(csv_path)
        print(f"  Trades exported: {csv_path}")

        # Collect
        for t in result.trades:
            all_trades.append({
                "symbol": t.symbol, "direction": t.direction,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "stop_loss": t.stop_loss, "target": t.target,
                "rr": t.rr, "pnl": round(t.pnl, 2), "result": t.result,
                "confidence": t.confidence, "bars_held": t.bars_held,
                "entry_time": str(t.entry_time), "exit_time": str(t.exit_time),
            })

        all_results.append({
            "symbol": symbol,
            "trades": len(result.trades),
            "win_rate": result.win_rate,
            "total_pnl": result.total_pnl,
            "max_drawdown": result.max_drawdown,
            "profit_factor": result.profit_factor,
        })

    # ─── COMBINED REPORT ─────────────────────────
    print("\n" + "=" * 60)
    print("  COMBINED RESULTS — ALL SYMBOLS")
    print("=" * 60)

    if all_trades:
        combined = compute_metrics(all_trades)
        print_report(combined)

        # Export combined CSV
        pd.DataFrame(all_trades).to_csv(
            "smc_trading_engine/backtest_combined_jan2026.csv", index=False)
        export_report_csv(combined,
            "smc_trading_engine/backtest_metrics_jan2026.csv")
        print("[OK] Combined results exported")
    else:
        print("  No trades generated across all symbols.")
        print("  This is expected with strict SMC filters on ~20 trading days.")

    # Per-symbol summary table
    if all_results:
        print("\n  PER-SYMBOL BREAKDOWN:")
        print(f"  {'Symbol':<25} {'Trades':>7} {'WR%':>7} {'PnL':>10} {'DD%':>7} {'PF':>7}")
        print("  " + "-" * 64)
        for r in all_results:
            print(f"  {r['symbol']:<25} {r['trades']:>7} {r['win_rate']:>6.1f}% "
                  f"{r['total_pnl']:>10.2f} {r['max_drawdown']:>6.1f}% {r['profit_factor']:>7.2f}")

    print("\n[DONE] Backtest complete.")


if __name__ == "__main__":
    main()
