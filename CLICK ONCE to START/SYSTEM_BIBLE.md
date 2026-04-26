# SYSTEM_BIBLE.md — SMC Trading System (V4.2.1)

> **Master Architecture Document** — Single source of truth for the SMC MTF Trading Engine, Research stack, Dashboard, and supporting infrastructure.
>
> **Engine version**: V4 Modular — `smc_mtf_engine_v4.py` (v4.2.1) — **LIVE on Railway**
> **Purpose**: Automated Smart Money Concepts (SMC) trading for Indian markets (NSE) via Zerodha Kite API with a Next.js/FastAPI research dashboard, Telegram executor, and Redis/SQLite persistence.
>
> If an AI agent (Cursor/Claude/Copilot) has only this file, it should be able to: understand the system, extend it, debug it, and run it.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Core Modules Breakdown](#3-core-modules-breakdown)
4. [State Management](#4-state-management)
5. [Configuration Layer](#5-configuration-layer)
6. [Dependencies](#6-dependencies)
7. [File & Folder Structure](#7-file--folder-structure)
8. [MCP Integration Plan](#8-mcp-integration-plan)
9. [Execution Flow](#9-execution-flow)
10. [Debugging Guide](#10-debugging-guide)
11. [Extension Guide](#11-extension-guide)
12. [Future Roadmap](#12-future-roadmap)

---

## 1. System Overview

### What this is
A production-grade algorithmic trading system that identifies institutional (Smart Money) footprints on Indian indices (NIFTY, BANKNIFTY, FIN SERVICE) and liquid stocks, fires multi-setup signals via Telegram, optionally auto-executes option trades through Zerodha Kite GTT OCO, and exposes everything (research ideas, running trades, analytics, agents) through a Next.js dashboard hosted at **stockswithgaurav.com**.

### Key capabilities
- **6 SMC Setups** (A, B, C, D, E, Hierarchical) running in parallel per tick, evaluated on multi-timeframe candles (5m/15m/30m/1h/4h/daily).
- **Confluence scoring** (max 10) across liquidity / location / HTF narrative / structure shift / execution quality.
- **Layered risk management**: W1 4-stage trailing, W2 circuit breaker (-3R), F1.4 cooldown (3 losses → 60min), F4.3 multi-day halt (-10R → 48h), F1.6 signal cap (5/day).
- **Research ranking engine** generating weekly SWING (1–8 wk) and LONGTERM (3–24 mo) ideas via `services/ranking_engine.py`.
- **Autonomous agents**: pre-market (tactical plan), post-market (EOD report), trade_manager (guardrails), risk_sentinel (breach alerts), OI intelligence, swing_alpha, longterm_investment.
- **Telegram executor bot** (`trade_executor_bot.py`) with inline buttons → lot selection → NFO order + GTT OCO (SL + target).
- **Live dashboard**: FastAPI backend (Railway web service) + Next.js frontend (Vercel) with WebSocket snapshot broadcast every 5s and 1Hz LTP streaming.
- **Crash recovery**: SQLite KV store (`utils/state_db.py`) persists `ACTIVE_TRADES`, circuit breaker state, zone states across restarts.
- **Distributed engine lock** via Redis (prevents duplicate engine instances on Railway).

### Markets & instruments
- **Indices**: NIFTY 50, NIFTY BANK, NIFTY FIN SERVICE (NSE).
- **Options**: NFO weekly/monthly ATM strikes (auto-resolved via `engine/expiry_manager.py`).
- **Equities**: liquid NSE stocks (gate: `is_liquid_stock()` ≥ 2M 5m volume).
- **Timezone**: IST; market hours 09:15–15:30; trading window 09:00–16:10 (engine scans).

### Live profile
- **Engine mode**: `AGGRESSIVE` → enables SETUP_A + SETUP_D + SETUP_E + HIERARCHICAL.
- **Hosting**: Railway (engine worker + FastAPI web), Vercel (Next.js frontend), Upstash/Railway Redis.
- **Daily auth**: `CLICK ONCE to START/RUN_ENGINE_ON_RAILWAY.bat` → Kite login → pushes `kite:access_token` to Redis → engine picks up without restart.

---

## 2. High-Level Architecture

### End-to-end flow
```
+--------------------+       +--------------------+       +--------------------+
| Zerodha Kite API   | <---> | Engine (Railway)   | <---> | Redis (snapshot,   |
| - historical_data  |       | smc_mtf_engine_v4  |       |   heartbeat, lock, |
| - ltp              |       | engine_runtime.py  |       |   signals:today,   |
| - place_order      |       |                    |       |   kite token)      |
| - place_gtt        |       +---------+----------+       +---------+----------+
+--------------------+                 |                            |
                                       |                            |
                   +-------------------+                            |
                   |                                                |
                   v                                                v
          +--------+---------+                         +------------+------------+
          | Telegram Bot     |                         | Dashboard Backend       |
          | trade_executor   |  signals + buttons      | FastAPI (Railway web)   |
          | _bot.py          | ----------------------> | dashboard/backend/      |
          +------------------+                         |   state_bridge.py       |
                   |                                   |   routes/*.py           |
                   | callbacks (lots, trade_yes)       |   websocket.py (5s)     |
                   v                                   +-----------+-------------+
          +------------------+                                     |
          | Kite order +     |                                     | REST + WS
          | GTT OCO leg      |                                     v
          +------------------+                         +------------+------------+
                                                       | Next.js Frontend        |
                                                       | (Vercel)                |
                                                       | stockswithgaurav.com    |
                                                       | /dashboard /research    |
                                                       | /analytics /journal     |
                                                       | /agents /oi-intel       |
                                                       +-------------------------+

                                   STORAGE
          +------------------+   +-----------------------+   +------------------+
          | smc_engine_state |   | dashboard.db          |   | CSV ledgers      |
          | .db (SQLite KV)  |   | (/data on Railway     |   | trade_ledger_    |
          | active trades,   |   |  web-volume)          |   | 2026.csv         |
          | circuit breaker  |   | 11 tables             |   | daily_pnl_log    |
          | zone states      |   | stock_reco, trades,   |   | paper_trade_log  |
          +------------------+   |  agent_logs, etc.     |   +------------------+
                                 +-----------------------+
```

### Trade lifecycle (summary)
`Market tick → fetch_multitf(symbol) → scan_symbol() runs 6 setups → confluence scoring → rank_signals → regime/OI/adaptive gates → risk_mgr approval → register ACTIVE_TRADE → Telegram alert → user clicks Trade → trade_executor_bot places NFO order + GTT OCO → monitor_active_trades() trails SL → SL/TP hit → log to trade_ledger_2026.csv + POST /api/journal/trade → dashboard journal updates`

### Deploy topology
- **Engine worker** (`Dockerfile.engine`, `railway-engine.toml`): runs `run_engine_railway.py` → starts `/health` server → launches `smc_mtf_engine_v4.run_live_mode()`.
- **Web service** (`Dockerfile`, `railway-web.toml`): runs `scripts/start_web.py` → uvicorn on `$PORT` → FastAPI app from `dashboard/backend/main.py`.
- **Frontend** (Vercel): `dashboard/frontend/` Next.js 16 / React 19 app. Env: `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_WS_URL`.
- **Shared**: Redis (via `REDIS_URL`), web-volume (`/data/dashboard.db`), engine-volume (for `smc_engine_state.db` + CSV ledgers).

---

## 3. Core Modules Breakdown

### 3.1 Market Data
**Files**: `smc_mtf_engine_v4.py` (lines 1200–1600), `engine/indicators.py`.

- `fetch_multitf(symbol)` — returns `{"5m": [...], "15m": [...], "30m": [...], "1h": [...], "4h": [...], "day": [...]}`. HTF candles are **stripped of the forming candle** (barstate-confirmed proxy); LTF keeps forming candle for real-time tap detection.
- `fetch_ohlc(symbol, interval)` — wraps `kite.historical_data` with per-request throttle, rate-limit backoff, and trading-day cache invalidation (`OHLC_CACHE` dict keyed by `(symbol, interval)`).
- `fetch_ltp(symbol)` — single-shot last traded price (used during monitor phase every ~1s).
- `get_token(symbol)` — resolves Kite instrument_token; caches in `TOKEN_CACHE`.
- `HTF_CACHE` / `HTF_CACHE_TIME` — multi-TF cache with 10-minute TTL per symbol.
- Rate limit safety: `api_throttle()` wraps every Kite call; respects `X req/sec`; retries with exponential backoff on `NetworkException`.

### 3.2 Multi-Timeframe Pipeline (MTF)
- **HTF bias**: `detect_htf_bias()` (from `engine/smc_detectors.py` via `smc_trading_engine.smc.bos_choch`) on 1H — returns `"LONG"` / `"SHORT"` / `None`.
- **Market regime**: `detect_market_regime()` (line 4020+) aggregates VWAP, opening-range, 15m bias, OI sentiment, market_state_engine → `BULLISH` / `BEARISH` / `NEUTRAL` (cached 120s).
- **Volatility regime**: ATR percentile rank vs rolling history → `LOW` / `NORMAL` / `HIGH` (cached 30 min via `VOL_REGIME_CACHE`).
- **Market state**: `engine/market_state_engine.get_market_state_label()` — finer classification: `BULLISH_REVERSAL`, `BEARISH_REVERSAL`, `TREND_CONTINUATION`, `RANGE`.

### 3.3 Strategy — All 6 Setups

All setups are invoked inside `scan_symbol(symbol)` (line 3693) and return either `None`, a signal `dict`, or a list of such dicts. Each setup output is then scored via `smc_confluence_score*()` and filtered by `min_score_for_signal`.

#### Setup A — HTF BOS Continuation
- **Logic** (lines 1683–1757):
  1. Detect 1H HTF BOS via `detect_htf_bias`.
  2. Scan 5m for Order Block + FVG (`detect_order_block`, `detect_fvg`).
  3. State machine in `STRUCTURE_STATE`: `FORMED → TAPPED → REACTION → FIRE`.
  4. Require `fvg_rejection()` + close back outside zone.
- **Entry**: FVG midpoint.
- **SL**: `recent_low - atr*0.3` (LONG) / `recent_high + atr*0.3` (SHORT).
- **Target**: `entry ± 2.0 × (entry - sl)` (RR 2.0, tuned via grid search Feb 2026).
- **Exit**: SL hit, TP hit, or structure invalidation (`invalidate_structure`).
- **Min score**: 7/10 (stocks), 5/10 (indices).

#### Setup B — Range Mean-Reversion
- **Logic** (lines 1798–1919):
  1. Detect HTF range on 1H (`high - low < 2×ATR`).
  2. Find OB at range extremes.
  3. Volume spike confirmation (`volume_expansion`, multiplier 1.2).
  4. Fresh entry check (`is_fresh_entry` — reject if price already 30% toward target).
  5. FVG optional but preferred.
- **Entry**: near OB low (LONG) / OB high (SHORT).
- **SL**: `OB_boundary ± atr*0.3` (dynamic buffer).
- **Target**: RR ≥ 2.0.
- **Exit**: SL/TP or range invalidation.
- **Status**: **Currently disabled** (`ACTIVE_STRATEGIES["SETUP_B"] = False`) — grid search showed -0.31R expectancy.

#### Setup C — Universal Zone (HTF zone → LTF tap → reaction)
- **Logic** (lines 1979–2307):
  1. **Zone discovery** across 5m/15m/30m/1h/4h — store best zones in `ZONE_STATE[symbol][LONG|SHORT]`, state = `ACTIVE`.
  2. **HTF invalidation**: if 1H structure flips, clear counter-direction zones (only if not yet `TAPPED`).
  3. **Tap** on 5m: price touches zone → state = `TAPPED`.
  4. **Reaction entry**: LONG = green close ABOVE zone high; SHORT = red close BELOW zone low.
  5. Guards: same-candle tap+reaction blocked, gap filter (>0.75%), wide-open downgrade (>1.5×ATR → half size).
- **Entry**: reaction candle close.
- **SL**: `zone_boundary ± atr*compute_dynamic_buffer()` (BankNifty 0.5×, others 0.3×).
- **Target**: RR 2.0 (1.6 for FIN SERVICE per backtest tuning).
- **Exit**: SL/TP. Per-symbol-direction daily dedup via `TRADED_TODAY`.
- **Gate**: `INDEX_ONLY=True` (stocks excluded).
- **Min score**: 3/10 (indices have relaxed threshold).

#### Setup D — CHoCH → BOS → OB + FVG
- **Logic** (lines 2412–2797): 4-stage pipeline persisted in `SETUP_D_STATE[symbol_SETUP_D]`.
  1. **CHoCH detection** (`detect_choch_setup_d`): 30-bar swing break, gap-day special (`detect_choch_opening_gap`), displacement check (range ≥ 1.2× avg_range).
  2. **BOS confirmation**: price must break 5-bar rolling extremes (or CHoCH level on gap days). Then scan fresh OB + FVG across multiple TFs.
  3. **FVG tap wait**: use FVG if available, else OB. Rescan FVG each candle (FIX-2) to catch tighter fresh FVG.
  4. **Reaction entry — 3 modes**:
     - `SWEEP`: wick beyond OB + close back inside → tight SL (wick − atr×0.1).
     - `DEEP`: candle in lower 40% of zone with wick rejection.
     - `DOUBLE_TAP`: 2nd+ touch at zone midpoint with confirmation.
- **Entry**: reaction candle close.
- **SL**: mode-dependent (see above); default `OB_boundary - atr*buffer`.
- **Target**: RR 2.0.
- **Patience gate**: max 20 bars after first tap (100 min on 5m) → state cleared.
- **Exit**: SL/TP.
- **Gates**: `INDEX_ONLY=True`, 1h ATR ≥ 12 (NIFTY) / 18 (FIN).
- **Min score**: 6/10.
- **Audit trail**: every decision appended to `SETUP_D_STRUCTURE_TRACE[symbol]` (last 50).

#### Setup E — Enhanced OB with Two-Tier CHoCH
- **Logic** (lines 2806–3150): similar to D, but with enhancements; state in `SETUP_E_STATE`.
  1. **Two-tier CHoCH** (`detect_choch_setup_e`): macro 1H HTF bias + micro 5m LTF structure break. Direction confirmed only if both align.
  2. **Swing-based BOS**: uses `detect_swing_points(left=2, right=2)` rather than rolling-extreme break (more noise-resistant).
  3. **OB v2** (`detect_order_block_v2`): 50-bar lookback, captures wick-rejection zones (tighter than standard OB).
  4. **Reaction entry**: same 3 modes as D (SWEEP/DEEP/DOUBLE_TAP) **plus direction flip**: if SL hit but structure supports opposite, `_try_direction_flip()` seeds a fresh SETUP_E state.
- **Entry / SL / Target**: same shape as D, RR gate ≥ 1.8 (slightly relaxed for wick-based entries).
- **Min score**: 5/10.

#### Setup Hierarchical (V3 Institutional)
- **Logic** (lines 1592–1620): delegates to `smc_trading_engine/strategy/entry_model.evaluate_entry(symbol, direction, candles, regime_flags)` — the ONLY import from the reusable SMC library.
- Evaluates 15m + 5m DataFrames with V3 strict logic: regime filter → HTF bias → liquidity sweep → BOS → retrace into OB/FVG → rejection candle.
- **Golden filters** (inside entry_model): min RR 1:3, volume expansion, displacement size, session time 09:30–14:30.
- Returns `TradeSetup` (entry, sl, target, RR) or `RejectedTrade` (reason).
- **Exit**: managed by main engine's trailing stops.

### 3.4 SMC Concepts (shared primitives)

Imported from `engine/smc_detectors.py` (which re-exports from `smc_trading_engine/smc/`):

| Concept | Function | Purpose |
|---|---|---|
| Order Block | `detect_order_block(candles)` | 30-bar OB: last opposite-color candle before displacement. Returns `(low, high)`. |
| Order Block v2 | `detect_order_block_v2(candles)` | 50-bar enhanced with wick-rejection zones. |
| Fair Value Gap | `detect_fvg(candles)` | 3-candle imbalance (candle[i-2].high < candle[i].low for bullish FVG). |
| BOS / CHoCH | `detect_choch(candles)`, `detect_choch_setup_d/e`, `detect_choch_opening_gap` | Swing-break detection. Returns `(direction, index)`. |
| Liquidity sweep | `liquidity_sweep_detected(candles)` | Equal H/L + wick beyond over 50-bar lookback. |
| Swing points | `detect_swing_points(candles, left, right)` | Fractal highs/lows for BOS reference. |
| Displacement | `engine/displacement_detector.detect_displacement` | Impulse candle relative to ATR. |
| Discount / Premium | `is_discount_zone`, `is_premium_zone` | Fib-based zone classification. |
| Equilibrium | `near_equilibrium` | Mid-range proximity. |

### 3.5 Risk & Trade Management

**File**: `smc_mtf_engine_v4.py` (lines 4400–5200).

- **W1 Trailing stops (4-stage)**:
  - `+1.0R` → SL to breakeven.
  - `+2.0R` → SL to `+1R`.
  - `+2.5R` → SL to `+2R`.
  - `+3.0R` → dynamic trail locking 75% of open profit.
  - SL only moves in profit direction, never backward.
- **W2 Circuit breaker**: `DAILY_PNL_R ≤ MAX_DAILY_LOSS_R (-3.0)` → `CIRCUIT_BREAKER_ACTIVE = True` → skip new entries, continue monitoring. Persisted via `state_db`.
- **F1.4 Cooldown**: `CONSECUTIVE_LOSSES ≥ 3` → `COOLDOWN_UNTIL = now + 60min`.
- **F1.6 Signal cap**: `DAILY_SIGNAL_COUNT` hard-gated at 5/day.
- **F4.3 Multi-day DD**: rolling 5-day PnL ≤ -10R → `MULTI_DAY_HALT_UNTIL = now + 48h`.
- **E7 Expiry day**: widen SL + reduce size on options expiry (`expiry_day_risk_adjustment`).
- **Risk manager** (`risk_mgr.is_signal_approved(sig, smc_score)` — external module): daily capital limits, per-symbol concentration cap, position sizing.

### 3.6 Confluence Scoring

**File**: `smc_mtf_engine_v4.py` (lines 3769–3870), functions `smc_confluence_score`, `smc_confluence_score_setup_d`, etc.

Scoring out of **10**; 5 component scores:

1. **Liquidity** — sweep detected (+2), minor liquidity (+1).
2. **Location** — discount/premium zone (+2), equilibrium (+1).
3. **HTF narrative** — aligned with HTF bias (+2), range (+1), counter (0).
4. **Structure shift** — strong CHoCH/BOS (+2), weak (+1).
5. **Execution** — OB + FVG (+2), OB or FVG alone (+1).

**Minimum thresholds for dispatch**:
- SETUP_D: ≥6
- SETUP_E: ≥5
- SETUP_A: ≥7 (stocks) / ≥5 (indices)
- SETUP_C: ≥3 (indices, relaxed)
- Others: ≥5

If score ≥ 6 → `risk_mult = 1.0` (full size); if score ≥ min but < 6 → `risk_mult = 0.5` (half size); else rejected and logged to `_REJECTION_LOG`.

### 3.7 Execution

**Files**: `trade_executor_bot.py` (separate process, long-running Telegram polling), `services/telegram_bot.py`.

- Signal → `send_signal_with_buttons(signal, alert_text, chat_id)` emits Telegram message with inline keyboard `[Trade Live ✅] [Observe 👁]`.
- User clicks Trade → bot presents lot selector `[3] [4] [5] [6]`.
- User selects → bot:
  1. Resolves NFO tradingsymbol (e.g. `NIFTY26MAR23800CE`) from cached `nfo_instruments.json`.
  2. Computes `qty = lots × LOT_SIZES[underlying]` (NIFTY 65, BANKNIFTY 15, FIN 25).
  3. `kite.place_order(tradingsymbol, exchange="NFO", transaction_type="BUY", quantity, order_type="MARKET", product="NRML")`.
  4. `kite.place_gtt(trigger_type="two-leg", trigger_values=[SL, TARGET], orders=[SL leg, Target leg])` — OCO protection.
- **Second Red Break (SRB)** strategy (`strategies/second_red_break.py`): **auto-executes** PUT order on 5m NIFTY without confirmation; trailing GTT modification once in profit.

### 3.8 Trade Management (Active trades)

**Function**: `monitor_active_trades(symbol, price)` (lines 4400–5200).

Every ~1s during signal window, per ticker in `ACTIVE_TRADES`:
1. Fetch LTP.
2. Check if SL hit → exit reason `SL_HIT` or `TRAIL_STOP`.
3. Check if TP hit → exit reason `TP_HIT`.
4. Apply W1 trailing logic (update trade dict's `sl` in place).
5. On exit:
   - Remove from `ACTIVE_TRADES`.
   - `DAILY_PNL_R += exit_r`.
   - Update `CONSECUTIVE_LOSSES` (++ on loss, reset on win).
   - Check `MAX_DAILY_LOSS_R` → possibly flip circuit breaker.
   - Check cooldown trigger.
   - Append to `trade_ledger_2026.csv` via `log_trade_to_csv()`.
   - `sync_trade_to_dashboard(trade_data)` → fire-and-forget POST `/api/journal/trade`.
   - `close_trade_graph(trade)` → finalize trade graph artifact.
   - Persist updated `ACTIVE_TRADES` via `state_db`.

**SRB trailing** (lines 6520–6550): at `+3.0R` peak, activate GTT trailing with 1.5R gap + 0.5R steps, modify GTT dynamically.

### 3.9 Reporting & Analytics

- **EOD report** (`send_eod_report` @ 16:00): aggregates today's trades from CSV, computes win rate, net PnL, sends Telegram summary.
- **Dashboard analytics** (`dashboard/backend/routes/analytics.py`):
  - `/api/analytics/summary` — total trades, win rate, profit factor, expectancy, drawdown.
  - `/api/analytics/equity-curve` — cumulative PnL.
  - `/api/analytics/by-setup` — per-setup stats.
  - `/api/analytics/rolling-winrate` — 20-trade rolling window.
  - `/api/analytics/time-of-day` — hour × win% heatmap.
  - `/api/analytics/drawdown-velocity`.
  - `/api/analytics/calendar-heatmap`.
- **Performance agent**: `services/feedback_analyzer.analyze_horizon(horizon)` → per-setup expectancy, R-multiple buckets → consumed by `agents/pre_market.py` for next-day tactical plan.
- **Post-market agent** (`agents/post_market.py` @ 15:30): EOD analyst report + logs `parameter_version` snapshot.

### 3.10 Notifications

**Telegram (primary)**:
- `services/telegram_bot.telegram_send(msg, chat_id=None)` — plaintext / Markdown / image.
- Signal alerts — button-based (`trade_executor_bot.send_signal_with_buttons`) or image-based (TradingView chart via `services/tv_mcp_bridge.capture_signal_chart`).
- Heartbeat every 30 min during market hours (`F4.5`).
- Startup bundle (once per process; dedup via Redis `engine:startup_telegram_cooldown`).
- Zone-tap alerts (5m `engine/smc_zone_tap.py`).
- Morning watchlist at 09:15–09:20.
- EOD summary at 16:00.

**Dashboard alerts**: agent findings surface as rows in `agent_logs` + `agent_action_queue` tables; frontend polls and shows badges.

**Signal dedup** (before Telegram):
- Hash key: `strategy_timeframe_symbol_timestamp`.
- Redis `signal:dedup:{signal_id}` with 3600s TTL → prevents duplicate alerts on engine restart.
- `signals:today:YYYY-MM-DD` Redis list (RPUSH) — audit log consumed by dashboard.

---

## 4. State Management

### 4.1 In-memory state (module globals in `smc_mtf_engine_v4.py`)

| Variable | Type | Shape | Mutation points |
|---|---|---|---|
| `ACTIVE_TRADES` | list (thread-safe via `ACTIVE_TRADES_LOCK`) | `[{symbol, direction, entry, sl, target, id, setup, smc_score, risk_mult, ...}, ...]` | `scan_symbol` register → append; `monitor_active_trades` → remove on exit |
| `DAILY_LOG` | list | Completed trades dicts | `monitor_active_trades` on SL/TP → append |
| `DAILY_PNL_R` | float | Running daily PnL in R | `monitor_active_trades` → `+= exit_r`; reset @ 09:00 |
| `CONSECUTIVE_LOSSES` | int | 0–N | Increment on loss, reset on win |
| `CIRCUIT_BREAKER_ACTIVE` | bool | Halt new entries | `monitor_active_trades` → True when PnL ≤ -3R |
| `COOLDOWN_UNTIL` | datetime / None | Cooldown expiry | Set to `now+60min` on 3-loss streak |
| `MULTI_DAY_HALT_UNTIL` | datetime / None | 48h halt expiry | Set by `check_multi_day_drawdown` |
| `STRUCTURE_STATE` | dict | `{key: {ob, fvg, stage, time}}` | Setup A state machine |
| `ZONE_STATE` | dict | `{symbol: {LONG: {zone, state, tf, created, has_fvg}, SHORT: {...}}}` | Setup C zone persistence (ACTIVE → TAPPED → FIRED) |
| `SETUP_D_STATE` | dict | `{symbol_SETUP_D: {bias, stage, choch_level, ob, fvg, time, ...}}` | Setup D 4-stage pipeline |
| `SETUP_E_STATE` | dict | `{symbol_SETUP_E: {bias, stage, ob, fvg, choch_time, ...}}` | Setup E pipeline + direction flip |
| `SETUP_D_STRUCTURE_TRACE` | dict | `{symbol: [{timestamp, stage, direction, entry, ...}]}` | Setup D audit (last 50/symbol) |
| `TRADED_TODAY` | set | `{clean_symbol}_LONG/SHORT` | Per-symbol-direction dedup; reset 09:00 |
| `DAILY_SIGNAL_COUNT` | int | 0–5 | Signal cap |
| `HTF_CACHE`, `OHLC_CACHE`, `TOKEN_CACHE` | dicts | Market-data caches | `fetch_*` methods |
| `MARKET_REGIME`, `VOLATILITY_REGIME` | str | `BULLISH/BEARISH/NEUTRAL`, `LOW/NORMAL/HIGH` | `detect_market_regime`, `detect_volatility_regime` |
| `_REJECTION_LOG`, `ADAPTIVE_BLOCK_LOG`, `ADAPTIVE_SCORE_LOG` | list / deque | Audit logs | `_log_signal_rejection`, adaptive gate |
| `ENGINE_STATE` | dict (thread-safe via `_ENGINE_STATE_LOCK`) | `{engine, market, nifty, banknifty, signals, trades, timestamp}` | `update_engine_state` |

### 4.2 Persistent state — SQLite key-value

**File**: `utils/state_db.py` → `smc_engine_state.db`.

```python
class StateDB:
    # Table: kv_store(store_name, key, value JSON, updated_at)
    set_value(store_name, key, value)
    get_value(store_name, key, default=None)
    get_all(store_name) -> dict
    delete_key(store_name, key)
    clear_store(store_name)
```

Stores restored at startup via `load_engine_states()` (line 5735):
- `active_trades` store → `ACTIVE_TRADES`.
- `engine_state` → circuit breaker flag, `DAILY_PNL_R`, `CONSECUTIVE_LOSSES`, `COOLDOWN_UNTIL`, `MULTI_DAY_HALT_UNTIL`.
- `zone_state`, `setup_d_state`, `setup_e_state` — via pickle in `engine_states_backup.pkl`.

### 4.3 Persistent state — Redis

See §6 / Redis keys below. Engine writes `engine:snapshot` (600s TTL) every tick for the dashboard to consume.

### 4.4 Persistent state — dashboard SQLite

**File**: `dashboard/backend/db/schema.py` → `DATA_DIR/dashboard.db` (Railway: `/data/dashboard.db`).

11 tables: `trades`, `agent_logs`, `parameter_versions`, `regime_history`, `agent_action_queue`, `stock_recommendations`, `running_trades`, `ranking_runs`, `performance_snapshots`, `recommendation_history`, `signal_events`. Portfolio tables live in `dashboard/backend/db/portfolio.py` (`portfolio_positions`, `portfolio_journal`).

Key constraints:
- `UNIQUE(symbol, horizon, status)` on `running_trades` — one ACTIVE per symbol+horizon.
- `UNIQUE(symbol, horizon, status)` on `portfolio_positions`.

### 4.5 Persistent state — CSV

- `trade_ledger_2026.csv` — canonical trade history. Columns: `date, symbol, direction, setup, entry, exit_price, result, pnl_r, score, notes`. CSV watcher thread in backend `main.py` auto-syncs to `trades` table every 30s on mtime change.
- `daily_pnl_log.csv` — daily aggregate PnL.
- `paper_trade_log.csv`, `paper_trade_outcomes.csv` — paper-mode logging.

---

## 5. Configuration Layer

### 5.1 Environment variables (`.env` + Railway service env)

| Var | Default | Purpose |
|---|---|---|
| `BACKTEST_MODE` | `"0"` | `"1"` disables live trading, uses synthetic data. |
| `KITE_API_KEY` | (required) | Zerodha Kite app API key. |
| `KITE_API_SECRET` | (required) | For token exchange. |
| `KITE_ACCESS_TOKEN` | (required) | Daily access token (or via Redis / `access_token.txt`). |
| `TELEGRAM_BOT_TOKEN` | (required) | Main bot token. |
| `TELEGRAM_CHAT_ID` | (required) | Primary alerts channel. |
| `SMC_PRO_CHAT_ID` | optional | Pro channel for premium alerts. |
| `REDIS_URL` | optional | Enables Redis features; engine gracefully degrades if unset. |
| `RAILWAY_ENVIRONMENT` | set by Railway | Triggers fail-closed Redis lock (prevents duplicate engines). |
| `ANTHROPIC_API_KEY` | optional | For chat LLM route + content generation. |
| `BANNERBEAR_API_KEY` | optional | Instagram carousel image gen. |
| `BACKEND_URL`, `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_WS_URL` | (frontend) | Railway web URL for Vercel. |

### 5.2 `config/settings.py` (Pydantic BaseSettings)

Exposes typed access to the above env vars plus:
- `engine_mode`: `"CONSERVATIVE" | "BALANCED" | "AGGRESSIVE"` (default: `AGGRESSIVE`).
- `paper_trading`: bool (default True in non-prod).
- `max_daily_loss_r`: float (-3.0).
- `max_daily_signals`: int (5).

### 5.3 `config/kite_auth.py` (token SSOT)

```python
def get_access_token() -> str:
    # 1. Redis "kite:access_token" (set by morning login callback)
    # 2. Env KITE_ACCESS_TOKEN
    # 3. access_token.txt file
```

Engine re-reads token every 2 minutes — `morning_login.bat` / `RUN_ENGINE_ON_RAILWAY.bat` works without engine restart.

### 5.4 Engine mode → strategy activation (module-level, line 348)

```python
ENGINE_MODE = "AGGRESSIVE"
ACTIVE_STRATEGIES = {
    "SETUP_A": True,
    "SETUP_B": False,       # grid-search expectancy -0.31R → disabled
    "SETUP_C": False,       # toggleable
    "SETUP_D": True,        # INDEX_ONLY gate inside scan
    "SETUP_E": True,        # INDEX_ONLY
    "HIERARCHICAL": True,
}
```

### 5.5 Tunable constants (top of engine file)

```python
MAX_DAILY_LOSS_R = -3.0
COOLDOWN_AFTER_STREAK = 3
MAX_DAILY_SIGNALS = 5
MULTI_DAY_DD_LIMIT = -10.0
MULTI_DAY_DD_WINDOW = 5
MULTI_DAY_HALT_HOURS = 48
SLIPPAGE_INDEX_PTS = 3
SLIPPAGE_STOCK_PCT = 0.05
EMA_FAST, EMA_SLOW, EMA_TREND = 10, 20, 200
ADX_THRESHOLD = 20
VOLUME_MULTIPLIER = 1.2
```

### 5.6 Modes of operation

- **LIVE**: default, real orders via Kite.
- **PAPER**: `PAPER_MODE=True` → logs to `paper_trade_log.csv`, no real orders.
- **BACKTEST**: `BACKTEST_MODE=1` → synthetic data, no live API calls; run via `scripts/run_backtest.py`.
- **DEBUG**: `DEBUG_MODE=True` (constant) → verbose logging to `live_trading_debug.log.YYYY-MM-DD`.

---

## 6. Dependencies

### Python runtime
- **Python 3.11+** (both engine and web).

### Engine-only (`requirements-engine.txt` — lightweight)
- `kiteconnect>=4.2.0` — Zerodha broker SDK.
- `pandas>=2.1.0`, `numpy>=1.24.0` — data handling.
- `redis>=5.0.0` — snapshot / heartbeat / lock / dedup.
- `mplfinance>=0.12.10` — trade graph rendering.
- `requests` — Telegram + dashboard POST.
- `python-telegram-bot>=21.0` — bot library (in `services/telegram_bot`).

### Web + full stack (`requirements-full.txt`, `requirements-railway.txt`)
- **API**: `fastapi>=0.110.0`, `uvicorn[standard]>=0.29.0`, `pydantic>=2.5.0`, `websockets>=12.0`.
- **DB**: `sqlalchemy>=2.0.0`, `aiosqlite>=0.20.0`.
- **Scheduling**: `apscheduler>=3.10.0` — agent runner cron.
- **Technical**: `ta>=0.11.0`, `pandas-ta>=0.3.14b1`, `ta-lib>=0.4.28` (excluded on Windows).
- **AI / ML**: `scikit-learn>=1.4.0`, `xgboost>=2.0.0`, `lightgbm>=4.3.0`, `optuna>=3.5.0`.
- **Backtest**: `backtrader>=1.9.78`.
- **Content**: `anthropic` (Claude), `bannerbear` (Instagram carousels).
- **Auxiliary data**: `yfinance>=0.2.30`, `ccxt>=4.2.0` (crypto).

### Frontend (`dashboard/frontend/package.json`)
- **Framework**: Next.js 16.1.6, React 19.2.3, TypeScript 5.
- **Styling**: Tailwind CSS 4, PostCSS.
- **Charts**: Recharts 3.7.0, Lightweight Charts 5.1.0.
- **Motion**: Framer Motion 12.38.0.
- **Icons**: Lucide React 0.575.0.

### External services
- **Zerodha Kite** (broker + data).
- **Telegram** (signals + executor bot).
- **Upstash / Railway Redis** (state).
- **Railway** (compute + volumes).
- **Vercel** (frontend).
- **Anthropic Claude** (optional — chat route + reasoning).
- **Bannerbear** (optional — content generation).
- **TradingView Desktop** (optional — chart screenshots via CDP MCP).

### Redis keys (complete inventory)

| Key | Type | TTL | Writer | Reader | Purpose |
|---|---|---|---|---|---|
| `kite:access_token` | string | 24h | morning login, `auto_login.py` | Engine `_reinit_kite()` | Zerodha session |
| `kite:token_ts` | string | 24h | login scripts | Engine (token-triggered reactivation) | Token timestamp IST |
| `engine:snapshot` | JSON | 600s | `engine_runtime.write_engine_snapshot` | Dashboard `state_bridge` (standalone), frontend | Full engine state |
| `engine_heartbeat` | ts | 120s | `engine_runtime.write_heartbeat` | Watchdog + dashboard health | Alive signal |
| `engine_lock` | PID | 600s (refresh 120s) | `engine_runtime.acquire_engine_lock` | Engine startup | Prevent duplicate instances |
| `engine_started_at` | ts | 86400s | lock acquisition | Dashboard uptime | Engine start time |
| `engine_version` | str | 86400s | `set_engine_version` | Dashboard system page | Running version |
| `engine_last_cycle` | ts | 120s | Main loop | Watchdog | Last successful cycle |
| `signals:today:YYYY-MM-DD` | list | 24h | Engine signal dispatch (RPUSH) | Dashboard | Daily signal audit |
| `signal:dedup:{signal_id}` | flag | 3600s | Engine pre-dispatch | Engine dedup | Prevent duplicate Telegram |
| `ltp:NIFTY`, `ltp:BANKNIFTY` | float | 60–300s | Realtime service | Frontend sparklines, command bar | Live index LTP |
| `ohlc:{symbol}:{interval}` | JSON | 5s | `dashboard/backend/cache` | Charts route | OHLC candles |
| `oi_snapshot` | JSON | 5s | OI agent | Dashboard OI route, WS | Aggregated OI |
| `option_chain:{symbol}` | JSON | 5s | Charts route | Frontend | Option chain + Greeks |
| `engine:startup_telegram_cooldown` | flag | 600s | `_send_startup_telegram_bundle` | Itself | Dedup startup alert |
| `ltp_updates` | pub/sub channel | — | Realtime service | WS broadcaster | 1Hz LTP stream |

---

## 7. File & Folder Structure

```
Trading Algo/
├── smc_mtf_engine_v4.py            # LIVE ENGINE — ~6500 lines (the heart)
├── engine_runtime.py               # Redis lock, heartbeat, snapshot publisher
├── trade_executor_bot.py           # Telegram inline-button handler + Kite orders
├── manual_trade_handler_v2.py      # Manual order sync from Kite
├── option_monitor_module.py        # Option chain monitoring
├── log_trade.py                    # Append to trade_ledger CSV
├── auto_login.py                   # Zerodha auto-login (Selenium)
├── kite_credentials.py             # (gitignored) hardcoded fallback creds
├── access_token.txt                # (gitignored) daily token fallback
├── run_engine_railway.py           # Railway entry; starts /health then engine
├── Dockerfile                      # Web service image
├── Dockerfile.engine               # Engine worker image
├── Procfile, nixpacks.toml         # Alternative deploy configs
├── railway-engine.toml             # Railway engine config
├── railway-web.toml                # Railway web config
├── requirements-full.txt           # All deps
├── requirements-engine.txt         # Engine-only deps
├── requirements-railway.txt        # Web-only deps
├── pyproject.toml                  # ruff, pytest, project metadata
├── package.json                    # Root (for some shared tooling)
├── CLAUDE.md                       # Agent-facing doc (condensed)
├── SYSTEM_BIBLE.md                 # THIS FILE
│
├── engine/                         # SMC detectors, intelligence, options
│   ├── config.py                   # Engine mode, ACTIVE_STRATEGIES
│   ├── indicators.py               # ATR, EMA, ADX, volume, kill zones, discount/premium
│   ├── smc_detectors.py            # OB, FVG, BOS, CHoCH, sweep, swing points
│   ├── liquidity_engine.py         # Phase 5 sweep detection
│   ├── market_state_engine.py      # Regime (BULLISH_REVERSAL, RANGE, etc.)
│   ├── oi_sentiment.py             # PCR, OI buildup, price-OI correlation
│   ├── oi_short_covering.py        # Strike-level short covering
│   ├── expiry_manager.py           # ATM strikes, rollover, post-expiry cutoff
│   ├── displacement_detector.py    # Impulse candle detection
│   ├── options.py                  # LiveTickStore, BankNiftySignalEngine (~1100 LOC)
│   ├── swing.py                    # Weekly swing, OB retest
│   ├── smc_zone_tap.py             # Confluence zone tap detection
│   └── paper_mode.py               # PaperTrader
│
├── services/                       # Enrichment, sync, analytics (22 modules)
│   ├── telegram_bot.py             # telegram_send() facade
│   ├── trade_tracker.py            # Seed + update running_trades rows; daemon
│   ├── dashboard_sync.py           # Fire-and-forget POST /api/journal/trade
│   ├── ranking_engine.py           # SWING / LONGTERM RankedIdea generation
│   ├── research_levels.py          # SMC-based swing/longterm levels
│   ├── portfolio_constructor.py    # Sector cap enforcement
│   ├── portfolio_tracker.py        # Live PnL aggregator
│   ├── portfolio_manager.py        # Portfolio lifecycle
│   ├── portfolio_risk.py           # VaR, DD, leverage
│   ├── factor_pipeline.py          # FactorRow builder
│   ├── data_quality.py             # QualityGateResult per symbol
│   ├── feedback_analyzer.py        # Per-setup expectancy → next-day plan
│   ├── signal_explainer.py         # extract_swing_signals, etc.
│   ├── reasoning_engine.py         # Plain-English reasoning per pick
│   ├── technical_scanner.py        # Pattern scans
│   ├── fundamental_analysis.py     # P/E, ROE, growth
│   ├── news_analysis.py            # News sentiment
│   ├── market_intelligence.py      # Macro context
│   ├── market_regime.py            # Regime classifier
│   ├── idea_selector.py            # Slot allocation
│   ├── universe_manager.py         # NSE universe cache
│   ├── price_resolver.py           # CMP arbitration (Kite → yfinance)
│   ├── trade_graph.py              # OHLC + fills chart render
│   ├── trade_graph_hooks.py        # build_/update_/close_trade_graph hooks
│   ├── tv_mcp_bridge.py            # TradingView chart screenshots
│   ├── instagram_carousel/         # Bannerbear content gen
│   └── notion_content/             # Notion API bridge
│
├── agents/                         # 10 autonomous agents
│   ├── base.py                     # BaseAgent, AgentResult dataclass
│   ├── runner.py                   # APScheduler orchestrator
│   ├── pre_market.py               # 08:45 IST tactical plan
│   ├── post_market.py              # 15:30 EOD analyst report
│   ├── trade_manager.py            # 5-min guardrails, SL proximity
│   ├── risk_sentinel.py            # 1-min breach detection
│   ├── oi_intelligence_agent.py    # OI snapshot polling
│   ├── swing_alpha_agent.py        # Weekly Mon 08:30 SWING scan
│   ├── longterm_investment_agent.py# Weekly LONGTERM scan
│   └── reasoning_validator.py      # Sanity-checks reasoning
│
├── smc_trading_engine/             # REUSABLE SMC LIBRARY (only entry_model used live)
│   ├── smc/
│   │   ├── order_blocks.py
│   │   ├── fvg.py
│   │   ├── bos_choch.py
│   │   ├── market_structure.py
│   │   └── liquidity.py
│   ├── strategy/
│   │   ├── entry_model.py          # <-- ONLY LIVE IMPORT: evaluate_entry()
│   │   ├── risk_management.py      # RiskManager (for backtest)
│   │   └── signal_generator.py
│   ├── regime/                     # RegimeControlFlags
│   ├── config/, data/, execution/, backtest/
│
├── strategies/
│   └── second_red_break.py         # SRB auto-execute + trailing GTT
│
├── config/
│   ├── settings.py                 # Pydantic BaseSettings
│   └── kite_auth.py                # Token SSOT (Redis → env → file)
│
├── utils/
│   ├── state_db.py                 # SQLite KV (smc_engine_state.db)
│   └── helpers.py
│
├── dashboard/
│   ├── backend/                    # FastAPI app
│   │   ├── main.py                 # App, startup/shutdown, router registration
│   │   ├── state_bridge.py         # engine globals ↔ dashboard (live + standalone)
│   │   ├── websocket.py            # 5s snapshot broadcast + 1Hz LTP
│   │   ├── cache.py                # Redis with in-memory fallback
│   │   ├── rate_limit.py           # 60 req/min/IP
│   │   ├── realtime.py             # Kite LTP streamer
│   │   ├── db/
│   │   │   ├── schema.py           # 11 tables, create_stock_recommendation etc.
│   │   │   └── portfolio.py        # portfolio_positions, portfolio_journal
│   │   └── routes/
│   │       ├── trades.py           # Active trades, zone state, daily PnL
│   │       ├── analytics.py        # Equity, win%, setup breakdown, drawdown
│   │       ├── journal.py          # Trade history with filters
│   │       ├── research.py         # Swing/longterm ideas, running trades, coverage
│   │       ├── agents.py           # Agent status, action queue
│   │       ├── charts.py           # OHLC + zones overlay
│   │       ├── chat.py             # Claude LLM chat
│   │       ├── system.py           # Health, version, tactical plan
│   │       ├── oi_intelligence.py  # PCR, bias, short covering
│   │       ├── engine_router.py    # Decision trace / audit
│   │       ├── kite.py             # Kite integration
│   │       ├── market_intelligence.py
│   │       ├── portfolio.py        # Portfolio CRUD
│   │       └── content.py          # Notion webhook, content calendar
│   └── frontend/                   # Next.js 16 / React 19
│       ├── app/                    # App router pages
│       │   ├── page.tsx            # → redirect /research
│       │   ├── dashboard/          # Active trades + live PnL
│       │   ├── research/           # Swing/longterm/portfolio/coverage
│       │   ├── analytics/          # Charts
│       │   ├── journal/            # Trade history
│       │   ├── agents/             # Agent logs + action queue
│       │   ├── oi-intelligence/    # PCR gauge, heatmaps
│       │   └── market-intelligence/
│       ├── components/             # UI components (SwingIdeasTable etc.)
│       ├── lib/
│       │   ├── api.ts              # Typed fetch wrapper
│       │   └── ws.ts               # useWebSocket hook
│       └── .env.production         # BACKEND_URL, WS_URL
│
├── scripts/                        # CLI scripts
│   ├── run_backtest.py
│   ├── generate_signals.py
│   ├── evaluate_performance.py
│   ├── trade_logger.py
│   ├── start_web.py
│   ├── start_dev.ps1
│   ├── market_engine.py
│   ├── check_backend_health.ps1
│   └── launchers/                  # Legacy batch (use CLICK ONCE TO START)
│
├── models/                         # Dataclasses
│   ├── running_trade.py
│   └── stock_recommendation.py
│
├── tests/                          # pytest suite
│
├── CLICK ONCE to START/            # Windows operator scripts
│   ├── RUN_ENGINE_ON_RAILWAY.bat   # Daily login → Redis → Railway redeploy
│   ├── RUN_ENGINE_ON_LOCAL.bat
│   ├── morning_login.ps1
│   ├── go_live.bat
│   ├── GENERATE_PRE_MARKET_POST.bat
│   ├── ARCHIVE_SIGNALS.bat
│   ├── SCHEDULE_DAILY_POSTS.ps1
│   └── SYSTEM_OVERVIEW.md          # operator-facing doc
│
├── docs/
│   └── archive/                    # Historical audit + deploy notes
│
├── State files (gitignored — regenerated at runtime)
│   ├── smc_engine_state.db         # SQLite KV crash recovery
│   ├── engine_states_backup.pkl    # Zone state pickle
│   ├── active_trades.json
│   ├── active_setups.json
│   ├── position_cache.json
│   ├── positions_state.json
│   ├── ob_alerted.json
│   ├── oi_sc_active_trades.json
│   ├── option_active_trades.json
│   ├── nfo_instruments.json        # NFO instrument cache
│   ├── access_token.txt
│   └── daily_log.json
│
└── CSV ledgers
    ├── trade_ledger_2026.csv       # Canonical trade history
    ├── daily_pnl_log.csv
    ├── paper_trade_log.csv
    ├── paper_trade_outcomes.csv
    ├── backtest_*.csv
    └── oi_unwinding_backtest_results.csv
```

**Key routing** — where does new work go?
- New **SMC detector** → `engine/smc_detectors.py` or `smc_trading_engine/smc/`.
- New **setup** → `smc_mtf_engine_v4.py` `detect_setup_X()` + register in `scan_symbol`.
- New **risk rule** → `smc_mtf_engine_v4.py` in `monitor_active_trades` or extracted `risk_mgr`.
- New **API endpoint** → `dashboard/backend/routes/<name>.py` + register in `main.py`.
- New **dashboard widget** → `dashboard/frontend/components/<name>.tsx` + wire into relevant page.
- New **agent** → `agents/<name>.py` extending `BaseAgent`; register with `agents/runner.py`.
- New **service integration** → `services/<name>.py`.

---

## 8. MCP Integration Plan

Model Context Protocol (MCP) servers let AIs (Cursor, Claude Desktop) take typed actions against this system. The following MCP servers are planned — each exposes a tool namespace, runs as a lightweight Python process, and uses the same Redis + SQLite as the main system.

### 8.1 AI Trade Analyst MCP
**Purpose**: Let Claude/Cursor explain any trade / signal / rejection on demand by reading real engine state.

- **Tools**:
  - `get_active_trades()` → list from `ACTIVE_TRADES` via Redis `engine:snapshot`.
  - `get_trade_graph(trade_id)` → full reasoning DAG from `dashboard.db` via `/api/trades/{id}/graph`.
  - `explain_rejection(signal_id)` → read `_REJECTION_LOG` / `ADAPTIVE_BLOCK_LOG`.
  - `run_post_trade_analysis(trade_id)` → apply `services/feedback_analyzer` to that trade in isolation.
- **Input**: trade or signal ID.
- **Output**: structured JSON + plain-English narrative (RR, confluence breakdown, regime, setup).
- **Connection point**: Reads from Redis + dashboard DB only — no engine mutation. Safe to deploy as standalone process.

### 8.2 Smart Watchlist MCP
**Purpose**: Dynamic watchlist building from SMC criteria outside engine hours.

- **Tools**:
  - `scan_universe(criteria)` — `criteria = {setup: "A|D|E", min_score: float, direction: "LONG|SHORT", tf: "1h|4h"}` — iterates liquid stocks + indices, reuses `scan_symbol()` logic but without registration.
  - `add_to_watchlist(symbol, reason)` → writes to `morning_watchlist.flag` + `stock_recommendations` (status `WATCHLIST`).
  - `suggest_swing(horizon="SWING"|"LONGTERM")` → invokes `services/ranking_engine.generate_rankings(horizon, top_k)`.
- **Input**: SMC-style criteria or natural language.
- **Output**: list of candidate `RankedIdea` rows with entry / SL / target / confidence / reasoning.
- **Connection point**: Wraps `services/ranking_engine.py` + `services/research_levels.py`. Writes to `stock_recommendations` via `db/schema.create_stock_recommendation`.

### 8.3 Real-Time Alert Intelligence MCP
**Purpose**: Let AI query live market state + synthesize custom alerts that engine doesn't natively emit.

- **Tools**:
  - `get_market_snapshot()` → Redis `engine:snapshot` + `oi_snapshot`.
  - `get_regime()` → `MARKET_REGIME`, `VOLATILITY_REGIME`, `MARKET_STATE_LABEL`.
  - `get_liquidity_zones(symbol)` → wraps `engine/liquidity_engine.detect_liquidity_sweep` via reading latest candles.
  - `emit_custom_alert(channel, text, priority)` → `services/telegram_bot.telegram_send` (gated, rate-limited).
- **Input**: symbol / alert criteria.
- **Output**: current state dict + optionally dispatch Telegram.
- **Connection point**: Reads Redis (read-only); writes limited to Telegram via `telegram_bot` (never touches `ACTIVE_TRADES`).

### 8.4 Auto Video Brain MCP
**Purpose**: Auto-generate daily/weekly market-recap scripts + carousel posts from trading data (powers the `stockswithgaurav` content engine).

- **Tools**:
  - `build_eod_script(date)` → aggregate `trade_ledger_2026.csv` + `regime_history` → narrative text.
  - `build_weekly_wrap()` → multi-day aggregation.
  - `render_carousel(template, data)` → Bannerbear API via `services/instagram_carousel`.
  - `schedule_post(platform, payload, when)` → Notion / Instagram buffer via `services/notion_content`.
- **Input**: date range + template name.
- **Output**: rendered image URLs + caption text + schedule confirmation.
- **Connection point**: Reads `trade_ledger`, `agent_logs`, `regime_history` from dashboard DB. Writes to external platforms only (not to trading state).

### 8.5 SMC Learning MCP
**Purpose**: Educational explainer for SMC concepts grounded in actual engine implementations (for students / subscribers).

- **Tools**:
  - `explain_concept(concept)` — concepts: `order_block`, `fvg`, `bos`, `choch`, `liquidity_sweep`, `displacement`, `setup_a..e`. Returns: definition + actual code reference (file + line) + recent example from `SETUP_D_STRUCTURE_TRACE`.
  - `show_recent_example(concept, count=5)` → pulls last N occurrences from audit logs + renders mini charts via `services/trade_graph`.
  - `quiz_user(topic)` → generates Q&A from the concept map.
- **Input**: concept name, optional symbol.
- **Output**: Markdown explanation + attached images + code citations.
- **Connection point**: Read-only on `engine/smc_detectors.py` source + `SETUP_D_STRUCTURE_TRACE` + `_REJECTION_LOG`. No engine mutation.

### MCP deployment pattern (generic)
- Each MCP is a Python process under `mcp_servers/<name>/server.py` exposing JSON-RPC over stdio.
- Shared dependencies: `redis`, `sqlite3`, `pandas`, `kiteconnect` (read-only LTP if needed).
- Config in `mcp.config.json` (Cursor / Claude Desktop), referencing absolute paths.
- Auth: MCP servers inherit the same `.env` / `config/settings.py`; no extra credentials.
- Safety: all tools that mutate trading state (`emit_custom_alert`) must check `BACKTEST_MODE` + `is_market_open()` + rate-limit.

---

## 9. Execution Flow

### 9.1 Daily operator flow (human)
1. Run `CLICK ONCE to START/RUN_ENGINE_ON_RAILWAY.bat` between 08:30–09:15 IST.
2. Zerodha login opens → enter credentials + TOTP.
3. Script uploads `kite:access_token` to Redis, updates `kite:token_ts`.
4. Railway engine service picks up new token within 2 minutes — no manual redeploy.

### 9.2 Engine startup sequence (`run_live_mode()` @ line 5648)
```
Phase 0  load_engine_states()          # ACTIVE_TRADES, zone states, circuit breaker
         _reinit_kite()                # pull token from Redis → env → file
         test_data_connection()        # verify kite.profile()
         _send_startup_telegram_bundle()   # dedup via Redis cooldown

Phase 1  while True:
           ENGINE_LAST_LOOP_AT = now
           if not is_market_open():
             reset_daily_state() at 09:00; sleep 5min; continue
           if not is_signal_window():
             sleep to next minute; continue

Phase 2  Morning tasks:
           09:15-09:20: send_morning_watchlist
           09:20:       bn_signal_engine.run_bias_scan
           09:30:       lock OI bias + run_swing_scan (once)

Phase 3  Risk barriers:
           check_multi_day_drawdown()  # F4.3
           risk_mgr.can_trade_today()
           if CIRCUIT_BREAKER_ACTIVE or COOLDOWN_UNTIL>now: skip scan, monitor only

Phase 4  Intelligence cache refresh:
           MARKET_REGIME      (120s TTL)
           VOLATILITY_REGIME  (30min TTL)
           OPTION_CHAIN_DATA  (30min TTL)

Phase 5  Scan loop:
           every 5min: scan_ema_crossover (NIFTY, BANKNIFTY)
           every 5min: scan_zone_taps + format_zone_tap_alert
           every 5min slot change: scan_second_red_break → auto-exec PUT
           for symbol in build_scan_universe():
             all_signals += scan_symbol(symbol)
             monitor_active_trades(symbol, fetch_ltp(symbol))

Phase 6  Signal pipeline (top 5):
           rank_signals(all_signals)
           for sig in ranked:
             if should_suppress_signal(sig, MARKET_REGIME): drop
             if OI bias conflicts: drop
             if not _adaptive_signal_gate(sig): drop
             score = _ai_signal_score(sig)
             if not risk_mgr.is_signal_approved(sig, score): drop
             if already_alerted_today(sig): drop
             # passes → continue Phase 7

Phase 7  Dispatch:
           if time > 14:00 and not index: skip (backtest evidence)
           if DAILY_SIGNAL_COUNT >= 5: skip
           register ACTIVE_TRADE (slippage-adjusted entry, risk_mult, expiry SL adj)
           build_trade_graph(sig); capture_signal_chart (TradingView)
           _send_trade_buttons(sig, alert_text)
           RPUSH signals:today:YYYY-MM-DD <signal_id>

Phase 8  Active monitoring (interleaved):
           for trade in ACTIVE_TRADES:
             price = fetch_ltp
             trailing W1 logic, SL/TP check
             on exit: DAILY_PNL_R+=exit_r; update streak; log CSV;
                      sync_trade_to_dashboard; close_trade_graph; persist

Phase 9  EOD (16:00):
           send_eod_report()  # aggregate, Telegram summary

Phase 10 wait_for_next_minute()  # align to candle close
```

### 9.3 Signal → Trade realization (Flow 3)
```
scan_symbol → detect_setup_X → signal_dict
 → smc_confluence_score → rank_signals
 → regime / OI / adaptive / risk gates
 → _send_trade_buttons (Telegram inline keyboard)
   ↓ user clicks [Trade Live ✅]
 → bot asks lot count [3][4][5][6]
   ↓ user selects
 → resolve NFO symbol (engine/expiry_manager.get_atm_strikes)
 → kite.place_order(MARKET, NRML)
 → kite.place_gtt(two-leg: SL + TARGET)
 → edit Telegram message: "✅ ORDER PLACED + GTT SET"
```

### 9.4 Trade close → Dashboard journal (Flow 4)
```
monitor_active_trades detects SL/TP
 → pnl_r = (exit - entry) / risk (direction-aware)
 → ACTIVE_TRADES.remove(); state_db.set_value("active_trades", ...)
 → close_trade_graph(trade)
 → pd.DataFrame([trade]).to_csv(trade_ledger_2026.csv, mode='a')
 → sync_trade_to_dashboard(trade_data) → POST /api/journal/trade
   ↓ backend
 → routes/journal.py INSERT INTO trades (...)
 → CSV watcher thread also syncs (idempotent) every 30s
   ↓ frontend
 → /api/journal/trades polled every 10s OR WS snapshot pushed
 → journal page re-renders
```

### 9.5 Research scan → Portfolio (Flow 2)
```
agents/swing_alpha_agent (Mon 08:30) OR /api/research/swing auto-scan
 → services/ranking_engine.generate_rankings(horizon, top_k)
   → universe_manager → data_quality → factor_pipeline
   → research_levels (SMC-based SL/TP)
   → reasoning_engine (plain-English)
   → portfolio_constructor.apply_sector_cap (max_per_sector=2)
 → db/schema.create_stock_recommendation (status=ACTIVE, agent_type=SWING|LONGTERM)
 → INSERT ranking_runs (audit)
 → INSERT recommendation_history (CREATED event)
   ↓ dashboard
 → /api/research/swing: SELECT + resolve live CMP (price_resolver) + action_tag
   (EXECUTE_NOW when gap<=0.25, WAIT_FOR_RETEST, IN_MOTION, MISSED)
 → frontend SwingIdeasTable renders with CmpFreshnessBadge
   ↓ user clicks [Trade]
 → POST /api/portfolio/add → db/portfolio.insert (UNIQUE sym+horizon+status)
```

### 9.6 Dashboard snapshot broadcast (Flow 1)
```
Engine tick → engine_runtime.write_engine_snapshot({
  active_trades, daily_pnl_r, signals_today, zone_state,
  market_regime, timestamp, index_ltp
}) → Redis SET engine:snapshot EX 600
    + write_heartbeat → engine_heartbeat EX 120

Dashboard backend:
  state_bridge.get_engine_snapshot():
    LIVE mode (engine module imported): direct read from globals (deep-copied)
    STANDALONE mode: Redis GET engine:snapshot → fallback state_db → memory cache
  /api/snapshot, /api/active-trades, /api/daily-pnl, /api/zone-state

WebSocket (every 5s):
  websocket.start_broadcast_loop → broadcast {type:"snapshot", data:{...}}
  + pub/sub ltp_updates (1Hz): {type:"ltp_update", symbol, ltp}

Frontend:
  useWebSocket hook → onmessage → setSnapshot → re-render
  fallback: REST poll /api/snapshot every 10s
```

---

## 10. Debugging Guide

### 10.1 Is the engine alive?
- Railway engine service logs → look for `Phase 1` tick every minute.
- Check Redis `engine_heartbeat` TTL > 0 (`redis-cli TTL engine_heartbeat`).
- `GET /api/system/health` returns `engine_heartbeat_age_sec` — healthy < 120s.
- Telegram 30-min heartbeat (if silent > 1h in market hours, something's wrong).

### 10.2 Engine runs but no signals
| Symptom | Likely cause | Check |
|---|---|---|
| Log shows "CIRCUIT_BREAKER_ACTIVE" | Daily PnL ≤ -3R | `state_db.get_value("engine_state", "daily_pnl_r")` |
| Log "COOLDOWN_UNTIL" | 3 consecutive losses | `state_db.get_value("engine_state", "cooldown_until")` |
| Log "MULTI_DAY_HALT_UNTIL" | Rolling 5-day ≤ -10R | `state_db.get_value("engine_state", "multi_day_halt_until")` |
| "DAILY_SIGNAL_COUNT=5" | Cap hit | Reset requires 09:00 tomorrow |
| "Signal suppressed: BEARISH regime" | Regime vs direction mismatch | Inspect `MARKET_REGIME` in Redis snapshot |
| "Min score X < threshold Y" | Confluence below gate | Read `_REJECTION_LOG` + `smc_breakdown` |
| "Adaptive gate blocked" | Low-expectancy setup in hostile regime | `ADAPTIVE_BLOCK_LOG` |
| "Already alerted today" | Per-symbol-direction dedup | `TRADED_TODAY` set |

### 10.3 Trade opened but not tracked in dashboard
- Verify `sync_trade_to_dashboard` didn't silently fail (check `services/dashboard_sync.py` retry queue).
- CSV watcher: confirm `trade_ledger_2026.csv` mtime changed + backend restarted reading.
- Force sync: call `/api/journal/refresh` (if exposed) or restart web service.

### 10.4 Kite API issues
- Token expired: check `kite:token_ts` age; re-run `RUN_ENGINE_ON_RAILWAY.bat`.
- Rate limit: engine has `api_throttle`; if persistent, inspect `fetch_ohlc` logs for `NetworkException`.
- Instrument not found: delete `nfo_instruments.json` to force refresh on next start.

### 10.5 Dashboard shows stale data
- Check `state_bridge.get_engine_snapshot()` response field `data_source`:
  - `"live"` = engine imported locally (good).
  - `"redis"` = reading Redis (normal on Railway web service).
  - `"memory_cache"` = Redis down, reading stale 10-min cache — **investigate Redis**.
- Check `redis_available` boolean.
- Check WebSocket connection in browser dev tools (should be active; if disconnected → frontend polls REST as fallback).

### 10.6 Dashboard research shows wrong count / action
- Run `curl https://<backend>/api/research/swing` directly → compare to UI.
- Check `stock_recommendations` table: `SELECT agent_type, status, created_at FROM stock_recommendations ORDER BY created_at DESC LIMIT 20`.
- If `action_tag` looks wrong, inspect `routes/research.py` `_compute_action_tag` (signed gap + is_long branching).

### 10.7 Reading audit logs
- **Decision trace**: `GET /api/engine/decision-trace?symbol=NIFTY 50&setup=SETUP_D` → reads `SETUP_D_STRUCTURE_TRACE`.
- **Rejection log**: `signal_rejections_today.json` at workspace root (append-only).
- **Agent logs**: `SELECT * FROM agent_logs ORDER BY run_time DESC` or `/api/agents/logs`.
- **Trade graph**: `GET /api/trades/{graph_id}/graph` + `/failure-analysis`.

### 10.8 Common gotchas
- **Thread safety**: any mutation of `ACTIVE_TRADES` must hold `ACTIVE_TRADES_LOCK`.
- **Forming candle**: HTF candles (30m+) have the forming candle stripped in `fetch_multitf` — don't look at last candle for HTF confirmation.
- **Timezone**: all times IST. `datetime.now()` without tz can be dangerous; prefer helpers in `utils/helpers.py`.
- **BACKTEST_MODE**: when set, `get_access_token()` may return empty — avoid calling Kite APIs.
- **Engine lock**: if engine crash-loops on Railway, check `engine_lock` key — it auto-expires in 600s but fast restarts can collide.
- **Circuit breaker persistence**: survives restarts — to manually clear in an emergency, `state_db.set_value("engine_state", "circuit_breaker_active", False)` then restart.

### 10.9 Log locations
- Railway engine: stdout → Railway log viewer.
- Local debug: `live_trading_debug.log.YYYY-MM-DD` in workspace root.
- Structured rejections: `signal_rejections_today.json`.
- Backtest: `backtest_*_output.txt` files.

---

## 11. Extension Guide

### 11.1 Add a new SMC Setup (e.g., Setup F)
1. Implement primitive detectors if needed in `engine/smc_detectors.py`.
2. Add `detect_setup_f(symbol, tf_data)` inside `smc_mtf_engine_v4.py` following the pattern of `detect_setup_d` (state dict in `SETUP_F_STATE`, persisted).
3. Register in `scan_symbol()` around line 3706 behind an `ACTIVE_STRATEGIES["SETUP_F"]` flag.
4. Add confluence scorer `smc_confluence_score_setup_f` + min-score entry in `min_score_for_signal` map.
5. Persist state in `save_engine_states` / `load_engine_states` (`state_db.set_value("setup_f_state", ...)`).
6. Add to `ACTIVE_STRATEGIES` dict at line 348 — default `False` until validated via `scripts/run_backtest.py`.
7. Run backtest: `python scripts/run_backtest.py --setup SETUP_F --days 90` → check expectancy.

### 11.2 Add an MCP server
1. Create `mcp_servers/<name>/server.py` with JSON-RPC handler.
2. Expose only **read-only** tools initially (reads Redis / dashboard.db).
3. Reuse `config/settings.py` + `config/kite_auth.py` for env.
4. Register in Cursor / Claude Desktop MCP config pointing to absolute path.
5. For any mutation tool, gate behind `BACKTEST_MODE` + `is_market_open()` + rate limit.
6. Add tests under `tests/mcp/test_<name>.py`.

### 11.3 Modify SL / trailing logic
- **Single-stage SL tweak**: edit `compute_sl_target_dynamic` in engine.
- **Trailing stages**: edit the W1 logic block in `monitor_active_trades` (lines ~4989–5040).
- **Per-setup SL buffer**: modify `compute_dynamic_buffer()` (currently returns 0.5× for BankNifty, 0.3× others).
- Always **test with backtest** before going live: `python scripts/run_backtest.py` → compare `expectancy_r` and `max_dd_r` to baseline.

### 11.4 Add a new broker
The engine assumes Zerodha Kite. To add (e.g., Dhan, Fyers):
1. Abstract Kite calls behind a `Broker` protocol in `config/broker_base.py`:
   ```python
   class Broker(Protocol):
       def historical_data(self, token, from_date, to_date, interval) -> list
       def ltp(self, symbols) -> dict
       def place_order(self, ...) -> str
       def place_gtt(self, ...) -> str
   ```
2. Implement `config/brokers/zerodha.py` (wrapping existing logic) + `config/brokers/<newbroker>.py`.
3. Select broker via `config/settings.py` → `BROKER_NAME`.
4. Replace all direct `kite.*` calls in `smc_mtf_engine_v4.py` + `trade_executor_bot.py` with `broker.*`.
5. Adjust `engine/expiry_manager.py` + `services/price_resolver.py` if instrument schema differs.

### 11.5 Add a new API endpoint
1. Create `dashboard/backend/routes/<name>.py` with FastAPI router.
2. Register in `dashboard/backend/main.py` (`app.include_router(<name>_router, prefix="/api/<name>")`).
3. If it needs engine state → use `state_bridge.get_engine_snapshot()` (read-only).
4. If it mutates `dashboard.db` → use helpers in `db/schema.py`.
5. Add typed client in `dashboard/frontend/lib/api.ts` + types.
6. Test via `curl` before wiring frontend.

### 11.6 Add a new agent
1. Create `agents/<name>.py` extending `agents/base.BaseAgent`.
2. Implement `run() -> AgentResult` returning `status`, `summary`, `findings`, `actions`, `metrics`.
3. Register in `agents/runner.py` with APScheduler cron (e.g., `trigger="cron", hour=9, minute=0`).
4. Log writes happen automatically via `BaseAgent.run()` → `agent_logs` table.
5. If it produces approval-gated actions → queue into `agent_action_queue` (status `PENDING`).
6. Expose status via `/api/agents/<name>` route.

### 11.7 Change confluence thresholds
- Edit `min_score_for_signal` dict in `scan_symbol` (line ~3879).
- Edit per-component weights inside `smc_confluence_score*()` functions.
- Always tune with backtest then forward-test 1 week in paper mode.

---

## 12. Future Roadmap

### Near-term (1–3 months)
- **Complete MCP suite**: ship the 5 MCPs in §8 starting with AI Trade Analyst + SMC Learning (read-only, lowest risk).
- **Options Greeks-aware entries**: integrate Delta / Theta filters from `option_chain:{symbol}` into Setup D/E entry logic (prevent buying high-theta decay OTMs late in session).
- **Portfolio risk dashboard v2**: live VaR, correlation matrix, sector exposure heatmap from `services/portfolio_risk.py`.
- **Live OI footprint overlay**: dashboard chart layer showing strike-level OI build-up / unwinding in real-time.
- **Setup C re-activation**: finish threshold tuning for stocks (currently INDEX_ONLY).

### Mid-term (3–6 months)
- **Broker abstraction** (§11.4) to support Dhan/Fyers as fallback.
- **ML-driven adaptive gate v2**: replace heuristic `_adaptive_signal_gate` with a trained classifier (features: setup, regime, vol, time, recent expectancy) → predicts signal survival probability.
- **Auto-position-sizing** from Kelly-fraction on rolling expectancy per setup.
- **Real-time TradingView Pine bridge**: send engine decisions as Pine inputs for visual confirmation on the desktop chart (via `services/tv_mcp_bridge`).
- **Backtest-to-live parity tests**: CI check that live engine produces the same signal as backtest on replayed historical day.

### Long-term (6–12 months)
- **Multi-account router**: fan out signals to N Kite accounts with per-account sizing / setup whitelist.
- **Index futures mode**: trade NIFTY / BANKNIFTY futures directly (not only options) — requires NRML margin logic.
- **Auto strategy discovery**: genetic algorithm over setup parameters (CHoCH lookback, RR, displacement factor) using Optuna, publishing new `SETUP_X` variants weekly.
- **Subscriber tier**: monetize via Telegram Pro channel + dashboard read-only access (stockswithgaurav).
- **Institutional-grade compliance**: audit trail export (SEBI-compliant), trade reconciliation with contract notes, tax lot tracking.
- **Crypto module**: reuse SMC primitives + `ccxt` to trade spot crypto; shared confluence scorer.
- **Mobile-first dashboard**: React Native or PWA wrapper.

### Research / experimental
- **Liquidity sweep prediction** using order-book imbalance (requires Level-2 data feed — not currently available on Kite).
- **Cross-asset regime detection**: NIFTY + USD/INR + Dow futures as joint regime features.
- **LLM-in-the-loop risk sentinel**: Claude reviews every signal pre-dispatch for "does it match our playbook" → veto right.

---

**End of SYSTEM_BIBLE.md** — Version tracked alongside engine version (`v4.2.1`). Update this document whenever a major setup, risk rule, state variable, Redis key, or route is added. Treat it as the contract between the system and any AI agent extending it.
