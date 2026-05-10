# Phase B — Watchlist Trust Report

## Summary

Hardening applies **one canonical server pipeline** (SQLite symbols → `build_operating_payload` → Redis live/LKG → GET `/api/watchlist/operating`) with explicit **trust metadata** and **feed de-duplication**.

## Synchronization audit

| Layer | Role |
|--------|------|
| SQLite `user_watchlist` | Authoritative symbol list |
| Redis `snapshot:watchlist_operating:{uid}` | Live TTL snapshot |
| Redis `snapshot:last_known_good:watchlist_operating:{uid}` | Fallback when live missing |
| WS `watchlist_delta` | Thin hint → client refetches operating snapshot |
| Frontend | Debounced refetch on hint; optimistic remove with rollback |

## Stale-state / race mitigation

- **`_trust` envelope** on operating payload: `schema`, `built_at_ms`, `engine_snapshot_time`, `symbol_count`, and **`bundle_revision`** (Redis `INCR watchlist:bundle_ver:{uid}` on each successful persist). Clients can compare revisions across tabs/refetches.
- Global **`_global_state_version`** remains on the payload via `attach_snapshot_meta`.
- Add/remove still calls **`invalidate_watchlist_os_cache` + `_refresh_watchlist_os`** (existing path).

## Feed stability

- **`append_feed_diff`**: after merging new events into history, **`_collapse_adjacent_duplicate_feed_events`** removes consecutive identical `(symbol, headline)` rows to reduce replay spam.

## Watchlist UI

- **Remove**: optimistic row removal + **`savedSymbols`** sync; rollback + message on API failure; **`loadOperating()`** after success for canonical reconciliation.

## Remaining risks (monitor)

- Multi-tab: both tabs refetch on WS hint — acceptable; revision fields help detect divergence.
- Very fast add/remove: rely on debounced WS hint + manual Sync button.
