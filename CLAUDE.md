# SMC Trading System

Automated Smart Money Concepts trading system for Indian markets (NSE) via Zerodha Kite API.
Engine version: V4 Modular (`smc_mtf_engine_v4.py`) — v4.2.1. **System is LIVE.**

## Key Entry Points
- **Live engine**: `smc_mtf_engine_v4.py` — main loop, signal generation, trade management
- **Engine runtime**: `engine_runtime.py` — Redis snapshot publishing, heartbeat, watchdog
- **Dashboard backend**: `dashboard/backend/main.py` — FastAPI on Railway
- **Dashboard frontend**: `dashboard/frontend/` — Next.js on Vercel (stockswithgaurav.com)
- **Launcher scripts**: `CLICK ONCE to START/` — daily login, engine start, sync

## Core Directories
| Directory | Purpose |
|-----------|---------|
| `engine/` | SMC detectors, indicators, market state, liquidity engine, expiry manager |
| `services/` | Telegram bot, OI intelligence, trade tracker, market hours, displacement |
| `agents/` | Pre/post market, OI agent, swing alpha, risk sentinel, trade manager |
| `config/` | Kite auth (`kite_auth.py`), settings (`settings.py`) |
| `dashboard/backend/routes/` | All REST API endpoints (trades, journal, analytics, research, charts) |
| `dashboard/backend/` | State bridge, WebSocket, cache, rate limiter, DB schema |
| `utils/` | State DB, helpers |
| `scripts/` | Trade logger, backtest runner, deployment scripts |
| `models/` | Dataclass models (running trades, etc.) |
| `smc_trading_engine/` | Reusable SMC library — only `strategy/entry_model.py` is imported by live engine |

## Trade Data Flow
1. Engine scans → `ACTIVE_TRADES` list (in-memory) in `smc_mtf_engine_v4.py`
2. Crash recovery → persisted to `smc_engine_state.db` (SQLite key-value via `utils/state_db.py`)
3. Dashboard → `engine:snapshot` Redis key (TTL 600s), read by `dashboard/backend/state_bridge.py`
4. On trade close → appended to `trade_ledger_2026.csv` + HTTP POST to dashboard (`POST /api/journal/trade`)
5. Signals → pushed to Redis `signals:today:YYYY-MM-DD` list + Telegram via `services/telegram_bot.py`
6. 1-click execution → `trade_executor_bot.py` handles Telegram button callbacks
7. Trade sync module → `services/dashboard_sync.py` (fire-and-forget with retry queue)

## Redis Keys (Critical)
| Key | Purpose | TTL |
|-----|---------|-----|
| `kite:access_token` | Zerodha access token | 24h |
| `kite:token_ts` | Token timestamp (IST) — engine checks for fresh login | 24h |
| `signals:today:YYYY-MM-DD` | Signal list for dashboard (RPUSH) | 24h |
| `engine:snapshot` | Full engine state (trades, PnL, signals, regime) | 600s |
| `engine_heartbeat` | Alive signal | 120s |

## Deploy Architecture
- **Engine**: Railway worker service (`Dockerfile.engine`, `railway-engine.toml`)
- **Backend**: Railway web service (`railway-web.toml`)
- **Frontend**: Vercel — `dashboard/frontend/.env.production` has backend URL
- **Daily auth**: `CLICK ONCE to START/RUN_ENGINE_ON_RAILWAY.bat` → Kite login → Redis token

## Conventions
- IST timezone throughout, trading hours 09:00–16:10
- Risk measured in R-multiples (1R = 1 unit of risk)
- Signal dedup IDs: `SIG-{symbol}-{timestamp_hash}`
- All secrets in `.env` or Redis — never committed to code
- State files (`.json`, `.db`) are gitignored — regenerated at runtime

## File Organization
- `docs/archive/` — old deployment/audit markdown docs (not needed for coding)
- `scripts/launchers/` — legacy batch/ps1 launch scripts (canonical set is in `CLICK ONCE to START/`)
- `CLICK ONCE to START/SYSTEM_OVERVIEW.md` — full system documentation for AI agents
