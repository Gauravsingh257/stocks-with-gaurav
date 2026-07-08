"""
dashboard/backend/routes/risk_dashboard.py
===========================================
Read-only Risk Engine Dashboard API (internal). Serves the daily audit summary,
the live configuration, and the config version history. No trading logic —
purely observability over the logs the engine already writes.

Endpoints:
  GET  /api/risk-engine/summary?date=YYYY-MM-DD   daily audit summary
  GET  /api/risk-engine/config                    live config (+ auto-versions on change)
  GET  /api/risk-engine/config-history?limit=N     version history
  POST /api/risk-engine/config-history            record a manual change {reason}
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/risk-engine", tags=["risk-engine"])
log = logging.getLogger("dashboard.risk_dashboard")


@router.get("/summary")
def risk_summary(date: str | None = Query(default=None, description="YYYY-MM-DD (IST); defaults to today")):
    from dashboard.backend.services.risk_audit import daily_summary
    return daily_summary(date)


@router.get("/config")
def risk_config():
    """Live engine configuration. Also snapshots it to the version history so any
    env change made on the platform is captured (source=auto)."""
    from services.risk_engine import cfg
    from dashboard.backend.db.risk_config import record_config_change
    config = cfg()
    rec = {"recorded": False}
    try:
        rec = record_config_change(config, source="auto")
    except Exception as exc:
        log.debug("config auto-snapshot failed: %s", exc)
    return {"config": config, "versioned": rec.get("recorded", False)}


@router.get("/config-history")
def config_history(limit: int = Query(default=50, ge=1, le=200)):
    from dashboard.backend.db.risk_config import get_config_history
    return {"history": get_config_history(limit)}


class ConfigNote(BaseModel):
    reason: str


@router.post("/config-history")
def record_config_note(note: ConfigNote):
    """Annotate the current config with a human reason (source=manual) — e.g.
    'widened LT stop cap to 15% after Q3 review'."""
    from services.risk_engine import cfg
    from dashboard.backend.db.risk_config import record_config_change
    return record_config_change(cfg(), reason=note.reason, source="manual")
