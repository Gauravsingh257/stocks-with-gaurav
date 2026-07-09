"""
dashboard/backend/routes/rejection_analysis.py
==============================================
Read-only export of the historical validation funnel (signals_log) for the
Phase-1 "Discovery → Rejected" study. No trading logic — it only reads rows so
the analysis (forward returns, reason breakdown) can run offline.

A row with layer1_pass=1 AND final_selected=0 is a stock Discovery liked but the
downstream gates (quality / SMC / reachability / freshness) rejected — exactly
the set we want to score against what happened next.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/research", tags=["research"])
log = logging.getLogger("dashboard.rejection_analysis")
_IST = timezone(timedelta(hours=5, minutes=30))


@router.get("/signals-log-export")
def signals_log_export(
    days: int = Query(default=365, ge=1, le=730),
    limit: int = Query(default=12000, ge=1, le=50000),
    kind: str = Query(default="discovery_rejected",
                      pattern="^(discovery_rejected|selected|all)$"),
    horizon: str | None = Query(default=None, pattern="^(SWING|LONGTERM)$"),
):
    """Raw signals_log export + data-availability stats. Read-only."""
    from dashboard.backend.db.schema import get_connection
    conn = get_connection()
    try:
        # Data availability (whole table, so we know how much history exists).
        a = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*), COUNT(DISTINCT date), "
            "COALESCE(SUM(layer1_pass),0), COALESCE(SUM(final_selected),0), "
            "COALESCE(SUM(CASE WHEN layer1_pass=1 AND final_selected=0 THEN 1 ELSE 0 END),0) "
            "FROM signals_log"
        ).fetchone()
        availability = {
            "min_date": a[0], "max_date": a[1], "total_rows": a[2],
            "distinct_scan_days": a[3], "discovery_passed": a[4],
            "final_selected": a[5], "discovery_rejected": a[6],
        }

        cutoff = (datetime.now(_IST).date() - timedelta(days=days)).isoformat()
        if kind == "discovery_rejected":
            cond = "layer1_pass=1 AND final_selected=0"
        elif kind == "selected":
            cond = "final_selected=1"
        else:
            cond = "1=1"
        params: list = [cutoff]
        hz = ""
        if horizon:
            hz = "AND horizon=?"
            params.append(horizon.upper())

        rows = conn.execute(
            f"""
            SELECT scan_id, horizon, symbol, date, cmp, entry, stop_loss, target,
                   confidence, layer1_pass, layer2_pass, layer3_pass, final_selected,
                   rejection_reason
            FROM signals_log
            WHERE date >= ? {hz} AND {cond}
            ORDER BY date DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["rejection_reason"] = json.loads(d.get("rejection_reason") or "[]")
            except Exception:
                d["rejection_reason"] = []
            out.append(d)
        return {"availability": availability, "kind": kind, "days": days,
                "returned": len(out), "rows": out}
    finally:
        conn.close()
