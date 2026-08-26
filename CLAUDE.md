# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Scope of this file:** stable architecture and conventions only — things that stay true for
> months. **Current status, active work, blockers and next steps live in
> [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)**, not here. Historical reasoning ("why did we
> decide X?") lives in Claude Code's project memory.

# SMC Trading System

Automated Smart Money Concepts trading system for Indian markets (NSE) via Zerodha Kite API,
plus the public analytics site **stockswithgaurav.com**. **The system is LIVE and trades real money.**

- **Engine version:** `ENGINE_VERSION = "v4.2.1"` — [smc_mtf_engine_v4.py:430](smc_mtf_engine_v4.py#L430),
  published to Redis `engine_version` by `engine_runtime.set_engine_version()`. This constant is
  authoritative. (Older docs mention "V4.3r"; that was the **OI short-covering module's** version
  from commit `c0ed34a`, which never touched the engine file. Do not propagate it.)
- **Momentum engine** versions separately: `MOMENTUM_ENGINE_VERSION`, default `Momentum v2.1`.

## Commands

Python (repo root; a local venv exists at `.venv/`):

```bash
pytest                                 # full suite (~75 test files). NOTE: addopts includes -x, so it stops at the first failure
pytest tests/test_backtest.py -v       # one file
pytest tests/test_entry_gate.py::test_name -v   # one test
pytest -m "not slow"                   # skip slow tests
ruff check .                           # lint (line-length 120, target py311)
ruff check --fix .
mypy .                                 # type check (check_untyped_defs, ignore_missing_imports)
```

Pytest markers: `slow`, `integration` (needs API keys), `backtest`, `live` (needs live market data).
`tests/conftest.py` forces `BACKTEST_MODE=1` and `PAPER_TRADING=1`, so the suite never touches a
live broker. Coverage gate is `fail_under = 40`.

Frontend (`dashboard/frontend/`):

```bash
npm run dev          # next dev
npm run typecheck    # tsc --noEmit
npm run lint         # eslint .
npm run verify       # typecheck + build   <-- run before pushing frontend changes
npm run verify:all   # verify + lint
```

## Key Entry Points
- **Live engine**: `smc_mtf_engine_v4.py` — main loop, signal generation, trade management
- **Engine runtime**: `engine_runtime.py` — Redis snapshot publishing, heartbeat, watchdog
- **Dashboard backend**: `dashboard/backend/main.py` — FastAPI on Railway
- **Dashboard frontend**: `dashboard/frontend/` — Next.js on Vercel (stockswithgaurav.com)
- **Launcher scripts**: `CLICK ONCE to START/` — daily login, engine start, sync

## Core Directories
| Directory | Purpose |
|-----------|---------|
| `engine/` | SMC detectors, indicators, market state, liquidity engine, expiry manager, OI short-covering, FVG-tap setup |
| `services/` | The largest layer (60+ modules) — selection/decision pipeline, portfolio books, risk, regime, universe, delivery. See "Selection pipeline" below |
| `agents/` | Pre/post market, OI agent, swing alpha, long-term investment, risk sentinel, trade manager |
| `config/` | Kite auth (`kite_auth.py`), settings (`settings.py`) |
| `dashboard/backend/routes/` | All REST endpoints (28 routers) |
| `dashboard/backend/` | State bridge, WebSocket, cache, rate limiter, DB schema, `db/perf_stats.py` |
| `utils/` | State DB, helpers |
| `scripts/` | Trade logger, backtest runners, deployment scripts |
| `models/` | Dataclass models (running trades, etc.) |
| `backtest/`, `ai_learning/`, `strategies/`, `signals/`, `data/` | Backtesting, ML/pattern work, strategy defs, signal pipeline, ingestion |
| `smc_trading_engine/` | Reusable SMC library — only `strategy/entry_model.py` is imported by the live engine |

## The three books (independent — do not entangle them)
1. **Swing** — SMC pullback engine. The production-proven path.
2. **Long-term (LT)** — separate horizon, separate stats.
3. **Momentum** — independent third book (`services/momentum_*.py`), harvests leaders that never
   offer a pullback. Runs on the `web` service, gated by `MOMENTUM_PORTFOLIO_ENABLED` +
   `MOMENTUM_TRACKER_ENABLED`.

Swing and LT are **%-only** — there is no real ₹ ledger. All book metrics must go through
`dashboard/backend/db/perf_stats.py`; never sum per-trade % as a "return".

## Selection pipeline (the part that needs many files to understand)
Universe → factors → ranking → gates → ideas → portfolio:

- `services/universe_manager.py`, `universe_ohlc.py`, `universe_quality.py` — the tradable universe
  and its OHLC/quality backing (the `stock_universe` SQLite snapshot).
- `services/factor_pipeline.py`, `technical_scanner.py`, `fundamental_analysis.py`,
  `sector_strength.py`, `sector_classification.py` — inputs.
- `services/ranking_engine.py` → `services/research_levels.py` — cross-sectional scoring, then
  materializes entry/SL/targets. **User-visible levels must always be anchored to real OHLC**,
  never synthetic or hash-derived.
- `services/phase2_ranking.py` — SMC as a *ranking factor*, not a hard gate.
- **Two gates with opposite meanings of "admission"**: `services/entry_gate.py` (downstream,
  enforcing) vs `services/admission_gate.py` (upstream, shadow). Do not conflate them.
- `services/exceptionalism.py`, `decision_engine.py`, `decision_trace.py`, `reasoning_engine.py` —
  scoring plus the plain-English select/reject explanation. Never expose raw weights.
- `services/portfolio_manager.py`, `portfolio_constructor.py`, `risk_engine.py`,
  `regime_governor.py` — construction, sizing, cash gear.
- `services/outcome_labeling.py`, `research_outcome_tracker.py`, `validation_engine.py` — labels
  and forward outcomes that feed calibration.

## Portfolio Intelligence Layer (PIL)
Additive, **read-only** PMS layer above the three books — it only observes/measures/reports and
**never influences an engine**. Flag-gated (`PIL_ENABLED`). Code: `services/pil/` (accounting,
metrics, exposure, scorecard, analytics, allocation, health, reports, alerts, scheduler). API:
`dashboard/backend/routes/portfolio_intelligence.py` → `/api/intelligence/*`. Storage: isolated
`pil_*` tables (`dashboard/backend/db/pil.py`). UI: `dashboard/frontend/app/intelligence/*` (nav
gated by `NEXT_PUBLIC_PIL_ENABLED`). Reconstructs a ₹ virtual ledger from existing rows via a
configurable book-capital accounting model. Endpoints are **login-only** — holdings are not
public. Docs: `docs/pil/`.

## Public / SEO surface
`/stock/<symbol>` is server-rendered for ~2,100 NSE equities, plus a sitemap and JSON-LD. Two-tier
render: Tier 1 (the `stock_universe` snapshot) **blocks** and is the indexable floor; Tier 2 (live
analysis) is best-effort. Four rules protect the long tail — any per-page failure multiplies by
~2,100:
- Never emit `noindex` on a transient backend failure — only when the universe actively denies the
  symbol. Google honours noindex fast and reverses slowly.
- An undeployed route's 404 is indistinguishable from an unknown symbol's — return 200 plus a
  reason instead of 404.
- A Next dynamic segment needs `generateStaticParams` (even returning `[]`) or ISR is silently
  inert and every crawl re-renders.
- Never attach an `AbortSignal` to a fetch you want cached — it opts the route out of ISR. Use
  `Promise.race` for a render budget.

## Trade Data Flow
1. Engine scans → `ACTIVE_TRADES` list (in-memory) in `smc_mtf_engine_v4.py`
2. Crash recovery → persisted to `smc_engine_state.db` (SQLite key-value via `utils/state_db.py`)
3. Dashboard → `engine:snapshot` Redis key (TTL 600s), read by `dashboard/backend/state_bridge.py`
4. On trade close → appended to `trade_ledger_2026.csv` + HTTP POST to dashboard (`POST /api/journal/trade`)
5. Signals → pushed to Redis `signals:today:YYYY-MM-DD` list + Telegram. Sending is **inline in the
   engine** — `telegram_send()` ([smc_mtf_engine_v4.py:478](smc_mtf_engine_v4.py#L478)) and
   `telegram_send_signal()` (:542); delivery tracking and failure recording live in
   `services/signal_delivery.py`. (There is no `services/telegram_bot.py` — older docs claimed one.)
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
| `engine_version` | Engine version string | 24h |

## Deploy Architecture
- **Engine**: Railway worker service (`Dockerfile.engine`, `railway-engine.toml`)
- **Backend**: Railway web service (`railway-web.toml`)
- **Frontend**: Vercel — `dashboard/frontend/.env.production` has backend URL
- **Daily auth**: `CLICK ONCE to START/RUN_ENGINE_ON_RAILWAY.bat` → Kite login → Redis token
- **Production deploys from `main`**, not feature branches. Railway and Vercel both watch `main`
  and deploy in parallel — a push is not a deploy; verify it landed.
- **DNS is on Hostinger** (`ns1/ns2.dns-parking.com`), not Vercel — `vercel dns` can never manage
  this domain.

## Conventions
- IST timezone throughout. **The tradable session is 09:15–15:30**
  ([smc_mtf_engine_v4.py:156](smc_mtf_engine_v4.py#L156) gates entries to `09:15–15:16`); the
  engine *daemon* window is wider (`09:00–16:10`, some paths `09:00–16:30`). These are different
  things — don't collapse them.
- Risk measured in R-multiples (1R = 1 unit of risk)
- Signal dedup IDs: `SIG-{symbol}-{timestamp_hash}`
- All secrets in `.env` or Redis — never committed to code
- State files (`.json`, `.db`) are gitignored — regenerated at runtime
- Never put data-volume work (migrations/backfills) in a FastAPI startup handler — it 502s the web
  service via healthcheck timeout.
- Route all provider data through `services/market_data_validation.py`. NaN escapes both `is None`
  and truthiness checks, and SQLite binds NaN as NULL → `NOT NULL` crash.
- Positioning is **locked as analytics, not advice** (the site is not SEBI-registered). Copy, legal
  pages and JSON-LD must stay in that lane — no `Review`/`AggregateRating`/`FinancialService`
  schema, which would read as a rated advisory product.

## Working with flags
Most new capability ships behind an env flag that defaults to current behaviour, so merging is a
no-op until the flag is deliberately flipped. Rollback = remove the env var, usually with no
redeploy. When changing selection or risk behaviour, the calibration report is the single source
of truth — never tune on a handful of trades, and always run a disabled-rule control.

## Documentation map
Every long-lived `.md` carries a status banner near the top:

| Banner | Meaning |
|--------|---------|
| `STATUS: LIVE` | Describes something currently true. Trust it; keep it updated. |
| `STATUS: HISTORICAL` | Point-in-time record. Accurate for its date; **do not treat as current**. |
| `STATUS: REFERENCE` | Durable intent (e.g. `docs/PRODUCT_VISION.md`). Does not expire. |

- `docs/archive/` — retired deployment/audit docs
- `scripts/launchers/` — legacy batch/ps1 scripts (the canonical set is in `CLICK ONCE to START/`)
- `CLICK ONCE to START/SYSTEM_OVERVIEW.md` — a March 2026 snapshot. **This file supersedes it**
  wherever the two disagree.

## Other agent tooling in this repo
`.cursor/rules/*.mdc` holds Cursor rules that also encode real project constraints, notably: never
introduce look-ahead bias in `backtest/engine.py` (check SL before TP within the same candle);
document *why* an `engine/config.py` parameter changed, with backtest evidence; agents follow the
`agents/base.py` contract and route actions through the approval queue for human sign-off.
