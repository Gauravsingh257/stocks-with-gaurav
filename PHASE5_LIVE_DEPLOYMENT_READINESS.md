# PHASE 5 — LIVE DEPLOYMENT READINESS AUDIT

**Date:** February 23, 2026  
**Scope:** Connection management, error handling, crash recovery, monitoring, risk controls, operational procedures  
**Files Analyzed:** `smc_mtf_engine_v4.py` (main loop, connection, monitoring), `banknifty_signal_engine.py` (WebSocket), `manual_trade_handler_v2.py` (order execution), `risk_management.py` (risk engine), `option_monitor_module.py`, `utils/state_db.py`, `zerodha_login.py`, all `.bat` deployment scripts, `DAILY_RUN_CHECKLIST.md`

---

## EXECUTIVE SUMMARY

The live deployment infrastructure has **fundamental gaps** that make unattended operation dangerous. There is no auto-reconnect for Kite API, no watchdog/supervisor process, no crash recovery for active trades, hardcoded API secrets in plain text, and risk controls that are disconnected from the signal engine. The system produces **Telegram alerts only** — no auto-execution — but tracks virtual trades internally with no broker reconciliation. A single crash or token expiry loses all in-memory trade state.

**DEPLOYMENT VERDICT: HIGH RISK.** Suitable for monitored signal generation only, not for any form of automated or semi-automated trading.

---

## 1. CONNECTION MANAGEMENT

### 1.1 Kite API Connection (CRITICAL GAPS)

**Current flow:**
1. User manually runs `zerodha_login.py` → opens browser → pastes request_token → saves to `access_token.txt`
2. Engine reads token from file at startup: `kite.set_access_token(access_token)`
3. Single `test_data_connection()` health check at boot (fetches NIFTY LTP)
4. If check fails → prints error → `return` (engine exits gracefully)

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| C1 | **No token expiry detection during runtime** — Kite tokens expire at ~6:00 AM next day. If engine runs overnight (market closed loop), it will fail silently at 9:15 AM with stale token. No re-auth mechanism. | CRITICAL |
| C2 | **No API reconnection logic** — If Kite API throws `TokenException` or network drops mid-session, the outer `except` catches it, waits 60s, and retries. But `kite` object still holds expired token. | CRITICAL |
| C3 | **Connection failure at boot allows continuation** — Line 262: `# We allow it to continue (for offline testing maybe?) but it will fail on data fetch`. If Kite connection fails, `kite` is still `None` but engine proceeds to `run_live_mode()` where every API call will crash. | HIGH |
| C4 | **No heartbeat/keepalive** — No periodic health check during market hours to detect silent API degradation. | HIGH |
| C5 | **Token stored in plain text file** (`access_token.txt`) — Anyone with file access has full API control. | MEDIUM |

### 1.2 WebSocket Connection (BankNifty Signal Engine)

**Current flow:**
1. `BankNiftySignalEngine.initialize()` calls `refresh_contracts()` + `start_websocket()`
2. WebSocket runs in daemon thread via `KiteTicker`
3. Has `on_close`, `on_error`, `on_reconnect` handlers (just log, no recovery)

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| C6 | **WebSocket daemon thread dies silently** — If `_ws_thread` crashes, `tick_store.has_data()` simply returns False and engine falls back to API polling. Silent degradation with no alert. | HIGH |
| C7 | **No WebSocket health monitoring** — No periodic check if WebSocket is still connected and receiving ticks. Could be disconnected for hours without detection. | HIGH |
| C8 | **KiteTicker auto-reconnect relies on library defaults** — No custom reconnect params (`max_reconnect_delay`, `max_reconnect_tries`). Library defaults may give up after a few attempts. | MEDIUM |

### 1.3 Telegram Connection

**Current flow:**
- `telegram_send()` uses `requests.post()` with `timeout=5`
- On failure: `except Exception as e: print("Telegram error:", e)` — silently swallows
- No retry logic for Telegram failures

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| C9 | **Telegram failure is silent** — If Telegram is down, ALL alerts (trade signals, SL hits, circuit breaker) are lost with no fallback notification. | HIGH |
| C10 | **No retry/queue for failed messages** — Failed Telegram sends are lost permanently. Critical trade exit alerts could be missed. | HIGH |

---

## 2. ERROR HANDLING & CRASH RECOVERY

### 2.1 Main Loop Error Handling

```python
while True:
    try:
        # ... entire scan + trade loop ...
    except Exception as e:
        telegram_send(f"⚠️ SYSTEM ERROR\n{str(e)[:200]}")
        t.sleep(60)
```

**Assessment:** The outer try/except catches ALL exceptions, sends a Telegram alert (which itself could fail), waits 60 seconds, and retries. This is a reasonable last-resort handler, BUT:

| # | Issue | Severity |
|---|-------|----------|
| E1 | **No distinction between recoverable and fatal errors** — `TokenException` (needs re-auth) is handled the same as a transient network blip. Both get 60s sleep + retry. The token error will never self-heal. | CRITICAL |
| E2 | **Memory leak risk from infinite retry** — If a persistent error occurs (e.g., expired token), the loop retries every 60s indefinitely, accumulating error state, log file growth, and memory from failed API calls. | HIGH |
| E3 | **No error counter or escalation** — No tracking of consecutive errors. Should escalate (e.g., after 5 consecutive errors: send EMERGENCY alert + halt). | HIGH |
| E4 | **Per-symbol try/except in scan loop is good** — Individual symbol failures don't crash the whole scan cycle. This is well-designed. | OK |

### 2.2 Crash Recovery

**State persistence (`save_engine_states` / `load_engine_states`):**
- Uses pickle to save/load: `STRUCTURE_STATE`, `ZONE_STATE`, `SETUP_D_STATE`
- Saves after every full scan cycle
- Loads at startup in `run_live_mode()`

**CRITICAL MISSING:**

| # | Issue | Severity |
|---|-------|----------|
| E5 | **ACTIVE_TRADES is NOT persisted** — If the engine crashes, ALL virtual trades in progress are lost. On restart, the engine has no memory of open positions. Trade monitoring (SL/TP) stops. Trailing stops reset. | CRITICAL |
| E6 | **DAILY_LOG is NOT persisted** — Only saved as `trade_ledger_2026.csv` on trade close. If crash happens mid-trade, in-memory daily log is lost. EOD report may be incomplete. | HIGH |
| E7 | **DAILY_PNL_R, CONSECUTIVE_LOSSES, CIRCUIT_BREAKER_ACTIVE not persisted** — Crash resets the circuit breaker. Engine could exceed daily loss limit after restart. | HIGH |
| E8 | **No crash detection on startup** — Engine doesn't check if it was previously running and crashed. Should warn user about potentially orphaned trades. | MEDIUM |
| E9 | **Pickle format for state** — Binary pickle is fragile. Corruption from interrupted write = total state loss. Should use atomic write (temp file + rename) or SQLite (already have `state_db.py`). | MEDIUM |

### 2.3 Graceful Shutdown

| # | Issue | Severity |
|---|-------|----------|
| E10 | **No signal handler** — No `SIGINT`/`SIGTERM` handler or `atexit` hook. Ctrl+C kills the process without saving state. Active trades vanish. | HIGH |
| E11 | **No process lock** — Nothing prevents running two instances simultaneously. Two engines would generate duplicate signals and corrupt shared state files. | HIGH |

---

## 3. ORDER EXECUTION ARCHITECTURE

### 3.1 Main Engine = SIGNAL-ONLY

The main engine (`smc_mtf_engine_v4.py`) **never calls `kite.place_order()`**. It:
1. Detects signals → scores → ranks
2. Sends Telegram alerts with trade details
3. Tracks "virtual" trades in `ACTIVE_TRADES` list (in-memory only)
4. Monitors virtual SL/TP levels against live prices
5. Sends exit alerts via Telegram

**The user must manually enter trades on Zerodha after receiving Telegram alerts.**

### 3.2 Manual Trade Handler (`manual_trade_handler_v2.py`)

This is the ONLY component that places real orders. It:
1. Polls `kite.positions()` every 5 seconds to detect new manual positions
2. Auto-adopts non-algo positions with calculated SL/Target
3. Places SL-M (Stop-Loss Market) orders via `kite.place_order()`
4. Trails SL orders via `kite.modify_order()`

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| O1 | **Virtual vs real trade disconnect** — Engine tracks virtual trades (ACTIVE_TRADES) while manual handler tracks real positions. No reconciliation between the two. | CRITICAL |
| O2 | **Manual handler polls positions every 5 seconds inside the 1-second inner loop** — This means `kite.positions()` is called ~12 times/minute. Kite rate limit is 3 req/sec, so it's within limits but wasteful. | LOW |
| O3 | **`fetch_manual_orders()` calls `kite.orders()` once per symbol in scan loop** — Wait, correction: it's called once per cycle (line 4977), not per symbol. But the comment says "better to move it OUT of the symbol loop". Actually looking at the code, it IS called once per cycle since it's before the symbol loop. OK. | LOW |
| O4 | **SL order execution assumes NSE exchange always** — `exchange=self.kite.EXCHANGE_NSE` is hardcoded. NFO instruments would fail. | MEDIUM |
| O5 | **No order confirmation tracking** — After `place_order()`, the returned `order_id` is stored but never verified (order status not checked). Order could be rejected. | HIGH |

### 3.3 Log_trade_to_csv Issues

```python
if trade_dict["result"] == "WIN":
    pnl_r = rr * risk_mult
else:
    pnl_r = -1.0 * risk_mult
```

| # | Issue | Severity |
|---|-------|----------|
| O6 | **Trail wins logged incorrectly** — `result="WIN"` for trail wins uses full `rr * risk_mult` as PnL, but the actual exit was at the trailed SL level, not at target. Trail win at 1.5R gets logged as 2.5R (the full RR). | CRITICAL |
| O7 | **Breakeven exits not handled** — Any SL hit where `trade["sl"] == entry` is logged as LOSS with -1.0R, even though actual PnL is ~0. | HIGH |
| O8 | **BOM encoding in CSV** — `trade_ledger_2026.csv` has BOM marker (`ï»¿`) from pandas. Breaks standard CSV readers. | MEDIUM |

---

## 4. RISK CONTROLS IN LIVE MODE

### 4.1 Circuit Breaker System

**Current implementation:**
- `MAX_DAILY_LOSS_R = -3.0` → halts new entries for rest of day
- `COOLDOWN_AFTER_STREAK = 3` → pauses for ONE cycle after 3 consecutive losses
- Resets daily at `now < time(9, 0)`

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| R1 | **Cooldown resets after ONE cycle (1 minute)** — Line 4943: `CONSECUTIVE_LOSSES = 0` is inside the cooldown `continue` block. After 3 losses, it pauses for 60 seconds then resets. This is almost useless. Should pause for 30-60 minutes minimum. | CRITICAL |
| R2 | **No multi-day drawdown limit** — Circuit breaker resets daily. System can lose 3R/day for 10 days = 30R with no intervention. Need a rolling 5-day or weekly limit. | CRITICAL |
| R3 | **Circuit breaker state not persisted** — Crash + restart resets `DAILY_PNL_R` to 0. Engine can exceed daily loss limit. | HIGH |
| R4 | **Virtual trade PnL tracking vs real PnL** — `DAILY_PNL_R` tracks virtual trades, not actual broker P&L. Real manual trades are not counted. | HIGH |

### 4.2 Risk Management Module (`risk_management.py`)

**Critical discovery:** The `risk_management.py` module is **well-designed** but **only used by `manual_trade_handler_v2.py`**. The main engine DOES NOT use it.

| Feature | In `risk_management.py` | Used by Main Engine? |
|---------|------------------------|---------------------|
| Position sizing (1% risk) | ✅ `calculate_position_size()` | **NO** |
| RR validation (min 1:3) | ✅ `validate_rr_ratio()` | **NO** |
| Signal quality scoring | ✅ `is_signal_approved()` | **NO** |
| Daily trade count limit (10) | ✅ `can_trade_today()` | **NO** |
| Daily loss limit (5%) | ✅ `get_daily_pnl()` | **NO** |
| Setup win rate lookup | ✅ `get_setup_winrate()` | **NO** |

The main engine has its OWN inline risk controls (circuit breaker, cooldown) that are simpler and less robust than what `risk_management.py` offers.

| # | Issue | Severity |
|---|-------|----------|
| R5 | **Best risk module is unused by the main engine** — `risk_management.py` has position sizing, RR validation, quality scoring, but none of it is imported or called by `smc_mtf_engine_v4.py`. | CRITICAL |
| R6 | **Hardcoded fake win rates** — `SETUP_WINRATES` in `risk_management.py` has fabricated values (SETUP-D: 60% win rate). Live data shows SETUP-D has 30.6% win rate. These fake values could approve bad trades. | HIGH |
| R7 | **No global daily trade limit in main engine** — The engine can generate 20+ signals/day (Phase 4 showed 20 trades on a single day). `risk_management.py` has `MAX_TRADES_PER_DAY = 10` but it's not called. | HIGH |

### 4.3 Trade Deduplication

**Current flow:**
- `already_alerted_today(key)` checks SQLite via `state_db.py`
- Key format: `{clean_symbol}_{setup}_{direction}_{entry_rounded}`
- Separate `TRADED_TODAY` set resets at 9:00 AM
- `get_daily_trade_count(symbol)` counts per-symbol per-day

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| R8 | **Dedup key includes entry price** — `key = f"{clean_symbol}_{setup}_{direction}_{round(sig['entry'],1)}"`. Same symbol can trigger multiple times if entry price shifts by 0.1 point. On volatile instruments, this generates duplicates. | HIGH |
| R9 | **No cap on ranked_signals per cycle** — `ranked_signals[:5]` limits to 5 alerts per cycle, but with 60-second cycles, that's up to 300 alerts/hour theoretically. | MEDIUM |
| R10 | **Multiple setup types bypass per-symbol dedup** — A symbol could trigger SETUP-A, UNIVERSAL-LONG, and SETUP-D all in the same cycle since dedup keys differ by setup name. | HIGH |

---

## 5. SECURITY ASSESSMENT

| # | Issue | Severity |
|---|-------|----------|
| S1 | **API Key + Secret in plain text** — `kite_credentials.py` and `zerodha_login.py` contain `API_KEY` and `API_SECRET` in plain text. Anyone with file access has full Kite API control. | CRITICAL |
| S2 | **Telegram Bot Token hardcoded** — `BOT_TOKEN` is hardcoded in at least 3 files (`smc_mtf_engine_v4.py`, `manual_trade_handler_v2.py`, `option_monitor_module.py`). | HIGH |
| S3 | **No `.gitignore` for sensitive files** — `access_token.txt`, `kite_credentials.py`, and API keys could be accidentally committed. | HIGH |
| S4 | **`os.getenv()` with hardcoded fallbacks** — `BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8388602985:...")` — environment variable is never set, so the hardcoded fallback is always used. The env var pattern gives false sense of security. | MEDIUM |

---

## 6. OPERATIONAL PROCEDURES

### 6.1 Deployment Process

**Current:**
1. Manually run `run_login.bat` → paste token → token saved
2. Run `run_live_v4.bat` or `run_live_v4_optimized.bat`
3. Engine runs in foreground console window
4. User must keep PC on, connected, console open all day

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| D1 | **No process supervisor** — If engine crashes, it stays dead until user notices and restarts manually. No Windows Service, no Task Scheduler, no watchdog. | CRITICAL |
| D2 | **No auto-login** — Token expires daily at ~6 AM. Must manually log in every morning. No session refresh mechanism. | HIGH |
| D3 | **Runs in foreground console** — Accidentally closing the window kills the engine instantly with no state save. | HIGH |
| D4 | **Both `.bat` files are nearly identical** — `run_live_v4.bat` and `run_live_v4_optimized.bat` run the same Python script. The "optimized" version just has more echo statements. No actual optimization. | LOW |
| D5 | **No log rotation** — `live_trading_debug.log` grows indefinitely. Over weeks, could fill disk. | MEDIUM |
| D6 | **No system resource monitoring** — No checks for memory usage, disk space, CPU. Background prefetcher runs indefinitely. | MEDIUM |

### 6.2 Monitoring & Alerting

**What exists:**
- Telegram alerts for: trade signals, SL/TP hits, circuit breaker, and system errors
- Console `print()` statements for every scan cycle
- `live_trading_debug.log` file with INFO/ERROR logging
- EOD report at 4 PM via Telegram

**What's missing:**

| # | Issue | Severity |
|---|-------|----------|
| D7 | **No "engine is alive" heartbeat** — If engine hangs (deadlock, infinite loop in API call), no alert is sent. User has no way to know engine stopped working without checking the console. | CRITICAL |
| D8 | **No missed-scan detection** — If a scan cycle takes too long (>5 minutes due to API slowness for 500 stocks), no warning is raised. | HIGH |
| D9 | **No performance metric dashboard** — Win rate, drawdown, equity curve are only visible by manually parsing the CSV. No real-time view. | MEDIUM |

---

## 7. DATA PIPELINE IN LIVE MODE

### 7.1 OHLC Cache System

The engine has a background `data_prefetch_worker` that continuously updates OHLC data for all symbols across 4 timeframes. `fetch_ohlc()` serves from cache if data is < 15 minutes old.

**Issues:**

| # | Issue | Severity |
|---|-------|----------|
| P1 | **Prefetcher loops without sleep between full cycles** — After updating all 500+ stocks × 4 intervals (2000+ API calls), it immediately starts over. At 0.35s per call, one full cycle takes ~12 minutes. This means the prefetcher is ALWAYS running at max API rate. | HIGH |
| P2 | **15-minute cache TTL too long for 5-minute candles** — A 5m candle is stale after 5 minutes, but cache serves it for 15 minutes. Signals could be based on 3 candles-old data. | HIGH |
| P3 | **Prefetcher and main scan both call API** — During scan, `fetch_ohlc()` first checks cache, then makes its own API call if cache is stale. With 500 stocks × 4 TFs, the main scan ALSO generates thousands of API calls. Combined with prefetcher, this could hit Kite's daily/hourly limits. | HIGH |
| P4 | **No rate limit error detection** — If Kite returns HTTP 429 (Too Many Requests), the 3-retry loop will just retry and likely get 429 again. No exponential backoff. | HIGH |
| P5 | **Historical data lookback is always 15 days** — `datetime.now() - timedelta(days=15)` for every fetch. For daily timeframe, this gives only 10-11 candles. For weekly analysis (swing scanner), this is insufficient. | MEDIUM |

### 7.2 Token Cache

```python
def get_token(symbol: str):
    if symbol in TOKEN_CACHE: return TOKEN_CACHE[symbol]
    try:
        data = kite.ltp(symbol)
        TOKEN_CACHE[symbol] = data[symbol]["instrument_token"]
    except: pass
```

| # | Issue | Severity |
|---|-------|----------|
| P6 | **Token lookup via LTP call** — Each new symbol's instrument_token is fetched via `kite.ltp()` which is a full API call. Should use `kite.instruments()` once at startup instead. | MEDIUM |
| P7 | **Bare `except: pass`** — Token lookup failures are completely silenced. If Kite is down, every symbol just silently returns `None` and is skipped. | MEDIUM |

---

## 8. CRITICAL FINDINGS SUMMARY

### BLOCKER (Must fix before any live use)

| # | Finding | Risk |
|---|---------|------|
| B1 | **ACTIVE_TRADES not persisted** — Crash loses all open trade monitoring | Trade tracking completely lost |
| B2 | **No token expiry handling** — Engine dies silently at market open if token expired | Missed entire trading day |
| B3 | **Trail wins logged as full target PnL** — Trade ledger data is wrong | All win rate / PnL stats unreliable |
| B4 | **Cooldown resets after 1 minute** — Consecutive loss protection is fake | 11 consecutive losses (live data proves it) |
| B5 | **risk_management.py completely disconnected** — Best risk module unused | No position sizing, no quality gate |
| B6 | **No process supervisor / watchdog** — Crash = dead until manual restart | Hours of missed signals possible |
| B7 | **API credentials in plain text** — Full account access for anyone with file read | Security vulnerability |

### HIGH Severity

| # | Finding |
|---|---------|
| H1 | No multi-day drawdown circuit breaker — daily reset allows cumulative ruin |
| H2 | No heartbeat/alive signal — silent engine death undetectable |
| H3 | No error escalation — persistent errors retry forever without alerting |
| H4 | Circuit breaker state not persisted — crash resets daily loss counter |
| H5 | WebSocket failure is silent — degrades to API polling without warning |
| H6 | Telegram failures lose critical alerts permanently (no queue/retry) |
| H7 | Dedup key includes price — duplicate signals on volatile instruments |
| H8 | Multiple setups per symbol bypass dedup — can alert same stock 3x |
| H9 | Virtual vs real trade disconnect — engine tracks imaginary trades |
| H10 | No graceful shutdown handler — Ctrl+C loses all state |
| H11 | Prefetcher + scanner both making API calls — potential rate limit breach |
| H12 | Cache TTL (15min) too long for 5-minute timeframe decisions |
| H13 | No process lock — two instances corrupt shared state |
| H14 | SL order execution assumes NSE always — NFO orders would fail |

---

## 9. RECOMMENDATIONS (Priority Order)

### Immediate (Before next market day):

1. **Persist ACTIVE_TRADES** to SQLite (`state_db.py` already exists!) — save on every trade update, load on startup
2. **Add token expiry check** at startup: if token file is >20 hours old, refuse to start
3. **Fix `log_trade_to_csv`** to use actual `exit_r` (already computed in `monitor_active_trades`) instead of `rr * risk_mult`
4. **Fix cooldown** — change from 1-cycle reset to 60-minute timer
5. **Add process lock** — create a `engine.lock` file at startup, check on boot, remove on clean exit
6. **Add `atexit` handler** — save state on any exit (normal, Ctrl+C, crash)

### Short-term (This week):

7. **Integrate `risk_management.py`** into main engine — call `can_trade_today()`, `calculate_position_size()`, `is_signal_approved()` before sending alerts
8. **Add daily trade counter** — max 5 signals/day total (not per-symbol)
9. **Add heartbeat Telegram** — every 30 minutes: "Engine alive | Scanned X symbols | Y active trades | PnL: ZR"
10. **Add multi-day drawdown limit** — halt for 48 hours after cumulative -10R in rolling 5 days
11. **Move API credentials to environment variables** — remove all hardcoded keys from source files
12. **Add `.gitignore`** — exclude `access_token.txt`, `*.pkl`, `*.db`, `kite_credentials.py`

### Medium-term (Before production):

13. **Build Windows Service or Task Scheduler wrapper** with auto-restart on crash
14. **Add WebSocket health monitor** — alert if no ticks received for 5 minutes
15. **Implement Telegram message queue** with retry logic (3 retries, exponential backoff)
16. **Add error counter with escalation** — 5 consecutive main loop errors = EMERGENCY alert + halt
17. **Fix dedup key** — remove entry price from key, add per-symbol daily cap of 1 signal
18. **Add log rotation** — daily log files with 7-day retention
19. **Reconcile virtual trades with broker positions** — call `kite.positions()` to verify

---

**END OF PHASE 5 REPORT**

*Next: Phase 6 — Final Consolidated Report & Transformation Roadmap*
