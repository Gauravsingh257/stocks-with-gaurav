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


@router.get("/analytics")
def analytics():
    """Deep analytics: realised + open performance, Sharpe/Sortino, monthly, and
    sector/regime/entry-model/risk-model attribution."""
    from services.momentum_analytics import analytics as compute
    return compute()


@router.get("/report")
def report(date: str | None = Query(default=None)):
    """The latest daily Momentum Portfolio Report (from Redis)."""
    from datetime import date as _date
    day = date or _date.today().isoformat()
    try:
        from dashboard.backend.cache import _get_redis
        import json
        r = _get_redis()
        raw = r.get(f"momentum_portfolio:report:{day}") if r else None
        return {"date": day, "report": (json.loads(raw) if raw else None)}
    except Exception as exc:
        log.debug("report fetch failed: %s", exc)
        return {"date": day, "report": None}


@router.get("/funnel")
def funnel(days: int = Query(default=3, ge=1, le=3)):
    """Why the book picked what it picked — the per-stage selection funnel from
    the most recent daily reports (regime → discovery pool → data → eligibility →
    entry model → armed), with the top rejection gates. This is what makes an
    empty Momentum book self-explaining instead of silent."""
    from datetime import date as _date, timedelta
    import json
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
    except Exception as exc:
        log.debug("funnel redis unavailable: %s", exc)
        r = None
    out = []
    for i in range(days):
        day = (_date.today() - timedelta(days=i)).isoformat()
        raw = r.get(f"momentum_portfolio:report:{day}") if r is not None else None
        if not raw:
            continue
        rep = json.loads(raw)
        out.append({"date": day, "regime": rep.get("regime"),
                    "funnel": rep.get("funnel") or {},
                    "armed": rep.get("armed") or [],
                    "counts": rep.get("counts") or {}})
    return {"days": out, "count": len(out)}


@router.post("/run")
def run_cycle():
    """Manually trigger one orchestration cycle (respects all flags — returns
    {status: disabled} when the portfolio is off). Same entrypoint the scheduler
    uses."""
    from services.momentum_tracker import run_once
    return run_once()
