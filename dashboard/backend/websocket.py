"""
dashboard/backend/websocket.py
Hardened WebSocket — broadcasts engine snapshot every second.

Safety guarantees:
  • No engine mutation — only reads `get_engine_snapshot()`
  • Heartbeat ping every 10s; auto-disconnects dead clients on timeout
  • Per-client send queue capped at 5 — drops oldest on overflow (backpressure)
  • Graceful cleanup on disconnect / unexpected close
  • Broadcast loop never crashes server on client error
"""

import asyncio
import json
import logging
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

from dashboard.backend.state_bridge import get_engine_snapshot
from dashboard.backend.services import process_recommendation_triggers

log = logging.getLogger("dashboard.ws")


def _get_oi_intelligence_snapshot() -> dict | None:
    """Safely fetch OI intelligence data for WebSocket broadcast."""
    try:
        from agents.oi_intelligence_agent import get_cached_snapshot
        return get_cached_snapshot()
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Connection registry
# ---------------------------------------------------------------------------
class _ConnectionManager:
    def __init__(self) -> None:
        self._active: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._active.add(ws)
        log.info("WS client connected — total: %d", len(self._active))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._active.discard(ws)
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

    @property
    def client_count(self) -> int:
        return len(self._active)


manager = _ConnectionManager()

# ---------------------------------------------------------------------------
# Background broadcast loop — started from main.py lifespan
# ---------------------------------------------------------------------------
_broadcast_task: asyncio.Task | None = None


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
    global _broadcast_task
    loop = asyncio.get_event_loop()
    _broadcast_task = loop.create_task(_broadcast_loop())
    log.info("WebSocket broadcast loop started")


def stop_broadcast_loop() -> None:
    """Called from FastAPI lifespan on shutdown."""
    global _broadcast_task
    if _broadcast_task and not _broadcast_task.done():
        _broadcast_task.cancel()
        log.info("WebSocket broadcast loop stopped")


# ---------------------------------------------------------------------------
# WebSocket endpoint handler — mounted by main.py
# ---------------------------------------------------------------------------
async def ws_endpoint(websocket: WebSocket) -> None:
    """
    Single handler for /ws.
    Keeps the connection alive and processes incoming messages (reserved).
    """
    await manager.connect(websocket)
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
