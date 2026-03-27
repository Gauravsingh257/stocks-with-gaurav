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

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from dashboard.backend.rate_limit import RateLimitMiddleware

# Load .env file (OPENAI_API_KEY etc.)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from dashboard.backend.db import init_db, full_sync_from_csv, start_csv_watcher
from dashboard.backend.routes import trades_router, analytics_router, journal_router, agents_router, charts_router, chat_router, system_router, oi_intelligence_router, engine_router, research_router, kite_router, market_intelligence_router
from dashboard.backend.websocket import ws_endpoint, start_broadcast_loop, stop_broadcast_loop

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
        synced = full_sync_from_csv(force=True)
        log.info("[DB] Initial sync: %s trades loaded from trade_ledger_2026.csv", synced)
    except Exception as exc:
        log.warning("DB init/sync failed (non-fatal): %s", exc)
    try:
        start_csv_watcher(interval_seconds=30)
        log.info("[DB] CSV watcher started — auto-syncing every 30s on file change")
    except Exception as exc:
        log.warning("CSV watcher not started: %s", exc)
    start_broadcast_loop()
    try:
        from dashboard.backend.realtime import start_realtime_service
        start_realtime_service()
    except Exception as exc:
        log.debug("Realtime market data service not started: %s", exc)

    # ── Kite: log status and validate session ────────────────────────────────
    try:
        from config.kite_auth import log_kite_status, is_kite_available
        log_kite_status()
        if is_kite_available():
            from dashboard.backend.routes.charts import _get_kite
            k = _get_kite()
            if k is not None:
                k.profile()
                log.info("Kite: session validated (profile() OK)")
            else:
                log.warning("Kite: client init failed — check KITE_API_KEY and KITE_ACCESS_TOKEN")
        else:
            log.warning("Kite: credentials missing — OHLC/Charts will show offline until set")
    except Exception as exc:
        log.warning("Kite: startup validation failed — %s", exc)

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

app.add_middleware(RateLimitMiddleware)  # 60 req/min per IP — add first so it runs outermost
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
    allow_origin_regex=r"https://.*\.(vercel\.app|trycloudflare\.com|railway\.app)$",
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

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

# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_route(websocket: WebSocket):
    await ws_endpoint(websocket)

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
        "status":   "/api/system/health",
    }
