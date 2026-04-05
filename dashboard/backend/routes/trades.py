"""
dashboard/backend/routes/trades.py
REST endpoints for active trades, daily PnL, zone state, and trade graphs.
"""

from fastapi import APIRouter, HTTPException
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


# ---------------------------------------------------------------------------
# Trade Graph endpoints
# ---------------------------------------------------------------------------

@router.get("/trades/{graph_id}/graph")
def get_trade_graph(graph_id: str):
    """Full trade reasoning graph for a single trade."""
    from services.trade_graph_hooks import get_trade_graph as _get_graph
    data = _get_graph(graph_id)
    if not data:
        raise HTTPException(status_code=404, detail="Trade graph not found")
    return data


@router.get("/trades/{graph_id}/graph/website")
def get_trade_graph_website(graph_id: str):
    """D3.js / React Flow compatible graph data for website visualization."""
    from services.trade_graph_hooks import get_website_graph
    data = get_website_graph(graph_id)
    if not data:
        raise HTTPException(status_code=404, detail="Trade graph not found")
    return data


@router.get("/trades/{graph_id}/failure-analysis")
def get_failure_analysis(graph_id: str):
    """Failure path analysis for losing trades — debug why it lost."""
    from services.trade_graph_hooks import get_failure_analysis as _get_analysis
    data = _get_analysis(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail="No failure analysis (graph not found or trade was not a loss)")
    return {"graph_id": graph_id, "failure_path": data}


@router.get("/trades/{graph_id}/content/{platform}")
def get_content_prompt(graph_id: str, platform: str = "instagram"):
    """Generate content prompt from trade graph for a given platform."""
    if platform not in ("instagram", "twitter", "linkedin"):
        raise HTTPException(status_code=400, detail="Platform must be instagram, twitter, or linkedin")
    from services.trade_graph_hooks import generate_content_prompt
    prompt = generate_content_prompt(graph_id, platform=platform)
    if not prompt:
        raise HTTPException(status_code=404, detail="Trade graph not found")
    return {"graph_id": graph_id, "platform": platform, "prompt": prompt}


@router.get("/trades/{graph_id}/video-prompt")
def get_video_prompt(graph_id: str):
    """Generate video script prompt from trade graph."""
    from services.trade_graph_hooks import generate_video_prompt
    prompt = generate_video_prompt(graph_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Trade graph not found")
    return {"graph_id": graph_id, "prompt": prompt}


@router.get("/trades/{graph_id}/viral-content")
def get_viral_content(graph_id: str):
    """Viral narrative amplifier — scroll-stopping Instagram slides."""
    from services.trade_graph_hooks import get_viral_content as _get_viral
    data = _get_viral(graph_id)
    if not data:
        raise HTTPException(status_code=404, detail="Trade graph not found")
    return {"graph_id": graph_id, **data}


@router.get("/trades/{graph_id}/video-scenes")
def get_video_scenes(graph_id: str):
    """Emotion-synced video scene graph with SSML voiceover markup."""
    from services.trade_graph_hooks import get_video_scenes as _get_scenes
    data = _get_scenes(graph_id)
    if not data:
        raise HTTPException(status_code=404, detail="Trade graph not found")
    return {"graph_id": graph_id, "scenes": data, "total_duration": sum(s.get("duration_sec", 0) for s in data)}


@router.get("/trades/failure-patterns")
def get_failure_patterns():
    """Aggregate failure pattern analysis across all trade graphs."""
    from services.trade_graph_hooks import get_failure_patterns as _get_patterns
    data = _get_patterns()
    if not data:
        raise HTTPException(status_code=404, detail="No trade graphs found")
    return data


@router.get("/trades/{graph_id}/telegram-narrative")
def get_telegram_narrative(graph_id: str):
    """Rich narrative-driven Telegram signal text."""
    from services.trade_graph_hooks import get_telegram_narrative as _get_tg
    text = _get_tg(graph_id)
    if not text:
        raise HTTPException(status_code=404, detail="Trade graph not found")
    return {"graph_id": graph_id, "telegram_text": text}
