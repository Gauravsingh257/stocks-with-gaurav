"""
dashboard/backend/main.py
FastAPI application entry-point.

Start with:
    uvicorn dashboard.backend.main:app --reload --port 8000

Or:
    python -m uvicorn dashboard.backend.main:app --port 8000
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from dashboard.backend.rate_limit import RateLimitMiddleware
from dashboard.backend.request_metrics import ApiLatencyMiddleware


class NoCacheAPIMiddleware(BaseHTTPMiddleware):
    """Prevent CDN/browser heuristic caching of API JSON responses."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/") or path.startswith("/research/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

# Load .env file (OPENAI_API_KEY etc.)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from dashboard.backend.db import init_db, full_sync_from_csv, start_csv_watcher
from dashboard.backend.routes import trades_router, analytics_router, journal_router, agents_router, charts_router, chat_router, system_router, oi_intelligence_router, engine_router, research_router, kite_router, market_intelligence_router, portfolio_router, content_router, auth_router, terminal_router
from dashboard.backend.routes.watchlist_os import router as watchlist_os_router
from dashboard.backend.routes.command_center import router as command_center_router
from dashboard.backend.routes.user_product import router as user_product_router
from dashboard.backend.routes.risk_dashboard import router as risk_dashboard_router
from dashboard.backend.routes.rejection_analysis import router as rejection_analysis_router
from dashboard.backend.routes.momentum_portfolio import router as momentum_portfolio_router
from dashboard.backend.routes.portfolio_intelligence import router as portfolio_intelligence_router
from dashboard.backend.routes.product_analytics import router as product_analytics_router
from dashboard.backend.websocket import ws_endpoint, start_broadcast_loop, stop_broadcast_loop
from dashboard.backend.terminal_ws import trades_ws_endpoint, start_terminal_ws, stop_terminal_ws

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    log.info("Dashboard backend starting…")
    try:
        init_db()
        from dashboard.backend.db.schema import migrate_stock_recommendations
        migrate_stock_recommendations()
        try:
            from dashboard.backend.routes.auth import init_auth_tables
            init_auth_tables()
        except Exception as auth_exc:
            log.info("Auth table init skipped: %s", auth_exc)
        try:
            from dashboard.backend.routes.research import init_research_leads_table
            init_research_leads_table()
        except Exception as lead_exc:
            log.info("Research leads table init skipped: %s", lead_exc)
        synced = full_sync_from_csv(force=True)
        log.info("[DB] Initial sync: %s trades loaded from trade_ledger_2026.csv", synced)
    except Exception as exc:
        log.warning("DB init/sync failed (non-fatal): %s", exc)
    try:
        from dashboard.backend.db import cleanup_duplicate_running_trades
        removed = cleanup_duplicate_running_trades()
        if removed:
            log.info("[DB] Cleaned up %d duplicate running_trade rows", removed)
    except Exception as exc:
        log.warning("Running trade cleanup failed (non-fatal): %s", exc)
    try:
        start_csv_watcher(interval_seconds=30)
        log.info("[DB] CSV watcher started — auto-syncing every 30s on file change")
    except Exception as exc:
        log.warning("CSV watcher not started: %s", exc)
    start_broadcast_loop()
    try:
        start_terminal_ws()
        log.info("/ws/trades terminal channel started")
    except Exception as exc:
        log.warning("/ws/trades start failed (non-fatal): %s", exc)
    try:
        from dashboard.backend.lifecycle import start_lifecycle_watcher
        start_lifecycle_watcher()
        log.info("lifecycle watcher started (Phase 3)")
    except Exception as exc:
        log.warning("lifecycle watcher start failed (non-fatal): %s", exc)
    try:
        from dashboard.backend.realtime import start_realtime_service
        start_realtime_service()
        log.info("Realtime tick service: start_realtime_service() called")
    except Exception as exc:
        # 2026-05-26 incident: Monday's macro strip showed Friday's prices
        # ALL DAY because the realtime KiteTicker thread died silently and
        # this swallow was DEBUG-level (invisible at production INFO).
        # Bumped to ERROR with traceback so a future regression cannot hide.
        log.error("Realtime market data service FAILED to start: %s", exc,
                  exc_info=True)

    # ── Kite: validate session in a DAEMON THREAD ────────────────────────────
    # Why a thread: k.profile() is a synchronous HTTP call into Kite with no
    # timeout. When the token is expired or Kite is slow/unreachable, it can
    # block for minutes — Railway diagnosed exactly this on 2026-05-20 (deploy
    # failed at 04:53 even with healthcheckTimeout=300). The web service must
    # come up regardless of Kite health (Kite is for trade execution from the
    # engine; the dashboard must serve without it). Validation now runs in the
    # background; lifespan returns in seconds.
    def _validate_kite_async():
        try:
            from config.kite_auth import log_kite_status, is_kite_available
            log_kite_status()
            if not is_kite_available():
                log.warning("Kite: credentials missing — OHLC/Charts will show "
                            "offline until set")
                return
            from dashboard.backend.routes.charts import _get_kite
            k = _get_kite()
            if k is None:
                log.warning("Kite: client init failed — check KITE_API_KEY "
                            "and KITE_ACCESS_TOKEN")
                return
            k.profile()
            log.info("Kite: session validated (profile() OK)")
        except Exception as exc:
            log.warning("Kite: startup validation failed — %s", exc)

    import threading as _kth
    _kth.Thread(target=_validate_kite_async, daemon=True,
                name="kite-validate").start()

    try:
        from agents.runner import start_scheduler
        start_scheduler()
        log.info("Agent scheduler started")
    except Exception as exc:
        log.warning("Agent scheduler not started: %s", exc)
    try:
        from services.trade_tracker import start_trade_tracker
        start_trade_tracker()
        log.info("Trade price tracker started")
    except Exception as exc:
        log.warning("Trade tracker not started: %s", exc)
    # ── Portfolio system ─────────────────────────────────────────────────────
    try:
        from dashboard.backend.db.portfolio import init_portfolio_db, seed_portfolio_from_recommendations
        init_portfolio_db()
        # Boot seed is SILENT (no entry alerts) — pre-existing running trades
        # aren't "new" entries. Real-time alerts fire from the tracker's
        # _sync_engine_trades() for trades taken after boot.
        seeded = seed_portfolio_from_recommendations()
        if seeded:
            log.info("[Portfolio] Seeded %d positions from existing running_trades", len(seeded))
    except Exception as exc:
        log.warning("Portfolio DB init failed (non-fatal): %s", exc)
    try:
        # Watchlist→trigger→tracking lifecycle tables + source/metric columns.
        # Runs AFTER auth (user_positions) AND portfolio (portfolio_positions)
        # so the ALTER ADD COLUMN targets both existing tables.
        from dashboard.backend.db.watchlist_monitor import init_watchlist_monitor_tables
        init_watchlist_monitor_tables()
    except Exception as wl_exc:
        log.warning("Watchlist monitor DB init failed (non-fatal): %s", wl_exc)
    try:
        from services.portfolio_tracker import start_portfolio_tracker
        start_portfolio_tracker()
        log.info("Portfolio tracker started")
    except Exception as exc:
        log.warning("Portfolio tracker not started: %s", exc)
    try:
        # Independent Momentum scheduler — idles until MOMENTUM_TRACKER_ENABLED=1
        # AND MOMENTUM_PORTFOLIO_ENABLED=1 (Swing/LT tracker unaffected).
        from dashboard.backend.db.momentum_portfolio import init_momentum_db
        init_momentum_db()
        from services.momentum_tracker import start_momentum_tracker
        start_momentum_tracker()
        log.info("Momentum tracker started (gated)")
    except Exception as exc:
        log.warning("Momentum tracker not started: %s", exc)
    try:
        # Portfolio Intelligence Layer scheduler — idles until PIL_ENABLED +
        # PIL_REPORTS_ENABLED. Read-only over the engines; writes only pil_* tables.
        from dashboard.backend.db.pil import ensure_tables as _pil_ensure
        _pil_ensure()
        from services.pil.scheduler import start_pil_scheduler
        start_pil_scheduler()
        log.info("PIL scheduler started (gated)")
    except Exception as exc:
        log.warning("PIL scheduler not started: %s", exc)
    try:
        # Telegram Morning Brief — idles until MORNING_BRIEF_ENABLED=1. Reuses the
        # deterministic daily-brief builder + the shared Telegram sender.
        from dashboard.backend.services.morning_brief import start_morning_brief_scheduler
        start_morning_brief_scheduler()
        log.info("Morning brief scheduler started (gated)")
    except Exception as exc:
        log.warning("Morning brief scheduler not started: %s", exc)
    try:
        # First-party product-analytics event store (validation-phase KPIs/funnel).
        from dashboard.backend.db.analytics import ensure_tables as _pa_ensure
        _pa_ensure()
        log.info("Product-analytics event store ready")
    except Exception as exc:
        log.warning("Product-analytics store not initialised: %s", exc)
    # ── Pre-warm discovery cache in background ─────────────────────────────
    def _prewarm_discovery():
        try:
            import asyncio
            from dashboard.backend.routes.research import get_discovery
            log.info("[PREWARM] Starting discovery cache warm-up…")
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(get_discovery(top_k=20, min_turnover_cr=1.0))
            items = result.get("returned", 0) if isinstance(result, dict) else 0
            log.info("[PREWARM] Discovery cache warm — %d items ready", items)
        except Exception as exc:
            log.warning("[PREWARM] Discovery warm-up failed (non-fatal): %s", exc)

    import threading as _th
    _th.Thread(target=_prewarm_discovery, daemon=True, name="discovery-prewarm").start()

    # ── Pre-warm OI Intelligence snapshot ─────────────────────────────
    # The OI snapshot is generated by the agent scheduler every 60s
    # DURING market hours. Outside market hours (weekends, before
    # 09:15 IST, after 15:30 IST), the scheduler skips — so the cache
    # is empty and the OI Intelligence page shows "OI Radar is offline"
    # to the first visitor for up to 60 seconds (or forever on
    # weekends). Generating one snapshot at boot, regardless of market
    # hours, primes the cache so the first request gets data
    # immediately. Best-effort; if Kite is unreachable the scheduler
    # will fill in on the next tick.
    def _prewarm_oi_snapshot():
        try:
            log.info("[PREWARM] Generating initial OI snapshot…")
            from agents.oi_intelligence_agent import generate_snapshot
            from dashboard.backend.cache import OI_SNAPSHOT_KEY, set as cache_set, MARKET_DATA_TTL
            from dashboard.backend.redis_endpoint_cache import finalize_endpoint, valid_oi_payload
            snapshot = generate_snapshot()
            if snapshot:
                # Local in-process cache (5s TTL — for repeat hits within
                # the same minute before the next scheduler tick)
                cache_set(OI_SNAPSHOT_KEY, snapshot, MARKET_DATA_TTL)
                # Redis canonical LKG (survives container restarts and
                # serves as the fallback when in-process cache misses)
                finalize_endpoint("oi_intelligence", snapshot, valid_oi_payload)
                log.info("[PREWARM] OI snapshot ready (cache + Redis LKG primed)")
            else:
                log.warning("[PREWARM] OI snapshot generation returned empty; "
                            "scheduler will retry at next market-hours tick")
        except Exception as exc:
            log.warning("[PREWARM] OI snapshot prewarm failed (non-fatal): %s", exc)

    _th.Thread(target=_prewarm_oi_snapshot, daemon=True, name="oi-prewarm").start()

    # ── Pre-warm research swing/longterm snapshots ────────────────────────
    # In snapshot-only mode the swing/longterm Redis snapshots are written by
    # the daily scan + outcome tracker. On a fresh deploy (or once the 24h LKG
    # TTL elapses with no scan) they can be ABSENT, so /api/research/{swing,
    # longterm} serve empty and the live-CMP overlay never engages until the
    # next 08:30/08:40 IST scan. Rebuild them at boot from the EXISTING ACTIVE
    # recommendations only — no scan, no recommendation regeneration, no DB or
    # outcome writes — so the research tables + overlay work immediately after
    # every deploy. Best-effort; the daily scan + outcome tracker keep them fresh.
    def _prewarm_research_snapshots():
        try:
            from dashboard.backend.routes.research import _swing_payload, _longterm_payload
            from dashboard.backend.redis_endpoint_cache import (
                finalize_endpoint, valid_research_list_payload,
            )
        except Exception as exc:
            log.warning("[PREWARM] research snapshot prewarm unavailable: %s", exc)
            return
        for horizon, builder in (("swing", _swing_payload), ("longterm", _longterm_payload)):
            try:
                payload = builder(100)
                if isinstance(payload, dict) and payload.get("items"):
                    finalize_endpoint(horizon, payload, valid_research_list_payload)
                    log.info("[PREWARM] %s snapshot primed (%d items)", horizon, len(payload["items"]))
                else:
                    log.info("[PREWARM] %s snapshot skipped — no ACTIVE recommendations", horizon)
            except Exception:
                log.exception("[PREWARM] %s snapshot rebuild failed", horizon)

    _th.Thread(target=_prewarm_research_snapshots, daemon=True, name="research-prewarm").start()

    log.info("Dashboard backend ready")
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    try:
        from dashboard.backend.realtime import stop_realtime_service
        stop_realtime_service()
    except Exception:
        pass
    stop_broadcast_loop()
    try:
        stop_terminal_ws()
    except Exception:
        pass
    try:
        from dashboard.backend.lifecycle import stop_lifecycle_watcher
        stop_lifecycle_watcher()
    except Exception:
        pass
    try:
        from agents.runner import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    log.info("Dashboard backend shutdown complete")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title        = "SMC Trading Dashboard API",
    description  = "Live engine state, analytics, journal, and WebSocket feed",
    version      = "1.0.0",
    lifespan     = lifespan,
    docs_url     = "/docs",
    redoc_url    = "/redoc",
)

# ── CORS — allow Next.js dev server + all production origins ─────────────────
# Build the origins list, also supporting ALLOWED_ORIGINS env var override
# (comma-separated list of extra allowed origins for Railway env config).
_extra_origins = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
]

# ApiLatency first = closest to routes (Starlette executes middleware in reverse registration order)
app.add_middleware(ApiLatencyMiddleware)
app.add_middleware(RateLimitMiddleware)  # 60 req/min per IP — add first so it runs outermost
app.add_middleware(NoCacheAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",             # Next.js dev
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "https://*.trycloudflare.com",       # Cloudflare Tunnel
        "https://stockswithgaurav.com",
        "https://www.stockswithgaurav.com",
        "https://*.vercel.app",              # Vercel preview + production
        "https://*.railway.app",             # Railway-to-Railway internal calls
        *_extra_origins,
    ],
    allow_origin_regex=r"https://(stockswithgaurav[\w-]*\.vercel\.app|.*\.trycloudflare\.com|.*\.railway\.app)$",
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Unhandled-error handler that PRESERVES CORS headers ──────────────────────
# Starlette emits unhandled 500s from its OUTERMOST error middleware, which sits
# OUTSIDE CORSMiddleware — so a cross-origin 500 arrives at the browser with no
# Access-Control-Allow-Origin header and is reported as the opaque "Failed to
# fetch" instead of the real status. Echo the Origin back on errors so the
# frontend surfaces the actual 500 (this fixed the chart-data "Failed to fetch").
@app.exception_handler(Exception)
async def _cors_preserving_error_handler(request: Request, exc: Exception):
    from starlette.responses import JSONResponse

    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    origin = request.headers.get("origin")
    headers = (
        {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
        if origin
        else {}
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"}, headers=headers)


# ── REST routers ─────────────────────────────────────────────────────────────
app.include_router(trades_router)
app.include_router(analytics_router)
app.include_router(journal_router)
app.include_router(agents_router)
app.include_router(charts_router)
app.include_router(chat_router)
app.include_router(system_router)
app.include_router(oi_intelligence_router)
app.include_router(engine_router)  # Phase 7: decision trace
app.include_router(research_router)
app.include_router(kite_router)
app.include_router(market_intelligence_router)
app.include_router(portfolio_router)
app.include_router(content_router)
app.include_router(auth_router)
app.include_router(watchlist_os_router)
app.include_router(command_center_router)
app.include_router(user_product_router)
app.include_router(rejection_analysis_router)      # Phase-1: discovery→rejected export (read-only)
app.include_router(momentum_portfolio_router)      # Independent Momentum Portfolio (read-only API)
app.include_router(portfolio_intelligence_router)  # Portfolio Intelligence Layer (read-only; gated by PIL_ENABLED)
app.include_router(product_analytics_router)  # First-party product analytics (validation-phase KPIs/funnel)
app.include_router(terminal_router)  # Phase 2: /api/trades, /api/discovery-feed
app.include_router(risk_dashboard_router)  # Read-only Risk Engine Dashboard (internal)
try:
    from dashboard.backend.routes.watchlist_monitor import router as watchlist_monitor_router
    app.include_router(watchlist_monitor_router)
except Exception as _wl_exc:  # never block app startup on the new router
    log.warning("watchlist_monitor router not mounted: %s", _wl_exc)

try:
    from dashboard.backend.routes.screeners import router as screeners_router
    app.include_router(screeners_router)  # Scanner suite: read-only Redis-backed /api/screeners
except Exception as _sc_exc:  # never block app startup on the new router
    log.warning("screeners router not mounted: %s", _sc_exc)

# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_route(websocket: WebSocket):
    await ws_endpoint(websocket)


@app.websocket("/ws/trades")
async def trades_websocket_route(websocket: WebSocket):
    await trades_ws_endpoint(websocket)

# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "smc-dashboard"}


@app.get("/health/kite")
def health_kite():
    """Debug: check if Kite env vars are set (no secrets exposed)."""
    api_key_set = bool(os.getenv("KITE_API_KEY", "").strip())
    token_set = bool(os.getenv("KITE_ACCESS_TOKEN", "").strip())
    try:
        from config.kite_auth import get_api_key, get_access_token, is_kite_available
        return {
            "kite_api_key_set": api_key_set or bool(get_api_key()),
            "kite_access_token_set": token_set or bool(get_access_token()),
            "kite_ready": is_kite_available(),
            "hint": "Set KITE_API_KEY and KITE_ACCESS_TOKEN in Railway Variables, then Redeploy" if not token_set else None,
        }
    except Exception as e:
        return {"error": str(e), "kite_ready": False}


@app.get("/")
def root():
    return {
        "service":  "SMC Trading Dashboard API",
        "version":  "1.0.0",
        "docs":     "/docs",
        "health":   "/health",
        "health_full": "/api/system/health/full",
        "debug_cache": "/api/system/debug/cache",
        "status":   "/api/system/health",
    }
