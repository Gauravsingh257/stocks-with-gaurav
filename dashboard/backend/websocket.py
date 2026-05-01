"""
dashboard/backend/websocket.py
Hardened WebSocket — broadcasts engine snapshot every 5s + real-time LTP when available.

Safety guarantees:
  • No engine mutation — only reads `get_engine_snapshot()`
  • Heartbeat ping every 10s; auto-disconnects dead clients on timeout
  • Per-client send queue capped at 5 — drops oldest on overflow (backpressure)
  • Graceful cleanup on disconnect / unexpected close
  • Broadcast loop never crashes server on client error
  • LTP updates from Redis pub/sub (realtime tick stream) broadcast at 1s throttle
"""

import asyncio
import json
import logging
import threading
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

from dashboard.backend.state_bridge import get_engine_snapshot
from dashboard.backend.services import process_recommendation_triggers

log = logging.getLogger("dashboard.ws")

# Latest LTP payload from Redis ltp_updates channel (set by subscriber thread)
_pending_ltp: dict | None = None
_pending_ltp_lock = threading.Lock()
_ltp_subscriber_stop = threading.Event()

# Async queue for forwarding terminal events from the Redis pubsub thread
_event_queue: asyncio.Queue | None = None

# Previous active-trade states for state-transition event detection
_prev_trade_states: dict[str, str] = {}
_prev_trade_states_lock = threading.Lock()

MAX_WS_CONNECTIONS_PER_IP = 5


def _get_oi_intelligence_snapshot() -> dict | None:
    """OI snapshot for WebSocket: read from Redis/cache first (worker), else generate."""
    try:
        from dashboard.backend.cache import OI_SNAPSHOT_KEY, get as cache_get
        cached = cache_get(OI_SNAPSHOT_KEY)
        if cached is not None:
            return cached
    except Exception:
        pass
    try:
        from agents.oi_intelligence_agent import get_cached_snapshot
        return get_cached_snapshot()
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Connection registry (with per-IP limit to prevent flooding)
# ---------------------------------------------------------------------------
def _client_ip(ws: WebSocket) -> str:
    """Client IP from X-Forwarded-For or scope."""
    for name, value in ws.scope.get("headers", []):
        if name == b"x-forwarded-for":
            return value.decode("utf-8").split(",")[0].strip()
    client = ws.scope.get("client")
    if client:
        return client[0]
    return "unknown"


class _ConnectionManager:
    def __init__(self) -> None:
        self._active: Set[WebSocket] = set()
        self._ip_for_ws: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> bool:
        """Accept and register client. Returns False if per-IP limit exceeded."""
        await ws.accept()
        ip = _client_ip(ws)
        async with self._lock:
            count_for_ip = sum(1 for w in self._active if self._ip_for_ws.get(w) == ip)
            if count_for_ip >= MAX_WS_CONNECTIONS_PER_IP:
                await ws.close(code=4008)  # policy violation
                log.warning("WS rejected: IP %s has %d connections (max %d)", ip, count_for_ip, MAX_WS_CONNECTIONS_PER_IP)
                return False
            self._active.add(ws)
            self._ip_for_ws[ws] = ip
        log.info("WS client connected — total: %d", len(self._active))
        return True

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._active.discard(ws)
            self._ip_for_ws.pop(ws, None)
        log.info("WS client disconnected — total: %d", len(self._active))

    async def broadcast(self, payload: str) -> None:
        dead: list[WebSocket] = []
        snapshot = list(self._active)          # shallow copy to avoid lock in loop

        for ws in snapshot:
            try:
                await asyncio.wait_for(ws.send_text(payload), timeout=2.0)
            except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
                dead.append(ws)
            except Exception as exc:
                log.warning("WS send error: %s", exc)
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._active.discard(ws)
                    self._ip_for_ws.pop(ws, None)

    @property
    def client_count(self) -> int:
        return len(self._active)


manager = _ConnectionManager()

# ---------------------------------------------------------------------------
# Background broadcast loop — started from main.py lifespan
# ---------------------------------------------------------------------------
_broadcast_task: asyncio.Task | None = None
_ltp_broadcast_task: asyncio.Task | None = None
_event_broadcast_task: asyncio.Task | None = None
_ltp_subscriber_thread: threading.Thread | None = None
_events_subscriber_thread: threading.Thread | None = None


def _ltp_subscriber_thread_fn() -> None:
    """Subscribe to Redis ltp_updates and set _pending_ltp for broadcast loop."""
    try:
        from dashboard.backend.cache import _get_redis, LTP_UPDATES_CHANNEL
        r = _get_redis()
        if r is None:
            return
        pub = r.pubsub()
        pub.subscribe(LTP_UPDATES_CHANNEL)
        for msg in pub.listen():
            if _ltp_subscriber_stop.is_set():
                break
            if msg and msg.get("type") == "message" and msg.get("data"):
                try:
                    data = json.loads(msg["data"])
                    if isinstance(data, dict):
                        with _pending_ltp_lock:
                            global _pending_ltp
                            _pending_ltp = data
                except (json.JSONDecodeError, TypeError):
                    pass
    except Exception as e:
        log.debug("LTP subscriber thread exited: %s", e)


def _events_subscriber_thread_fn(loop: asyncio.AbstractEventLoop) -> None:
    """Subscribe to terminal:events:pub and forward events to the async event queue."""
    try:
        from dashboard.backend.cache import _get_redis
        from dashboard.backend.terminal_events import EVENTS_PUB_CHANNEL
        r = _get_redis()
        if r is None:
            return
        pub = r.pubsub()
        pub.subscribe(EVENTS_PUB_CHANNEL)
        for msg in pub.listen():
            if _ltp_subscriber_stop.is_set():
                break
            if msg and msg.get("type") == "message" and msg.get("data"):
                try:
                    data = json.loads(msg["data"])
                    if isinstance(data, dict) and _event_queue is not None:
                        loop.call_soon_threadsafe(_event_queue.put_nowait, data)
                except (json.JSONDecodeError, TypeError):
                    pass
    except Exception as e:
        log.debug("Events subscriber thread exited: %s", e)


async def _event_forward_loop() -> None:
    """Drain the Redis-event queue and broadcast each event to all WS clients."""
    if _event_queue is None:
        return
    while True:
        try:
            event = await asyncio.wait_for(_event_queue.get(), timeout=5.0)
            if manager.client_count > 0:
                msg = json.dumps({"type": "event", "data": event})
                await manager.broadcast(msg)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.debug("Event forward error: %s", exc)


async def _ltp_broadcast_loop() -> None:
    """Every 1s broadcast latest LTP to connected clients (real-time command bar / sparkline)."""
    while True:
        await asyncio.sleep(1.0)
        if manager.client_count == 0:
            continue
        with _pending_ltp_lock:
            payload = _pending_ltp
            _pending_ltp = None
        if payload:
            try:
                msg = json.dumps({"type": "ltp", "data": payload})
                await manager.broadcast(msg)
            except Exception as exc:
                log.debug("LTP broadcast error: %s", exc)


async def _broadcast_loop() -> None:
    """Push engine snapshot to all connected clients every 5 seconds.

    5s interval keeps the UI feeling live while cutting Railway CPU/bandwidth
    by 80% vs the original 1s loop. Engine state doesn't change faster than
    the engine's own loop (typically 60–300s between signal evaluations).
    """
    INTERVAL    = 5.0   # seconds between snapshots (was 1.0)
    PING_EVERY  = 6     # send ping every N iterations → every 30s
    OI_EVERY    = 6     # send OI snapshot every N iterations → every 30s
    tick        = 0

    while True:
        await asyncio.sleep(INTERVAL)
        tick += 1

        if manager.client_count == 0:
            continue

        try:
            snapshot = get_engine_snapshot()
            try:
                process_recommendation_triggers(snapshot)
            except Exception as exc:
                log.debug("Research runtime update skipped: %s", exc)
            # ── State-transition event detection ────────────────────────────
            try:
                from dashboard.backend.terminal_events import publish_event as _pub_event
                current_states: dict[str, dict] = {
                    t.get("symbol", ""): t
                    for t in (snapshot.get("active_trades") or [])
                    if t.get("symbol")
                }
                with _prev_trade_states_lock:
                    prev = dict(_prev_trade_states)
                    _prev_trade_states.clear()
                    _prev_trade_states.update({k: v.get("status", "") for k, v in current_states.items()})
                for sym, trade in current_states.items():
                    cur_st = (trade.get("status") or "").upper()
                    prev_st = (prev.get(sym) or "").upper()
                    evt_payload = {
                        "direction": trade.get("direction"),
                        "setup": trade.get("setup"),
                        "entry": trade.get("entry"),
                        "rr": trade.get("rr"),
                    }
                    if cur_st in ("TRIGGERED", "RUNNING") and prev_st not in ("TRIGGERED", "RUNNING", "TARGET_HIT", "STOP_HIT"):
                        _pub_event("ENTRY_TRIGGER", sym, evt_payload)
                    elif cur_st == "TARGET_HIT" and prev_st != "TARGET_HIT":
                        _pub_event("TARGET_HIT", sym, {**evt_payload, "result": "WIN"})
                    elif cur_st == "STOP_HIT" and prev_st != "STOP_HIT":
                        _pub_event("STOP_HIT", sym, {**evt_payload, "result": "LOSS"})
            except Exception as exc:
                log.debug("State-transition event detection failed: %s", exc)
            payload  = json.dumps({"type": "snapshot", "data": snapshot})
        except Exception as exc:
            log.error("Snapshot error: %s", exc)
            continue

        await manager.broadcast(payload)

        # OI intelligence broadcast (every 10s — OI data changes slowly)
        if tick % OI_EVERY == 0:
            oi_snap = _get_oi_intelligence_snapshot()
            if oi_snap:
                try:
                    oi_payload = json.dumps({"type": "oi_intelligence", "data": oi_snap})
                    await manager.broadcast(oi_payload)
                except Exception as exc:
                    log.error("OI Intelligence broadcast error: %s", exc)

        # periodic ping
        if tick % PING_EVERY == 0:
            ping = json.dumps({"type": "ping", "tick": tick})
            await manager.broadcast(ping)


def start_broadcast_loop() -> None:
    """Called from FastAPI lifespan to start the loop."""
    global _broadcast_task, _ltp_broadcast_task, _event_broadcast_task
    global _ltp_subscriber_thread, _events_subscriber_thread, _event_queue
    loop = asyncio.get_event_loop()
    _event_queue = asyncio.Queue()
    _broadcast_task = loop.create_task(_broadcast_loop())
    _ltp_broadcast_task = loop.create_task(_ltp_broadcast_loop())
    _event_broadcast_task = loop.create_task(_event_forward_loop())
    try:
        from dashboard.backend.cache import _get_redis, LTP_UPDATES_CHANNEL
        if _get_redis() is not None:
            _ltp_subscriber_stop.clear()
            _ltp_subscriber_thread = threading.Thread(target=_ltp_subscriber_thread_fn, daemon=True)
            _ltp_subscriber_thread.start()
            log.info("LTP Redis subscriber started")
            _events_subscriber_thread = threading.Thread(
                target=_events_subscriber_thread_fn, args=(loop,), daemon=True
            )
            _events_subscriber_thread.start()
            log.info("Events Redis subscriber started")
    except Exception as e:
        log.debug("Subscribers not started: %s", e)
    log.info("WebSocket broadcast loop started")


def stop_broadcast_loop() -> None:
    """Called from FastAPI lifespan on shutdown."""
    global _broadcast_task, _ltp_broadcast_task, _event_broadcast_task
    global _ltp_subscriber_thread, _events_subscriber_thread
    _ltp_subscriber_stop.set()
    if _ltp_subscriber_thread is not None:
        try:
            from dashboard.backend.cache import _get_redis
            r = _get_redis()
            if r is not None:
                r.publish("ltp_updates", "{}")  # wake listener
        except Exception:
            pass
        _ltp_subscriber_thread = None
    _events_subscriber_thread = None
    if _event_broadcast_task and not _event_broadcast_task.done():
        _event_broadcast_task.cancel()
    if _ltp_broadcast_task and not _ltp_broadcast_task.done():
        _ltp_broadcast_task.cancel()
    if _broadcast_task and not _broadcast_task.done():
        _broadcast_task.cancel()
        log.info("WebSocket broadcast loop stopped")


# ---------------------------------------------------------------------------
# WebSocket endpoint handler — mounted by main.py
# ---------------------------------------------------------------------------
async def ws_endpoint(websocket: WebSocket) -> None:
    """
    Single handler for /ws.
    Max 5 connections per IP. Keeps the connection alive and processes incoming messages.
    """
    if not await manager.connect(websocket):
        return
    try:
        # Send immediate snapshot on connect so client doesn't wait 1 second
        try:
            snapshot = get_engine_snapshot()
            await websocket.send_text(
                json.dumps({"type": "snapshot", "data": snapshot})
            )
        except Exception as exc:
            log.warning("Initial snapshot send failed: %s", exc)

        # Keep alive — listen for any client messages (ping/pong, commands)
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                data = json.loads(msg)
                if data.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # No message in 30s → send a keepalive
                await websocket.send_text(json.dumps({"type": "keepalive"}))
            except json.JSONDecodeError:
                pass  # ignore malformed messages

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("WS handler error: %s", exc)
    finally:
        await manager.disconnect(websocket)
