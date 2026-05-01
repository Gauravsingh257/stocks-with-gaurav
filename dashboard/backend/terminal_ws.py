"""
dashboard/backend/terminal_ws.py

Phase 2 — /ws/trades

Push-based real-time channel for the AI Trade Opportunity Terminal.

Wire diagram::

    engine ─► push_signal_to_redis()
              ├─► RPUSH signals:today:YYYY-MM-DD     (history)
              └─► PUBLISH terminal:signals:pub       (live fan-out)
                  PUBLISH terminal:events:pub        (discovery feed)

    fastapi ─► subscriber thread (PubSub)
              └─► asyncio.Queue ─► broadcast(json) ─► every WS client

The subscriber runs in a single daemon thread per process. The broadcast
loop is an asyncio task started from the FastAPI lifespan.

Frame envelope::

    { "type": "snapshot",   "data": { trades: [...], events: [...] } }   # initial
    { "type": "signal",     "data": <normalized_signal> }                # new signal
    { "type": "event",      "data": <discovery_event> }                  # new event
    { "type": "ping",       "ts": <epoch> }                              # keepalive
    { "type": "pong" }                                                   # client-init reply
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from dashboard.backend.terminal_events import (
    EVENTS_PUB_CHANNEL,
    SIGNALS_PUB_CHANNEL,
    get_recent_events,
    read_active_trades,
    read_today_signals,
)

log = logging.getLogger("dashboard.terminal_ws")

MAX_WS_CONNECTIONS_PER_IP = 8
PING_INTERVAL_SEC = 25
SNAPSHOT_REFRESH_SEC = 30  # safety re-broadcast even when no pub/sub events


# ─────────────────────────────────────────────────────────────────────────
# Connection manager
# ─────────────────────────────────────────────────────────────────────────

def _client_ip(ws: WebSocket) -> str:
    for name, value in ws.scope.get("headers", []):
        if name == b"x-forwarded-for":
            return value.decode("utf-8").split(",")[0].strip()
    client = ws.scope.get("client")
    return client[0] if client else "unknown"


class _Manager:
    def __init__(self) -> None:
        self._active: Set[WebSocket] = set()
        self._ip_for_ws: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> bool:
        await ws.accept()
        ip = _client_ip(ws)
        async with self._lock:
            ip_count = sum(1 for w in self._active if self._ip_for_ws.get(w) == ip)
            if ip_count >= MAX_WS_CONNECTIONS_PER_IP:
                await ws.close(code=4008)
                log.warning("/ws/trades rejected — IP %s already has %d connections", ip, ip_count)
                return False
            self._active.add(ws)
            self._ip_for_ws[ws] = ip
        log.info("/ws/trades client connected (total=%d)", len(self._active))
        return True

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._active.discard(ws)
            self._ip_for_ws.pop(ws, None)
        log.info("/ws/trades client disconnected (total=%d)", len(self._active))

    async def broadcast(self, payload: str) -> None:
        if not self._active:
            return
        dead: list[WebSocket] = []
        for ws in list(self._active):
            try:
                await asyncio.wait_for(ws.send_text(payload), timeout=2.0)
            except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
                dead.append(ws)
            except Exception as exc:
                log.warning("/ws/trades send failed: %s", exc)
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._active.discard(ws)
                    self._ip_for_ws.pop(ws, None)

    @property
    def count(self) -> int:
        return len(self._active)


_manager = _Manager()


# ─────────────────────────────────────────────────────────────────────────
# Redis subscriber  →  asyncio.Queue
# ─────────────────────────────────────────────────────────────────────────

# The subscriber runs in a regular thread (Redis PubSub.listen() is blocking).
# It hands frames to the asyncio event loop via call_soon_threadsafe so the
# broadcast loop can fan-out without blocking IO.

_subscriber_thread: Optional[threading.Thread] = None
_subscriber_stop = threading.Event()
_event_loop: Optional[asyncio.AbstractEventLoop] = None
_pending_queue: Optional[asyncio.Queue] = None
_broadcast_task: Optional[asyncio.Task] = None
_snapshot_task: Optional[asyncio.Task] = None


def _enqueue_threadsafe(frame: dict) -> None:
    if _event_loop is None or _pending_queue is None:
        return
    try:
        _event_loop.call_soon_threadsafe(_pending_queue.put_nowait, frame)
    except Exception as exc:
        log.debug("queue put failed: %s", exc)


def _subscriber_run() -> None:
    """Block on Redis PubSub.listen() and push frames into the asyncio queue."""
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
    except Exception:
        r = None
    if r is None:
        log.info("/ws/trades subscriber: Redis unavailable — push channel disabled")
        return
    try:
        pub = r.pubsub(ignore_subscribe_messages=True)
        pub.subscribe(SIGNALS_PUB_CHANNEL, EVENTS_PUB_CHANNEL)
        log.info("/ws/trades subscriber listening on %s, %s", SIGNALS_PUB_CHANNEL, EVENTS_PUB_CHANNEL)
    except Exception as exc:
        log.warning("/ws/trades subscriber init failed: %s", exc)
        return

    try:
        for msg in pub.listen():
            if _subscriber_stop.is_set():
                break
            if not msg or msg.get("type") != "message":
                continue
            channel = msg.get("channel")
            data = msg.get("data")
            if not data:
                continue
            try:
                payload = json.loads(data) if isinstance(data, (str, bytes, bytearray)) else data
            except (TypeError, ValueError):
                continue
            kind = "signal" if channel == SIGNALS_PUB_CHANNEL else "event"
            _enqueue_threadsafe({"type": kind, "data": payload})
    except Exception as exc:
        log.debug("/ws/trades subscriber exited: %s", exc)
    finally:
        try:
            pub.close()  # type: ignore[name-defined]
        except Exception:
            pass


async def _broadcast_loop() -> None:
    assert _pending_queue is not None
    while True:
        try:
            frame = await _pending_queue.get()
        except asyncio.CancelledError:
            break
        if _manager.count == 0:
            continue
        try:
            await _manager.broadcast(json.dumps(frame, default=str))
        except Exception as exc:
            log.debug("broadcast failed: %s", exc)


async def _periodic_snapshot_loop() -> None:
    """Belt-and-braces: re-emit a full snapshot every SNAPSHOT_REFRESH_SEC."""
    while True:
        try:
            await asyncio.sleep(SNAPSHOT_REFRESH_SEC)
        except asyncio.CancelledError:
            break
        if _manager.count == 0:
            continue
        try:
            payload = json.dumps({"type": "snapshot", "data": _build_snapshot()}, default=str)
            await _manager.broadcast(payload)
            await _manager.broadcast(json.dumps({"type": "ping", "ts": int(time.time())}))
        except Exception as exc:
            log.debug("snapshot loop failed: %s", exc)


def _build_snapshot() -> dict:
    """Initial payload sent to a freshly connected client."""
    try:
        active = read_active_trades()
        signals = read_today_signals()
        events = get_recent_events()
    except Exception as exc:
        log.debug("snapshot build failed: %s", exc)
        active, signals, events = [], [], []
    seen: set[str] = set()
    merged: list[dict] = []
    for t in active:
        sym = t.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(t)
    for s in reversed(signals):
        sym = s.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(s)
    return {"trades": merged, "events": events, "ts": int(time.time())}


# ─────────────────────────────────────────────────────────────────────────
# Public lifecycle hooks (called from main.py)
# ─────────────────────────────────────────────────────────────────────────

def start_terminal_ws() -> None:
    global _subscriber_thread, _event_loop, _pending_queue, _broadcast_task, _snapshot_task
    _event_loop = asyncio.get_event_loop()
    _pending_queue = asyncio.Queue(maxsize=1000)
    _broadcast_task = _event_loop.create_task(_broadcast_loop())
    _snapshot_task = _event_loop.create_task(_periodic_snapshot_loop())
    if _subscriber_thread is None or not _subscriber_thread.is_alive():
        _subscriber_stop.clear()
        _subscriber_thread = threading.Thread(
            target=_subscriber_run, daemon=True, name="terminal-ws-sub"
        )
        _subscriber_thread.start()
    log.info("/ws/trades broadcast + subscriber started")


def stop_terminal_ws() -> None:
    global _broadcast_task, _snapshot_task
    _subscriber_stop.set()
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is not None:
            r.publish(SIGNALS_PUB_CHANNEL, "{}")  # wake listener
    except Exception:
        pass
    if _broadcast_task and not _broadcast_task.done():
        _broadcast_task.cancel()
    if _snapshot_task and not _snapshot_task.done():
        _snapshot_task.cancel()


# ─────────────────────────────────────────────────────────────────────────
# WS endpoint
# ─────────────────────────────────────────────────────────────────────────

def _check_api_key(ws: WebSocket) -> bool:
    expected = os.getenv("TERMINAL_API_KEY", "").strip()
    if not expected:
        return True
    # Accept either ?api_key=... query param or x-api-key header.
    qp = ws.query_params.get("api_key", "") or ws.query_params.get("token", "")
    hdr = ""
    for name, value in ws.scope.get("headers", []):
        if name == b"x-api-key":
            hdr = value.decode("utf-8")
            break
    return (qp.strip() == expected) or (hdr.strip() == expected)


async def trades_ws_endpoint(ws: WebSocket) -> None:
    if not _check_api_key(ws):
        await ws.close(code=4401)
        return
    if not await _manager.connect(ws):
        return
    try:
        # Initial snapshot for hydrate
        await ws.send_text(json.dumps({"type": "snapshot", "data": _build_snapshot()}, default=str))

        last_ping = time.time()
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=PING_INTERVAL_SEC)
            except asyncio.TimeoutError:
                if time.time() - last_ping >= PING_INTERVAL_SEC:
                    await ws.send_text(json.dumps({"type": "ping", "ts": int(time.time())}))
                    last_ping = time.time()
                continue
            except WebSocketDisconnect:
                break
            try:
                data = json.loads(msg)
            except (TypeError, ValueError):
                continue
            if data.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
                last_ping = time.time()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("/ws/trades client error: %s", exc)
    finally:
        await _manager.disconnect(ws)
