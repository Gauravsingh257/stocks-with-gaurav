# Cloud Deployment Audit — Trading Platform

**Purpose:** Verify the system is fully cloud-hosted and runs independently of the developer's laptop.

**Platform:** Railway (Dashboard API + Engine worker) + shared Redis.

---

## Quick verdict (4 steps)

1. **Run the audit script:**
   ```bash
   export BACKEND_URL="https://YOUR-RAILWAY-URL"
   export REDIS_URL="redis://YOUR-REDIS"
   python scripts/audit_cloud_deployment.py
   ```

2. **Check these values in the output:**
   - `engine_heartbeat_age_sec` < 60  
   - `engine_last_cycle_age_sec` < 60  
   - API endpoints return 200  
   - Website status = live  

3. **If all above are OK**  
   → **SYSTEM FULLY CLOUD HOSTED — You can turn off your laptop safely.**

4. **If heartbeat stops or API fails**  
   → **SYSTEM DEPENDS ON LOCAL MACHINE — Laptop must stay on.**

The script prints **SYSTEM FULLY CLOUD HOSTED** or **SYSTEM DEPENDS ON LOCAL MACHINE** at the end based on these criteria.

---

## How to Run the Audit

### Automated checks (API + Redis)

```bash
# Set your public backend URL (and Redis URL if you have it)
export BACKEND_URL="https://YOUR-SERVICE.up.railway.app"
export REDIS_URL="redis://default:xxx@xxx.railway.app:port"

# Run audit script (requires: requests, redis)
python scripts/audit_cloud_deployment.py

# JSON report
python scripts/audit_cloud_deployment.py --json > audit_report.json
```

### Manual checks

Use the sections below to verify Railway dashboard, logs, Telegram, and WebSocket. Fill in the **Results** and **Final report** at the end.

---

## CHECK 1 — Website Deployment

**Verify:** Dashboard is live and API returns valid JSON.

| Test | How | Result |
|------|-----|--------|
| Public URL opens | Open frontend URL (e.g. Vercel or custom domain) in browser | ☐ 200 / ☐ Fail |
| Frontend loads | Confirm page renders without errors | ☐ OK / ☐ Fail |
| GET /health | `curl -s -o /dev/null -w "%{http_code}" $BACKEND_URL/health` | _____ |
| GET /api/system/health | `curl -s $BACKEND_URL/api/system/health \| jq .` | ☐ Valid JSON |
| GET /api/snapshot | `curl -s $BACKEND_URL/api/snapshot \| jq .engine_version` | ☐ Valid JSON |
| GET /api/agents/oi-intelligence | `curl -s $BACKEND_URL/api/agents/oi-intelligence \| jq .` | ☐ Valid JSON |

**Notes:**

---

## CHECK 2 — Railway Services

**Verify:** Both services are running with correct config.

| Item | Where | Result |
|------|--------|--------|
| Dashboard service status | Railway → Dashboard API service | ☐ Running |
| Engine worker service status | Railway → Engine worker service | ☐ Running |
| Restart policy | Deploy settings | ☐ ON_FAILURE (or equivalent) |
| Engine replicas | Engine service → Scaling | ☐ 1 replica |

**Engine worker logs** — confirm presence of:

- [ ] `Engine lock acquired (Redis)`
- [ ] `Heartbeat thread started`
- [ ] `Engine started with Redis lock` or `Engine started successfully`

**Notes:**

---

## CHECK 3 — Redis Monitoring

**Verify:** Required keys exist and update as expected.

Run (with Redis CLI or script):

```bash
# If you have redis-cli with REDIS_URL
redis-cli -u "$REDIS_URL" GET engine_lock
redis-cli -u "$REDIS_URL" GET engine_heartbeat
redis-cli -u "$REDIS_URL" GET engine_started_at
redis-cli -u "$REDIS_URL" GET engine_version
redis-cli -u "$REDIS_URL" GET engine_last_cycle
```

Or use the audit script (it computes ages):

```bash
python scripts/audit_cloud_deployment.py --backend $BACKEND_URL --redis $REDIS_URL
```

| Key | Exists | Updates | Result |
|-----|--------|---------|--------|
| engine_lock | ☐ Yes ☐ No | TTL ~600s, refresh ~120s | _____ |
| engine_heartbeat | ☐ Yes ☐ No | ~every 30s | _____ |
| engine_started_at | ☐ Yes ☐ No | Set at lock acquire | _____ |
| engine_version | ☐ Yes ☐ No | Set at lock acquire | _____ |
| engine_last_cycle | ☐ Yes ☐ No | After each scan cycle | _____ |

**Computed:**

- engine_heartbeat_age_sec: _____ (expected &lt; 60 when running)
- engine_last_cycle_age_sec: _____ (expected &lt; 60 when running)

**Notes:**

---

## CHECK 4 — Engine Lock Safety

**Verify:** Only one engine instance holds the lock.

| Test | How | Result |
|------|-----|--------|
| Lock exists | Redis GET engine_lock | ☐ Present |
| TTL refreshes | Check TTL twice ~2 min apart (e.g. Redis TTL command) | ☐ Refreshes ~120s |
| No second holder | Do not start a second engine; confirm only one PID in lock value | ☐ Single PID |

**Notes:**

---

## CHECK 5 — Engine Independence From Laptop

**Verify:** Engine runs in Railway only; no dependency on local machine.

| Evidence | Result |
|----------|--------|
| Railway worker container is running | ☐ Yes |
| Redis heartbeat keeps updating with local scripts stopped | ☐ Yes |
| No localhost required for engine (engine uses REDIS_URL, Kite, Telegram from env) | ☐ Confirmed |

**Conclusion:** Will the system continue running if the developer turns off their laptop?

- [ ] **Yes** — Engine and API run on Railway; Redis is cloud-hosted.
- [ ] **No** — Some component still depends on local machine (describe below).

**Notes:**

---

## CHECK 6 — Telegram Signal Pipeline

**Verify:** Signals are generated by the engine worker and delivered via Telegram.

| Test | Result |
|------|--------|
| Signal generated by engine (check Railway engine logs) | ☐ Observed |
| Signal dedupe in place (no duplicate alerts on restart/retry) | ☐ OK |
| Telegram message delivered | ☐ OK |

**Log phrases to look for:** `Signal generated`, `Signal dedupe`, `Telegram send` (or equivalent in your logging).

**Notes:**

---

## CHECK 7 — WebSocket / Live Data

**Verify:** Dashboard gets live data without local processes.

| Test | Result |
|------|--------|
| WebSocket connection established (browser dev tools → Network → WS) | ☐ OK |
| Snapshot updates arriving (e.g. live page updates) | ☐ OK |
| Index LTP / sparkline updates | ☐ OK |
| Frontend updates without any local script running | ☐ OK |

**Notes:**

---

## CHECK 8 — Crash Recovery

**Verify:** Worker restarts and lock is safe.

| Test | How | Result |
|------|-----|--------|
| Worker restarts on crash | Trigger restart or redeploy; check logs | ☐ Restarts |
| Redis lock expires if worker dies (TTL 600s) | After crash, lock key should expire | ☐ Expires |
| Engine reacquires lock on restart | After deploy/restart, logs show lock acquired | ☐ Reacquires |

**Notes:**

---

## Final Report

**Date:** _______________  
**Audited by:** _______________  
**Backend URL:** _______________  
**Frontend URL:** _______________

### 1. Website status

- [ ] Live (HTTP 200, frontend and API respond)
- [ ] Not live / Partial (describe): _______________

### 2. Railway service status

- [ ] Both services running (Dashboard API + Engine worker)
- [ ] Restart policy enabled
- [ ] Engine replicas = 1
- [ ] Issues: _______________

### 3. Redis monitoring values

- engine_heartbeat_age_sec: _____
- engine_last_cycle_age_sec: _____
- engine_version: _____
- engine_started_at (epoch): _____

### 4. Engine health status

- [ ] Running (heartbeat & last_cycle &lt; 60s)
- [ ] Stale (heartbeat or last_cycle &gt; 60s)
- [ ] Alive but stuck (heartbeat &lt; 60s, last_cycle &gt; 120s)
- [ ] Offline (heartbeat &gt; 120s or missing)

### 5. Telegram pipeline status

- [ ] Signals generated by engine
- [ ] Dedupe working
- [ ] Messages delivered
- [ ] Issues: _______________

### 6. Laptop independence

- [ ] **SYSTEM FULLY CLOUD HOSTED — Laptop can be turned off safely**
- [ ] **SYSTEM DEPENDS ON LOCAL MACHINE — Laptop must remain on**

### Issues discovered and recommended fixes

| Issue | Severity | Recommended fix |
|-------|----------|------------------|
|       |          |                  |
|       |          |                  |

---

## Verdict (choose one)

**SYSTEM FULLY CLOUD HOSTED — Laptop can be turned off safely**

or

**SYSTEM DEPENDS ON LOCAL MACHINE — Laptop must remain on**

---

*Use `scripts/audit_cloud_deployment.py` for repeatable API and Redis checks; complete this document for a full audit including Railway, Telegram, and WebSocket.*
