"""
Watchlist Operating System API — snapshot-first per-user Redis payloads.

Live key:   snapshot:watchlist_operating:{user_id}
LKG key:    snapshot:last_known_good:watchlist_operating:{user_id}
Digest key: snapshot:watchlist_digest:{user_id} (telemetry / observability)
Trace key:  watchlist:event_trace:{user_id} (rolling 200-event lifecycle log)

Cold GET never returns an empty items[] when SQLite still has symbols — fixes empty page after add.

Does NOT replace SQLite watchlist storage or POST/DELETE /api/watchlist.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
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
TRACE_PREFIX = "watchlist:event_trace:"
TRACE_TTL = 86400
TRACE_MAX_EVENTS = 200


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
        "_trust": {
            "schema": "phase_b_v1",
            "built_at_ms": int(now * 1000),
            "engine_snapshot_time": (snap.get("snapshot_time") if isinstance(snap, dict) else None),
            "symbol_count": len(symbols),
        },
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

    # Only skip persist when items=[] AND SQLite also has no symbols for this user.
    # Previously this guard skipped the write when LKG had items, which caused the
    # watchlist page to stay empty after a user added their first symbol (the rebuild
    # returned enriched=[] because research data was still warming up, but SQLite had
    # the symbol). Now we only skip if SQLite confirms the user truly has no symbols.
    if len(items) == 0:
        db_symbols = _user_symbols(uid)
        if db_symbols:
            logger.warning(
                "watchlist_os: empty enriched result but SQLite has %d symbol(s) uid=%s — persisting with empty items to show stale indicator",
                len(db_symbols),
                uid,
            )
            # Fall through — write the snapshot so the UI at least shows stale state
            # rather than staying on the LKG with no stale indicator.
        else:
            prev = cache_get(_lkg_key(uid))
            if prev and isinstance(prev, dict) and len(prev.get("items") or []) > 0:
                logger.warning(
                    "watchlist_os: skip persist empty snapshot (no db symbols, preserve LKG) uid=%s",
                    uid,
                )
                return

    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is not None:
            rev = int(r.incr(f"watchlist:bundle_ver:{int(uid)}"))
            tm = body.get("_trust") if isinstance(body.get("_trust"), dict) else {}
            tm["bundle_revision"] = rev
            body["_trust"] = tm
    except Exception:
        pass

    cache_set(_live_key(uid), body, ttl_seconds=WL_OS_TTL)
    cache_set(_lkg_key(uid), body, ttl_seconds=WL_OS_LKG_TTL)

    gv_after = 0
    try:
        from dashboard.backend.global_state_version import bump_global_state_version

        gv_after = bump_global_state_version("watchlist_operating_persist")
    except Exception:
        pass

    # Publish WS delta with version info so clients can reject stale hints
    try:
        from dashboard.backend.watchlist_notify import publish_watchlist_os_refresh

        rev = (body.get("_trust") or {}).get("bundle_revision", 0)
        publish_watchlist_os_refresh(
            uid,
            kind="hint",
            global_state_version=gv_after or body.get("_global_state_version"),
            snapshot_version=rev,
        )
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


def _append_event_trace(uid: int, action: str, symbol: str | None, extra: Dict[str, Any] | None = None) -> str:
    """Append one lifecycle event to watchlist:event_trace:{uid} (rolling 200-event list, TTL 24h)."""
    event_id = str(uuid.uuid4())
    event: Dict[str, Any] = {
        "event_id": event_id,
        "action": action,
        "user_id": uid,
        "ts_ms": int(time.time() * 1000),
    }
    if symbol:
        event["symbol"] = symbol
    if extra:
        event.update(extra)
    try:
        import json as _json
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is not None:
            key = f"{TRACE_PREFIX}{int(uid)}"
            pipe = r.pipeline(transaction=False)
            pipe.rpush(key, _json.dumps(event))
            pipe.ltrim(key, -TRACE_MAX_EVENTS, -1)
            pipe.expire(key, TRACE_TTL)
            pipe.execute()
    except Exception as exc:
        logger.debug("event trace append failed uid=%s: %s", uid, exc)
    return event_id


def _validate_watchlist_os_payload(payload: Any) -> tuple[bool, list[str]]:
    """
    Structural validation for a watchlist OS snapshot.
    Returns (ok, issues). Never wipes UI on malformed payload — caller requests resync instead.
    """
    issues: list[str] = []
    if not isinstance(payload, dict):
        return False, ["not_a_dict"]
    if "items" in payload and not isinstance(payload["items"], list):
        issues.append("items_not_list")
    if "feed" in payload and not isinstance(payload["feed"], list):
        issues.append("feed_not_list")
    written_at = payload.get("_snapshot_written_at")
    if written_at is not None:
        try:
            age_sec = time.time() - float(written_at)
            if age_sec > WL_OS_TTL * 4:
                issues.append(f"snapshot_too_old_{int(age_sec)}s")
        except (TypeError, ValueError):
            issues.append("invalid_snapshot_written_at")
    gv = payload.get("_global_state_version")
    if gv is not None and not isinstance(gv, int):
        issues.append("invalid_global_state_version")
    return len(issues) == 0, issues


def invalidate_watchlist_os_cache(uid: int) -> None:
    """Drop live snapshot so the next GET rebuilds or serves LKG."""
    from dashboard.backend.cache import delete as cache_delete

    cache_delete(_live_key(uid))


def _refresh_watchlist_os(uid: int, trigger: str = "background") -> Dict[str, Any]:
    """Rebuild + persist watchlist OS snapshot. Returns dict with persisted + version info."""
    try:
        body = _build_watchlist_os_payload(uid)
        _persist_watchlist_os(uid, body)
        gv = body.get("_global_state_version", 0)
        rev = (body.get("_trust") or {}).get("bundle_revision", 0)
        _append_event_trace(uid, f"refresh_{trigger}", None, {
            "global_state_version": gv,
            "bundle_revision": rev,
            "symbol_count": len(body.get("items") or []),
        })
        return {"persisted": True, "global_state_version": gv, "bundle_revision": rev}
    except Exception as exc:
        logger.warning("watchlist_os background refresh failed uid=%s: %s", uid, exc)
        return {"persisted": False}


def _attach_row_versions(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach row_version + updated_at to each item for last-write-wins reconciliation."""
    items = payload.get("items")
    if not isinstance(items, list):
        return payload
    now_ms = int(time.time() * 1000)
    snap_ts = payload.get("_snapshot_written_at")
    snap_ms = int(float(snap_ts) * 1000) if snap_ts is not None else now_ms
    for item in items:
        if not isinstance(item, dict):
            continue
        if "row_version" not in item:
            item["row_version"] = snap_ms
        if "updated_at" not in item:
            item["updated_at"] = snap_ms
    return payload


@router.get("/operating")
def watchlist_operating_system(
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    Snapshot-first: Redis live → LKG → synchronous build if DB has symbols (no empty shell).
    Validates payload structure before serving. Rejects malformed snapshots and triggers resync.
    """
    uid = int(user["sub"])

    if not WATCHLIST_OS_SNAPSHOT_ONLY:
        body = _build_watchlist_os_payload(uid)
        _persist_watchlist_os(uid, body)
        return _attach_row_versions(body)

    from dashboard.backend.cache import get as cache_get

    hit = cache_get(_live_key(uid))
    if hit is not None:
        ok, issues = _validate_watchlist_os_payload(hit)
        if not ok:
            logger.warning("watchlist_os: live snapshot invalid uid=%s issues=%s — falling to LKG", uid, issues)
            _append_event_trace(uid, "live_invalid", None, {"issues": issues})
            hit = None  # fall through to LKG

    if hit is not None:
        return _attach_row_versions(hit)

    lkg = cache_get(_lkg_key(uid))
    if lkg is not None:
        ok, issues = _validate_watchlist_os_payload(lkg)
        if not ok:
            logger.warning("watchlist_os: LKG snapshot invalid uid=%s issues=%s — forcing sync build", uid, issues)
            _append_event_trace(uid, "lkg_invalid", None, {"issues": issues})
            lkg = None  # fall through to sync build

    if lkg is not None:
        out = dict(lkg)
        out["snapshot_stale"] = True
        out["snapshot_source"] = "last_known_good"
        background_tasks.add_task(_refresh_watchlist_os, uid, "visibility")
        return _attach_row_versions(out)

    symbols = _user_symbols(uid)
    if symbols:
        body = _build_watchlist_os_payload(uid)
        _persist_watchlist_os(uid, body)
        return _attach_row_versions(body)

    background_tasks.add_task(_refresh_watchlist_os, uid, "cold")
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
