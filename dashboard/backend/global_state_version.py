"""
Unified global state version for snapshot-first orchestration (Railway + Redis).

Canonical counter: snapshot:global_state_version
Legacy mirror:   snapshot:global_version (must stay equal for existing readers)

All major bundle commits should use bump_unified_global_version() so both keys advance together.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger("dashboard.global_state_version")

KEY_GLOBAL_STATE_VERSION = "snapshot:global_state_version"
KEY_GLOBAL_VERSION_LEGACY = "snapshot:global_version"
KEY_LAST_BUMP_META = "snapshot:global_state_bump_meta"


def _get_redis():
    try:
        from dashboard.backend.cache import _get_redis as _gr

        return _gr()
    except Exception:
        return None


def _read_legacy_global_version(r) -> int:
    try:
        raw = r.get(KEY_GLOBAL_VERSION_LEGACY)
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


def read_global_state_version() -> int:
    """Return current unified global state version (0 if Redis unavailable)."""
    r = _get_redis()
    if r is None:
        return 0
    try:
        raw = r.get(KEY_GLOBAL_STATE_VERSION)
        if raw is not None:
            return int(raw)
        return _read_legacy_global_version(r)
    except Exception:
        return 0


def bump_unified_global_version(r, origin: str) -> int:
    """
    INCR global_state_version; mirror the same integer to global_version.
    Seeds new counter from legacy global_version once so we never roll backward.
    """
    try:
        if r.get(KEY_GLOBAL_STATE_VERSION) is None:
            seed = _read_legacy_global_version(r)
            if seed > 0:
                r.set(KEY_GLOBAL_STATE_VERSION, seed)
        v = int(r.incr(KEY_GLOBAL_STATE_VERSION))
        r.set(KEY_GLOBAL_VERSION_LEGACY, str(v))
        meta = {
            "last_origin": str(origin)[:120],
            "last_ts": time.time(),
            "version": v,
        }
        r.setex(KEY_LAST_BUMP_META, 86400, json.dumps(meta, default=str))
        return v
    except Exception as e:
        log.debug("bump_unified_global_version failed: %s", e)
        return 0


def bump_global_state_version(origin: str) -> int:
    """Increment unified version from application code (watchlist rebuild, etc.)."""
    r = _get_redis()
    if r is None:
        return 0
    return bump_unified_global_version(r, origin)


def _parse_iso_age_ms(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts or not isinstance(iso_ts, str):
        return None
    try:
        raw = iso_ts.replace("Z", "+00:00")
        if "+" not in raw and raw.count("-") >= 3:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw)
        now = datetime.now(dt.tzinfo or timezone.utc)
        return max(0.0, (now - dt).total_seconds() * 1000.0)
    except Exception:
        return None


def attach_snapshot_meta(
    payload: Dict[str, Any],
    *,
    origin: str,
    reference_iso_for_age: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Attach consistent envelope fields for API / Redis-backed snapshots.

    Does not mutate external schemas beyond additive underscore keys.
    Age uses reference_iso_for_age (e.g. engine snapshot_time) when provided.
    Falls back to payload.snapshot_time, then to 0ms (just-built scaffold).
    _snapshot_age_ms is always a number — never None — so frontend stale rejection
    can always make a real decision rather than silently bypassing the check.
    """
    gv = read_global_state_version()
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    ref = (
        reference_iso_for_age
        if reference_iso_for_age is not None
        else payload.get("snapshot_time")
    )
    if ref:
        age_ms = _parse_iso_age_ms(str(ref))
    else:
        # No reference timestamp at all → this is a freshly-built scaffold served right now.
        # Report age=0 so the client knows it's not stale data being replayed.
        age_ms = 0.0

    payload["_global_state_version"] = gv
    payload["_snapshot_ts"] = now_iso
    payload["_snapshot_origin"] = origin
    payload["_snapshot_age_ms"] = round(age_ms, 1) if age_ms is not None else 0.0
    return payload
