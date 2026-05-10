"""WebSocket broadcast telemetry (in-process)."""

from __future__ import annotations

import time
from typing import Any, Dict

_last_broadcast_bytes: int = 0
_last_broadcast_type: str = ""
_total_broadcasts: int = 0
_last_broadcast_mono: float = 0.0
_bytes_sum: int = 0
_connections_accepted: int = 0
_last_stream_sequence: int = 0


def record_broadcast(
    payload_utf8_len: int, msg_type: str, stream_sequence: int | None = None
) -> None:
    global _last_broadcast_bytes, _last_broadcast_type, _total_broadcasts
    global _last_broadcast_mono, _bytes_sum, _last_stream_sequence
    _last_broadcast_bytes = payload_utf8_len
    _last_broadcast_type = msg_type
    _total_broadcasts += 1
    _bytes_sum += payload_utf8_len
    _last_broadcast_mono = time.monotonic()
    if stream_sequence is not None:
        _last_stream_sequence = int(stream_sequence)


def record_ws_connection_accepted() -> None:
    global _connections_accepted
    _connections_accepted += 1


def get_ws_telemetry() -> Dict[str, Any]:
    now = time.monotonic()
    age_ms = None
    if _last_broadcast_mono > 0:
        age_ms = round((now - _last_broadcast_mono) * 1000, 1)
    avg_b = None
    if _total_broadcasts > 0:
        avg_b = round(_bytes_sum / _total_broadcasts, 1)
    return {
        "last_broadcast_bytes": _last_broadcast_bytes,
        "last_broadcast_type": _last_broadcast_type,
        "total_broadcasts_since_boot": _total_broadcasts,
        "connections_accepted_since_boot": _connections_accepted,
        "avg_broadcast_bytes": avg_b,
        "last_broadcast_age_ms": age_ms,
        "last_stream_sequence": _last_stream_sequence,
    }
