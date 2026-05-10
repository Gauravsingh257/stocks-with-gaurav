# Phase B — Realtime Engine Report

## WebSocket ordering

Every outbound frame includes:

- **`stream_sequence`**: monotonic integer per Railway process (shared across snapshot, digest, LTP, ping, OI, events, watchlist_delta).
- Existing **`global_state_version`** / envelope timestamps unchanged.

Clients **reject** frames where `stream_sequence` regresses vs the last accepted value (after resetting cursor on each new `WebSocket` `open`). This blocks out-of-order replay without blanking the UI.

## LTP / index consistency

- **`lib/realtimeRegistry.ts`**: single overlay for **`index_ltp`** merged with snapshot baseline.
- **`useWebSocket`**: on **snapshot** → `replaceIndexLtpFromSnapshot`; on **digest**/**ltp** → `mergeIndexLtpOverlay`; REST **`/api/snapshot`** (poll + resync) also seeds the registry.
- **`MarketCommandBar`** and **`MarketIntelStrip`** read **`useMergedIndexLtp(snapshot?.index_ltp)`** so index prints match across strips.

## Snapshot regression

- Existing **`rejectStaleUpdate`** (GV) + **`rejectStaleSnapshotAge`** retained.
- Sequence check runs **before** GV to drop transport-level reordering.

## Observability (`GET /api/system/debug/platform`)

New **`websocket_realtime_health`**:

- `stream_sequence_current`
- `last_broadcast_stream_sequence` (from telemetry)
- `ws_event_queue_depth` (async queue from Redis → WS)
- `avg_snapshot_age_ms` (from engine snapshot `_snapshot_age_ms` when present)
- Client-only counters documented as **`null`** until an optional future ingest exists.

## Redis / Railway notes

- **`watchlist:bundle_ver:{uid}`** incremented on watchlist OS persist (trust revision).
- No localhost-only paths added.
