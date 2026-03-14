"""
dashboard/backend/routes/agents.py
Agent management REST endpoints.

GET  /api/agents/status              — all agents + last-run + next-run
GET  /api/agents/logs                — paginated agent logs (filter by agent name)
GET  /api/agents/queue               — pending action queue
POST /api/agents/queue/{id}/approve  — approve an action
POST /api/agents/queue/{id}/reject   — reject an action
POST /api/agents/run/{agent_name}    — trigger agent manually now
"""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from dashboard.backend.db import get_connection

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ── Status ───────────────────────────────────────────────────────────────────

@router.get("/status")
def agent_statuses():
    """Return last-run info + next scheduled run for all 4 agents."""
    try:
        from agents.runner import get_agent_statuses
        return get_agent_statuses()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Logs ─────────────────────────────────────────────────────────────────────

@router.get("/logs")
def agent_logs(
    agent: str | None = Query(None, description="Filter by agent name"),
    limit: int        = Query(50, ge=1, le=200),
    offset: int       = Query(0, ge=0),
):
    conn  = get_connection()
    query = "SELECT * FROM agent_logs"
    params: list = []

    if agent:
        query  += " WHERE agent_name = ?"
        params.append(agent)

    query += " ORDER BY run_time DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = conn.execute(query, params).fetchall()

    count_query = "SELECT COUNT(*) as c FROM agent_logs"
    count_params: list = []
    if agent:
        count_query  += " WHERE agent_name = ?"
        count_params.append(agent)
    total = conn.execute(count_query, count_params).fetchone()["c"]
    conn.close()

    def _parse(row):
        d = dict(row)
        for key in ("findings_json", "actions_json", "metrics_json"):
            raw = d.get(key)
            if raw:
                try:
                    d[key.replace("_json", "")] = json.loads(raw)
                except json.JSONDecodeError:
                    d[key.replace("_json", "")] = []
            else:
                d[key.replace("_json", "")] = []
        return d

    return {
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "items":  [_parse(r) for r in rows],
    }


# ── Action Queue ─────────────────────────────────────────────────────────────

@router.get("/queue")
def action_queue(
    status: str | None = Query(None, description="PENDING | APPROVED | REJECTED"),
    limit:  int        = Query(50, ge=1, le=200),
):
    conn   = get_connection()
    query  = "SELECT * FROM agent_action_queue"
    params: list = []

    if status:
        query  += " WHERE status = ?"
        params.append(status.upper())

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    def _parse(row):
        d = dict(row)
        raw = d.get("payload_json")
        if raw:
            try:
                d["payload"] = json.loads(raw)
            except json.JSONDecodeError:
                d["payload"] = {}
        return d

    return [_parse(r) for r in rows]


@router.post("/queue/{action_id}/approve")
def approve_action(action_id: int):
    """Mark a queued action as APPROVED."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM agent_action_queue WHERE id = ?", (action_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Action {action_id} not found")
    if row["status"] != "PENDING":
        conn.close()
        raise HTTPException(status_code=400, detail=f"Action is already {row['status']}")

    conn.execute(
        "UPDATE agent_action_queue SET status='APPROVED', processed_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), action_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "action_id": action_id, "new_status": "APPROVED"}


@router.post("/queue/{action_id}/reject")
def reject_action(action_id: int):
    """Mark a queued action as REJECTED."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM agent_action_queue WHERE id = ?", (action_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Action {action_id} not found")
    if row["status"] != "PENDING":
        conn.close()
        raise HTTPException(status_code=400, detail=f"Action is already {row['status']}")

    conn.execute(
        "UPDATE agent_action_queue SET status='REJECTED', processed_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), action_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "action_id": action_id, "new_status": "REJECTED"}


# ── Manual trigger ────────────────────────────────────────────────────────────

@router.post("/run/{agent_name}")
def run_agent_now(agent_name: str):
    """Trigger an agent to run immediately. Returns the AgentResult."""
    try:
        from agents.runner import run_agent_now as _run
        result = _run(agent_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return result
