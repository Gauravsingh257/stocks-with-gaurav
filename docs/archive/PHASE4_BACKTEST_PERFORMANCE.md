# PHASE 4 — BACKTEST & PERFORMANCE READINESS AUDIT

**Date:** February 23, 2026  
**Scope:** All backtesting infrastructure, trade results, and live performance data  
**Connected Files Analyzed:** `smc_mtf_engine_v4.py`, `banknifty_signal_engine.py`, `backtest_standalone_phase4.py`, `backtest_phase4_validation.py`, `backtest_options_signal.py`, `smc_trading_engine/backtest/backtest_engine.py`, `smc_trading_engine/backtest/performance_metrics.py`

---

## EXECUTIVE SUMMARY

| Metric | Standalone Backtest | SMC Index Backtest | **LIVE Trading** |
|--------|--------------------|--------------------|------------------|
| Trades | 56 | 77 | **178** |
| Win Rate | 32.1% | 31.2% | **33.7%** |
| Net PnL | +6.08R | -5.00R | **+4.25R** |
| Profit Factor | 1.20 | 0.91 | **1.04** |
| Max Drawdown | 11.95R | N/A | **27.00R** |
| Max Consec Losses | 6 | N/A | **11** |
| Expectancy/Trade | -0.034R | -0.065R | **+0.024R** |

**VERDICT: NOT DEPLOYMENT-READY.** The system is marginally break-even at best. A 27R drawdown with 11 consecutive losses would blow any reasonable account. The ~33% win rate with ~1.8R average win and ~0.9R average loss produces near-zero expectancy — essentially random with edge eaten by costs.

---

## 1. BACKTEST INFRASTRUCTURE ASSESSMENT

### 1.1 Three Separate Backtest Systems (CRITICAL FRAGMENTATION)

The project contains **three independent backtest systems** that use **different detection logic**:

| System | File | Detection Source | Status |
|--------|------|-----------------|--------|
| Package Backtester | `smc_trading_engine/backtest/backtest_engine.py` | `evaluate_entry()` from package | **BROKEN** — crashes on import (missing `regime_controller`) |
| Standalone Phase4 | `backtest_standalone_phase4.py` | Own inline `detect_ob()`, `detect_fvg()`, `detect_bos()` | Runs but uses **different logic** than live engine |
| Walk-Forward | `backtest_phase4_validation.py` | Imports actual `smc_mtf_engine_v4` with mocked APIs | Closest to live, but requires complex mocking |

**CRITICAL FINDING:** The standalone backtest (`backtest_phase4_results.csv`) uses its **OWN detection functions** that differ significantly from the live engine:

| Function | Standalone Backtest | Live Engine (`smc_mtf_engine_v4.py`) |
|----------|-------------------|--------------------------------------|
| `detect_ob` | 20-bar lookback, scans all candles | 5 fixed positions only (indices -1 to -5) |
| `detect_fvg` | Last 10 candles scanned | Last 3 candles only |
| `detect_bos` | Rolling max/min (similar) | Rolling max/min (similar) |
| Trading hours | 9:15-10:00, 11:00-13:00 only | 9:15-15:05 (full session) |
| Trailing stop | Breakeven at 1R, ratchet at 2R/2.5R | Different trailing logic per setup |

**This means backtest results DO NOT represent what the live engine actually does.** The standalone backtest is testing a different strategy.

### 1.2 Package Backtester — DEAD

- `backtest_results_metrics.csv` contains **ALL ZEROS** — zero trades, zero PnL, zero everything
- Root cause: `evaluate_entry()` imports `regime_controller` which doesn't exist
- The entire `smc_trading_engine/backtest/` package has never produced valid results

### 1.3 No Unified Backtest Framework

There is no single backtest that:
- Uses the **exact same** detection functions as the live engine
- Tests across multiple instruments (stocks + indices)
- Runs on sufficient historical data (6-12 months minimum)
- Accounts for realistic execution (slippage, partial fills, liquidity)

---

## 2. LIVE TRADING PERFORMANCE ANALYSIS

### 2.1 Aggregate Statistics (178 trades, Feb 4-23 2026)

```
Total Trades:      178
Wins:              60  (33.7%)
Losses:            118 (66.3%)
Net PnL:           +4.25R
Profit Factor:     1.04
Avg Win:           1.83R
Avg Loss:          0.90R
Expectancy:        +0.024R per trade
Max Drawdown:      27.00R
Max Consec Losses: 11
```

### 2.2 Per-Setup Performance

| Setup | Trades | Win Rate | Net PnL |
|-------|--------|----------|---------|
| UNIVERSAL-LONG | 81 | 33.3% | +4.00R |
| SETUP-D | 49 | 30.6% | -4.00R |
| UNIVERSAL-SHORT | 43 | 37.2% | +3.25R |
| B | 3 | 66.7% | +3.00R |
| SETUP-D-V2 | 1 | 0.0% | -1.00R |
| A-FVG-BUY | 1 | 0.0% | -1.00R |

**Key Observations:**
- **SETUP-D is a net loser** (-4.00R on 49 trades, 30.6% WR) — should be disabled immediately
- **UNIVERSAL setups** are marginally positive but with massive variance
- **SETUP-B** shows promise (66.7% WR) but only 3 trades — not statistically significant
- SETUP-A and SETUP-C appear to have generated zero trades in live (or were disabled)

### 2.3 Excessive Trading (RED FLAG)

| Date | Trades | Notes |
|------|--------|-------|
| 2026-02-06 | **20** | Extreme — multiple duplicate signals on same symbols |
| 2026-02-10 | **20** | Same issue — dedup failure |
| 2026-02-16 | 16 | Still excessive |
| 2026-02-19 | 15 | Too many |
| 2026-02-05 | 10 | High |

A disciplined SMC system should generate **2-4 signals per day maximum**. Taking 20 trades in a single day means:
- The deduplication engine (`already_alerted_today`) is failing or being bypassed
- Multiple setups are triggering on the same symbol within minutes
- No throttle on total daily trade count (only per-symbol limit exists)

### 2.4 The 27R Drawdown Problem

A 27R max drawdown means if risking 1% per trade, the account would have drawn down **27%** in less than 3 weeks. With 11 consecutive losses, any trader would have (and should have) stopped the system.

The circuit breaker (`MAX_DAILY_LOSS_R = -3.0` and `COOLDOWN_AFTER_STREAK = 3`) should have prevented this, but:
- It resets daily, so cumulative multi-day drawdowns are uncontrolled
- 3-loss cooldown is too small — it just pauses briefly then continues losing

---

## 3. BACKTEST RESULTS DEEP-DIVE

### 3.1 Standalone Backtest (56 trades, Jan-Feb 2026)

```
Win Rate:          32.1% (18 wins, 30 losses, 8 breakeven)
Net PnL:           +6.08R
Profit Factor:     1.20
Avg Win:           2.00R
Avg Loss:          1.00R  
Expectancy:        -0.034R (NEGATIVE after accounting for breakevens)
Max Drawdown:      11.95R
Max Consec Losses: 6
```

- Only tested on **NIFTY 50 + BANK NIFTY** (2 instruments) — no stock universe testing
- All trades use **RR 2.5** and **confluence 3** — no variation, suggesting scoring isn't differentiating
- The positive net PnL is driven by a few large trail wins (4.36R, 4.6R) — remove those and it's deeply negative
- **7-week period is too short** for statistical significance (need 200+ trades minimum)

### 3.2 SMC Index Backtest (77 trades, Jan-Feb 2026)

```
Win Rate:          31.2%
Net PnL:           -5.00R (NET NEGATIVE)
Profit Factor:     0.91  (BELOW 1.0 = LOSING SYSTEM)
```

- All UNIVERSAL-LONG/SHORT setups only  
- **Every quality metric is zero**: confidence=0, regime=UNKNOWN, displacement=0, ob_age=0
- This means the scoring system, regime filter, and quality metrics were **not running** during this backtest
- A backtest where all metadata is zero is testing only raw BOS + OB + FVG detection — and it loses money

### 3.3 Options Signal Backtest

- `backtest_options_signal.py` tests the BankNifty options signal engine on only **5 trading days** (Feb 14-20)
- Far too small a sample to draw conclusions
- No results CSV found — may not have been run successfully

---

## 4. DATA PIPELINE ASSESSMENT

### 4.1 Historical Data Source

- All backtests use **Kite API** (`kite.historical_data()`) for candle data
- No offline data store — every backtest run requires live API calls
- API rate limits (3 calls/sec) make large-scale backtesting slow
- No data validation or gap detection in any backtest

### 4.2 Data Issues

| Issue | Impact | Severity |
|-------|--------|----------|
| No offline historical data store | Can't run backtests without API connection; slow iteration | HIGH |
| No survivorship bias handling | Stock universe as of today, not as of backtest date | MEDIUM |
| No dividend/split adjustment verification | Could distort price levels for OB/FVG detection | MEDIUM |
| `daily_pnl_log.csv` has only 2 lines | Daily P&L tracking is non-functional | HIGH |
| No tick-level data for options backtest | Options backtest uses 5-min candles, misses intraday OI dynamics | HIGH |

### 4.3 Logging & Tracking Gaps

- `daily_pnl_log.csv`: Essentially empty (2 lines). The `send_eod_report()` function writes here but appears to rarely execute
- `trade_ledger_2026.csv`: Has BOM encoding issues (`ï»¿` prefix on first column) — will cause silent failures in any analysis script that doesn't handle it
- No equity curve tracking or account-level position sizing in any backtest — all PnL is in raw R-multiples with no compounding

---

## 5. CONNECTED FILES STATUS

### Files directly imported by `smc_mtf_engine_v4.py`:

| File | Role | Status |
|------|------|--------|
| `kite_credentials.py` | API key storage | Works (hardcoded API_KEY) |
| `smc_trading_engine/strategy/entry_model.py` | Imported for `evaluate_entry` | **CRASHES** — missing `regime_controller` |
| `manual_trade_handler_v2.py` | Manual trade integration | Functional but untested in backtest |
| `option_monitor_module.py` | Options monitoring | Functional, separate from signals |
| `banknifty_signal_engine.py` | Options signal engine | Functional but **never backtested properly** |
| `utils/state_db.py` | JSON state persistence | Functional |

### Files directly imported by `banknifty_signal_engine.py`:

| File | Role | Status |
|------|------|--------|
| `kite_credentials.py` | API key | Works |
| `kiteconnect` (KiteTicker) | WebSocket ticks | Works in live only |

### NOTE: The `BankNiftySignalEngine` class exists in TWO places:

1. `banknifty_signal_engine.py` (standalone file, ~1230 lines)
2. `smc_mtf_engine_v4.py` lines ~3855-4799 (merged copy, ~945 lines)

The main engine imports from `banknifty_signal_engine.py` at line 34 BUT also has a full inline copy. This creates confusion about which version is actually used at runtime. The inline version in the main engine would shadow the import if both are loaded.

---

## 6. STATISTICAL SIGNIFICANCE ASSESSMENT

| Dataset | Trades | Min Needed | Verdict |
|---------|--------|------------|---------|
| Standalone backtest | 56 | 200+ | **INSUFFICIENT** — can't draw conclusions |
| SMC index backtest | 77 | 200+ | **INSUFFICIENT** |
| Live trading | 178 | 200+ | **Nearly sufficient** but only 14 trading days |
| Options backtest | ~0 | 100+ | **NO DATA** |

**For a 33% win rate system:**
- Need at least **200 trades** to estimate true win rate within ±5% confidence
- Need at least **500 trades** across different market regimes (trending, ranging, volatile)
- Current data covers only **Jan-Feb 2026** — a single market regime

---

## 7. CRITICAL FINDINGS SUMMARY

### BLOCKER Issues (Must fix before any deployment)

| # | Issue | Impact |
|---|-------|--------|
| B1 | **Backtest uses different detection logic than live engine** | Backtest results are meaningless for predicting live performance |
| B2 | **27R max drawdown with 11 consecutive losses** | Would blow a real account |
| B3 | **20 trades/day on multiple days** — dedup failure | Uncontrolled risk, commission drain |
| B4 | **SETUP-D is a confirmed net loser** (-4R on 49 trades) | Actively destroying capital |
| B5 | **No unified backtest framework** that uses actual engine logic | Can't validate any changes |
| B6 | **Package backtester produces ZERO results** (broken import) | Dead code |

### HIGH Severity

| # | Issue | Impact |
|---|-------|--------|
| H1 | No daily trade count limit (global) | Allows 20+ trades/day |
| H2 | No multi-day drawdown circuit breaker | Daily reset allows cumulative ruin |
| H3 | BankNiftySignalEngine exists in two places (import + inline) | Shadowing confusion |
| H4 | All quality metadata = 0 in SMC backtest | Scoring/regime systems not functional |
| H5 | daily_pnl_log.csv essentially empty | No daily tracking |
| H6 | No offline historical data store | Backtesting requires live API |
| H7 | Trade ledger has BOM encoding | Breaks standard CSV parsing |
| H8 | Insufficient sample size across all datasets | Can't establish statistical edge |

### MEDIUM Severity

| # | Issue | Impact |
|---|-------|--------|
| M1 | Sharpe ratio calculation uses sqrt(252) on per-trade returns | Overstates risk-adjusted performance |
| M2 | Drawdown computed from cumulative PnL, not equity percentage | Distorted for varying position sizes |
| M3 | No transaction cost modeling in standalone backtest | Results are overly optimistic |
| M4 | Options backtest covers only 5 days | Zero statistical power |
| M5 | No walk-forward or out-of-sample testing performed | Overfitting risk unknown |

---

## 8. RECOMMENDATIONS FOR PHASE 5+

### Immediate Actions (Before any live trading resumes):

1. **DISABLE SETUP-D** — confirmed net negative performance
2. **Add global daily trade limit** (max 4-5 trades/day across all symbols)
3. **Add multi-day drawdown breaker** (halt for 24-48 hours after -10R cumulative)
4. **Build a unified backtest** that imports detection functions directly from `smc_mtf_engine_v4.py`

### Backtest Infrastructure Rebuild:

1. Create offline historical data store (download and cache via Kite API)
2. Build single backtester that uses exact live engine functions
3. Test on minimum 6 months of data across multiple market regimes
4. Implement walk-forward optimization with out-of-sample validation
5. Add transaction cost modeling (slippage + brokerage + STT)
6. Target: 500+ trades in backtest before considering live deployment

### Performance Targets for Go-Live:

| Metric | Minimum Required | Current |
|--------|-----------------|---------|
| Win Rate | > 40% | 33.7% |
| Profit Factor | > 1.5 | 1.04 |
| Expectancy | > 0.3R | 0.024R |
| Max Drawdown | < 10R | 27.00R |
| Max Consec Losses | < 6 | 11 |
| Daily Trade Limit | 2-5 | 20 (uncontrolled) |
| Backtest Sample | 500+ trades | 56-178 |

---

**END OF PHASE 4 REPORT**

*Next: Phase 5 — Live Deployment Readiness (connection management, error handling, recovery, monitoring)*
