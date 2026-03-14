"""
dashboard/backend/routes/oi_intelligence.py
OI Intelligence Agent REST endpoint.

GET  /api/agents/oi-intelligence  — full OI intelligence snapshot
"""

import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("dashboard.oi_intelligence")

router = APIRouter(prefix="/api/agents", tags=["oi-intelligence"])


@router.get("/oi-intelligence")
def oi_intelligence_snapshot():
    """Return the unified OI intelligence snapshot."""
    try:
        from agents.oi_intelligence_agent import generate_snapshot
        return generate_snapshot()
    except Exception as exc:
        logger.error(f"OI Intelligence error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
