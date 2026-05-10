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


def publish_watchlist_os_refresh(user_id: int, *, kind: str = "hint") -> None:
    """Notify subscribers that this user's operating snapshot was rebuilt."""
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        r.publish(
            WATCHLIST_OS_PUB_CHANNEL,
            json.dumps({"user_id": int(user_id), "kind": str(kind)}),
        )
    except Exception as exc:
        log.debug("watchlist_os publish skipped: %s", exc)
