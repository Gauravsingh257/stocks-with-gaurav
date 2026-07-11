"""
dashboard/backend/routes/product_analytics.py
==============================================
First-party PRODUCT analytics for the Sprint-1 validation window. Distinct from
routes/analytics.py (that one is the PUBLIC trade track-record on /api/analytics).
This one measures product usage (events, KPIs, activation funnel).

  POST /api/product-analytics/event    public ingest (anon or authed) — best effort
  GET  /api/product-analytics/health    ADMIN — the 16 Product-Health KPIs
  GET  /api/product-analytics/funnel    ADMIN — the activation funnel

Ingest is intentionally open (anonymous visitors generate events); reporting is
admin-only. Admin = JWT role ADMIN or a known owner email (mirrors the frontend
TopBar gate).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dashboard.backend.routes.auth import get_current_user, get_optional_user
from dashboard.backend.db import analytics as store

router = APIRouter(prefix="/api/product-analytics", tags=["product-analytics"])
log = logging.getLogger("dashboard.product_analytics")

# Owner emails always treated as admin (belt-and-suspenders with the role check).
_ADMIN_EMAILS = {"hellogaurav2577@gmail.com"}


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    role = str(user.get("role") or "").upper()
    email = str(user.get("email") or "").strip().lower()
    if role == "ADMIN" or email in _ADMIN_EMAILS:
        return user
    raise HTTPException(status_code=403, detail="Admin only")


class EventIn(BaseModel):
    event: str
    anon_id: Optional[str] = None
    session_id: Optional[str] = None
    path: Optional[str] = None
    device: Optional[str] = None
    props: Optional[Dict[str, Any]] = None


@router.post("/event")
def ingest_event(body: EventIn, user: Optional[dict] = Depends(get_optional_user)):
    """Store one product event. Anonymous allowed; user_id attached when a valid
    bearer token is present. Never fails the client — analytics must not break UX."""
    try:
        uid = None
        if user is not None:
            try:
                uid = int(user.get("sub"))
            except (TypeError, ValueError):
                uid = None
        store.insert_event(
            event=body.event,
            anon_id=body.anon_id,
            user_id=uid,
            session_id=body.session_id,
            path=body.path,
            device=body.device,
            props=body.props,
        )
    except Exception as exc:  # pragma: no cover - best effort
        log.debug("product-analytics ingest error: %s", exc)
    return {"ok": True}


@router.get("/health")
def health(days: int = Query(7, ge=1, le=90), _admin: dict = Depends(require_admin)):
    return store.health_kpis(days)


@router.get("/funnel")
def funnel(days: int = Query(7, ge=1, le=90), _admin: dict = Depends(require_admin)):
    return store.activation_funnel(days)
