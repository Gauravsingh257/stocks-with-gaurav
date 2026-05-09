"""
dashboard/backend/routes/oi_intelligence.py
OI Intelligence Agent REST endpoint.

Snapshot-first: Redis cache (5s TTL) → canonical endpoint LKG → optional on-demand
generation only when OI_SNAPSHOT_REQUEST_GENERATION=true (default false).
"""

import logging
import os

from fastapi import APIRouter, HTTPException

from dashboard.backend.cache import OI_SNAPSHOT_KEY, get as cache_get, set as cache_set, MARKET_DATA_TTL
from dashboard.backend.redis_endpoint_cache import finalize_endpoint, serve_cached_endpoint, valid_oi_payload
from dashboard.backend.services.oi_interpretation_engine import enrich_oi_snapshot_with_interpretation

logger = logging.getLogger("dashboard.oi_intelligence")

router = APIRouter(prefix="/api/agents", tags=["oi-intelligence"])

_ALLOW_REQUEST_GENERATION = os.getenv("OI_SNAPSHOT_REQUEST_GENERATION", "false").lower() in ("1", "true", "yes")


@router.get("/oi-intelligence")
def oi_intelligence_snapshot():
    """Return OI snapshot from cache / Redis LKG; never blocks on heavy generation unless opted in."""
    cached = cache_get(OI_SNAPSHOT_KEY)
    if cached is not None:
        payload = enrich_oi_snapshot_with_interpretation(dict(cached))
        return finalize_endpoint("oi_intelligence", payload, valid_oi_payload)

    fb = serve_cached_endpoint("oi_intelligence")
    if fb:
        merged = dict(fb)
        merged.setdefault("snapshot_stale", True)
        merged.setdefault("snapshot_source", merged.get("snapshot_source") or "redis_canonical")
        merged["oi_snapshot_wait_for_worker"] = True
        return finalize_endpoint("oi_intelligence", merged, valid_oi_payload)

    if not _ALLOW_REQUEST_GENERATION:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "oi_snapshot_not_ready",
                "retry_after_seconds": 30,
                "hint": "Worker fills oi_snapshot on a timer. Set OI_SNAPSHOT_REQUEST_GENERATION=true only for debugging.",
            },
        )

    try:
        from agents.oi_intelligence_agent import generate_snapshot

        snapshot = generate_snapshot()
        cache_set(OI_SNAPSHOT_KEY, snapshot, MARKET_DATA_TTL)
        payload = enrich_oi_snapshot_with_interpretation(snapshot)
        return finalize_endpoint("oi_intelligence", payload, valid_oi_payload)
    except Exception as exc:
        logger.error("OI Intelligence error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
