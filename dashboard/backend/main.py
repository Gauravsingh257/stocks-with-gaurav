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

# Load .env file (OPENAI_API_KEY etc.)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from dashboard.backend.db import init_db, full_sync_from_csv, start_csv_watcher
from dashboard.backend.routes import trades_router, analytics_router, journal_router, agents_router, charts_router, chat_router, system_router, oi_intelligence_router, engine_router, research_router
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
    init_db()
    synced = full_sync_from_csv(force=True)
    log.info(f"[DB] Initial sync: {synced} trades loaded from trade_ledger_2026.csv")
    start_csv_watcher(interval_seconds=30)
    log.info("[DB] CSV watcher started — auto-syncing every 30s on file change")
    start_broadcast_loop()
    try:
        from agents.runner import start_scheduler
        start_scheduler()
        log.info("Agent scheduler started")
    except Exception as exc:
        log.warning("Agent scheduler not started: %s", exc)
    log.info("Dashboard backend ready at http://localhost:8000")
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
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

# ── CORS — allow Next.js dev server + production ─────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # Next.js dev
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "https://*.trycloudflare.com",  # Cloudflare Tunnel
        "https://stockswithgaurav.com",
        "https://www.stockswithgaurav.com",
    ],
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

# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_route(websocket: WebSocket):
    await ws_endpoint(websocket)

# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "smc-dashboard"}

@app.get("/")
def root():
    return {
        "service":  "SMC Trading Dashboard API",
        "version":  "1.0.0",
        "docs":     "http://localhost:8000/docs",
        "websocket": "ws://localhost:8000/ws",
    }
