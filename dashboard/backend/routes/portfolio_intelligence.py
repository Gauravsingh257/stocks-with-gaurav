"""
dashboard/backend/routes/portfolio_intelligence.py
==================================================
Read-only API for the Portfolio Intelligence Layer (PIL) — Part 11.

Namespace: /api/intelligence/*. Every endpoint is gated by PIL_ENABLED via the
`_guard` dependency, so with the flag unset the whole surface returns 404 and the
layer is invisible. PIL never writes to an engine table; the only mutating
endpoints here manage PIL's own allocation targets / config (added in later
commits).

This module is intentionally thin — all computation lives in services/pil/*.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/intelligence", tags=["portfolio-intelligence"])
log = logging.getLogger("dashboard.pil")


def _guard() -> None:
    """404 the entire PIL surface when the master flag is off."""
    from services.pil import config as pil_config
    if not pil_config.enabled():
        raise HTTPException(status_code=404, detail="Portfolio Intelligence Layer is disabled")


@router.get("/status")
def status():
    """Lightweight liveness + config snapshot (does NOT require the guard so the
    frontend can detect whether PIL is enabled)."""
    from services.pil import config as pil_config
    return {"enabled": pil_config.enabled(), "config": pil_config.cfg()}


@router.get("/combined", dependencies=[Depends(_guard)])
def combined():
    """Full ledger + Part-1 metrics for Swing / Long-Term / Momentum / Combined."""
    from services.pil import accounting, metrics
    books = accounting.reconstruct_all()
    met = metrics.metrics_all(books)
    return {
        "books": {
            b: {
                "ledger": {k: v for k, v in ledger.items() if k != "equity_curve"},
                "equity_curve": ledger["equity_curve"],
                "metrics": met[b],
            }
            for b, ledger in books.items()
        },
        "order": ["SWING", "LONGTERM", "MOMENTUM", "COMBINED"],
    }


@router.get("/comparison", dependencies=[Depends(_guard)])
def comparison():
    """Side-by-side Part-1 metric blocks only (no positions/curves) — the compact
    payload the Master Dashboard comparison grid consumes."""
    from services.pil import accounting, metrics
    books = accounting.reconstruct_all()
    met = metrics.metrics_all(books)
    return {"metrics": met, "order": ["SWING", "LONGTERM", "MOMENTUM", "COMBINED"]}


@router.get("/exposure", dependencies=[Depends(_guard)])
def exposure():
    """Cross-portfolio exposure, concentration, correlation + threshold warnings."""
    from services.pil import accounting, exposure as exp
    return exp.compute(accounting.reconstruct_all())


@router.get("/risk", dependencies=[Depends(_guard)])
def risk():
    """Compact risk view: concentration, beta, diversification, correlation,
    per-book risk scores + active warnings."""
    from services.pil import accounting, exposure as exp, metrics
    books = accounting.reconstruct_all()
    e = exp.compute(books)
    met = metrics.metrics_all(books)
    return {
        "portfolio_beta": e["portfolio_beta"],
        "hhi": e["hhi"],
        "effective_holdings": e["effective_holdings"],
        "diversification_score": e["diversification_score"],
        "top10_pct": e["top10_pct"],
        "cash_pct": e["cash_pct"],
        "liquidity_coverage_pct": e["liquidity_coverage_pct"],
        "correlation": e["correlation"],
        "risk_scores": {b: met[b]["risk_score"] for b in met},
        "max_drawdowns": {b: met[b]["max_drawdown_pct"] for b in met},
        "warnings": e["warnings"],
    }


@router.get("/scorecards", dependencies=[Depends(_guard)])
def scorecards(scope: str = Query("daily", pattern="^(daily|monthly)$"),
               period: str | None = None, refresh: bool = False):
    """Engine scorecards for all books. Returns the stored card when present
    (unless refresh=1), else generates + persists on demand."""
    from services.pil import scorecard
    from dashboard.backend.db import pil as pildb
    if not refresh:
        # try cache first (fast path for the dashboard)
        cached = {}
        for b in ("SWING", "LONGTERM", "MOMENTUM", "COMBINED"):
            card = pildb.get_scorecard(scope, b, period) if period else None
            if card:
                cached[b] = card
        if len(cached) == 4:
            return {"scope": scope, "period": period, "cards": cached, "cached": True}
    cards = scorecard.generate_and_store(scope, period)
    return {"scope": scope, "period": cards.get("COMBINED", {}).get("period", period),
            "cards": cards, "cached": False}


@router.get("/analytics", dependencies=[Depends(_guard)])
def analytics():
    """Combined-portfolio analytics: contribution, correlation, diversification
    benefit, optimal allocation, engine leaderboard."""
    from services.pil import accounting, analytics as an
    return an.compute(accounting.reconstruct_all())


class WhatIfRequest(BaseModel):
    weights: dict[str, float]


@router.post("/analytics/what-if", dependencies=[Depends(_guard)])
def analytics_what_if(req: WhatIfRequest):
    """Recompute combined risk/return under arbitrary engine weights."""
    from services.pil import accounting, analytics as an
    return an.what_if(accounting.reconstruct_all(), req.weights)


@router.get("/analytics/playback", dependencies=[Depends(_guard)])
def analytics_playback(start: str | None = None, end: str | None = None):
    from services.pil import accounting, analytics as an
    return an.playback(accounting.reconstruct_all(), start, end)


@router.get("/allocation", dependencies=[Depends(_guard)])
def allocation():
    """Current vs target capital allocation, drift, rebalancing needs."""
    from services.pil import accounting, allocation as al
    return al.compute(accounting.reconstruct_all())


class AllocationTargets(BaseModel):
    weights: dict[str, float]


@router.post("/allocation/targets", dependencies=[Depends(_guard)])
def set_allocation_targets(req: AllocationTargets):
    """Persist target allocation weights (normalised). PIL-only config write."""
    from services.pil import accounting, allocation as al
    try:
        al.set_targets(req.weights)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return al.compute(accounting.reconstruct_all())


@router.get("/health", dependencies=[Depends(_guard)])
def health():
    """Per-book + combined health scores → GREEN/YELLOW/RED (Part 6)."""
    from services.pil import accounting, health as h
    return h.compute(accounting.reconstruct_all())


@router.get("/reports", dependencies=[Depends(_guard)])
def reports(kind: str = Query("daily", pattern="^(daily|monthly)$"),
            period: str | None = None):
    """Return a stored report (latest of `kind` if no period), or generate it."""
    from services.pil import reports as rep
    from dashboard.backend.db import pil as pildb
    stored = pildb.get_report(kind, period)
    if stored:
        return stored
    report = rep.build_monthly(period) if kind == "monthly" else rep.build_daily(period)
    return {"kind": kind, "period": report["period"], "payload": report,
            "html": report.get("html"), "generated": True}


@router.get("/reports/list", dependencies=[Depends(_guard)])
def reports_list(kind: str = Query("daily", pattern="^(daily|monthly)$"), limit: int = 24):
    from dashboard.backend.db import pil as pildb
    return {"kind": kind, "reports": pildb.list_reports(kind, limit)}


@router.post("/reports/generate", dependencies=[Depends(_guard)])
def reports_generate(kind: str = Query("daily", pattern="^(daily|monthly)$"),
                     period: str | None = None):
    """On-demand generation + persistence (also sends Telegram if enabled)."""
    from services.pil import reports as rep
    report = rep.generate_and_store(kind, period, notify=True)
    return {"kind": kind, "period": report["period"], "html": report.get("html"),
            "payload": {k: v for k, v in report.items() if k != "html"}}


@router.get("/config", dependencies=[Depends(_guard)])
def config():
    from services.pil import config as pil_config
    return pil_config.cfg()
