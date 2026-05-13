"""
Thin Watchlist OS fan-out over Redis pub/sub → WebSocket layer.

Payload contains only user_id + kind (no symbols, no prices) — clients refetch
snapshot-first GET /api/watchlist/operating when the hint matches their session.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("dashboard.watchlist_notify")

WATCHLIST_OS_PUB_CHANNEL = "watchlist_os:notify"


def publish_watchlist_os_refresh(
    user_id: int,
    *,
    kind: str = "hint",
    global_state_version: int | None = None,
    snapshot_version: int | None = None,
    event_id: str | None = None,
) -> None:
    """Notify subscribers that this user's operating snapshot was rebuilt."""
    import uuid as _uuid
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        payload: dict = {
            "user_id": int(user_id),
            "kind": str(kind),
            "event_id": event_id or str(_uuid.uuid4()),
        }
        if global_state_version is not None:
            payload["global_state_version"] = int(global_state_version)
        if snapshot_version is not None:
            payload["snapshot_version"] = int(snapshot_version)
        r.publish(WATCHLIST_OS_PUB_CHANNEL, json.dumps(payload))
    except Exception as exc:
        log.debug("watchlist_os publish skipped: %s", exc)
