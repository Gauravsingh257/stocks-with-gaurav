"""
dashboard/backend/lifecycle_bus.py
==================================
In-process publish/subscribe bus for lifecycle events, plus the SSE stream that
carries them to the browser.

Replaces polling. Every module that changes a trade's state publishes here; the
Track Record page (and any other screen) subscribes to one SSE endpoint and
reacts the moment something happens, instead of re-asking every 60 seconds.

    Engine / Portfolio / Momentum
              |
              v
        publish(event)
              |
        +-----+-----+
        |           |
   SSE subscribers  future consumers (analytics, alerting)

Deliberately in-process and dependency-free: the dashboard runs as a single
web service, so a queue would add operational surface without adding delivery
guarantees we can actually use. Subscribers are bounded queues that DROP the
oldest event when a slow client backs up — a stalled browser tab must never be
able to block a trading write path. Loss is acceptable because every payload
carries the ledger version, so a client that misses an event still learns it is
stale on the next one and refetches.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_MAX_QUEUE = 64          # per-subscriber backlog before the oldest is dropped
_HEARTBEAT_SECONDS = 25  # keeps proxies from closing an idle SSE connection

_subscribers: set[asyncio.Queue] = set()
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_version = 0


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Remember the serving loop so publishes from worker THREADS (the trackers
    are plain threads, not coroutines) can hand off safely."""
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _subscribers.discard(q)


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def _deliver(payload: dict) -> None:
    with _lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # Slow consumer: drop its OLDEST event, never the newest, and never
            # block the publisher.
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                pass


def publish(event: str, **data: Any) -> None:
    """Fan out one lifecycle event. Safe from any thread; never raises."""
    global _version
    try:
        with _lock:
            _version += 1
            version = _version
        payload = {"event": event, "version": version, "ts": time.time(), **data}
        loop = _loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(_deliver, payload)
        else:
            _deliver(payload)
    except Exception:
        logger.debug("[LifecycleBus] publish failed (non-fatal)", exc_info=True)


def current_version() -> int:
    with _lock:
        return _version


async def event_stream(request=None):
    """SSE generator: one `data:` line per lifecycle event, plus heartbeats."""
    q = subscribe()
    try:
        yield f"data: {json.dumps({'event': 'CONNECTED', 'version': current_version()})}\n\n"
        while True:
            if request is not None:
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    pass
            try:
                payload = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_SECONDS)
                yield f"data: {json.dumps(payload)}\n\n"
            except asyncio.TimeoutError:
                # Comment frame — keeps the connection alive without waking the
                # client's handler.
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        raise
    finally:
        unsubscribe(q)
