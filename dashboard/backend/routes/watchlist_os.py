"""
Watchlist Operating System API — snapshot-first per-user Redis payloads.

Live key:   snapshot:watchlist_operating:{user_id}
LKG key:    snapshot:last_known_good:watchlist_operating:{user_id}
Digest key: snapshot:watchlist_digest:{user_id} (telemetry / observability)

Cold GET never returns an empty items[] when SQLite still has symbols — fixes empty page after add.

Does NOT replace SQLite watchlist storage or POST/DELETE /api/watchlist.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends

from dashboard.backend.routes.auth import get_current_user
from dashboard.backend.services.watchlist_intel_service import (
    append_feed_diff,
    build_operating_payload,
    load_feed_only,
    market_alignment,
    merge_idea_maps,
    retention_hints,
)
from dashboard.backend.services.decision_intelligence_engine import (
    build_virtual_portfolios,
    promotion_transition_touch,
    rollup_touch,
)
from dashboard.backend.db import get_connection
from dashboard.backend.state_bridge import get_engine_snapshot

logger = logging.getLogger("dashboard.watchlist_os")

router = APIRouter(prefix="/api/watchlist", tags=["watchlist-os"])

WL_OS_TTL = int(os.getenv("WATCHLIST_OS_SNAPSHOT_TTL_SEC", "45"))
WL_OS_LKG_TTL = int(os.getenv("WATCHLIST_OS_LKG_TTL_SEC", "86400"))
WATCHLIST_OS_SNAPSHOT_ONLY = os.getenv("WATCHLIST_OS_SNAPSHOT_ONLY", "true").lower() in ("1", "true", "yes")
RESEARCH_SNAPSHOT_ONLY = os.getenv("RESEARCH_SNAPSHOT_ONLY", "true").lower() in ("1", "true", "yes")

DIGEST_PREFIX = "snapshot:watchlist_digest:"


def _live_key(uid: int) -> str:
    return f"snapshot:watchlist_operating:{int(uid)}"


def _lkg_key(uid: int) -> str:
    return f"snapshot:last_known_good:watchlist_operating:{int(uid)}"


def _digest_key(uid: int) -> str:
    return f"{DIGEST_PREFIX}{int(uid)}"


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


def _research_idea_map() -> Dict[str, Any]:
    """Prefer Redis swing/longterm snapshots when RESEARCH_SNAPSHOT_ONLY — avoids heavy CMP resolution here."""
    try:
        if RESEARCH_SNAPSHOT_ONLY:
            from dashboard.backend.redis_endpoint_cache import serve_cached_research_list

            sw = serve_cached_research_list("swing") or {}
            lt = serve_cached_research_list("longterm") or {}
            return merge_idea_maps(list(sw.get("items") or []), list(lt.get("items") or []))
        from dashboard.backend.routes.research import _longterm_payload, _swing_payload

        swing = _swing_payload(120)
        lt = _longterm_payload(120)
        return merge_idea_maps(list(swing.get("items") or []), list(lt.get("items") or []))
    except Exception as exc:
        logger.warning("research map for watchlist OS failed: %s", exc)
        return {}


def _build_watchlist_os_payload(uid: int) -> Dict[str, Any]:
    symbols = _user_symbols(uid)
    rmap = _research_idea_map()

    try:
        snap = get_engine_snapshot()
    except Exception:
        snap = {}

    _t0 = time.perf_counter_ns()
    enriched = build_operating_payload(
        symbols,
        rmap,
        snap if isinstance(snap, dict) else {},
        uid,
    )
    _build_ms = round((time.perf_counter_ns() - _t0) / 1_000_000, 2)
    try:
        promotion_transition_touch(uid, enriched)
        rollup_touch(uid, enriched)
    except Exception as exc:
        logger.debug("decision rollup skipped: %s", exc)
    try:
        feed_tail = append_feed_diff(uid, enriched)
    except Exception as exc:
        logger.debug("feed append skipped: %s", exc)
        feed_tail = load_feed_only(uid, 12)

    now = time.time()
    body: Dict[str, Any] = {
        "ok": True,
        "engine_version": "watchlist_os_v2",
        "items": enriched,
        "feed": feed_tail,
        "retention": retention_hints(enriched),
        "market_alignment": market_alignment(snap if isinstance(snap, dict) else {}),
        "counts": {
            "symbols": len(symbols),
            "with_research": sum(1 for x in enriched if x.get("meta", {}).get("has_research_row")),
        },
        "decision_portfolios": build_virtual_portfolios(enriched),
        "decision_engine_version": "phase5_5_v1",
        "snapshot_stale": False,
        "snapshot_source": "live",
        "_snapshot_written_at": now,
        "_watchlist_os_schema": "v2",
        "_watchlist_build_ms": _build_ms,
    }
    try:
        from dashboard.backend.global_state_version import attach_snapshot_meta

        attach_snapshot_meta(body, origin="watchlist_operating")
    except Exception:
        pass
    return body


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

    try:
        from dashboard.backend.watchlist_notify import publish_watchlist_os_refresh

        publish_watchlist_os_refresh(uid, kind="hint")
    except Exception:
        pass

    try:
        from dashboard.backend.global_state_version import bump_global_state_version

        bump_global_state_version("watchlist_operating_persist")
    except Exception:
        pass

    try:
        enriched = body.get("items") if isinstance(body.get("items"), list) else []
        mx = 0.0
        for x in enriched:
            try:
                mx = max(mx, float(x.get("readiness_pct") or 0))
            except (TypeError, ValueError):
                pass
        digest = {
            "user_id": uid,
            "updated_at": body.get("_snapshot_written_at") or time.time(),
            "symbol_count": len(enriched),
            "max_readiness_pct": round(mx, 1),
            "build_ms": body.get("_watchlist_build_ms"),
        }
        cache_set(_digest_key(uid), digest, ttl_seconds=WL_OS_TTL)
    except Exception as exc:
        logger.debug("watchlist digest persist skipped: %s", exc)


def invalidate_watchlist_os_cache(uid: int) -> None:
    """Drop live snapshot so the next GET rebuilds or serves LKG."""
    from dashboard.backend.cache import delete as cache_delete

    cache_delete(_live_key(uid))


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
    Snapshot-first: Redis live → LKG → synchronous build if DB has symbols (no empty shell).
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

    symbols = _user_symbols(uid)
    if symbols:
        body = _build_watchlist_os_payload(uid)
        _persist_watchlist_os(uid, body)
        return body

    background_tasks.add_task(_refresh_watchlist_os, uid)
    return {
        "ok": True,
        "engine_version": "watchlist_os_v2",
        "items": [],
        "feed": [],
        "retention": {},
        "market_alignment": {},
        "counts": {"symbols": 0, "with_research": 0},
        "decision_portfolios": {
            "intraday_momentum": [],
            "mtf_swing": [],
            "short_term_growth": [],
            "long_term_compounders": [],
            "slot_cap": 30,
            "note": "Virtual ranking — not an executed portfolio allocation.",
        },
        "decision_engine_version": "phase5_5_v1",
        "snapshot_stale": True,
        "snapshot_source": "warming",
        "hint": "No symbols saved yet — add stocks from Research or stock pages.",
    }


@router.get("/feed")
def watchlist_feed_only(user: dict = Depends(get_current_user), limit: int = 40):
    uid = int(user["sub"])
    return {"ok": True, "items": load_feed_only(uid, min(limit, 80))}
