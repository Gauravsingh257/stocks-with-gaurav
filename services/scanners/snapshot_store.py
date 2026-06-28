"""
services/scanners/snapshot_store.py — Redis snapshot read/write for scanners.

Shared by the producer (scripts/scanner_cron.py, writes) and the consumer
(dashboard/backend/routes/screeners.py, reads). Mirrors the proven
redis_endpoint_cache pattern (non-empty write gate + last-known-good fallback)
but with scanner-appropriate TTLs and an isolated `scanner:*` key namespace so
it can NEVER collide with the engine's discovery/watchlist snapshots.

Keys:
  scanner:{name}:{tf}          live result  (TTL ~25h)
  scanner:{name}:{tf}:lkg      last-known-good fallback (TTL 7d)
  scanner:index                catalog of available scanners + computed_at (TTL 25h)

A write is REFUSED (keeping the previous good snapshot) when the new payload has
zero rows AND an error/empty marker — exactly like the engine's discovery write
gate. A scan that legitimately finds zero matches still writes (with empty rows)
only when it ran cleanly (no error); that is controlled by the caller via the
`allow_empty` flag on write_snapshot.
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("services.scanners.snapshot_store")

LIVE_TTL_SEC = int(os.getenv("SCANNER_LIVE_TTL_SEC", str(25 * 3600)))   # ~25h
LKG_TTL_SEC = int(os.getenv("SCANNER_LKG_TTL_SEC", str(7 * 86400)))     # 7d
INDEX_KEY = "scanner:index"


def _get_redis():
    """Reuse the dashboard cache Redis client when available (web side); else
    connect directly from REDIS_URL (cron-worker side). Returns None if no Redis."""
    # Web backend path — shares the pooled client + reconnect logic.
    try:
        from dashboard.backend.cache import _get_redis as _gr
        r = _gr()
        if r is not None:
            return r
    except Exception:
        pass
    # Standalone (cron worker) path.
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis as _redis_lib
        client = _redis_lib.from_url(url, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:
        log.warning("scanner snapshot_store: redis unavailable (%s)", exc)
        return None


def live_key(name: str, tf: str) -> str:
    return f"scanner:{name}:{tf}"


def lkg_key(name: str, tf: str) -> str:
    return f"scanner:{name}:{tf}:lkg"


def _rows_of(payload: dict | None) -> list:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    return rows if isinstance(rows, list) else []


def write_snapshot(name: str, tf: str, payload: dict, *, allow_empty: bool = True) -> bool:
    """Persist a scanner result. Returns True if written, False if the write was
    refused (and the previous good snapshot was preserved).

    Write gate:
      - reject if payload is not a dict or carries payload["error"];
      - reject empty-rows payloads when allow_empty is False;
      - otherwise write live + LKG atomically (pipeline).
    """
    r = _get_redis()
    if r is None:
        log.warning("scanner write skipped (no redis) %s:%s", name, tf)
        return False
    if not isinstance(payload, dict) or payload.get("error"):
        log.warning("scanner write refused (invalid/error payload) %s:%s", name, tf)
        return False

    rows = _rows_of(payload)
    if not rows and not allow_empty:
        log.info("scanner write refused (empty rows, allow_empty=False) %s:%s", name, tf)
        return False

    now = time.time()
    enriched = dict(payload)
    enriched["scanner"] = name
    enriched["timeframe"] = tf
    enriched["_written_at"] = now
    enriched.setdefault("hits", len(rows))
    raw = json.dumps(enriched, default=str)

    try:
        pipe = r.pipeline(transaction=True)
        pipe.setex(live_key(name, tf), LIVE_TTL_SEC, raw)
        # Only refresh LKG when we actually have rows — never let an empty clean
        # scan erase the last good non-empty fallback.
        if rows:
            pipe.setex(lkg_key(name, tf), LKG_TTL_SEC, raw)
        pipe.execute()
        return True
    except Exception as exc:
        log.warning("scanner write failed %s:%s (%s)", name, tf, exc)
        return False


def read_snapshot(name: str, tf: str) -> dict | None:
    """Prefer live snapshot; fall back to last-known-good (stamped stale).

    Returns None only when neither live nor LKG exists (or Redis is down).
    """
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(live_key(name, tf))
        if raw:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                obj.setdefault("snapshot_source", "live")
                obj["snapshot_stale"] = False
                return obj
    except Exception as exc:
        log.debug("scanner live read failed %s:%s (%s)", name, tf, exc)
    # Fallback
    try:
        raw = r.get(lkg_key(name, tf))
        if raw:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                obj["snapshot_source"] = "last_known_good"
                obj["snapshot_stale"] = True
                obj.setdefault("snapshot_stale_reason", "live_snapshot_expired_or_missing")
                return obj
    except Exception as exc:
        log.debug("scanner lkg read failed %s:%s (%s)", name, tf, exc)
    return None


def write_index(entries: list[dict]) -> bool:
    """Write the catalog of available scanners (name, tf, label, computed_at, hits)."""
    r = _get_redis()
    if r is None:
        return False
    try:
        r.setex(INDEX_KEY, LIVE_TTL_SEC, json.dumps({"scanners": entries, "_written_at": time.time()}, default=str))
        return True
    except Exception as exc:
        log.warning("scanner index write failed (%s)", exc)
        return False


def read_index() -> dict | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(INDEX_KEY)
        if raw:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
    except Exception:
        return None
    return None
