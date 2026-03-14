"""
dashboard/backend/routes/trades.py
REST endpoints for active trades, daily PnL, and zone state.
"""

from fastapi import APIRouter
from dashboard.backend.state_bridge import get_engine_snapshot

router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/snapshot")
def get_snapshot():
    """Full engine snapshot — consumed by frontend on initial load."""
    return get_engine_snapshot()


@router.get("/active-trades")
def get_active_trades():
    """List of currently running trades."""
    snap = get_engine_snapshot()
    return {
        "trades": snap["active_trades"],
        "count":  snap["active_trade_count"],
        "timestamp": snap["snapshot_time"],
    }


@router.get("/daily-pnl")
def get_daily_pnl():
    """Today's running PnL metrics."""
    snap = get_engine_snapshot()
    return {
        "daily_pnl_r":        snap["daily_pnl_r"],
        "consecutive_losses": snap["consecutive_losses"],
        "signals_today":      snap["signals_today"],
        "traded_today":       snap["traded_today"],
        "circuit_breaker":    snap["circuit_breaker_active"],
        "market_regime":      snap["market_regime"],
        "timestamp":          snap["snapshot_time"],
    }


@router.get("/zone-state")
def get_zone_state():
    """Active SMC zones (LONG/SHORT per symbol)."""
    snap = get_engine_snapshot()
    return {
        "zones":     snap["zone_state"],
        "timestamp": snap["snapshot_time"],
    }


@router.get("/engine-status")
def get_engine_status():
    """Engine health and config info."""
    snap = get_engine_snapshot()
    return {
        "engine_live":       snap["engine_live"],
        "engine_mode":       snap["engine_mode"],
        "active_strategies": snap["active_strategies"],
        "index_only":        snap["index_only"],
        "paper_mode":        snap["paper_mode"],
        "max_daily_signals": snap["max_daily_signals"],
        "max_daily_loss_r":  snap["max_daily_loss_r"],
        "timestamp":         snap["snapshot_time"],
    }
