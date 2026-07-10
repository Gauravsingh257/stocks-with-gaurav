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

from fastapi import APIRouter, HTTPException, Depends

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


@router.get("/config", dependencies=[Depends(_guard)])
def config():
    from services.pil import config as pil_config
    return pil_config.cfg()
