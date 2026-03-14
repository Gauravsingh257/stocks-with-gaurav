"""
Backtest NIFTY + BANKNIFTY from Jan 2026 to Feb 24, 2026
========================================================
1. Fetch fresh 5m + 1h data from Kite API
2. Store in a fresh SQLite DB
3. Run backtest with current optimal config
4. Print results
"""

import os
import sys
import logging
from datetime import datetime, timedelta

# Fix Windows console encoding for box-drawing chars
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Step 1: Connect to Kite ───────────────────────────────────────
print("=" * 70)
print("  BACKTEST: NIFTY + BANKNIFTY  |  Jan 1 – Feb 24, 2026")
print("=" * 70)

from kite_credentials import API_KEY
from kiteconnect import KiteConnect

token_path = os.path.join(os.path.dirname(__file__), "access_token.txt")
with open(token_path, "r") as f:
    access_token = f.read().strip()

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(access_token)

# Quick health check
try:
    ltp = kite.ltp("NSE:NIFTY 50")
    print(f"[OK] Kite connected — NIFTY LTP: {ltp['NSE:NIFTY 50']['last_price']}")
except Exception as e:
    print(f"[FAIL] Kite connection failed: {e}")
    print("  Run zerodha_login.py to refresh the token.")
    sys.exit(1)

# ─── Step 2: Fetch fresh data ──────────────────────────────────────
from backtest.data_store import DataStore
from backtest.data_fetcher import KiteDataFetcher

DB_PATH = os.path.join(os.path.dirname(__file__), "backtest_jan_feb_2026.db")

# Remove old DB if exists to get clean data
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
    print("[INFO] Removed old DB, starting fresh")

store = DataStore(DB_PATH)
fetcher = KiteDataFetcher(kite, store)

SYMBOLS = ["NSE:NIFTY 50", "NSE:NIFTY BANK"]
START_DATE = datetime(2026, 1, 1)
END_DATE = datetime(2026, 2, 24, 15, 30)  # today's close

print(f"\n[FETCH] Fetching {len(SYMBOLS)} symbols from {START_DATE.date()} to {END_DATE.date()}...")

for sym in SYMBOLS:
    print(f"\n  Fetching {sym}...")
    result = fetcher.fetch_symbol(
        sym,
        months=2,  # ~60 days
        timeframes=["5minute", "60minute"],
        end_date=END_DATE,
    )
    print(f"  → {result}")

# Filter to only Jan 1+ data
print("\n[INFO] Verifying stored data...")
for sym in SYMBOLS:
    c5 = store.get_candles(sym, "5minute")
    c1h = store.get_candles(sym, "60minute")
    if c5:
        print(f"  {sym}: 5m={len(c5)} ({c5[0]['date'][:10]} -> {c5[-1]['date'][:10]})")
    if c1h:
        print(f"  {sym}: 1h={len(c1h)} ({c1h[0]['date'][:10]} -> {c1h[-1]['date'][:10]})")

# ─── Step 3: Run backtest ──────────────────────────────────────────
from backtest.engine import BacktestEngine, BacktestConfig
from backtest.runner import (
    load_data_from_store, run_backtest, calculate_metrics,
    print_report, export_trades_csv
)

config = BacktestConfig(
    enable_setup_a=True,
    enable_setup_b=False,   # disabled per grid search
    enable_setup_c=True,
    enable_setup_d=False,
    min_smc_score=5,
    atr_buffer_mult=0.1,    # grid search optimal (Feb 2026)
    default_rr_a=2.0,       # grid search optimal (was 3.0)
    default_rr_c=2.0,       # grid search optimal
    apply_costs=True,
)

print("\n" + "=" * 70)
print("  RUNNING BACKTEST...")
print("=" * 70)

data = load_data_from_store(store, SYMBOLS, ["5minute", "60minute"])

if not data:
    print("[ERROR] No data loaded — cannot run backtest")
    store.close()
    sys.exit(1)

# Run without walk-forward (only ~2 months of data, not enough to split)
engine = BacktestEngine(config)
all_trades = engine.run_multi(data)
metrics = calculate_metrics(all_trades)

print_report(metrics, "NIFTY + BANKNIFTY  |  Jan–Feb 2026")

# Export trades
CSV_PATH = os.path.join(os.path.dirname(__file__), "backtest_jan_feb_2026_trades.csv")
export_trades_csv(all_trades, CSV_PATH)
print(f"\n[EXPORT] Trades saved to: {CSV_PATH}")

# ─── Step 4: Per-symbol breakdown ──────────────────────────────────
print("\n" + "=" * 70)
print("  PER-SYMBOL BREAKDOWN")
print("=" * 70)

for sym in SYMBOLS:
    sym_trades = [t for t in all_trades if t.symbol == sym]
    if sym_trades:
        m = calculate_metrics(sym_trades)
        short_name = sym.replace("NSE:", "")
        print(f"\n  {short_name}: {m['total_trades']} trades | "
              f"WR={m['win_rate']*100:.1f}% | PF={m['profit_factor']:.3f} | "
              f"E={m['expectancy_r']:+.4f}R | DD={m['max_drawdown_r']:.2f}R | "
              f"Total={m['total_r']:+.2f}R | Sharpe={m['sharpe_ratio']:.2f}")
        # Per-setup
        for setup, sm in sorted(m.get("per_setup", {}).items()):
            print(f"    {setup}: {sm['trades']}T WR={sm['win_rate']*100:.1f}% "
                  f"PF={sm['profit_factor']:.3f} E={sm['expectancy_r']:+.4f}R")
    else:
        print(f"\n  {sym}: No trades")

# ─── Step 5: Trade list summary ────────────────────────────────────
if all_trades:
    print(f"\n{'='*70}")
    print(f"  RECENT TRADES (last 20)")
    print(f"{'='*70}")
    print(f"  {'Date':<12} {'Symbol':<14} {'Setup':<10} {'Dir':<6} {'Entry':>10} {'Exit':>10} {'R':>7} {'Reason'}")
    print(f"  {'-'*12} {'-'*14} {'-'*10} {'-'*6} {'-'*10} {'-'*10} {'-'*7} {'-'*10}")
    for t in all_trades[-20:]:
        sym_short = t.symbol.replace("NSE:", "")
        entry_date = t.entry_time[:10] if t.entry_time else "?"
        print(f"  {entry_date:<12} {sym_short:<14} {t.setup:<10} {t.direction:<6} "
              f"{t.entry_price:>10.2f} {t.exit_price:>10.2f} {t.r_multiple:>+7.2f} {t.exit_reason}")

store.close()
print(f"\n[DONE] Backtest complete.")
