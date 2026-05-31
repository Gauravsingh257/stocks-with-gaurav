"""
dashboard/backend/routes/watchlist_monitor.py
==============================================
Active watchlist APIs — the pre-portfolio staging layer. Research ideas and
manual additions use the SAME endpoints (no special cases). Triggered / bought
ideas flow into the per-user user_positions book, which the shared
PositionTrackingService tracks.

Endpoints (all per-user, auth-gated):
  GET    /api/watchlist/monitor              list cards (+ live CMP + status)
  POST   /api/watchlist/monitor              add idea (manual or research)
  POST   /api/watchlist/monitor/{id}/arm     arm the entry trigger
  POST   /api/watchlist/monitor/{id}/buy-cmp buy now at CMP → user_position
  POST   /api/watchlist/monitor/{id}/ignore  dismiss a paper trigger (→ ARMED)
  DELETE /api/watchlist/monitor/{id}         remove
  GET    /api/watchlist/preferences          auto_entry + sizing defaults
  PUT    /api/watchlist/preferences          update prefs
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dashboard.backend.routes.auth import get_current_user, _normalize_symbol
from dashboard.backend.db.watchlist_monitor import (
    create_watchlist_idea, list_watchlist, get_watchlist_idea, remove_watchlist_idea,
    update_watchlist_fields, log_watchlist_event, promote_watchlist_to_user_position,
    get_user_pref, set_user_pref,
)
from services.entry_trigger_service import compute_zone_state, size_position

router = APIRouter(tags=["watchlist-monitor"])
log = logging.getLogger("dashboard.watchlist_monitor")


class AddIdeaRequest(BaseModel):
    symbol: str
    entry_low: float
    entry_high: float
    stop_loss: float
    target_1: float | None = None
    target_2: float | None = None
    pattern: str | None = None
    tag: str | None = None
    capital: float | None = None
    risk_percent: float | None = None
    auto_entry_override: bool | None = None
    valid_until: str | None = None
    notes: str | None = None
    source: str = "MANUAL"


class PrefsRequest(BaseModel):
    auto_entry: bool | None = None
    default_capital: float | None = None
    default_risk_percent: float | None = None


def _resolve_cmp(symbol: str) -> float | None:
    try:
        from services.trade_tracker import _fetch_cmp_batch
        return (_fetch_cmp_batch([symbol]) or {}).get(symbol)
    except Exception:
        return None


@router.get("/api/watchlist/monitor")
def list_monitor(user: dict = Depends(get_current_user)):
    uid = int(user["sub"])
    rows = list_watchlist(uid)
    syms = list({r["symbol"] for r in rows})
    try:
        from services.trade_tracker import _fetch_cmp_batch
        prices = _fetch_cmp_batch(syms) or {}
    except Exception:
        prices = {}
    items = []
    for r in rows:
        cmp = prices.get(r["symbol"]) or r.get("cmp")
        lo, hi = float(r["entry_low"]), float(r["entry_high"])
        # display status: stored lifecycle for armed/triggered, else live zone
        if r.get("triggered") or r.get("armed"):
            status = r["status"]
        else:
            status = compute_zone_state(cmp, lo, hi) if cmp else r["status"]
        dist = round((cmp - (lo + hi) / 2) / ((lo + hi) / 2) * 100, 2) if cmp else None
        d = dict(r)
        d.update({"cmp": cmp, "live_status": status, "distance_pct": dist})
        items.append(d)
    return {"items": items, "count": len(items)}


@router.post("/api/watchlist/monitor")
def add_idea(req: AddIdeaRequest, user: dict = Depends(get_current_user)):
    uid = int(user["sub"])
    sym = _normalize_symbol(req.symbol)
    if not sym:
        raise HTTPException(400, "symbol required")
    if req.entry_high < req.entry_low:
        raise HTTPException(400, "entry_high must be >= entry_low")
    pref = get_user_pref(uid)
    capital = req.capital if req.capital is not None else pref.get("default_capital")
    risk = req.risk_percent if req.risk_percent is not None else pref.get("default_risk_percent")
    qty = size_position(capital, risk, (req.entry_low + req.entry_high) / 2, req.stop_loss)
    payload = req.model_dump()
    payload.update({"symbol": sym, "capital": capital, "risk_percent": risk,
                    "calculated_quantity": qty,
                    "auto_entry_override": (None if req.auto_entry_override is None
                                            else int(req.auto_entry_override))})
    wid = create_watchlist_idea(uid, payload)
    return {"ok": True, "id": wid, "calculated_quantity": qty}


@router.post("/api/watchlist/monitor/{idea_id}/arm")
def arm(idea_id: int, user: dict = Depends(get_current_user)):
    uid = int(user["sub"])
    idea = get_watchlist_idea(idea_id, uid)
    if not idea:
        raise HTTPException(404, "not found")
    if idea["status"] in ("ACTIVE", "CLOSED", "EXPIRED"):
        raise HTTPException(409, f"cannot arm a {idea['status']} idea")
    update_watchlist_fields(idea_id, armed=1, status="ARMED")
    log_watchlist_event(idea_id, uid, "ARMED")
    return {"ok": True, "status": "ARMED"}


@router.post("/api/watchlist/monitor/{idea_id}/buy-cmp")
def buy_cmp(idea_id: int, user: dict = Depends(get_current_user)):
    uid = int(user["sub"])
    idea = get_watchlist_idea(idea_id, uid)
    if not idea:
        raise HTTPException(404, "not found")
    if idea["status"] in ("ACTIVE", "CLOSED", "EXPIRED"):
        raise HTTPException(409, f"already {idea['status']}")
    cmp = _resolve_cmp(idea["symbol"])
    if cmp is None:
        raise HTTPException(503, "live price unavailable")
    pid = promote_watchlist_to_user_position(idea, cmp, "CMP_BUY")
    if pid is None:
        raise HTTPException(409, "Already Active")
    log_watchlist_event(idea_id, uid, "BUY_CMP", notes=f"@ {cmp}")
    return {"ok": True, "position_id": pid, "entry": cmp}


@router.post("/api/watchlist/monitor/{idea_id}/ignore")
def ignore(idea_id: int, user: dict = Depends(get_current_user)):
    uid = int(user["sub"])
    idea = get_watchlist_idea(idea_id, uid)
    if not idea:
        raise HTTPException(404, "not found")
    # dismiss a paper trigger → back to ARMED (keeps watching)
    update_watchlist_fields(idea_id, status="ARMED", triggered=0, trigger_time=None)
    log_watchlist_event(idea_id, uid, "IGNORED")
    return {"ok": True, "status": "ARMED"}


@router.delete("/api/watchlist/monitor/{idea_id}")
def remove(idea_id: int, user: dict = Depends(get_current_user)):
    uid = int(user["sub"])
    if not remove_watchlist_idea(idea_id, uid):
        raise HTTPException(404, "not found")
    return {"ok": True}


@router.get("/api/watchlist/preferences")
def get_prefs(user: dict = Depends(get_current_user)):
    return get_user_pref(int(user["sub"]))


@router.put("/api/watchlist/preferences")
def put_prefs(req: PrefsRequest, user: dict = Depends(get_current_user)):
    uid = int(user["sub"])
    set_user_pref(uid, **{k: v for k, v in req.model_dump().items() if v is not None})
    return get_user_pref(uid)
