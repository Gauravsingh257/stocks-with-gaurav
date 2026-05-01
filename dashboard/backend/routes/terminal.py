"""
dashboard/backend/routes/terminal.py

Phase 2 — REST endpoints for the AI Trade Opportunity Terminal.

  GET /api/trades             → latest standardized signals (active + today's queue)
  GET /api/trades/{symbol}    → detailed setup explanation for one ticker
  GET /api/discovery-feed     → recent events (NEW_SETUP / SWEEP / TRIGGER …)
  GET /api/terminal/health    → liveness + Redis status (no auth)

Optional API-key gate via env var ``TERMINAL_API_KEY``. When unset, endpoints
are open (matches the rest of the dashboard). When set, callers must send
``X-API-Key`` header or ``?api_key=`` query param.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from dashboard.backend.terminal_events import (
    EVENTS_RING_SIZE,
    get_recent_events,
    get_signal_by_symbol,
    read_active_trades,
    read_today_signals,
)

router = APIRouter(prefix="/api", tags=["terminal"])


# ─────────────────────────────────────────────────────────────────────────
# Optional API-key auth
# ─────────────────────────────────────────────────────────────────────────

def _require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    api_key: Optional[str] = Query(default=None),
) -> None:
    expected = os.getenv("TERMINAL_API_KEY", "").strip()
    if not expected:
        return  # open mode
    provided = (x_api_key or api_key or "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


# ─────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────

@router.get("/trades", dependencies=[Depends(_require_api_key)])
def list_trades(
    limit: int = Query(default=50, ge=1, le=200),
    direction: Optional[str] = Query(default=None, pattern="^(LONG|SHORT|long|short)$"),
    setup: Optional[str] = Query(default=None, pattern="^[A-Da-d]$"),
    status: Optional[str] = Query(default=None, pattern="^(WAITING|TAPPED|TRIGGERED|TARGET_HIT|STOP_HIT)$"),
):
    """Latest standardized signals — active trades first, then today's queue.

    Each item conforms to the public schema:
      ``symbol, direction, entry, sl, target, rr, setup, confidence,
        status, analysis{...}, timestamp``.
    """
    active = read_active_trades()
    todays = read_today_signals()

    # Merge: active trades dominate per symbol, then append non-duplicate signals.
    seen: set[str] = set()
    merged: list[dict] = []
    for t in active:
        sym = t.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(t)
    for s in reversed(todays):  # newest first
        sym = s.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(s)

    if direction:
        d = direction.upper()
        merged = [m for m in merged if m.get("direction") == d]
    if setup:
        s = setup.upper()
        merged = [m for m in merged if m.get("setup") == s]
    if status:
        st = status.upper()
        merged = [m for m in merged if m.get("status") == st]

    return {
        "trades": merged[:limit],
        "count": min(len(merged), limit),
        "total": len(merged),
        "active_count": len(active),
        "signal_count": len(todays),
    }


@router.get("/trades/{symbol}", dependencies=[Depends(_require_api_key)])
def get_trade(symbol: str):
    """Detailed setup explanation for a single ticker."""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol_required")
    record = get_signal_by_symbol(sym)
    if not record:
        raise HTTPException(status_code=404, detail=f"no_signal_for_{sym}")
    return record


@router.get("/discovery-feed", dependencies=[Depends(_require_api_key)])
def get_discovery_feed(limit: int = Query(default=30, ge=1, le=EVENTS_RING_SIZE)):
    """Recent terminal events (newest first)."""
    events = get_recent_events(limit=limit)
    return {"events": events, "count": len(events)}


@router.get("/terminal/health")
def terminal_health():
    """Open health endpoint — used by frontend to detect live mode."""
    try:
        from dashboard.backend.cache import is_redis_available
        redis_up = bool(is_redis_available())
    except Exception:
        redis_up = False
    return {
        "ok": True,
        "redis": redis_up,
        "auth_required": bool(os.getenv("TERMINAL_API_KEY", "").strip()),
        "events_ring_size": EVENTS_RING_SIZE,
    }
