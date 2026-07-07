"""
dashboard/backend/routes/portfolio.py

Portfolio API — persistent portfolio with two buckets (SWING, LONGTERM).
Read-only + management endpoints for the research page.
"""

import logging
import threading
from fastapi import APIRouter, HTTPException, Query

from dashboard.backend.redis_endpoint_cache import finalize_endpoint, valid_portfolio_summary_payload
from pydantic import BaseModel

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])
log = logging.getLogger("dashboard.portfolio")


# ── Request models ──────────────────────────────────────────────────────────

class AddPositionRequest(BaseModel):
    symbol: str
    horizon: str  # SWING or LONGTERM
    entry_price: float
    stop_loss: float
    target_1: float | None = None
    target_2: float | None = None
    confidence_score: float = 0.0
    reasoning: str = ""
    recommendation_id: int | None = None


class ClosePositionRequest(BaseModel):
    exit_price: float
    exit_reason: str = "MANUAL"


# ── Portfolio overview ──────────────────────────────────────────────────────

@router.get("/summary")
def portfolio_summary():
    """Full portfolio with swing + longterm positions, counts, and journal stats."""
    from services.portfolio_manager import get_portfolio_summary
    return finalize_endpoint("portfolio_summary", get_portfolio_summary(), valid_portfolio_summary_payload)


@router.get("/swing")
def swing_portfolio(limit: int = Query(default=50, ge=1, le=50)):
    """Swing book: ACTIVE (live) + PENDING (armed, awaiting entry) positions.

    `count` is the LIVE (active) count — analytics/return only ever reflect live
    positions. Armed rows carry status='PENDING' so the UI shows them as
    'Awaiting Entry' with no P&L."""
    from dashboard.backend.db.portfolio import get_portfolio, get_portfolio_counts, MAX_SWING_POSITIONS
    positions = get_portfolio("SWING", include_pending=True)
    counts = get_portfolio_counts()
    return {
        "items": positions[:limit],
        "count": counts["swing"],
        "pending": counts["swing_pending"],
        "used": counts["swing_used"],
        "max": MAX_SWING_POSITIONS,
        "horizon": "SWING",
    }


@router.get("/longterm")
def longterm_portfolio(limit: int = Query(default=50, ge=1, le=50)):
    """Long-term book: ACTIVE (live) + PENDING (armed) positions. See /swing."""
    from dashboard.backend.db.portfolio import get_portfolio, get_portfolio_counts, MAX_LONGTERM_POSITIONS
    positions = get_portfolio("LONGTERM", include_pending=True)
    counts = get_portfolio_counts()
    return {
        "items": positions[:limit],
        "count": counts["longterm"],
        "pending": counts["longterm_pending"],
        "used": counts["longterm_used"],
        "max": MAX_LONGTERM_POSITIONS,
        "horizon": "LONGTERM",
    }


@router.get("/counts")
def portfolio_counts():
    """Active position counts per horizon."""
    from dashboard.backend.db.portfolio import get_portfolio_counts
    return get_portfolio_counts()


# ── Portfolio management ──────────────────────────────────────────────────

@router.post("/add")
def add_position(req: AddPositionRequest):
    """Add a stock to the portfolio.

    A manual add is an explicit user entry, so it goes LIVE immediately
    (pending=False). The automated promotion paths arm-on-tap instead.
    """
    from services.portfolio_manager import promote_to_portfolio
    try:
        pos_id = promote_to_portfolio(
            symbol=req.symbol,
            horizon=req.horizon,
            entry_price=req.entry_price,
            stop_loss=req.stop_loss,
            target_1=req.target_1,
            target_2=req.target_2,
            confidence_score=req.confidence_score,
            reasoning=req.reasoning,
            recommendation_id=req.recommendation_id,
            pending=False,
        )
        return {"ok": True, "position_id": pos_id, "symbol": req.symbol, "horizon": req.horizon}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{position_id}/close")
def close_position(position_id: int, req: ClosePositionRequest):
    """Close a portfolio position and journal it."""
    from services.portfolio_manager import close_portfolio_position
    try:
        result = close_portfolio_position(position_id, req.exit_price, req.exit_reason)
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{position_id}")
def get_position(position_id: int):
    """Get a single position by ID."""
    from dashboard.backend.db.portfolio import get_position_by_id
    pos = get_position_by_id(position_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    return pos


# ── Journal (immutable trade history) ──────────────────────────────────────

@router.get("/journal/all")
def journal_all(horizon: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    """Closed trade journal — immutable history. Never deleted."""
    from dashboard.backend.db.portfolio import get_journal
    entries = get_journal(horizon, limit)
    return {"items": entries, "count": len(entries)}


@router.get("/journal/stats")
def journal_stats(horizon: str | None = None):
    """Aggregate performance stats from closed trades."""
    from dashboard.backend.db.portfolio import get_journal_stats
    return get_journal_stats(horizon)


# ── Auto-promote (fills empty slots from recommendations) ──────────────────

@router.post("/auto-promote")
def auto_promote():
    """Fill empty portfolio slots from highest-confidence recommendations."""
    from services.idea_selector import select_and_promote
    swing_count = select_and_promote("SWING")
    longterm_count = select_and_promote("LONGTERM")
    return {"ok": True, "promoted": {"swing": swing_count, "longterm": longterm_count}}


# ── Reconcile entries (one-time arm-on-tap migration) ─────────────────────

@router.post("/reconcile-entries")
def reconcile_entries(dry_run: bool = Query(default=True), token: str = Query(default="")):
    """One-time reconciliation for arm-on-tap: reclassify ACTIVE positions whose
    planned entry was never actually traded through into PENDING (phantom
    entries), preserving genuinely-triggered ones. dry_run=True (default) returns
    the impact report WITHOUT mutating anything. Set dry_run=false to apply.

    Self-heals: re-runs the PENDING migration first (idempotent) so an apply can
    never fail because a startup rebuild lost a lock. Returns schema diagnostics
    + any per-position errors instead of a bare 500.

    Guarded by PORTFOLIO_RECONCILE_TOKEN when set."""
    import os
    required = os.getenv("PORTFOLIO_RECONCILE_TOKEN", "").strip()
    if not dry_run and required and token != required:
        raise HTTPException(status_code=403, detail="reconcile token required to apply")

    from dashboard.backend.db.portfolio import migrate_portfolio_pending, portfolio_schema_diag
    # Ensure the schema is fully migrated (retry the rebuild now, off the startup
    # hot path where a tracker connection may have held the write lock).
    try:
        migrate_portfolio_pending()
    except Exception as exc:
        log.exception("reconcile: migration retry failed")
        return {"ok": False, "stage": "migration", "error": str(exc), "schema": portfolio_schema_diag()}

    schema = portfolio_schema_diag()
    if not dry_run and not schema.get("allows_pending"):
        # Refuse to apply against a table that still forbids PENDING.
        return {"ok": False, "stage": "precheck",
                "error": "table CHECK still forbids PENDING — migration incomplete",
                "schema": schema}

    from services.portfolio_reconcile import reconcile_portfolio_entries
    try:
        report = reconcile_portfolio_entries(dry_run=dry_run)
        report["ok"] = True
        report["schema"] = schema
        return report
    except Exception as exc:
        log.exception("reconcile: apply failed")
        return {"ok": False, "stage": "apply", "error": str(exc), "schema": portfolio_schema_diag()}


# ── Seed from existing data (one-time migration) ──────────────────────────

@router.post("/seed")
def seed_from_existing():
    """One-time: seed portfolio from existing active running_trades."""
    from dashboard.backend.db.portfolio import seed_portfolio_from_recommendations
    count = seed_portfolio_from_recommendations()
    return {"ok": True, "seeded": count}


# ── Refresh prices (manual trigger) ──────────────────────────────────────

@router.post("/refresh-prices")
def refresh_prices():
    """Manually trigger a portfolio price update cycle."""
    from services.portfolio_tracker import _update_portfolio_prices
    updated = _update_portfolio_prices()
    return {"ok": True, "updated": updated}


# ── Feedback & Learning endpoints ─────────────────────────────────────────

@router.get("/feedback")
def feedback_summary():
    """Feature importance ranking + adaptive weight adjustments from historical trades."""
    from services.feedback_analyzer import get_feedback_summary
    return get_feedback_summary()


@router.get("/feedback/{horizon}")
def feedback_horizon(horizon: str):
    """Factor-level analysis for a specific horizon (SWING/LONGTERM)."""
    from services.feedback_analyzer import analyze_horizon
    horizon = horizon.upper()
    if horizon not in ("SWING", "LONGTERM"):
        raise HTTPException(400, "horizon must be SWING or LONGTERM")
    result = analyze_horizon(horizon)
    return {
        "horizon": result.horizon,
        "total_trades": result.total_trades,
        "total_wins": result.total_wins,
        "total_losses": result.total_losses,
        "win_rate": round(result.total_wins / result.total_trades, 4) if result.total_trades else 0,
        "avg_win_pct": round(result.avg_win_pct, 2),
        "avg_loss_pct": round(result.avg_loss_pct, 2),
        "feature_importance": result.feature_importance,
        "weight_adjustments": result.weight_adjustments,
        "sufficient_data": result.total_trades >= 10,
    }


@router.post("/feedback/refresh")
def feedback_refresh():
    """Force recompute feedback analysis (clear cache)."""
    from services.feedback_analyzer import invalidate_cache
    invalidate_cache()
    return {"ok": True, "message": "Feedback cache cleared — next query will recompute"}


@router.get("/regime")
def market_regime():
    """Current market regime (TRENDING_UP/DOWN/SIDEWAYS) and scoring adjustments."""
    from services.market_regime import get_regime_summary
    return get_regime_summary()


@router.get("/risk")
def portfolio_risk():
    """Portfolio risk summary — sector exposure, drawdown, alerts."""
    from services.portfolio_risk import get_risk_summary
    return get_risk_summary()
