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


@router.get("/config", dependencies=[Depends(_guard)])
def config():
    from services.pil import config as pil_config
    return pil_config.cfg()
