"""
dashboard/backend/routes/oi_intelligence.py
OI Intelligence Agent REST endpoint.
API reads from cache (5s TTL); worker or first request populates it.
GET  /api/agents/oi-intelligence  — full OI intelligence snapshot
"""

import logging
from fastapi import APIRouter, HTTPException

from dashboard.backend.cache import OI_SNAPSHOT_KEY, get as cache_get, set as cache_set, MARKET_DATA_TTL
from dashboard.backend.redis_endpoint_cache import finalize_endpoint, valid_oi_payload

logger = logging.getLogger("dashboard.oi_intelligence")

router = APIRouter(prefix="/api/agents", tags=["oi-intelligence"])


@router.get("/oi-intelligence")
def oi_intelligence_snapshot():
    """Return the unified OI intelligence snapshot (from cache or generate)."""
    cached = cache_get(OI_SNAPSHOT_KEY)
    if cached is not None:
        return finalize_endpoint("oi_intelligence", cached, valid_oi_payload)
    try:
        from agents.oi_intelligence_agent import generate_snapshot
        snapshot = generate_snapshot()
        cache_set(OI_SNAPSHOT_KEY, snapshot, MARKET_DATA_TTL)
        return finalize_endpoint("oi_intelligence", snapshot, valid_oi_payload)
    except Exception as exc:
        logger.error("OI Intelligence error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
