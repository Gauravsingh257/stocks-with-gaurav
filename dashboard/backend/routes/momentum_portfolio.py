"""
dashboard/backend/routes/momentum_portfolio.py
===============================================
Read-only API for the INDEPENDENT Momentum Portfolio. Separate namespace
(/api/momentum-portfolio) so it never collides with the Swing/LT portfolio API.
Mirrors that API's shape so the UI can reuse the same components.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/momentum-portfolio", tags=["momentum-portfolio"])
log = logging.getLogger("dashboard.momentum_portfolio")


@router.get("/summary")
def summary():
    from services.momentum_portfolio_manager import get_summary
    return get_summary()


@router.get("/holdings")
def holdings():
    from dashboard.backend.db.momentum_portfolio import get_portfolio, get_counts
    active = [p for p in get_portfolio(include_pending=False) if p["status"] == "ACTIVE"]
    return {"items": active, "count": len(active), **get_counts()}


@router.get("/pending")
def pending():
    from dashboard.backend.db.momentum_portfolio import get_portfolio
    rows = [p for p in get_portfolio(include_pending=True) if p["status"] == "PENDING"]
    return {"items": rows, "count": len(rows)}


@router.get("/journal")
def journal(limit: int = Query(default=100, ge=1, le=500)):
    from dashboard.backend.db.momentum_portfolio import get_journal
    items = get_journal(limit)
    return {"items": items, "count": len(items)}


@router.get("/journal/stats")
def journal_stats():
    from dashboard.backend.db.momentum_portfolio import get_journal_stats
    return get_journal_stats()


@router.get("/config")
def config():
    from services.momentum_engine.config import cfg
    c = cfg()
    keys = [k for k in c if k.startswith("MOMENTUM_") or k.startswith("MOM_")]
    return {"config": {k: c[k] for k in keys}}
