# Phase B — Performance Report

## WebSocket payload & telemetry

- **`record_broadcast`** now stores **`last_stream_sequence`** alongside byte counts.
- Duplicate **`record_broadcast`** calls removed (single accounting path inside **`_ws_json_envelope`**).

## Ordering vs bandwidth

- **No reduction** in snapshot cadence; digest-first cadence unchanged (`WS_FULL_SNAPSHOT_EVERY_TICKS`).
- **`stream_sequence`** adds a small integer per frame — negligible vs typical snapshot JSON.

## Redis

- One **`INCR`** per watchlist OS persist: **`watchlist:bundle_ver:{uid}`** (8-byte scale).

## Client work

- **`rejectOutOfOrder`** drops stale frames without full snapshot replay (avoids churn).
- **Visibility resync** triggers one **`GET /api/snapshot`** — bounded cost.

## Suggested ops metrics (manual)

| Metric | Where |
|--------|--------|
| Broadcast rate | `websocket_telemetry.total_broadcasts_since_boot` |
| Avg frame size | `avg_broadcast_bytes` |
| Queue backlog | `websocket_realtime_health.ws_event_queue_depth` |
| Stream head | `stream_sequence_current` |

## Redis hit ratio

Unchanged in this phase (no cache policy changes).
