"""
dashboard/backend/routes/sectors.py — read-only Sector Rotation API (Phase 2).

    GET /api/sectors/status
    GET /api/sectors/rotation?tf=D|W&benchmark=market|nifty50&trail=10
    GET /api/sectors/constituents?sector=IT

(NoCacheAPIMiddleware sets no-store on every /api response, so caching here is
Redis-side only.) Serves precomputed `sector_rotation_daily` rows (Phase 1, written by the 16:40 IST
job). Nothing is calculated from prices on a request: a rotation payload is a
table read, shaped by services/sector_rotation_view.py and cached in Redis until
the table's latest date or write time changes.

Flags (env, read per request so no redeploy is needed to flip them):
  SECTOR_ROTATION_API_ENABLED  default off — rotation/constituents return 404 when off.
  SECTOR_ROTATION_PAGE_PUBLIC  default off — the /sectors page renders only with
                               ?preview=1 until this is on (validation phase).
  SECTOR_ROTATION_HOMEPAGE_ENABLED  default off — the homepage widget (Phase 3)
                               renders only when this AND the API flag are on;
                               otherwise the homepage keeps its static preview.

Nothing in ranking, selection, portfolio or trading reads these endpoints.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

log = logging.getLogger("dashboard.routes.sectors")
router = APIRouter(tags=["sectors"])

_CACHE_PREFIX = "sector:rotation:api:v1"
_CACHE_TTL = int(os.getenv("SECTOR_ROTATION_API_CACHE_SEC", "3600"))
_STAMP_TTL = 60.0
_stamp_cache: dict = {"at": 0.0, "value": None}


def _flag(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


def api_enabled() -> bool:
    return _flag("SECTOR_ROTATION_API_ENABLED")


def page_public() -> bool:
    return _flag("SECTOR_ROTATION_PAGE_PUBLIC")


def homepage_widget() -> bool:
    return api_enabled() and _flag("SECTOR_ROTATION_HOMEPAGE_ENABLED")


def _disabled() -> JSONResponse:
    return JSONResponse(status_code=404, content={"enabled": False, "detail": "sector rotation is not enabled"})


def _stamp() -> dict:
    now = time.time()
    if _stamp_cache["value"] is not None and now - _stamp_cache["at"] < _STAMP_TTL:
        return _stamp_cache["value"]
    from dashboard.backend.db.sector_rotation import latest_stamp

    value = latest_stamp()
    _stamp_cache.update(at=now, value=value)
    return value


@router.get("/api/sectors/status")
def sectors_status():
    out = {"api_enabled": api_enabled(), "page_public": page_public(), "homepage_widget": homepage_widget()}
    if out["api_enabled"]:
        try:
            out["latest"] = _stamp()
        except Exception as exc:
            log.warning("sector status stamp failed: %s", exc)
            out["latest"] = None
    return JSONResponse(content=out, headers={"Cache-Control": "no-store"})


@router.get("/api/sectors/rotation")
def sectors_rotation(
    tf: str = Query("D", pattern="^(D|W)$"),
    benchmark: str = Query("market", pattern="^(market|nifty50)$"),
    trail: int = Query(10, ge=2, le=20),
):
    if not api_enabled():
        return _disabled()
    from services.sector_rotation_engine import DAILY, WEEKLY
    from services.sector_rotation_view import build_rotation_payload

    try:
        stamp = (_stamp() or {}).get(tf) or {}
        key = f"{_CACHE_PREFIX}:{tf}:{benchmark}:{trail}:{stamp.get('date')}:{stamp.get('written_at')}"
        from dashboard.backend.cache import get as cache_get
        from dashboard.backend.cache import set as cache_set

        cached = cache_get(key)
        if isinstance(cached, dict) and cached.get("sectors") is not None:
            from services.sector_rotation_view import freshness

            cached["freshness"] = freshness(cached.get("as_of"), datetime.now(UTC))
            cached["cache"] = "hit"
            return JSONResponse(content=cached)

        from dashboard.backend.db.sector_rotation import read_recent

        accel_len = (DAILY if tf == "D" else WEEKLY).accel_len
        rows = read_recent(tf, max(trail, accel_len + 1) + accel_len)
        payload = build_rotation_payload(rows, tf=tf, bench=benchmark, trail=trail,
                                         now=datetime.now(UTC))
        payload["generated_at"] = time.time()
        if payload["sectors"]:
            cache_set(key, payload, ttl_seconds=_CACHE_TTL)
        payload["cache"] = "miss"
        return JSONResponse(content=payload)
    except Exception as exc:
        log.exception("sector rotation payload failed")
        return JSONResponse(status_code=503, content={"enabled": True, "error": "unavailable",
                                                      "detail": str(exc)[:160]})


@router.get("/api/sectors/constituents")
def sectors_constituents(sector: str = Query(..., min_length=1, max_length=64)):
    """Current-universe equities in one sector, from the weekly `stock_universe`
    snapshot. No per-stock calculation — each figure is the stored weekly value."""
    if not api_enabled():
        return _disabled()
    try:
        from dashboard.backend.db.schema import get_connection
        from services.universe_ohlc import _get_redis, _read_manifest

        manifest = _read_manifest(_get_redis(), None) or {}
        current = set((manifest.get("index") or {}).keys())
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT symbol, company_name, sector_source, price, market_cap_cr, turnover_cr, "
                "ret_1y_pct, pct_from_52w_high, refreshed_at FROM stock_universe "
                "WHERE instrument = 'EQUITY' AND sector = ? ORDER BY COALESCE(turnover_cr, 0) DESC",
                (sector,),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(r) for r in rows if not current or r["symbol"].upper() in current]
        for it in items:
            it["liquid"] = bool(it.get("turnover_cr") is not None and it["turnover_cr"] >= 1.0)
        refreshed = max((it.get("refreshed_at") or "" for it in items), default="") or None
        return JSONResponse(content={
            "enabled": True, "sector": sector, "count": len(items), "items": items,
            "refreshed_at": refreshed,
            "note": "Figures are from the weekly stock-universe refresh. 'Liquid' = average traded value "
                    "of at least ₹1 Cr/day, the threshold the sector metrics use.",
        })
    except Exception as exc:
        log.exception("sector constituents failed")
        return JSONResponse(status_code=503, content={"enabled": True, "error": "unavailable",
                                                      "detail": str(exc)[:160]})
