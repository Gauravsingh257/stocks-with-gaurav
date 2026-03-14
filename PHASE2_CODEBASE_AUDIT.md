# PHASE 2 — COMPLETE CODEBASE INVESTIGATION REPORT

**Date:** February 23, 2026  
**Auditor:** Senior Quant Researcher / Institutional Trading Systems Architect  
**Scope:** All 66 Python files across the Trading Algo workspace  
**Verdict:** 🔴 **NOT READY FOR LIVE TRADING** — Critical issues found

---

## 1. EXECUTIVE ARCHITECTURE OVERVIEW

### System Map

```
┌─────────────────────────────────────────────────────────┐
│               smc_mtf_engine_v4.py (5202 lines)         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │ Setup A  │ │ Setup B  │ │ Setup C  │ │ Setup D   │  │
│  │ HTF BOS  │ │ Range    │ │Universal │ │CHOCH+OB   │  │
│  │Continuat.│ │Reversion │ │ Zones    │ │+FVG       │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │Hierarchi.│ │EMA Cross │ │BN Signal │ │Swing Scan │  │
│  │V3 Strict │ │ 10/20    │ │Opt Engine│ │Daily+Wkly │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │   Confluence Scoring → Priority Ranking → Alert   │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────┐  │
│  │   Trade Monitor → Trailing SL → EOD Report        │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
      │               │              │
      ▼               ▼              ▼
 ┌─────────┐   ┌──────────┐   ┌──────────┐
 │  Kite   │   │ Telegram │   │  SQLite  │
 │  API    │   │   Bot    │   │ State DB │
 └─────────┘   └──────────┘   └──────────┘
```

### Parallel System (smc_trading_engine/ package)

A **separate modular system** exists under `smc_trading_engine/` with its own:
- SMC detection (bos_choch, order_blocks, fvg, liquidity, market_structure)
- Strategy layer (entry_model, risk_management)
- Backtest engine
- Execution engine

**CRITICAL FINDING:** These two systems are **partially connected but fundamentally inconsistent**. The main engine imports `evaluate_entry` from the package, but uses its own SMC detection logic for Setups A-D that is **completely different** from the package's implementations.

---

## 2. FILE-BY-FILE CRITICAL FINDINGS

### 2.1 — smc_mtf_engine_v4.py (5202 lines) — THE CORE

#### 🔴 CRITICAL BUGS

| # | Location | Bug | Severity |
|---|----------|-----|----------|
| C1 | Line ~193 | **Telegram bot token hardcoded in source code** — `BOT_TOKEN = "8388602985:AAEiombJFTGv0Dx9UZeeKkpKeo0hem9hv8I"`. This is a **security vulnerability**. Anyone with access to this code can send messages to your Telegram groups. | 🔴 CRITICAL |
| C2 | Line ~194 | **Chat IDs hardcoded** — Same issue. Should be env vars only (the `os.getenv` fallbacks defeat the purpose). | 🔴 CRITICAL |
| C3 | Line ~616-617 | **Dead code / unreachable return**: `return 0.0` followed by another `return 0.0`. The second return is unreachable. The killzone_confidence function returns 0.0 for ALL times after 13:00, meaning **no trades are taken after 1 PM**. This may be intentional but is extremely restrictive. | 🟡 HIGH |
| C4 | Lines ~299-305 | **STRUCTURE_STATE declared twice** — First on line ~276, then again on line ~299. The second declaration overwrites the first to `{}`. No actual harm, but indicates messy refactoring. | 🟡 MEDIUM |
| C5 | Line ~616 | **Killzone 10:00-11:00 = 0.0 confidence** — Completely blocked. This is the most active institutional trading period (London open overlap for some markets). For Indian markets this covers the Morning Session Continuation where major moves happen. Blocking this entirely is a **major alpha leak**. | 🔴 HIGH |
| C6 | Lines ~1647-1648 | **`_require_high_confluence` uses `dir()` check** — `sig_require_high_confluence if 'sig_require_high_confluence' in dir() else False`. Using `dir()` to check local variable existence is an anti-pattern that can break unexpectedly. Should use a simple default. | 🟡 MEDIUM |
| C7 | Lines ~1270-1330 | **Setup B LONG uses `compute_sl_target` (static buffer)** but SHORT uses `compute_sl_target_dynamic` — **Asymmetric risk treatment** between directions. LONG positions get worse (static 0.3x ATR) stop placement than SHORT (dynamic by symbol). | 🔴 HIGH |
| C8 | Lines ~4528-4530 | **PUT target calculation is wrong**: `target_price = entry_price + (risk * rr)` for BUY PUT — this assumes option premium increases when price drops, which is directionally correct, BUT the SL and entry are both based on `previous_low` and `new_low` of the option premium, not the underlying. The vol_buffer applies the same direction for both CALL and PUT SLs: `sl_price = sl_price - vol_buffer` — this is **wrong for PUT** where SL should be `sl_price + vol_buffer`. | 🔴 CRITICAL |
| C9 | Lines ~4019-4045 | **`active_expiry_map` is used but never initialized** in `__init__` — `self.active_expiry_map` is accessed in `refresh_contracts()` but is not in the constructor, causing an `AttributeError` on first call. | 🔴 CRITICAL |
| C10 | Line ~160 | **Module-level `print()` at import time** — `print("ENGINE BOOTED")` executes when the file is imported by any other module, not just when run directly. This causes unwanted side effects during backtesting and testing. | 🟡 MEDIUM |
| C11 | Line ~159 | **`telegram_send()` called at module level** (line ~461): The boot message fires on import. During backtesting, this is guarded by `BACKTEST_MODE`, but the print still fires. | 🟡 MEDIUM |

#### 🟠 LOGIC FLAWS

| # | Location | Issue |
|---|----------|-------|
| L1 | `detect_htf_bias()` | Scans backwards from current candle but only looks at 20-candle windows. If the last 20 candles are a pullback within a larger uptrend, it will return SHORT (bearish BOS), causing **whipsaws and counter-trend trades**. Should use multi-scale analysis. |
| L2 | `detect_order_block()` | Only checks indices -7 to -3 (last 4 candles before the most recent 3). This is an extremely narrow window. Real OBs can form 15-30 candles back and still be valid if unmitigated. |
| L3 | `detect_fvg()` | Only checks the **last 3 candles** (`candles[-3]` and `candles[-1]`). This means it can only detect FVGs that just formed. Older valid FVGs are completely missed. |
| L4 | `detect_fvg()` tolerance | Uses 0.2% tolerance (`0.998` / `1.002`). This is extremely tight for indices but may be too loose for stocks. Should be ATR-proportional. |
| L5 | `is_range()` | Uses `2 * ATR` threshold for range detection on last 20 candles. This is too tight — most consolidation ranges are 2-4x ATR. Result: almost everything is classified as trending, making Setup B nearly non-functional. |
| L6 | `detect_setup_c()` Zone Discovery | Iterates ALL timeframes every tick to discover zones but always **overwrites with the "closest to price" zone**. This means a high-quality daily OB can be replaced by a noisy 5m OB just because it's closer. Should prioritize HTF zones. |
| L7 | `smc_confluence_score()` | `strong_structure_shift()` checks only the **last candle's body vs ATR**. This is not a genuine structure shift — it's just "was the last candle big?" True structural shifts require swing point breaks. |
| L8 | `compute_priority()` | INDEX_KEYWORDS includes "CNX" which is not a standard NSE symbol prefix. Also includes "FIN" which matches "BAJFINANCE", "BAJAJFINSV" etc. — regular stocks are incorrectly treated as indices. |
| L9 | `build_scan_universe()` | Stocks are only scanned every 6 minutes (`minute % 6 == 0`). In a 5-minute candle system, this means stocks are scanned at most once per cycle, but the timing is not aligned to candle closes. |
| L10 | `monitor_active_trades()` | Trail Stage 3 sets SL to `entry + risk * 2.0` but the trail trigger is `current_r >= 2.5`. This means once price reaches +2.5R, the SL jumps to +2.0R, locking only 2R. The gap between Stage 2 (SL at +1R, trigger at +2.0R) is **1R of unprotected profit**. There's no Stage 2.5. |
| L11 | `BankNiftySignalEngine.detect_session_low()` | Method `detect_session_low` is called in `_collect_morning_signals()` (line ~4799) but does not exist as a method — only `detect_monthly_low` exists. This will crash at runtime during morning bias collection. |
| L12 | Main loop | `fetch_manual_orders()` is called once at the top of the scan loop but also commented as "run once per loop, not per symbol" while being inside the `for symbol in scan_universe` block — however it's actually placed before the loop, which is correct but the code comment is confusing. |

#### 🟡 PERFORMANCE ISSUES

| # | Issue | Impact |
|---|-------|--------|
| P1 | `fetch_multitf()` makes 6 API calls per symbol (4 HTF + 2 LTF) per cycle. With 500 stocks scanned every 6 min, that's 3000 API calls in <6 min. Kite limits to ~3 calls/sec = 1000 calls in 5.5 min. **API throttling will cause timeouts.** | 🔴 HIGH |
| P2 | `data_prefetch_worker()` runs an infinite loop updating ALL symbols across ALL intervals. With 500+ symbols × 4 intervals = 2000+ requests per cycle. No sleep between cycles. Will saturate API. | 🔴 HIGH |
| P3 | OHLC cache uses 15-minute staleness threshold. For 5-minute candles, this means you're often working with stale data (up to 3 candles old). | 🟡 MEDIUM |
| P4 | `generate_chart()` creates matplotlib charts synchronously in the main loop. Matplotlib with Agg backend is fast, but `mpf.plot()` with volume + addplots takes ~500ms-1s. | 🟡 LOW |
| P5 | The inner monitoring loop (`while True` at bottom of main loop) calls `fetch_ltp()` for every active trade every second. With 5+ active trades, that's 5+ API calls/sec, hitting Kite rate limits. | 🔴 HIGH |

#### 🟡 STATE MANAGEMENT ISSUES

| # | Issue |
|---|-------|
| S1 | `STRUCTURE_STATE`, `ZONE_STATE`, `SETUP_D_STATE` are all in-memory dicts. They're persisted to `engine_states_backup.pkl` at end of each cycle, but **loss of power between saves loses all state**. |
| S2 | `TRADED_TODAY` set is cleared on day reset, but if the engine crashes mid-day and restarts, it won't know which symbols were already traded. The `active_setups.json` handles dedup, but `TRADED_TODAY` doesn't persist. |
| S3 | `ACTIVE_TRADES` list is never persisted to disk. A crash mid-trade means **orphaned positions** with no SL monitoring. |
| S4 | `OHLC_CACHE` is in-memory only. After restart, all cache is cold, causing a burst of API calls. |
| S5 | `EMA_LAST_PROCESSED` dict grows unbounded. After days/weeks, memory bloats. |
| S6 | `MANUAL_ORDER_CACHE` is a set that grows unbounded and is never persisted. |

---

### 2.2 — risk_management.py (426 lines)

| # | Issue | Severity |
|---|-------|----------|
| R1 | `MIN_RR_RATIO = 3.0` here but main engine uses RR ≥ 2.0 for most setups. The risk_management module is **largely unused** by the main engine. | 🟡 MEDIUM |
| R2 | `SETUP_WINRATES` are hardcoded estimates with no evidence from actual backtest results. | 🟡 HIGH |
| R3 | `calculate_signal_quality()` uses 4 factors with arbitrary weights (40/30/20/10%) that are not statistically validated. | 🟡 MEDIUM |
| R4 | `get_daily_trade_count()` name collision with the function in smc_mtf_engine_v4.py. Different signatures (one takes symbol, one takes no args). If imported, this causes confusion. | 🟡 MEDIUM |
| R5 | `enhance_signal()` adjusts target to meet MIN_RR=3.0 if too low. This is **dangerous** — it sets unrealistic targets, inflating apparent RR but not actual achievability. | 🔴 HIGH |

---

### 2.3 — smc_confluence_engine.py (290 lines)

| # | Issue | Severity |
|---|-------|----------|
| CE1 | **Duplicate functions** — `calculate_atr()`, `liquidity_sweep_detected()`, `is_discount_zone()`, `is_premium_zone()`, `detect_htf_state()` are all duplicated from smc_mtf_engine_v4.py. If one is fixed, the other remains broken. | 🟡 HIGH |
| CE2 | `BOT_TOKEN` hardcoded again (third location in codebase). | 🔴 CRITICAL |
| CE3 | The `approve_smc_trade()` function is called from the main engine's signal dispatch, but its score overlaps with the main engine's own `smc_confluence_score()`. **Two scoring systems run on the same signal**, potentially with different results. | 🟡 HIGH |

---

### 2.4 — banknifty_signal_engine.py (1054 lines)

| # | Issue | Severity |
|---|-------|----------|
| BN1 | **Entire file is dead code** — The `BankNiftySignalEngine` class is now defined inside `smc_mtf_engine_v4.py` (lines ~3850-5100). This file is imported but the class from it is **overridden** by the inline definition. | 🟡 HIGH |
| BN2 | Import at top of main engine: `from banknifty_signal_engine import BankNiftySignalEngine` — but then the class is redefined later in the same file. The imported version is shadowed. | 🟡 MEDIUM |

---

### 2.5 — option_monitor_module.py (287 lines) & live_option_monitor.py (377 lines)

| # | Issue | Severity |
|---|-------|----------|
| OM1 | `OptionMonitor` uses `pickle` for NFO instruments cache but doesn't check file age. Stale contract data (from previous expiry week) can cause wrong strike selection. | 🔴 HIGH |
| OM2 | WebSocket error handling reconnects but doesn't verify the new connection received fresh data before resuming signal generation. | 🟡 MEDIUM |

---

### 2.6 — smc_trading_engine/ Package

| # | Issue | Severity |
|---|-------|----------|
| PKG1 | `data_fetcher.py` `_get_token()` calls `kite.instruments()` (downloads ~100K records) on **every single call**. Token cache declared but never populated. | 🔴 CRITICAL |
| PKG2 | `data_fetcher.py` `RATE_LIMIT_INTERVAL = 0.35` declared but **never enforced**. | 🔴 HIGH |
| PKG3 | `execution_core.py` `MAX_TRADES_PER_DAY=2` conflicts with `risk_management.py` `max_trades=100`. Live and backtest have different limits. | 🔴 HIGH |
| PKG4 | `execution_core.py` `close_trade()` adds R-multiple PnL to `current_equity` as if it were absolute INR — equity tracking is meaningless. | 🔴 HIGH |
| PKG5 | `backtest_engine.py` PnL is in raw price points, not position-sized. Win rate metrics are correct but profit/loss figures are not realistic. | 🔴 HIGH |
| PKG6 | `backtest_engine.py` Sharpe ratio uses `sqrt(252)` annualization (daily trades), but trades are intraday. Overestimates Sharpe by ~4x. | 🟡 HIGH |
| PKG7 | `entry_model.py` imports from `smc_trading_engine.regime.regime_controller` — **this module does not exist in the workspace**. Will cause `ImportError` at runtime. | 🔴 CRITICAL |
| PKG8 | `entry_model.py` `compute_confidence()` is defined but **never called** — confidence is hardcoded to 8.5. | 🟡 MEDIUM |
| PKG9 | `live_execution.py` has **duplicate method definitions** (`_send_telegram` and `get_order_log` defined twice). | 🟡 MEDIUM |
| PKG10 | `order_blocks.py` `OBStatus.EXPIRED` enum defined but no expiry logic exists. | 🟡 LOW |
| PKG11 | `liquidity.py` `min_sweep_atr` parameter declared but never used in sweep detection. | 🟡 LOW |
| PKG12 | `state_db.py` uses Python 3.10+ union type syntax (`dict | str | int`). Will crash on Python 3.9. | 🟡 MEDIUM |

---

### 2.7 — Supporting Scripts

| File | Issue | Severity |
|------|-------|----------|
| `kite_credentials.py` | Contains plain API key. Should use env vars or secrets management. | 🔴 CRITICAL |
| `zerodha_login.py` | 15 lines — just reads token. Minimal, but no error handling. | 🟡 LOW |
| `manual_trade_handler_v2.py` | Uses `requests` to poll Telegram updates. No retry/backoff logic. | 🟡 MEDIUM |
| `htf_structure_cache.py` | `calculate_atr()` duplicated for **4th time** in the codebase. | 🟡 HIGH |
| `swing_scanner.py` (865 lines) | Separate swing scanner with its own data fetch — not integrated with main engine caching. Fetches 500 stocks × 2 intervals = 1000 API calls per scan. | 🟡 HIGH |

---

## 3. EXECUTION FLOW (END-TO-END TRACE)

```
1. Engine boots → loads stock universe (500 symbols)
2. Connects to Kite API → initializes WebSocket
3. Starts background data prefetcher (all symbols, all TFs)
4. Main loop begins:
   a. Health check → market hours guard
   b. Morning watchlist (9:15-9:20)
   c. OI bias scan (9:20) → lock bias (9:30)
   d. Swing scanner (9:30-9:45)
   e. For each scan cycle:
      i.   EMA crossover check (every 5 min, indices only)
      ii.  Build scan universe (indices always, stocks every 6 min)
      iii. For each symbol:
           - fetch_multitf → 6 API calls
           - scan_symbol → runs Setups A, B, C, D, Hierarchical
           - Each setup: detect bias → detect OB/FVG → state machine → signal
           - Confluence score (0-10) → filter by score threshold
           - Market regime filter → OI bias filter
      iv.  Rank signals by priority → send top 5 alerts
      v.   Register as active trades
   f. Inner monitoring loop (1-second poll):
      - Check LTP for all active trades
      - Trailing SL logic
      - Manual trade sync
      - Option monitor poll
      - BN signal engine poll
5. EOD report at 16:00
6. Persist engine states to pickle
```

### Critical Observations:

1. **No order execution** — The engine only sends Telegram alerts. There is NO automatic order placement via Kite. The user must manually place trades. This means the trailing SL logic is **purely theoretical** unless the user manually adjusts their stops.

2. **No position sizing integration** — The risk_management module calculates position sizes but the main engine never calls it. Signal alerts don't include quantity.

3. **Two separate scoring systems** — `smc_confluence_score()` in the main engine AND `approve_smc_trade()` from smc_confluence_engine.py run independently on each signal.

---

## 4. RISK OF STATE CORRUPTION

| Scenario | Consequence |
|----------|-------------|
| Engine crash mid-day | Active trades lost (no monitoring), TRADED_TODAY lost (duplicate signals), ZONE_STATE lost (zones re-discovered from scratch) |
| Kite token expiry mid-session | All API calls fail silently (caught exceptions), engine continues with stale cached data, sends signals based on old data |
| Multi-process launch | SQLite WAL mode handles concurrent reads, but in-memory state (ACTIVE_TRADES, ZONE_STATE etc.) is process-local. Running two instances causes duplicate signals and duplicate trades |
| Day rollover without restart | Most state resets correctly in the `if now < time(9, 0)` block, but `EMA_LAST_PROCESSED` and `MANUAL_ORDER_CACHE` are never reset |

---

## 5. RACE CONDITIONS

| Location | Risk |
|----------|------|
| `OHLC_CACHE` | Protected by `OHLC_CACHE_LOCK` — OK |
| `ACTIVE_TRADES` | Modified in main loop AND inner monitoring loop. Both run in same thread, so no actual race, but `monitor_active_trades()` calls `.remove()` during iteration which can cause `ValueError` if list is modified externally. |
| `STRUCTURE_STATE` | Modified by `detect_setup_a()`, `cleanup_structure_state()`, and `save_engine_states()`. All in same thread — OK |
| `LiveTickStore._ticks` | Protected by `_lock` — OK |
| `bn_signal_engine.contracts` | Modified in `refresh_contracts()` called from `poll()` thread AND main thread — **potential race** |
| `ZONE_STATE` | Modified by `detect_setup_c()` for each symbol. Since symbols are scanned sequentially, no race — OK |

---

## 6. API USAGE ANALYSIS (KITE CONNECT)

**Kite API Limits:**
- Historical data: 3 requests/second
- Quotes/LTP: 1 request/second per subscription
- Order API: 10 requests/second

**Our usage pattern (worst case, 500 stocks + 2 indices):**

| Operation | Calls | Frequency | Rate |
|-----------|-------|-----------|------|
| fetch_multitf (6 TFs × 502 symbols) | 3012 | Every 6 min | 8.4/sec ❌ |
| fetch_ltp (monitoring) | 5-10 | Every 1 sec | 5-10/sec ❌ |
| Prefetcher (4 TFs × 502 symbols) | 2008 | Continuous | 2.8/sec ⚠️ |
| Option chain (21 strikes × 2) | 42 | Every 30 min | OK ✅ |

**Verdict:** The engine will be rate-limited to ~3 calls/sec max. Full scans of 500 stocks are physically impossible within Kite's limits. **The 0.35s sleep between calls allows ~2.8 calls/sec, but the prefetcher + main loop + monitoring compete for the same rate limit.**

---

## 7. SUMMARY OF CRITICAL FINDINGS

### 🔴 BLOCKER ISSUES (Must Fix Before Live)

1. **Hardcoded secrets** (BOT_TOKEN, CHAT_ID, API_KEY) in 3+ files
2. **`active_expiry_map` not initialized** — BankNiftySignalEngine crashes on first use
3. **`detect_session_low()` method doesn't exist** — Morning bias collection crashes
4. **`regime_controller` import missing** — Package entry_model crashes on import
5. **API rate limiting will cause cascading failures** with 500-stock universe
6. **No actual order execution** — Trailing stops are fictional
7. **No state persistence for active trades** — Crash = orphaned positions
8. **PUT option SL buffer direction is wrong** in BankNiftySignalEngine
9. **Data fetcher `_get_token` downloads 100K instruments every call**

### 🟠 HIGH-SEVERITY ISSUES

10. Two scoring systems running independently
11. Setup B asymmetric SL buffer (LONG static vs SHORT dynamic)
12. Killzone blocking 10:00-13:00 morning session (loss of prime alpha window)
13. `calculate_atr()` duplicated 4 times across codebase
14. Backtest PnL in price points, not position-sized
15. Risk management module largely disconnected from main engine
16. Stock universe scan timing misaligned with candle closes
17. OHLC cache 15-min staleness for 5-min strategy

### 🟡 MEDIUM-SEVERITY ISSUES

18. Dead code (banknifty_signal_engine.py import shadowed)
19. Unbounded memory growth (EMA_LAST_PROCESSED, MANUAL_ORDER_CACHE)
20. `SETUP_WINRATES` are fabricated numbers
21. Target adjustment in risk_management.py inflates RR artificially
22. Swing scanner not integrated with main engine caching
23. No proper logging rotation
24. No graceful shutdown handler
25. No heartbeat/health check monitoring

---

## 8. RECOMMENDATIONS (PRIORITY ORDER)

### Immediate (Before Any Trading)
1. Move all secrets to environment variables or a `.env` file
2. Fix `active_expiry_map` initialization
3. Fix or remove `detect_session_low()` reference
4. Fix `regime_controller` import (add stub or fix path)
5. Reduce stock universe to 50-100 most liquid stocks
6. Persist `ACTIVE_TRADES` to disk after every change

### Short-Term (Before Live)
7. Consolidate `calculate_atr` into a single shared utility
8. Unify scoring systems (remove duplicate smc_confluence_engine)
9. Add proper order execution via Kite API
10. Implement position sizing in signal output
11. Fix killzone schedule (allow 10:00-13:00 with higher filter)
12. Add graceful shutdown (signal handlers + state save)

### Medium-Term (Production Quality)
13. Refactor 5200-line monolith into modular files
14. Align backtest and live execution PnL models
15. Implement proper backtest with position sizing
16. Add unit tests for all SMC detection functions
17. Add integration tests for setup detection
18. Implement proper rate limiter (token bucket)
19. Add monitoring dashboard (Grafana/simple web UI)

---

*End of Phase 2 Report. Proceed to Phase 3 for theoretical validation.*
