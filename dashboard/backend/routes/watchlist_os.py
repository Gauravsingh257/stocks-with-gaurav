"""
Watchlist Operating System API — extends /api/watchlist with intelligence payloads.

Does NOT replace SQLite watchlist storage or POST/DELETE /api/watchlist.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

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


@router.get("/operating")
def watchlist_operating_system(user: dict = Depends(get_current_user)):
    """
    Full watchlist OS: enriched per-symbol intelligence, feed tail, retention hints.
    All trade levels are gated server-side (show_trade_levels) — frontend must honor.
    """
    uid = int(user["sub"])
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
    }


@router.get("/feed")
def watchlist_feed_only(user: dict = Depends(get_current_user), limit: int = 40):
    uid = int(user["sub"])
    return {"ok": True, "items": load_feed_only(uid, min(limit, 80))}
