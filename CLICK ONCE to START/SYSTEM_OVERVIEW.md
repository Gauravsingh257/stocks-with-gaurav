# SMC Trading System — Complete System Documentation

> **Purpose:** Single reference document for any AI agent (Cursor, GPT, Claude, etc.)
> to understand, debug, and modify this trading system without full codebase context.
>
> **Last updated:** 2026-03-18 (architecture fix: WS, REST, Engine→Redis snapshot)
> **Engine version:** V4 Modular (smc_mtf_engine_v4.py) — v4.2.1

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Execution Flow](#2-execution-flow)
3. [Core Components](#3-core-components)
4. [Signal Flow](#4-signal-flow)
5. [Time and Scheduling](#5-time-and-scheduling)
6. [Token and Auth Flow](#6-token-and-auth-flow)
7. [Failure Handling](#7-failure-handling)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Dashboard / Website](#9-dashboard--website)
10. [Folder Structure](#10-folder-structure)
11. [Known Risks / Edge Cases](#11-known-risks--edge-cases)
12. [AI Usage Guide](#12-ai-usage-guide)
13. [Quick Debug Guide](#13-quick-debug-guide)

---

## 1. System Overview

### What This System Does

An automated **Smart Money Concepts (SMC)** trading system for Indian markets
(NSE — NIFTY 50, BANKNIFTY, F&O stocks) using the Zerodha Kite API. It:

- Scans markets in real-time during trading hours (09:00–16:10 IST)
- Detects institutional trade setups using SMC patterns (order blocks, FVGs, BOS, CHoCH)
- Scores signals by confluence and risk
- Sends trade alerts via Telegram
- Tracks paper/live trades with trailing stops
- Provides a web dashboard for monitoring

### Architecture

```
┌─────────────────┐     git push      ┌──────────────┐
│  LOCAL MACHINE   │ ────────────────> │   GITHUB     │
│  (Windows)       │   (sync.bat)      │   (main)     │
│                  │                   └──────┬───────┘
│  • Code editing  │                          │ auto-deploy
│  • Token login   │                          │
│  • zerodha_login │           ┌───────────────┼───────────────┐
└─────────────────┘           │               │               │
                              v               v               v
                     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
                     │   RAILWAY    │ │   RAILWAY    │ │   VERCEL     │
                     │   ENGINE     │ │   WEB        │ │   FRONTEND   │
                     │              │ │              │ │              │
                     │ • Main loop  │ │ • FastAPI    │ │ • Next.js    │
                     │ • Signals    │ │ • Dashboard  │ │ • Dashboard  │
                     │ • Telegram   │ │   API + /ws  │ │   UI         │
                     │ • /health    │ │ • /health    │ │ • .env.prod  │
                     │ • Snapshot → │ │ • Snapshot ← │ │              │
                     │   Redis      │ │   Redis      │ │              │
                     └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
                            │                │                │
                            └───────┬────────┘                │
                                    v                         │
                           ┌──────────────┐                   │
                           │    REDIS     │                   │
                           │              │      REST: /api/* via rewrites
                           │ • kite token │      WS: direct to Railway Web
                           │ • engine lock│  <────────────────┘
                           │ • heartbeat  │    (browser connects directly
                           │ • signal     │     via NEXT_PUBLIC_WS_URL;
                           │   dedup      │     Vercel does NOT proxy WS)
                           │ • engine:    │
                           │   snapshot   │
                           │ • ltp:NIFTY  │
                           │ • ltp:BANK   │
                           │   NIFTY      │
                           └──────────────┘
```

### Key Constraints

- **Zerodha token expires daily at midnight IST** — must run login bat each morning
- **Indian market hours:** 09:15–15:30 IST (engine uses 09:00–16:30 window for buffer)
- **Signal window:** 09:00–16:10 IST (no new signals after 16:10)
- **Paper mode by default** — set `PAPER_MODE=False` in engine/config.py for live

---

## 2. Execution Flow

### Daily Workflow

```
Morning:
1. User runs RUN_ENGINE_ON_RAILWAY.bat
   → Opens Zerodha login page
   → User pastes redirect URL
   → zerodha_login.py exchanges request_token for access_token
   → Token stored in: access_token.txt + .env + Redis (kite:access_token + kite:token_ts)

2. Railway engine (already running 24/7) detects new token within 120s
   → _reinit_kite() validates via kite.profile()
   → If token_ts is today → _fresh_token_today = True → signals start immediately
   → If logged in after 09:15, catch-up scan runs once

Trading Day (09:00–16:10):
3. Engine scans every ~60s:
   → fetch_multitf (5m, 15m, 1h data via Kite API)
   → Run all active strategies (Setup A/C/D, Hierarchical, EMA, etc.)
   → Score signals by SMC confluence
   → Risk management approval
   → Send approved signals via Telegram
   → Monitor active trades (SL/target/trail)

4. Sub-engines run in parallel:
   → BankNifty Options Signal Engine (poll every cycle)
   → OI Short Covering Detector
   → Zone Tap Scanner (5m + 1m followup threads)
   → Option Monitor (monthly low taps)

End of Day (16:00–16:10):
5. EOD report sent at 16:00
6. Signal system pauses at 16:10 → Telegram: "Signal system PAUSED"
7. Engine loop continues (website stays live, watchdog keeps running)

Night:
8. Engine sleeps in 10s chunks (safe_sleep) with watchdog pings
9. Before 09:00 → daily state reset (trades, circuit breaker, zones, etc.)
```

### Code Changes → Production

```
Local edit → sync.bat → git add -A → git commit → git pull --rebase → git push
                                                                          │
                                                    Railway auto-deploys ←┘
                                                    (uses Dockerfile.engine)
                                                    Vercel auto-deploys (frontend)
```

---

## 3. Core Components

### ENGINE — Main Loop (`smc_mtf_engine_v4.py`)

The heart of the system. ~5100 lines. Single file containing:

| Function | Purpose |
|----------|---------|
| `run_engine_main()` | Entry point. Starts /health server, acquires Redis lock, starts watchdog + heartbeat, then calls `run_live_mode()` in a loop. |
| `run_live_mode()` | Main trading loop: token refresh → market guard → signal window → scan → signal dispatch → trade monitor. |
| `scan_symbol(symbol)` | Per-symbol scan: fetches multi-TF data, runs all active setups, scores by confluence, filters by min score. |
| `fetch_ltp(symbol)` | Live price via `_kite_call(kite.ltp)` with 10s timeout. |
| `fetch_ohlc(symbol, interval)` | Historical candles via `_kite_call(kite.historical_data)` with 15-min cache. |
| `fetch_multitf(symbol)` | Returns dict of `{5m, 15m, 1h, daily}` OHLC data. |
| `_kite_call(fn, *args, timeout=10)` | Wraps any Kite API call in ThreadPoolExecutor with hard timeout. |
| `_respect_api_throttle()` | Global 350ms spacing between Kite API calls. |
| `telegram_send(msg)` | General Telegram send with 2 retries + dedup. |
| `telegram_send_signal(msg)` | Signal-specific: 3 retries + CRITICAL log on failure. |
| `telegram_send_image(path, caption)` | Chart image send with text-only fallback. |
| `telegram_send_with_buttons(msg, buttons)` | Inline button send with text-only fallback. |
| `is_market_open()` | True during Mon–Fri 09:00–16:30 IST. |
| `is_signal_window()` | True during Mon–Fri 09:00–16:10 IST. |
| `monitor_active_trades(symbol, price)` | Trail stop / SL / target hit detection. |
| `wait_for_next_minute()` | Sleeps until next minute candle close (uses `safe_sleep`). |
| `_publish_redis_snapshot()` | Builds a dict of engine state (active trades, PnL, signals, regime, index LTP) and writes to Redis via `engine_runtime.write_engine_snapshot()`. Called at end of every loop iteration, market-closed sleep, and signal-window sleep. |
| `update_engine_state(**kw)` | Updates `ENGINE_STATE` dict; now also writes NIFTY/BANKNIFTY LTP to Redis via `engine_runtime.set_index_ltp()`. |

### RUNTIME (`engine_runtime.py`)

| Function | Purpose |
|----------|---------|
| `safe_sleep(seconds)` | Sleep in 10s chunks, calling `write_last_cycle()` before each. **Must be used instead of `time.sleep()` for any sleep > 10s.** |
| `write_last_cycle()` | Updates local timestamp + Redis key. Called at top of each loop iteration and inside `safe_sleep`. |
| `set_engine_stage(stage)` | Sets `engine_stage` string (e.g., `DATA_FETCH`, `STRATEGY_SCAN`). Shown in watchdog crash alerts. |
| `_watchdog_loop()` | Daemon thread: if `_last_cycle_local` > 180s stale → send Telegram alert with stage → `os._exit(1)`. |
| `acquire_engine_lock()` | Redis SETNX lock (600s TTL). Only one engine instance can run. |
| `refresh_engine_lock()` | Refresh lock TTL every 120s. |
| `start_heartbeat_thread()` | Writes `engine_heartbeat` to Redis every 30s. |
| `should_send_signal(id)` / `mark_signal_sent(id)` | Redis-based signal deduplication (1h TTL). |
| `write_engine_snapshot(snap)` | Writes full engine state (trades, PnL, signals, LTP, regime, mode) to Redis `engine:snapshot` (600s TTL). Called by `_publish_redis_snapshot()` in engine. |
| `set_index_ltp(nifty, banknifty)` | Writes NIFTY/BANKNIFTY LTP to Redis (`ltp:NIFTY`, `ltp:BANKNIFTY`, 300s TTL). Called by `update_engine_state()` when index prices are fetched. |

### STRATEGIES

All strategies are enabled/disabled via `ACTIVE_STRATEGIES` dict in `engine/config.py`.

| Strategy | File | Description |
|----------|------|-------------|
| Setup A | `smc_mtf_engine_v4.py` (`detect_setup_a`) | HTF bias → 5m OB + FVG confluence state machine |
| Setup C | `smc_mtf_engine_v4.py` (`detect_setup_c`) | Universal zone tap (OB/FVG on multiple TFs) |
| Setup D | `smc_mtf_engine_v4.py` (`detect_setup_d`) | BOS + FVG entry (index only) |
| Hierarchical | `smc_trading_engine/strategy/entry_model.py` (`evaluate_entry`) | Multi-TF SMC with session/structure validation |
| EMA Crossover | `smc_mtf_engine_v4.py` (`ema_crossover_scan`) | 10/20 EMA cross on 5m for NIFTY/BANKNIFTY |
| Zone Tap 5m | `engine/smc_zone_tap.py` (`scan_zone_taps`) | 5m OB/FVG tap with pattern confirmation |
| Zone Tap 1m | `engine/smc_zone_tap.py` (daemon thread) | 1m follow-up after 5m zone detected |
| BankNifty Options | `engine/options.py` (`BankNiftySignalEngine`) | Low-break, OI bias, directional bias |
| OI Short Covering | `engine/oi_short_covering.py` (`scan_short_covering`) | Per-strike OI drop + price rise detection |
| Option Monitor | `option_monitor_module.py` (`OptionMonitor`) | Monthly low tap detection |

### DATA LAYER

| Component | Location | Purpose |
|-----------|----------|---------|
| `_kite_call()` | `smc_mtf_engine_v4.py` | 10s timeout wrapper for all Kite API calls |
| `OHLC_CACHE` | `smc_mtf_engine_v4.py` | In-memory cache, 15-min TTL per (symbol, interval) |
| `TOKEN_CACHE` | `smc_mtf_engine_v4.py` | Symbol → instrument_token mapping |
| `_respect_api_throttle()` | `smc_mtf_engine_v4.py` | 350ms min spacing between API calls |
| `data_prefetch_worker()` | `smc_mtf_engine_v4.py` | Background thread that pre-caches OHLC data |

### SMC DETECTORS (`smc_detectors.py`)

Pure detection functions — no side effects, no Telegram:

| Function | Returns |
|----------|---------|
| `detect_htf_bias(candles)` | `"BULLISH"` / `"BEARISH"` / `None` |
| `detect_order_block(candles, bias)` | OB dict or `None` |
| `detect_fvg(candles, bias)` | FVG dict or `None` |
| `detect_choch(candles)` | CHoCH signal or `None` |
| `detect_swing_points(candles)` | List of swing highs/lows |
| `is_discount_zone(price, swings)` | `True` / `False` |
| `smc_confluence_score(...)` | Integer score 0–10 |

### RISK MANAGEMENT (`risk_management.py`)

| Function | Purpose |
|----------|---------|
| `can_trade_today()` | Returns `(bool, reason)` — checks daily PnL, trade count, circuit breaker |
| `is_signal_approved(signal, smc_score)` | Returns `(approved, reason, quality)` |
| `calculate_position_size(entry, sl, ...)` | Position sizing based on account risk |
| `RiskManager` class | Orchestrates all risk checks |

---

## 4. Signal Flow

```
Data Ingestion          Strategy             Scoring           Dispatch           Delivery
─────────────          ────────             ───────           ────────           ────────
fetch_multitf() ──→ scan_symbol() ──→ smc_confluence  ──→ ranked_signals ──→ telegram_send_signal()
                    ├─ detect_setup_a     _score()          [:5]                 ├─ 3 retries
                    ├─ detect_setup_c                                            ├─ Redis dedup
fetch_ltp()    ──→  ├─ detect_setup_d  ◄── min score       Risk mgr            ├─ CRITICAL log
                    ├─ evaluate_entry      filter           approval            └─ text fallback
                    └─ ema_crossover                        │
                                                            ▼
                                              is_signal_window()  ← HARD BLOCK
                                              already_alerted_today()
                                              daily cap check
                                              expiry day check
```

### Where Signals Can Fail (Silent Failure Points)

| Point | Guard |
|-------|-------|
| Kite API hang | `_kite_call()` 10s timeout → returns `None` → no data → no signals (logged) |
| Telegram send failure | `telegram_send_signal()` retries 3x → `CRITICAL` log if all fail |
| Image upload failure | `telegram_send_image()` falls back to text-only signal |
| Button send failure | `telegram_send_with_buttons()` falls back to plain text |
| Outside signal window | `is_signal_window()` gate in main loop + all sub-engine `send_alert` methods |
| Duplicate signal | Redis `signal_id` dedup (1h TTL) + per-symbol daily `already_alerted_today()` |
| Risk rejected | `risk_mgr.is_signal_approved()` → logged + skipped |

### Signal Window Enforcement (ALL paths)

| Signal Source | Where `is_signal_window()` Is Checked |
|---------------|---------------------------------------|
| Main SMC signals (A/C/D/H) | Main loop gate (before scan) |
| EMA Crossover | Main loop gate (before scan) |
| Zone Tap 5m | Main loop gate (before `scan_zone_taps`) |
| Zone Tap 1m (daemon thread) | Inside `_scan()` → checks `is_signal_window()` before `telegram_send()` |
| BankNifty Options | `send_alert()` method → checks `is_signal_window()` |
| OI Short Covering | Main loop gate (before `scan_short_covering`) |
| Option Monitor | `telegram_send()` method → checks `is_signal_window()` |

---

## 5. Time and Scheduling

### IST Handling

- **All engine code uses `now_ist()`** — returns `datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)` (naive IST).
- Railway runs in UTC. The `now_ist()` function converts to IST automatically.
- All time comparisons use `datetime.time(H, M)` against naive IST.

### Key Time Windows

| Window | Start | End | Purpose |
|--------|-------|-----|---------|
| Market open | 09:00 | 16:30 | `is_market_open()` — general market state |
| Signal window | 09:00 | 16:10 | `is_signal_window()` — signal generation |
| Signal paused | 16:10 | 09:00 | Engine runs, no signals, Telegram "PAUSED" |
| Market closed | 16:30 | 09:00 | `is_market_open()=False`, 5 min sleep cycles |
| Morning reset | < 09:00 | 09:00 | Daily state cleared (trades, circuit breaker, zones) |
| EOD report | 16:00 | — | Sent once before close |
| Morning watchlist | 09:15–09:20 | — | Sent once |
| OI bias scan | 09:20–09:21 | — | First OI reading |

### `safe_sleep()` Usage

```
CRITICAL RULE: Never use time.sleep() directly in engine code.
Always use engine_runtime.safe_sleep() to keep the watchdog alive.
```

`safe_sleep(N)` sleeps in 10s chunks, calling `write_last_cycle()` before each.
The watchdog kills the process if `write_last_cycle()` hasn't been called in 180s.

---

## 6. Token and Auth Flow

### Token Generation

```
User runs RUN_ENGINE_ON_RAILWAY.bat
  → Opens browser to Zerodha login
  → User pastes redirect URL in terminal
  → zerodha_login.py:
      1. extract_request_token(url) → request_token
      2. kite.generate_session(request_token, api_secret) → access_token
      3. Saves to:
         ├── access_token.txt (local file)
         ├── .env (KITE_ACCESS_TOKEN line)
         └── Redis (kite:access_token + kite:token_ts, TTL 24h)
```

### Token Resolution Priority (`config/kite_auth.py → get_access_token()`)

1. **Redis** `kite:access_token` (primary — used by Railway engine)
2. **Env** `KITE_ACCESS_TOKEN`
3. **File** `access_token.txt`

### Token Refresh in Engine

- Engine polls `get_access_token()` every **120 seconds**
- If token changed → `kite.set_access_token(new) → kite.profile()` (validated with timeout)
- If `kite:token_ts` is today's date and time is 09:00–16:10 → `_fresh_token_today = True`
- `_fresh_token_today` **overrides** the signal window gate → signals start immediately
- If token is refreshed after 09:15, a catch-up scan runs immediately

### Token Expiry

- Zerodha tokens expire at **midnight IST** (non-negotiable)
- `check_token_age(max_hours=20)` checks Redis `kite:token_ts` age
- If token invalid at startup → Telegram: "TOKEN INVALID" alert → engine exits

---

## 7. Failure Handling

### Watchdog System

```
engine_runtime._watchdog_loop() (daemon thread)
  │
  ├── Starts after 5-min grace period (for initial imports)
  ├── Checks _last_cycle_local every 30s
  │
  └── If age > 180s:
        ├── Log: "ENGINE STUCK at: {engine_stage}"
        ├── Redis: track kill in engine:watchdog_kills
        ├── If ≥3 kills in 5 min → "CRASH LOOP DETECTED" alert
        ├── Else → "ENGINE STUCK at: {stage}" alert
        └── os._exit(1) → Railway restarts container
```

### Engine Stage Tracking

Updated at each major phase via `engine_runtime.set_engine_stage()`:

```
INIT → LOOP_START → DATA_FETCH → STRATEGY_SCAN → SIGNAL_DISPATCH → TRADE_MONITOR
                                                                          │
Special idle stages: MARKET_CLOSED_SLEEP, SIGNAL_WINDOW_SLEEP, RECOVERY_WAIT
```

### Kite API Protection

| Protection | Mechanism |
|------------|-----------|
| Hang prevention | `_kite_call()` — 10s hard timeout via ThreadPoolExecutor |
| Rate limiting | `_respect_api_throttle()` — 350ms min spacing |
| Retry | `fetch_ohlc` retries 3x with exponential backoff |
| Token expiry | `kite.profile()` validation at startup + every 120s refresh |

### Telegram Protection

| Protection | Mechanism |
|------------|-----------|
| Regular messages | `telegram_send()` — 2 retries, exponential backoff |
| Trade signals | `telegram_send_signal()` — 3 retries + CRITICAL log |
| Image upload fail | Falls back to text-only signal |
| Button send fail | Falls back to plain text signal |
| Missing credentials | CRITICAL log, no silent drop |

### Redis Protection

| Protection | Mechanism |
|------------|-----------|
| Lock lost | Engine exits gracefully → Railway restarts |
| Redis down | `_get_redis()` returns None → features degrade gracefully |
| Signal dedup fail | Fail-open: sends signal anyway (prevents silent drops) |

---

## 8. Deployment Architecture

### Railway Engine Service

**Config:** `railway-engine.toml` (must set "Config file path" in Railway dashboard)

```toml
[build]
dockerfilePath = "Dockerfile.engine"

[deploy]
startCommand = "python run_engine_railway.py"
healthcheckPath = "/health"
healthcheckTimeout = 120
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

**Startup sequence** (`run_engine_railway.py`):
1. Start `engine_api.app` in daemon thread → `/health` responds immediately
2. Wait for `/health` to respond (up to 30s)
3. Set `SKIP_ENGINE_HTTP=1`
4. Import and run `smc_mtf_engine_v4.run_engine_main()`

### Dockerfile.engine

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements-engine.txt .
RUN pip install --no-cache-dir -r requirements-engine.txt
COPY . .
CMD ["python", "run_engine_railway.py"]
```

### Environment Variables (Railway)

| Variable | Purpose |
|----------|---------|
| `KITE_API_KEY` | Zerodha API key |
| `KITE_API_SECRET` | Zerodha API secret |
| `REDIS_URL` | Redis connection string |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `PORT` | HTTP port (Railway sets this) |
| `RAILWAY_ENVIRONMENT` | Auto-set by Railway; engine uses for Railway-specific behavior |

### Environment Variables (Vercel)

| Variable | Purpose |
|----------|---------|
| `BACKEND_URL` | Railway Web URL — used at build time by Next.js rewrites for `/api/*` proxy |
| `NEXT_PUBLIC_BACKEND_URL` | Same URL — available in browser JS for API client and WS URL derivation |
| `NEXT_PUBLIC_WS_URL` | `wss://<Railway-Web>/ws` — direct WebSocket URL (Vercel cannot proxy WS) |

> These are also stored in `dashboard/frontend/.env.production` for the Vercel build.
> If you change the Railway Web URL, update both Vercel env vars AND `.env.production`.

### Redis Keys

| Key | Purpose | TTL |
|-----|---------|-----|
| `kite:access_token` | Zerodha access token | 24h |
| `kite:token_ts` | Token generation timestamp | 24h |
| `engine_lock` | Single-instance lock | 600s (refreshed every 120s) |
| `engine_heartbeat` | Last heartbeat timestamp | 120s |
| `engine_last_cycle` | Last main loop timestamp | 300s |
| `engine_started_at` | Engine start time | None |
| `engine_version` | Engine version string | None |
| `engine:watchdog_kills` | Crash-loop detection list | 300s |
| `engine:snapshot` | Full engine state snapshot (trades, PnL, signals, regime, mode, index LTP) | 600s |
| `ltp:NIFTY` | NIFTY 50 last traded price | 300s |
| `ltp:BANKNIFTY` | NIFTY BANK last traded price | 300s |
| `{signal_id}` | Signal dedup marker | 3600s |

---

## 9. Dashboard / Website

### Backend (Railway Web Service)

- **Entry:** `scripts/start_web.py` → starts `dashboard.backend.main:app`
- **Framework:** FastAPI
- **Key routes:** `/health`, `/api/status`, `/api/snapshot`, `/api/signals`, `/api/trades`, `/api/kite/*`, `/api/system/health`, `/ws`
- **State:** Reads from `state_bridge.py` (live engine globals OR Redis `engine:snapshot` in standalone mode)

### Frontend (Vercel — stockswithgaurav.com)

- **Framework:** Next.js (TypeScript)
- **Key pages:** Live dashboard, OI Intelligence, Analytics, Charts, Journal, Research, Agents
- **REST API:** Fetches via Next.js rewrites (`/api/*` → `BACKEND_URL/api/*` at build time)
- **WebSocket:** Connects directly to Railway Web via `NEXT_PUBLIC_WS_URL` (Vercel cannot proxy WebSocket)
- **Env file:** `.env.production` bakes `BACKEND_URL`, `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_WS_URL` into the build

### How Dashboard Gets Engine State

```
┌──────────────────────────────────────────────────────────────────────┐
│ LIVE MODE (engine + web in same process)                             │
│                                                                      │
│ Engine globals ──→ state_bridge.py (direct import) ──→ /api/snapshot │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ STANDALONE MODE (engine and web on separate Railway services)        │
│                                                                      │
│ Engine ──→ _publish_redis_snapshot() ──→ Redis engine:snapshot        │
│                                                  │                   │
│ Web ──→ state_bridge.py reads engine:snapshot ←──┘                   │
│   ├── /api/snapshot (REST)                                           │
│   └── /ws (WebSocket — pushes snapshot every ~5s)                    │
│                                                                      │
│ Frontend (Vercel):                                                   │
│   ├── WebSocket: wss://Railway-Web/ws (via NEXT_PUBLIC_WS_URL)       │
│   │   └── exponential backoff, max 5 retries, then fallback to REST  │
│   └── REST polling: /api/snapshot (via Next.js rewrites)             │
└──────────────────────────────────────────────────────────────────────┘
```

### WebSocket Architecture

- **Vercel limitation:** Vercel does NOT support WebSocket proxy/upgrade
- Frontend connects directly to Railway Web URL for WebSocket
- `useWebSocket.ts` resolves WS URL: `NEXT_PUBLIC_WS_URL` → derive from `NEXT_PUBLIC_BACKEND_URL` → `localhost` (dev only)
- On production, if neither env var is set, WS is disabled (avoids broken same-domain attempts)
- Reconnect uses exponential backoff (base 2s, max 30s, max 5 attempts)
- OI Intelligence page (`oi-intelligence/page.tsx`) has its own WS connection with same backoff logic

### Environment Variables (Vercel)

| Variable | Purpose | Required |
|----------|---------|----------|
| `BACKEND_URL` | Railway Web URL — used by Next.js rewrites at build time for `/api/*` proxying | Yes |
| `NEXT_PUBLIC_BACKEND_URL` | Same Railway Web URL — available in browser JS for API client and WS fallback | Yes |
| `NEXT_PUBLIC_WS_URL` | `wss://<Railway-Web>/ws` — direct WebSocket URL bypassing Vercel | Yes |

### Key Backend Files for Dashboard Data

| File | Purpose |
|------|---------|
| `dashboard/backend/state_bridge.py` | Builds engine snapshot (merges Redis `engine:snapshot` in standalone mode) |
| `dashboard/backend/cache.py` | Redis cache layer; includes `get_engine_snapshot_from_redis()` |
| `dashboard/backend/routes/system.py` | `/api/system/health` — engine status, Kite status, worker status |
| `dashboard/frontend/lib/useWebSocket.ts` | WS client with env-based URL resolution, backoff, REST fallback |
| `dashboard/frontend/lib/api.ts` | REST API client; logs error if `NEXT_PUBLIC_BACKEND_URL` missing on prod |
| `dashboard/frontend/components/MarketCommandBar.tsx` | Top bar — fetches health + snapshot; removed `if(!base)` guard |

---

## 10. Folder Structure

```
Trading Algo/
├── smc_mtf_engine_v4.py          # MAIN ENGINE (~5100 lines) — all strategies + loop
├── engine_runtime.py             # Runtime: watchdog, safe_sleep, lock, heartbeat, snapshot + LTP write to Redis
├── smc_detectors.py              # Pure SMC detection functions
├── risk_management.py            # Risk checks, position sizing
├── option_monitor_module.py      # Monthly low tap monitor
├── manual_trade_handler_v2.py    # Manual trade sync
├── trade_executor_bot.py         # Telegram trade buttons
├── run_engine_railway.py         # Railway bootstrap (health first)
├── zerodha_login.py              # Token generation CLI
├── kite_credentials.py           # API key reference
├── log_trade.py                  # Trade logging
│
├── engine/                       # Engine sub-modules
│   ├── config.py                 # All mutable state + constants
│   ├── options.py                # BankNifty signal engine (~1600 lines)
│   ├── oi_short_covering.py      # Strike-level OI detector (~1500 lines)
│   ├── oi_sentiment.py           # Aggregate OI/PCR analysis
│   ├── smc_zone_tap.py           # Zone tap scanner (5m + 1m threads)
│   ├── market_state_engine.py    # Market regime detection
│   ├── expiry_manager.py         # Expiry date logic
│   ├── indicators.py             # Killzone, ATR, EMA, ADX
│   ├── swing.py                  # Swing trade scanner
│   ├── displacement_detector.py  # Displacement events
│   ├── liquidity_engine.py       # Liquidity level detection
│   └── paper_mode.py             # Paper trade logging
│
├── config/                       # Auth configuration
│   ├── kite_auth.py              # Token resolution (Redis → env → file)
│   └── settings.py               # Additional settings
│
├── dashboard/
│   ├── backend/                  # FastAPI backend
│   │   ├── main.py               # Web service entry
│   │   ├── engine_api.py         # Engine /health endpoint
│   │   ├── state_bridge.py       # Engine → dashboard data bridge (reads Redis engine:snapshot in standalone mode)
│   │   ├── cache.py              # Redis cache layer (incl. get_engine_snapshot_from_redis)
│   │   ├── routes/               # API route handlers
│   │   └── db/                   # Database schema
│   └── frontend/                 # Next.js frontend
│       ├── app/                  # Pages (live, analytics, OI, etc.)
│       ├── components/           # Shared UI components
│       ├── lib/                  # API client, WebSocket (useWebSocket.ts, api.ts)
│       └── .env.production       # Baked env vars (BACKEND_URL, WS_URL) for Vercel build
│
├── smc_trading_engine/           # Hierarchical strategy module
│   ├── strategy/entry_model.py   # evaluate_entry() — Hierarchical setup
│   ├── smc/                      # BOS, CHoCH, FVG, OB, liquidity
│   ├── regime/                   # Market regime classification
│   └── execution/                # Live/paper execution
│
├── agents/                       # AI agents (pre/post market, risk)
├── ai_learning/                  # ML pattern recognition
├── backtest/                     # Backtesting engine
├── services/                     # Research/ranking services
├── utils/                        # state_db, logging
├── tests/                        # Test suite
│
├── CLICK ONCE to START/          # Windows batch shortcuts
│   ├── RUN_ENGINE_ON_RAILWAY.bat # Morning token → Railway
│   ├── RUN_ENGINE_ON_LOCAL.bat   # Local engine startup
│   └── sync.bat                  # Git sync shortcut
│
├── Dockerfile.engine             # Railway engine container
├── railway-engine.toml           # Engine service config
├── railway.toml                  # Web service config
├── sync.bat                      # Full sync script
├── .env                          # Environment variables (NEVER commit)
├── .cursorignore                 # AI context exclusions
└── requirements-engine.txt       # Python dependencies
```

---

## 11. Known Risks / Edge Cases

### Active Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Token expires at midnight | Medium | User must run login bat daily. Engine sends "TOKEN INVALID" alert on failure. |
| Kite API rate limit (3 req/sec) | Low | `_respect_api_throttle()` enforces 350ms spacing |
| Railway memory limit | Low | Daily cache clears (EMA_LAST_PROCESSED, DAILY_LOG, MANUAL_ORDER_CACHE) |
| Redis connection lost | Low | All Redis operations have try/except; engine degrades gracefully |

### Historical Issues (Fixed)

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| Crash loop (12:25 AM, 12:33, 12:41...) | `t.sleep(300)` > watchdog threshold (180s) | Replaced with `safe_sleep()` |
| Signals after 16:10 | Zone tap 1m thread, OptionMonitor, BN engine bypassed window | Added `is_signal_window()` to all `send_alert` methods |
| Engine stuck on Kite hang | Bare `kite.profile()`, `kite.ltp()` without timeout | All calls via `_kite_call()` with 10s timeout |
| Wrong timezone (UTC on Railway) | `datetime.now()` used everywhere | Replaced with `now_ist()` / `datetime.now(_IST)` |
| Watchdog kills during startup | Watchdog started before engine ready | 5-min grace period before watchdog activates |
| WebSocket never connects on Vercel | Vercel serverless cannot proxy WS upgrades; frontend defaulted to same-domain `/ws` | Frontend forces direct Railway WS URL via `NEXT_PUBLIC_WS_URL`; exponential backoff + REST fallback |
| REST API fails on production | `BACKEND_URL` unset → Next.js rewrites targeted `localhost:8000` | `.env.production` bakes Railway URL; build-time warning in `next.config.ts`; error log in `api.ts` |
| Dashboard shows empty/stale data | Engine only wrote heartbeat to Redis, no full state snapshot | Engine now writes `engine:snapshot` to Redis (600s TTL) via `_publish_redis_snapshot()` every loop, including market-closed/signal-sleep paths |
| NIFTY/BANKNIFTY show "—" | Index LTP not written to Redis | `set_index_ltp()` in `engine_runtime.py`; called by `update_engine_state()` + included in `engine:snapshot` |
| Kite shows "OFF" incorrectly | `MarketCommandBar.tsx` had `if(!base) return` guard that blocked health fetch when env missing | Removed the guard; health now fetches via same-origin `/api/*` rewrites |
| Worker status overwrites engine status | `system.py` set `engine_status = "stale"` when `MARKET_ENGINE_LAST_UPDATE_KEY` was missing (standalone mode) | Now only downgrades when key exists AND is stale; missing key = standalone, keep heartbeat as source of truth |
| Engine snapshot TTL too short | `ENGINE_SNAPSHOT_TTL_SEC` was 120s but market-closed sleep is 300s, causing snapshot to expire | Increased to 600s to survive 5-min sleep cycles with margin |
| Duplicate `write_last_cycle()` in engine_runtime | Two definitions existed; older one lacked watchdog compatibility | Removed the redundant definition |

---

## 12. AI Usage Guide

### Which Files to Check For:

| Problem | Primary File | Secondary |
|---------|-------------|-----------|
| Signal issues | `smc_mtf_engine_v4.py` | `engine/config.py` (ACTIVE_STRATEGIES) |
| Crashes / freezes | `engine_runtime.py` | `smc_mtf_engine_v4.py` (run_live_mode) |
| Token / auth | `config/kite_auth.py` | `zerodha_login.py` |
| OI / options signals | `engine/options.py` | `engine/oi_short_covering.py` |
| Zone tap signals | `engine/smc_zone_tap.py` | |
| Dashboard API | `dashboard/backend/engine_api.py` | `dashboard/backend/state_bridge.py`, `dashboard/backend/cache.py` |
| WebSocket issues | `dashboard/frontend/lib/useWebSocket.ts` | `dashboard/frontend/.env.production`, `dashboard/frontend/app/oi-intelligence/page.tsx` |
| Engine→Dashboard data | `dashboard/backend/state_bridge.py` | `engine_runtime.py` (`write_engine_snapshot`), `dashboard/backend/cache.py` |
| Health/status display | `dashboard/backend/routes/system.py` | `dashboard/frontend/components/MarketCommandBar.tsx` |
| Risk / position sizing | `risk_management.py` | |
| SMC detection logic | `smc_detectors.py` | |
| Deployment | `Dockerfile.engine` + `railway-engine.toml` | `run_engine_railway.py` |

### Where NOT to Make Changes (High Risk)

- **`run_engine_railway.py`** — Bootstrap sequence; changing startup order can break Railway healthcheck
- **`engine_runtime.py` watchdog thresholds** — Changing `ENGINE_CYCLE_WATCHDOG_SEC` without updating `safe_sleep` chunk sizes can cause crash loops
- **`_respect_api_throttle()` timing** — Lowering below 350ms risks Kite rate limit bans
- **Redis key names** — Shared between engine, dashboard, and runtime; changing breaks coordination. Key names include: `engine:snapshot`, `ltp:NIFTY`, `ltp:BANKNIFTY`, `engine_heartbeat`, `engine_lock`, `kite:access_token`
- **`is_signal_window()` / `is_market_open()`** — Used across multiple files; changes must be synchronized
- **`_publish_redis_snapshot()`** — Engine→Redis data bridge; removing or breaking it will cause dashboard to show stale/empty data
- **`dashboard/frontend/.env.production`** — Contains Railway URLs baked into Vercel build; incorrect values break all API + WS connections
- **WebSocket URL resolution in `useWebSocket.ts`** — Production safety logic prevents broken same-domain WS on Vercel; do not revert to relative `/ws`

### Safe Areas for Modification

- **`engine/config.py`** — Strategy toggles, parameters, thresholds
- **`smc_detectors.py`** — Pure detection functions (no side effects)
- **`risk_management.py`** — Risk thresholds and position sizing
- **Individual strategy functions** — `detect_setup_a/c/d` in `smc_mtf_engine_v4.py`
- **Dashboard frontend** — `dashboard/frontend/` (isolated from engine)
- **Test files** — `tests/` (no production impact)

### Entry Points

| Purpose | File | Function |
|---------|------|----------|
| Engine start | `run_engine_railway.py` | `main()` |
| Engine main | `smc_mtf_engine_v4.py` | `run_engine_main()` |
| Trading loop | `smc_mtf_engine_v4.py` | `run_live_mode()` |
| Symbol scan | `smc_mtf_engine_v4.py` | `scan_symbol(symbol)` |
| Web backend | `scripts/start_web.py` | Starts FastAPI |
| Token login | `zerodha_login.py` | `main()` |

---

## 13. Quick Debug Guide

### "If X happens → check Y"

| Symptom | Check |
|---------|-------|
| **No signals at all** | 1. `is_signal_window()` — is it 09:00–16:10? 2. `ACTIVE_STRATEGIES` in `engine/config.py` — are strategies enabled? 3. Token valid? Check Telegram for "TOKEN INVALID" alert 4. `_fresh_token_today` — was token refreshed today? |
| **Engine crash loop** | 1. Check watchdog Telegram alert — what `stage` is shown? 2. If stage = `MARKET_CLOSED_SLEEP` → `safe_sleep()` is broken 3. If stage = `DATA_FETCH` → Kite API hanging → check `_kite_call` timeout 4. Redis `engine:watchdog_kills` — how many kills in 5 min? |
| **Signals fire after 16:10** | Check all signal paths have `is_signal_window()` gate. Key files: `engine/smc_zone_tap.py` (1m thread), `engine/options.py` (`send_alert`), `option_monitor_module.py` (`telegram_send`) |
| **"ENGINE STUCK" once then recovers** | Normal — a single slow Kite API call exceeded timeout. Watchdog restarted, engine recovered. No action needed unless repeated. |
| **Token not picked up** | 1. Check `kite:access_token` in Redis 2. Check `kite:token_ts` — is it today's date? 3. Engine polls every 120s — wait 2 min 4. Check engine logs for "Kite token refresh failed" |
| **No data / empty scans** | 1. Check `fetch_ohlc` return — is it `[]`? 2. Kite session valid? Run `kite.profile()` 3. Rate limited? Check for "NetworkException" in logs 4. Holiday? Market may be closed |
| **Dashboard shows stale data** | 1. Check `engine_heartbeat` Redis key — is it recent? 2. Check `engine:snapshot` Redis key — is it present and < 600s old? 3. Check `state_bridge.py` — is it reading snapshot in standalone mode? 4. Frontend: check `NEXT_PUBLIC_BACKEND_URL` env var is set |
| **WebSocket not connecting** | 1. Check `NEXT_PUBLIC_WS_URL` is set in Vercel to `wss://<Railway-Web>/ws` 2. Check Railway Web service is running and `/ws` is accessible 3. Browser console: look for `WS CONNECTING →`, `WS CONNECTED`, or `WS FAILED` 4. Vercel CANNOT proxy WS — frontend must connect directly to Railway |
| **NIFTY/BANKNIFTY show "—"** | 1. Check `ltp:NIFTY` and `ltp:BANKNIFTY` Redis keys — present? 2. Engine must call `set_index_ltp()` via `update_engine_state()` 3. Check `engine:snapshot` → `index_ltp` field 4. `state_bridge.py` merges `index_ltp` from snapshot |
| **Kite shows "OFF" on dashboard** | 1. Check `/api/system/health` → `kite_connected` field 2. Is `kite:access_token` in Redis valid and today's date? 3. Was `MarketCommandBar.tsx` guard (`if (!base) return`) removed? It should be. |
| **Engine status says "stale" incorrectly** | 1. Check `routes/system.py` — `MARKET_ENGINE_LAST_UPDATE_KEY` logic 2. In standalone mode, if worker key is missing, engine_status should NOT be overwritten 3. Engine heartbeat (30s TTL) is the source of truth |
| **Railway deploy fails** | 1. Check `requirements-engine.txt` — missing dependency? 2. Check `Dockerfile.engine` — syntax ok? 3. Railway dashboard → Build Logs for exact error |
| **Telegram not sending** | 1. Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars 2. Check engine logs for `[Telegram]` lines 3. Bot may be blocked or chat ID wrong — test with `test_telegram.py` |

---

> **Confirmation:** This documentation is sufficient for an AI agent to understand and operate
> on the system without full repo context. It covers architecture, execution flow, signal pipeline,
> failure handling, deployment, and provides actionable debug guidance for all common scenarios.
>
> **See also:** `docs/WEBSITE_AUDIT.md` for the full website audit and applied fix details.
