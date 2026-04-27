"""
dashboard/backend/redis_endpoint_cache.py

Deterministic JSON responses for dashboard API endpoints:
  - Atomic Redis writes (live + last_known_good)
  - Validation gate (skip overwrite on bad payloads)
  - Fallback to last_known_good with snapshot_stale / snapshot_source flags

Works across multiple Railway web instances (Redis as single source of truth).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Callable

log = logging.getLogger("dashboard.redis_endpoint_cache")

LIVE_PREFIX = "snapshot:endpoint:"
LKG_PREFIX = "snapshot:last_known_good:"
META_PREFIX = "snapshot:endpoint_meta:"
STATUS_PREFIX = "snapshot:endpoint_status:"
SIZE_PREFIX = "snapshot:endpoint_size:"

LIVE_TTL_SEC = int(os.getenv("ENDPOINT_SNAPSHOT_LIVE_TTL_SEC", "600"))
LKG_TTL_SEC = int(os.getenv("ENDPOINT_SNAPSHOT_LKG_TTL_SEC", "86400"))

# Mirrors for discovery buckets (human-readable keys)
KEY_DISCOVERY_FULL = "snapshot:discovery"
KEY_WATCHLIST = "snapshot:watchlist"
KEY_FINAL = "snapshot:final"


def _get_redis():
    try:
        from dashboard.backend.cache import _get_redis as _gr
        return _gr()
    except Exception:
        return None


def slug_from_params(prefix: str, **params: Any) -> str:
    """Stable short slug from sorted params."""
    canonical = json.dumps(params, sort_keys=True, default=str)
    h = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"{prefix}:{h}"


def _live_key(slug: str) -> str:
    return f"{LIVE_PREFIX}{slug}"


def _lkg_key(slug: str) -> str:
    return f"{LKG_PREFIX}{slug}"


def load_last_known_good(slug: str) -> dict | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(_lkg_key(slug))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def store_endpoint_snapshot(slug: str, payload: dict) -> None:
    """Persist validated payload to Redis (atomic pipeline)."""
    r = _get_redis()
    if r is None:
        return
    try:
        now = time.time()
        enriched = dict(payload)
        enriched["_endpoint_written_at"] = now
        enriched["_endpoint_slug"] = slug
        raw = json.dumps(enriched, default=str)
        size_b = len(raw.encode("utf-8"))
        meta = {"slug": slug, "ts": now, "size_bytes": size_b, "ok": True}

        pipe = r.pipeline(transaction=True)
        pipe.setex(_live_key(slug), LIVE_TTL_SEC, raw)
        pipe.setex(_lkg_key(slug), LKG_TTL_SEC, raw)
        pipe.setex(f"{META_PREFIX}{slug}", min(LIVE_TTL_SEC, 3600), json.dumps(meta, default=str))
        pipe.setex(f"{STATUS_PREFIX}{slug}", min(LIVE_TTL_SEC, 3600), "success")
        pipe.setex(f"{SIZE_PREFIX}{slug}", min(LIVE_TTL_SEC, 3600), str(size_b))
        pipe.execute()
    except Exception as e:
        log.debug("store_endpoint_snapshot failed %s: %s", slug, e)


def _mirror_discovery_slices(r, pipe, payload: dict, ttl: int) -> None:
    """Mirror discovery buckets to fixed keys for debugging / optional fast reads."""
    try:
        wt = payload.get("watchlist") if isinstance(payload.get("watchlist"), list) else []
        fn = payload.get("final_trades") if isinstance(payload.get("final_trades"), list) else []
        pipe.setex(KEY_DISCOVERY_FULL, ttl, json.dumps(payload, default=str))
        pipe.setex(KEY_WATCHLIST, ttl, json.dumps(wt, default=str))
        pipe.setex(KEY_FINAL, ttl, json.dumps(fn, default=str))
    except Exception:
        pass


def finalize_endpoint(
    slug: str,
    payload: dict | None,
    is_valid: Callable[[dict], bool],
    *,
    mirror_discovery: bool = False,
) -> dict:
    """
    If payload is valid → store live + LKG, return with snapshot_stale=False.
    Else → return last_known_good if present with snapshot_stale=True.
    Else → return original payload with snapshot_stale=True (no fallback).
    """
    if payload is None:
        payload = {}

    out = dict(payload)

    if isinstance(payload, dict) and is_valid(payload):
        store_endpoint_snapshot(slug, payload)
        if mirror_discovery:
            r = _get_redis()
            if r is not None:
                try:
                    pipe = r.pipeline(transaction=True)
                    _mirror_discovery_slices(r, pipe, payload, LIVE_TTL_SEC)
                    pipe.execute()
                except Exception:
                    pass
        out["snapshot_stale"] = False
        out["snapshot_source"] = "live"
        out.pop("snapshot_stale_reason", None)
        return out

    fallback = load_last_known_good(slug)
    if fallback is not None:
        fb = dict(fallback)
        fb["snapshot_stale"] = True
        fb["snapshot_source"] = "last_known_good"
        fb["snapshot_stale_reason"] = "invalid_or_empty_live_payload"
        fb["_requested_slug"] = slug
        return fb

    out["snapshot_stale"] = True
    out["snapshot_source"] = "live_no_fallback"
    out["snapshot_stale_reason"] = "invalid_live_no_cached_fallback"
    out["_requested_slug"] = slug
    return out


# --- Validators -------------------------------------------------------------

def valid_discovery_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    if p.get("scan_id") == "timeout":
        return False
    if p.get("error"):
        return False
    for k in ("items", "final_trades", "watchlist", "discovery"):
        if k not in p:
            return False
    return True


def valid_research_list_payload(p: dict) -> bool:
    """Swing / longterm list endpoints."""
    if not isinstance(p, dict):
        return False
    if not isinstance(p.get("items"), list):
        return False
    c = p.get("count", len(p.get("items") or []))
    return isinstance(c, (int, float))


def valid_running_trades_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    return isinstance(p.get("items"), list)


def valid_coverage_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    return "target_universe" in p and "latest" in p


def valid_layer_report_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    if p.get("available") is False:
        return False
    return True


def valid_performance_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    return "total_recommendations" in p


def valid_validation_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    sid = p.get("scan_id")
    if not sid:
        return False
    return isinstance(p.get("items"), list)


def valid_oi_payload(p: dict) -> bool:
    if not isinstance(p, dict) or not p:
        return False
    return True


def valid_agents_status_payload(p: dict) -> bool:
    return isinstance(p, dict) and isinstance(p.get("agents"), list)


def valid_scan_status_payload(p: dict) -> bool:
    return isinstance(p, dict) and "horizons" in p and "in_flight" in p


def valid_portfolio_summary_payload(p: dict) -> bool:
    return isinstance(p, dict) and "swing" in p and "longterm" in p


def endpoint_debug_inventory() -> dict[str, Any]:
    """Collect TTL/status for all snapshot:endpoint:* keys."""
    r = _get_redis()
    if r is None:
        return {"available": False}
    out: dict[str, Any] = {"available": True, "endpoints": {}}
    keys: list[str] = []
    try:
        cur = 0
        while True:
            cur, batch = r.scan(cursor=cur, match=f"{LIVE_PREFIX}*", count=100)
            keys.extend(batch if isinstance(batch, list) else [])
            if cur == 0:
                break
    except Exception:
        try:
            keys = list(r.keys(f"{LIVE_PREFIX}*"))
        except Exception:
            keys = []

    try:
        for key in keys:
            try:
                ttl = r.ttl(key)
                slug = key.replace(LIVE_PREFIX, "", 1)
                size_k = f"{SIZE_PREFIX}{slug}"
                st_k = f"{STATUS_PREFIX}{slug}"
                meta_k = f"{META_PREFIX}{slug}"
                meta_raw = r.get(meta_k)
                meta = json.loads(meta_raw) if meta_raw else None
                out["endpoints"][slug] = {
                    "live_key": key,
                    "ttl_sec": ttl if ttl >= 0 else None,
                    "exists": ttl != -2,
                    "size_bytes": r.get(size_k),
                    "status": r.get(st_k),
                    "meta": meta,
                }
            except Exception:
                continue
    except Exception as e:
        out["error"] = str(e)

    # Fixed mirror keys
    for label, rk in (
        ("discovery_full", KEY_DISCOVERY_FULL),
        ("watchlist", KEY_WATCHLIST),
        ("final_trades", KEY_FINAL),
    ):
        try:
            ttl = r.ttl(rk)
            out.setdefault("mirrors", {})[label] = {"key": rk, "ttl_sec": ttl if ttl >= 0 else None, "exists": ttl != -2}
        except Exception:
            pass

    return out
