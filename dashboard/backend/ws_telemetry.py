"""WebSocket broadcast telemetry (in-process)."""

from __future__ import annotations

from typing import Any, Dict

_last_broadcast_bytes: int = 0
_last_broadcast_type: str = ""
_total_broadcasts: int = 0


def record_broadcast(payload_utf8_len: int, msg_type: str) -> None:
    global _last_broadcast_bytes, _last_broadcast_type, _total_broadcasts
    _last_broadcast_bytes = payload_utf8_len
    _last_broadcast_type = msg_type
    _total_broadcasts += 1


def get_ws_telemetry() -> Dict[str, Any]:
    return {
        "last_broadcast_bytes": _last_broadcast_bytes,
        "last_broadcast_type": _last_broadcast_type,
        "total_broadcasts_since_boot": _total_broadcasts,
    }
