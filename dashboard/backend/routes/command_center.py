"""Phase 6 — Command Center + daily brief APIs."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from dashboard.backend.routes.auth import get_optional_user
from dashboard.backend.global_state_version import attach_snapshot_meta
from dashboard.backend.services.command_center_service import (
    build_command_center_payload,
    build_daily_market_brief,
)
from dashboard.backend.state_bridge import get_engine_snapshot

router = APIRouter(tags=["command-center"])


@router.get("/api/command-center")
def api_command_center(user: dict | None = Depends(get_optional_user)):
    """Snapshot-first trader OS dashboard — richer when authenticated (watchlist Redis)."""
    uid = int(user["sub"]) if user else None
    role = user.get("role") if user else None
    payload = build_command_center_payload(user_id=uid, role=role)
    ref = None
    try:
        es = get_engine_snapshot()
        if isinstance(es, dict):
            ref = es.get("snapshot_time")
    except Exception:
        pass
    payload["_snapshot_written_at"] = time.time()
    attach_snapshot_meta(payload, origin="command_center", reference_iso_for_age=ref)
    return payload


@router.get("/api/market/daily-brief")
def api_daily_brief(user: dict | None = Depends(get_optional_user)):
    """Deterministic market intelligence digest — no LLM. Narrative beats when signed in."""
    uid = int(user["sub"]) if user else None
    payload = build_daily_market_brief(user_id=uid)
    ref = None
    try:
        es = get_engine_snapshot()
        if isinstance(es, dict):
            ref = es.get("snapshot_time")
    except Exception:
        pass
    attach_snapshot_meta(payload, origin="daily_brief", reference_iso_for_age=ref)
    return payload
