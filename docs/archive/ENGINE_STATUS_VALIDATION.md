# Engine status validation — 9 AM / market hours vs 24/7

## Summary: status is **not** gated by 9 AM

- **Engine status** (running / stale / offline) is based **only on Redis heartbeat**, 24/7.
- **Heartbeat** is written every 30 seconds by a **dedicated thread** in the engine, regardless of market open/closed.
- So if the engine process is running and connected to Redis, the dashboard should show **running** or **stale** even outside 9–15:30.

---

## What *is* time-based (9:15–15:30 IST)

| Item | When it runs | Purpose |
|------|----------------|--------|
| **Main scan loop** (signals, scans) | Market hours only | `is_market_open()`; when closed, loop sleeps 5 min and `continue` |
| **engine_last_cycle** (Redis) | Only when a full scan cycle completes | So "Last signal cycle" is only updated during market hours |
| **Telegram "Engine Alive" ping** | Market hours only | F4.5 heartbeat message every 30 min (in-loop) |
| **Lock refresh** | Every 2 min, 24/7 | In main loop but before market guard, so runs even when market closed |

---

## What runs 24/7 (no 9 AM check)

| Item | Where | Purpose |
|------|--------|--------|
| **Redis heartbeat** | `engine_runtime._heartbeat_loop()` (daemon thread) | Dashboard uses this for engine_live / engine_running |
| **Redis lock** | Main loop, every 2 min | Single-instance guarantee |
| **engine_started_at** | Set once at lock acquire | Uptime display |

---

## Why you might see "offline" outside market hours

If the dashboard shows **engine_status: offline** when the engine is deployed:

1. **Heartbeat not in Redis** — Engine may have crashed, or not started the heartbeat thread (e.g. failed before `start_heartbeat_thread()`), or engine and dashboard use different Redis.
2. **Heartbeat older than 120s** — Engine process stopped or lost Redis connection.

It is **not** because of 9 AM logic: the status logic does not use time of day.

---

## "Last signal cycle" age when market is closed

- **engine_last_cycle** is only updated when the main loop completes a full iteration (including `write_last_cycle()`).
- When market is closed, the loop does `sleep(300); continue` and never reaches `write_last_cycle()`.
- So outside market hours, **engine_last_cycle_age_sec** can be large (e.g. "12 hours ago"). That is expected and does **not** set status to offline; status is heartbeat-based only.

---

## Code references

- Heartbeat thread: `engine_runtime.start_heartbeat_thread()` → `_heartbeat_loop()` → `write_heartbeat()` every 30s (`smc_mtf_engine_v4.py` calls this right after lock acquire).
- Status from heartbeat: `state_bridge.get_engine_snapshot()` → Redis `engine_heartbeat` → `engine_live` / `engine_running`; `system.py` → `engine_status` = running | stale | offline.
- Last cycle only in market: `write_last_cycle()` called after `save_engine_states()` in main loop; when `not is_market_open()` the loop `continue`s before that.
