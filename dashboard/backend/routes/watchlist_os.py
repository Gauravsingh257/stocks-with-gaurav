"""
Watchlist Operating System API — snapshot-first per-user Redis payloads.

Live key:   snapshot:watchlist_operating:{user_id}
LKG key:    snapshot:last_known_good:watchlist_operating:{user_id}

GET serves cache/LKG only; heavy merge runs in BackgroundTasks after response (or legacy sync if opted out).

Does NOT replace SQLite watchlist storage or POST/DELETE /api/watchlist.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends

from dashboard.backend.routes.auth import get_current_user
from dashboard.backend.routes.research import _longterm_payload, _swing_payload
from dashboard.backend.services.watchlist_intel_service import (
    append_feed_diff,
    build_operating_payload,
    load_feed_only,
    market_alignment,
    merge_idea_maps,
    retention_hints,
)
from dashboard.backend.db import get_connection
from dashboard.backend.state_bridge import get_engine_snapshot

logger = logging.getLogger("dashboard.watchlist_os")

router = APIRouter(prefix="/api/watchlist", tags=["watchlist-os"])

WL_OS_TTL = int(os.getenv("WATCHLIST_OS_SNAPSHOT_TTL_SEC", "45"))
WL_OS_LKG_TTL = int(os.getenv("WATCHLIST_OS_LKG_TTL_SEC", "86400"))
WATCHLIST_OS_SNAPSHOT_ONLY = os.getenv("WATCHLIST_OS_SNAPSHOT_ONLY", "true").lower() in ("1", "true", "yes")


def _live_key(uid: int) -> str:
    return f"snapshot:watchlist_operating:{int(uid)}"


def _lkg_key(uid: int) -> str:
    return f"snapshot:last_known_good:watchlist_operating:{int(uid)}"


def _user_symbols(user_id: int) -> List[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol FROM user_watchlist WHERE user_id = ? ORDER BY added_at DESC",
            (user_id,),
        ).fetchall()
        return [r["symbol"] for r in rows]
    finally:
        conn.close()


def _build_watchlist_os_payload(uid: int) -> Dict[str, Any]:
    """Full merge — runs in background refresh only when snapshot-only mode is on."""
    symbols = _user_symbols(uid)
    try:
        swing = _swing_payload(120)
        lt = _longterm_payload(120)
        rmap = merge_idea_maps(list(swing.get("items") or []), list(lt.get("items") or []))
    except Exception as exc:
        logger.warning("research payload for watchlist OS failed: %s", exc)
        rmap = {}

    try:
        snap = get_engine_snapshot()
    except Exception:
        snap = {}

    enriched = build_operating_payload(symbols, rmap, snap if isinstance(snap, dict) else {})
    try:
        feed_tail = append_feed_diff(uid, enriched)
    except Exception as exc:
        logger.debug("feed append skipped: %s", exc)
        feed_tail = load_feed_only(uid, 12)

    return {
        "ok": True,
        "engine_version": "watchlist_os_v1",
        "items": enriched,
        "feed": feed_tail,
        "retention": retention_hints(enriched),
        "market_alignment": market_alignment(snap if isinstance(snap, dict) else {}),
        "counts": {
            "symbols": len(symbols),
            "with_research": sum(1 for x in enriched if x.get("meta", {}).get("has_research_row")),
        },
        "snapshot_stale": False,
        "snapshot_source": "live",
    }


def _persist_watchlist_os(uid: int, body: Dict[str, Any]) -> None:
    from dashboard.backend.cache import get as cache_get
    from dashboard.backend.cache import set as cache_set

    items = body.get("items") if isinstance(body.get("items"), list) else []
    if len(items) == 0:
        prev = cache_get(_lkg_key(uid))
        if prev and isinstance(prev, dict) and len(prev.get("items") or []) > 0:
            logger.warning(
                "watchlist_os: skip persist empty snapshot (preserve LKG) uid=%s",
                uid,
            )
            return

    cache_set(_live_key(uid), body, ttl_seconds=WL_OS_TTL)
    cache_set(_lkg_key(uid), body, ttl_seconds=WL_OS_LKG_TTL)


def _refresh_watchlist_os(uid: int) -> None:
    try:
        body = _build_watchlist_os_payload(uid)
        _persist_watchlist_os(uid, body)
    except Exception as exc:
        logger.warning("watchlist_os background refresh failed uid=%s: %s", uid, exc)


@router.get("/operating")
def watchlist_operating_system(
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    Snapshot-first: Redis live → LKG → warming shell; merge runs in BackgroundTasks.
    """
    uid = int(user["sub"])

    if not WATCHLIST_OS_SNAPSHOT_ONLY:
        body = _build_watchlist_os_payload(uid)
        _persist_watchlist_os(uid, body)
        return body

    from dashboard.backend.cache import get as cache_get

    hit = cache_get(_live_key(uid))
    if hit is not None:
        return hit

    lkg = cache_get(_lkg_key(uid))
    if lkg is not None:
        out = dict(lkg)
        out["snapshot_stale"] = True
        out["snapshot_source"] = "last_known_good"
        background_tasks.add_task(_refresh_watchlist_os, uid)
        return out

    background_tasks.add_task(_refresh_watchlist_os, uid)
    return {
        "ok": True,
        "engine_version": "watchlist_os_v1",
        "items": [],
        "feed": [],
        "retention": {},
        "market_alignment": {},
        "counts": {"symbols": 0, "with_research": 0},
        "snapshot_stale": True,
        "snapshot_source": "warming",
        "hint": "Watchlist intelligence snapshot is warming — retry shortly.",
    }


@router.get("/feed")
def watchlist_feed_only(user: dict = Depends(get_current_user), limit: int = 40):
    uid = int(user["sub"])
    return {"ok": True, "items": load_feed_only(uid, min(limit, 80))}
