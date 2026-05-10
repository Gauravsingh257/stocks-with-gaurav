"""Phase 6 — User engagement + product metrics hooks (Redis-first)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from dashboard.backend.routes.auth import get_current_user

router = APIRouter(tags=["user-product"])
log = logging.getLogger("dashboard.user_product")

PRODUCT_PREFIX = "product:v1:"
SESSION_DAY_KEY = PRODUCT_PREFIX + "session_day:"
INTEL_HASH_PREFIX = PRODUCT_PREFIX + "intel:"
ACTIVE_SET_PREFIX = PRODUCT_PREFIX + "active_uids:"
ALERT_QUEUE_PREFIX = PRODUCT_PREFIX + "in_app_queue:"


def _today() -> str:
    return date.today().isoformat()


def _touch_session(uid: int) -> None:
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        day = _today()
        r.incr(f"{SESSION_DAY_KEY}{day}:pulses")
        r.expire(f"{SESSION_DAY_KEY}{day}:pulses", 4 * 86400)
        r.sadd(f"{ACTIVE_SET_PREFIX}{day}", str(int(uid)))
        r.expire(f"{ACTIVE_SET_PREFIX}{day}", 4 * 86400)
    except Exception as exc:
        log.debug("session touch skipped: %s", exc)


class IntelEventBody(BaseModel):
    event: str = Field(..., min_length=1, max_length=64)
    symbol: Optional[str] = Field(None, max_length=32)
    meta: Optional[Dict[str, Any]] = None


@router.post("/api/user/session-pulse")
def session_pulse(user: dict = Depends(get_current_user)):
    """Lightweight heartbeat for product_metrics (active users / engagement proxy)."""
    _touch_session(int(user["sub"]))
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}


@router.post("/api/user/intel/event")
def intel_event(body: IntelEventBody, user: dict = Depends(get_current_user)):
    """
    Track coarse engagement for personalization foundations (no PII beyond user id).
    Events: setup_view, watchlist_open, sector_focus, command_center_open, etc.
    """
    uid = int(user["sub"])
    _touch_session(uid)
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return {"ok": True, "stored": False}
        day = _today()
        key = f"{INTEL_HASH_PREFIX}{uid}:{day}"
        r.hincrby(key, body.event, 1)
        if body.symbol:
            r.hincrby(key, f"sym:{body.symbol.upper()}", 1)
        r.expire(key, 14 * 86400)
        try:
            from dashboard.backend.services.retention_engine_service import touch_intel_event_metric

            touch_intel_event_metric()
        except Exception:
            pass
        return {"ok": True, "stored": True}
    except Exception as exc:
        log.debug("intel event skipped: %s", exc)
        return {"ok": True, "stored": False, "note": str(exc)}


@router.post("/api/user/visit-mark")
def visit_mark(user: dict = Depends(get_current_user)):
    """
    Snapshot desk fingerprints for revisit psychology — call when ending a session (e.g. navigate away).
    Next load compares against this digest.
    """
    uid = int(user["sub"])
    try:
        from dashboard.backend.services.command_center_service import _load_watchlist_os
        from dashboard.backend.services.retention_engine_service import save_visit_digest

        wl_items, _ = _load_watchlist_os(uid)
        save_visit_digest(uid, wl_items)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/user/revisit-summary")
def revisit_summary(user: dict = Depends(get_current_user)):
    """Psychology-safe deltas vs last visit-mark."""
    uid = int(user["sub"])
    try:
        from dashboard.backend.services.command_center_service import _load_watchlist_os
        from dashboard.backend.services.retention_engine_service import build_revisit_psychology

        wl_items, _ = _load_watchlist_os(uid)
        out = build_revisit_psychology(uid, wl_items)
        if out.get("lines"):
            try:
                from dashboard.backend.cache import _get_redis
                from dashboard.backend.services.retention_engine_service import METRICS_REVISIT_DAY

                r = _get_redis()
                if r is not None:
                    mk = f"{METRICS_REVISIT_DAY}{_today()}"
                    r.incr(mk)
                    r.expire(mk, 5 * 86400)
            except Exception:
                pass
        return {"ok": True, **out}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "lines": []}


@router.get("/api/user/evolution/{symbol}")
def evolution_timeline(
    symbol: str,
    user: dict = Depends(get_current_user),
    limit: int = 32,
):
    """Per-symbol evolution story (Redis timeline). FREE: shorter cap."""
    uid = int(user["sub"])
    role = str(user.get("role") or "FREE").upper()
    cap = min(80, max(8, limit))
    if role not in ("PREMIUM", "ADMIN"):
        cap = min(cap, 24)
    try:
        from dashboard.backend.services.retention_engine_service import get_evolution_timeline

        items = get_evolution_timeline(uid, symbol, cap)
        return {
            "ok": True,
            "symbol": symbol.replace("NSE:", "").strip().upper(),
            "items": items,
            "cap": cap,
            "premium": role in ("PREMIUM", "ADMIN"),
        }
    except Exception as exc:
        return {"ok": False, "items": [], "error": str(exc)}


@router.get("/api/user/activity-feed")
def activity_feed(user: dict = Depends(get_current_user), limit: int = 30):
    """In-app notification queue preview (server-side caps)."""
    uid = int(user["sub"])
    cap = max(5, min(limit, 50))
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return {"ok": True, "items": []}
        raw = r.lrange(f"{ALERT_QUEUE_PREFIX}{uid}", 0, cap - 1)
        items: List[Dict[str, Any]] = []
        for row in raw or []:
            try:
                items.append(json.loads(row.decode() if isinstance(row, bytes) else row))
            except Exception:
                continue
        return {"ok": True, "items": items}
    except Exception as exc:
        return {"ok": False, "items": [], "error": str(exc)}


def read_product_metrics() -> Dict[str, Any]:
    """Observability-only aggregates for GET /api/system/debug/platform."""
    out: Dict[str, Any] = {"date": _today()}
    try:
        from dashboard.backend.db import get_connection

        conn = get_connection()
        out["users_registered"] = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        wl = conn.execute("SELECT COUNT(DISTINCT user_id) AS c FROM user_watchlist").fetchone()
        out["users_with_watchlist"] = wl["c"] if wl else 0
        rows = conn.execute("SELECT COUNT(*) AS c FROM user_watchlist").fetchone()
        out["watchlist_rows_total"] = rows["c"] if rows else 0
        conn.close()
    except Exception as e:
        out["db_error"] = str(e)

    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            out["redis_available"] = False
            return out
        out["redis_available"] = True
        day = _today()
        try:
            out["session_pulses_today"] = int(r.get(f"{SESSION_DAY_KEY}{day}:pulses") or 0)
        except (TypeError, ValueError):
            out["session_pulses_today"] = None
        try:
            out["active_users_today_approx"] = int(r.scard(f"{ACTIVE_SET_PREFIX}{day}"))
        except Exception:
            out["active_users_today_approx"] = None

        cur = 0
        digest_n = 0
        for _ in range(80):
            cur, batch = r.scan(cur, match="snapshot:watchlist_digest:*", count=48)
            digest_n += len(batch)
            if cur == 0:
                break
        out["watchlist_digest_keys_approx"] = digest_n

        intel_keys = 0
        cur = 0
        for _ in range(60):
            cur, batch = r.scan(cur, match=f"{INTEL_HASH_PREFIX}*", count=40)
            intel_keys += len(batch)
            if cur == 0:
                break
        out["user_intel_keys_approx"] = intel_keys
    except Exception as e:
        out["redis_error"] = str(e)

    out["notes"] = {
        "avg_session_duration": "requires client beacon — not tracked server-side yet",
        "alert_engagement": "increment via session-pulse + intel/event; refine with notification taps later",
        "premium_conversion_readiness": "role distribution query optional — use users.role histogram offline",
        "revisit_frequency": "approximate via active_users_today vs registered when correlated offline",
    }
    return out


def enqueue_in_app_alert(user_id: int, kind: str, headline: str, symbol: Optional[str] = None) -> None:
    """Called by future hooks (promotion/deterioration jobs). Keep payload small."""
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        payload = json.dumps(
            {
                "kind": kind,
                "headline": headline[:240],
                "symbol": symbol,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            default=str,
        )
        k = f"{ALERT_QUEUE_PREFIX}{int(user_id)}"
        r.lpush(k, payload)
        r.ltrim(k, 0, 49)
        r.expire(k, 14 * 86400)
    except Exception:
        pass
