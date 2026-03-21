"""
dashboard/backend/routes/research.py
Research Center APIs for swing ideas, long-term ideas, and running trades.
"""

import logging
from fastapi import APIRouter, HTTPException, Query

from dashboard.backend.db import get_ranking_runs, get_stock_recommendations, list_running_trades
from services.universe_manager import load_nse_universe

router = APIRouter(tags=["research"])
log = logging.getLogger("dashboard.research")


def _swing_payload(limit: int) -> dict:
    rows = get_stock_recommendations("SWING", limit=limit)
    items: list[dict] = []
    for row in rows:
        targets = row.get("targets", [])
        target_1 = float(targets[0]) if len(targets) > 0 else None
        target_2 = float(targets[1]) if len(targets) > 1 else None
        entry = float(row["entry_price"])
        stop = float(row["stop_loss"]) if row.get("stop_loss") is not None else entry * 0.95
        risk = abs(entry - stop)
        reward = abs((target_2 or target_1 or entry) - entry)
        rr = reward / max(risk, 0.01)
        items.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "setup": row.get("setup") or "SWING",
                "entry_price": entry,
                "stop_loss": stop,
                "target_1": target_1,
                "target_2": target_2,
                "risk_reward": round(rr, 2),
                "confidence_score": float(row.get("confidence_score", 0)),
                "expected_holding_period": row.get("expected_holding_period") or "1-8 weeks",
                "technical_signals": row.get("technical_signals", {}),
                "fundamental_signals": row.get("fundamental_signals", {}),
                "sentiment_signals": row.get("sentiment_signals", {}),
                "technical_factors": row.get("technical_factors", {}),
                "fundamental_factors": row.get("fundamental_factors", {}),
                "sentiment_factors": row.get("sentiment_factors", {}),
                "reasoning_summary": row.get("reasoning", ""),
                "created_at": row.get("created_at"),
            }
        )
    return {"items": items, "count": len(items)}


def _longterm_payload(limit: int) -> dict:
    rows = get_stock_recommendations("LONGTERM", limit=limit)
    items: list[dict] = []
    for row in rows:
        targets = row.get("targets", [])
        items.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "long_term_thesis": row.get("reasoning", ""),
                "fair_value_estimate": row.get("fair_value_estimate"),
                "entry_zone": row.get("entry_zone") or [],
                "long_term_target": row.get("long_term_target") or (targets[0] if targets else None),
                "risk_factors": row.get("risk_factors") or [],
                "time_horizon": row.get("expected_holding_period") or "6-24 months",
                "confidence_score": float(row.get("confidence_score", 0)),
                "technical_signals": row.get("technical_signals", {}),
                "fundamental_signals": row.get("fundamental_signals", {}),
                "sentiment_signals": row.get("sentiment_signals", {}),
                "fundamental_factors": row.get("fundamental_factors", {}),
                "technical_factors": row.get("technical_factors", {}),
                "sentiment_factors": row.get("sentiment_factors", {}),
                "created_at": row.get("created_at"),
            }
        )
    return {"items": items, "count": len(items)}


def _running_trades_payload(limit: int) -> dict:
    rows = list_running_trades(limit=limit, active_only=True)
    items: list[dict] = []
    for row in rows:
        targets = [float(t) for t in row.get("targets", [])]
        entry = float(row["entry_price"])
        current = float(row["current_price"])
        stop = float(row["stop_loss"])
        max_target = max(targets) if targets else entry
        range_size = max(max_target - entry, 0.01)
        progress = max(0.0, min(1.0, (current - entry) / range_size))
        if current <= stop * 1.01:
            color = "red"
        elif progress >= 0.75:
            color = "green"
        else:
            color = "yellow"
        items.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "entry_price": entry,
                "current_price": current,
                "stop_loss": stop,
                "targets": targets,
                "profit_loss": float(row.get("profit_loss", 0)),
                "drawdown": float(row.get("drawdown", 0)),
                "distance_to_target": row.get("distance_to_target"),
                "distance_to_stop_loss": row.get("distance_to_stop_loss"),
                "status": row.get("status", "RUNNING"),
                "progress": round(progress, 4),
                "progress_color": color,
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    return {"items": items, "count": len(items)}


@router.get("/api/research/swing")
@router.get("/research/swing")
def get_swing_research(limit: int = Query(12, ge=1, le=100)):
    return _swing_payload(limit)


@router.get("/api/research/longterm")
@router.get("/research/longterm")
def get_longterm_research(limit: int = Query(12, ge=1, le=100)):
    return _longterm_payload(limit)


@router.get("/api/research/running-trades")
@router.get("/research/running-trades")
def get_running_trades(limit: int = Query(40, ge=1, le=200)):
    return _running_trades_payload(limit)


@router.get("/api/research/coverage")
@router.get("/research/coverage")
def get_research_coverage(target_universe: int = Query(1800, ge=100, le=5000)):
    universe = load_nse_universe(target_size=target_universe)
    swing_latest = get_ranking_runs(horizon="SWING", limit=1)
    long_latest = get_ranking_runs(horizon="LONGTERM", limit=1)

    def _shape(row: dict | None) -> dict | None:
        if not row:
            return None
        scanned = int(row.get("universe_scanned", 0))
        requested = int(row.get("universe_requested", target_universe))
        coverage_pct = round((scanned / requested) * 100, 2) if requested > 0 else 0.0
        return {
            "run_time": row.get("run_time"),
            "universe_requested": requested,
            "universe_scanned": scanned,
            "quality_passed": int(row.get("quality_passed", 0)),
            "ranked_candidates": int(row.get("ranked_candidates", 0)),
            "selected_count": int(row.get("selected_count", 0)),
            "coverage_pct": coverage_pct,
        }

    return {
        "target_universe": target_universe,
        "available_universe": universe.actual_size,
        "sources": universe.sources,
        "latest": {
            "SWING": _shape(swing_latest[0] if swing_latest else None),
            "LONGTERM": _shape(long_latest[0] if long_latest else None),
        },
    }


@router.get("/api/research/ranking-runs")
@router.get("/research/ranking-runs")
def get_research_ranking_runs(
    horizon: str | None = Query(None, description="SWING or LONGTERM"),
    limit: int = Query(20, ge=1, le=200),
):
    h = horizon.upper() if horizon else None
    if h and h not in {"SWING", "LONGTERM"}:
        raise HTTPException(status_code=400, detail="horizon must be SWING or LONGTERM")
    return {"items": get_ranking_runs(horizon=h, limit=limit)}


def _run_scan(agent_name: str, label: str) -> dict:
    """Run a research scan agent. Returns dict with ok/status/result or error."""
    try:
        from agents.runner import run_agent_now
        result = run_agent_now(agent_name)
    except Exception as exc:
        log.exception("Research run_agent_now failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if "error" in result:
        return {
            "ok": False,
            "scan": label,
            "agent": agent_name,
            "status": "error",
            "message": result["error"],
            "result": result,
        }

    return {
        "ok": True,
        "scan": label,
        "agent": agent_name,
        "status": result.get("status", "OK"),
        "summary": result.get("summary", ""),
        "result": result,
    }


@router.post("/api/research/run/swing")
@router.post("/research/run/swing")
def run_swing_scan():
    """Trigger swing scan. Always returns JSON; never 502."""
    try:
        return _run_scan("SwingTradeAlphaAgent", "swing")
    except HTTPException:
        raise
    except Exception as e:
        log.exception("run_swing_scan failed: %s", e)
        return {
            "ok": False,
            "scan": "swing",
            "agent": "SwingTradeAlphaAgent",
            "status": "error",
            "message": str(e),
            "result": {},
        }


@router.post("/api/research/run/longterm")
@router.post("/research/run/longterm")
def run_longterm_scan():
    """Trigger long-term scan. Always returns JSON; never 502."""
    try:
        return _run_scan("LongTermInvestmentAgent", "longterm")
    except HTTPException:
        raise
    except Exception as e:
        log.exception("run_longterm_scan failed: %s", e)
        return {
            "ok": False,
            "scan": "longterm",
            "agent": "LongTermInvestmentAgent",
            "status": "error",
            "message": str(e),
            "result": {},
        }
