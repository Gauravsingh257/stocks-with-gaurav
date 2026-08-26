# 🔍 Launch Readiness Audit — stockswithgaurav.com

> **STATUS: HISTORICAL** · workstream: `platform` · last substantive update: 2026-07-05
> Pre-launch audit. Its one critical blocker (JWT forgery) was FIXED in PR #58 and deployed - the 'NOT ready' verdict at the top refers to 2026-07-04 and is no longer the current state.
> Current project state lives in [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md).

**Date:** 2026-07-04 · **Scope:** live backend (`web-production-2781a.up.railway.app`) + frontend/backend code
**Method:** live API probing + source review. Authorized security testing on owner's own system.

---

## 1. Executive Summary

**Verdict: 🚫 NOT ready for public launch** at time of audit. One critical authentication vulnerability was confirmed exploitable against production.

> **UPDATE 2026-07-04 — fixes applied in code (not yet deployed):**
> - ✅ **§2 JWT blocker FIXED** — fallback-decode path removed, hardcoded constant deleted, per-process random fallback added. Verified locally: forged-constant token now REJECTED, legit token ACCEPTED. Live Redis secret confirmed to be a real 64-char random (not the constant), so **no forced re-login needed** once deployed.
> - ✅ **§3.2/§3.3 track-record framing FIXED** — "Algorithm-Generated Signals" badge, honesty banner, "Resolved N of M" denominator on Hit Rate & Avg P&L, new Resolved card.
> - ✅ **§3.4 empty-state FIXED** — ACTIVE relabeled "Awaiting Entry", dashes now show "Awaiting" + tooltip.
> - ⏳ **§3.1 stale-data badge** — not yet done (still recommended).
> **Deploy still required** for these to reach production.

| Severity | Count | Items |
|---|---|---|
| 🚫 Blocker | 1 | JWT forgery / full account takeover |
| ⚠️ Fix-before-launch | 4 | Stale-data labeling, track-record framing, "ACTIVE" empty-state UX, small-sample stats |
| ✅ Ready | — | CORS, screener anon lock, site-wide disclaimer |

The good news: access-control architecture, screener gating, CORS, and the compliance disclaimer are largely sound. The blocker is a single hardcoded secret, and it is a clean fix.

---

## 2. 🚫 BLOCKER — JWT forgery via hardcoded fallback secret (auth bypass / account takeover)

**File:** [dashboard/backend/routes/auth.py:35](dashboard/backend/routes/auth.py#L35), exploited via [auth.py:224-232](dashboard/backend/routes/auth.py#L224-L232)

**Root cause.** `decode_token()` first validates against the real (Redis-provided) secret, but on failure it *falls back to trying a constant string that is published in the source code*:
```python
_JWT_SECRET_FALLBACK = "swg-default-secret-change-me-in-prod"
...
except jwt.InvalidTokenError:
    if secret != _JWT_SECRET_FALLBACK:
        try:
            return _coerce_user(jwt.decode(token, _JWT_SECRET_FALLBACK, ...))  # accepts forged tokens
```
Anyone who reads the repo (or guesses this well-known-style default) can forge a JWT for **any `sub` (user id) and any `role`**, and production will accept it.

**Confirmed exploit (live, today).** I minted a token signed with that constant and:
- `GET /api/auth/me` → returned **your** account: `{"id":1,"email":"hellogaurav2577@gmail.com","role":"FREE"}`
- `GET /api/watchlist` → returned **your private watchlist** (`LODHA`) — no login, no password.

**Impact:** full account takeover of any user by id, privilege escalation to `PREMIUM`/`ADMIN`, read/modify any user's watchlist and positions. Trivial to automate across all user ids. This is disqualifying for a public launch.

**Fix (do all three):**
1. **Remove the fallback-decode path entirely.** A token that fails the real secret must be rejected — never re-tried against a constant. Delete the `if secret != _JWT_SECRET_FALLBACK: jwt.decode(..., _JWT_SECRET_FALLBACK, ...)` block.
2. **Remove the hardcoded constant** `_JWT_SECRET_FALLBACK`. In production, require the secret to come from Redis/env and **fail closed** (refuse to issue/verify tokens) if absent, rather than silently using a known string.
3. **Rotate the secret** after deploy (delete Redis `auth:jwt_secret` so a fresh random one seeds, or set a strong `JWT_SECRET` env). This invalidates any tokens an attacker may have already forged. Existing legitimate users simply re-login.

_Note:_ the fallback existed to survive Redis being unreachable in dev. Keep dev working via an explicit `JWT_SECRET` env var in dev, not a shipped constant.

---

## 3. ⚠️ Fix-before-launch

### 3.1 Stale data presented without a clear "as of" — screeners
`GET /api/screeners/supertrend_flip/1D` returned `"snapshot_stale": true`, `"snapshot_source": "last_known_good"`, `as_of: "2026-07-03"` — a day old (today 2026-07-04). The lock/teaser works, but **the UI must surface staleness prominently** (e.g. "Data as of 3 Jul — refreshing"). Showing a day-old scan as current misleads traders. Verify the cron producer (`scripts/scanner_cron.py`) is actually running on schedule; a persistent LKG fallback means the producer is failing silently.
**Fix:** render `as_of` + a stale badge whenever `snapshot_stale`; alert/monitor when the producer misses a run.

### 3.2 Track-record framing — algorithmic signals shown as a "track record"
`GET /api/research/track-record` is fully public and returns `summary`: `total_picks:100, resolved:11, target_hit:5, stop_hit:6, hit_rate_pct:45.5, avg_pnl_pct:6.02`. These are **automated agent signals, not executed/realized trades** (consistent with alert-mode validation per project notes). The site-wide disclaimer covers "generated by automated algorithms," which is good, but the page itself should explicitly label these as **hypothetical/algorithmic signals, not realized returns**, to avoid a mis-selling complaint.
**Fix:** add an on-page label near the stats: "Algorithmic signals — hypothetical, not executed trades. Past performance ≠ future results."

### 3.3 Small-sample statistics shown as headline numbers
`avg_pnl_pct: 6.02` and `hit_rate 45.5%` are computed over **only 11 resolved of 100** picks. Headlining a 6% average from an 11-trade sample is statistically fragile and easy to attack.
**Fix:** always show denominator ("45.5% — 5/11 resolved"), and avoid presenting `avg_pnl` as a performance promise. Consider hiding aggregate stats until N is meaningful (e.g. ≥30 resolved).

### 3.4 "ACTIVE" picks render blank fields (empty-state UX)
Several public picks (`NYKAA`, `OBEROIRLTY`, `ACMESOLAR`) show `status:"ACTIVE"` with `current_price:null, pnl_pct:null, days_held:null`. To a public visitor this looks broken. "ACTIVE" (pending trigger) vs "RUNNING" (live) is not self-explanatory.
**Fix:** in [research/track-record/page.tsx](dashboard/frontend/app/research/track-record/page.tsx), show "Awaiting entry trigger" for ACTIVE and render "—" with a tooltip rather than blank cells.

---

## 4. ✅ Verified sound

- **Screener anonymous gating** — anon gets `locked:true, rows:[]` with only a blurred `sample_locked` (scores/tiers, no symbols). API does **not** leak full rows to anonymous callers. [screeners.py:41-44](dashboard/backend/routes/screeners.py#L41-L44) ✅
- **CORS** — `Access-Control-Allow-Origin` is **not** reflected for `evil.com`; only `https://stockswithgaurav.com` is allowed even though `allow-credentials:true`. No wildcard-with-credentials leak. ✅
- **Site-wide compliance disclaimer** — present in all three shells: authenticated ([LayoutClient.tsx:76](dashboard/frontend/components/LayoutClient.tsx#L76)), public dashboard ([PublicAuthFrame.tsx:44](dashboard/frontend/components/PublicAuthFrame.tsx#L44)), and landing. States: educational only, **not SEBI-registered**, no buy/sell/hold recommendations, algorithmic, data may be delayed. Strong baseline. ✅
- **Auth-required endpoints** reject missing/invalid tokens with 401 (verified control request). ✅ (undermined only by §2)

---

## 5. Not fully verified in this pass (recommended before launch)

These need deeper work than a first pass allowed — flagging honestly rather than asserting:
- **Indicator correctness** — recompute Supertrend(10,3)+EMA10 for a handful of the 12 current hits against Kite OHLC to confirm the scanner's filter output is accurate and lookahead-free (`scripts/scanner_cron.py`).
- **Universe cleanliness** — `data/nse_universe_full.json` (modified, uncommitted) + T2T/SME lists: confirm no delisted/suspended/illiquid symbols surface without a warning.
- **Ledger reconciliation** — cross-check `trade_ledger_2026.csv` and the journal DB against what `/api/analytics` and `/api/journal` render for realized trades.
- **Per-page empty/error sweep** — click through every public page logged-out and confirm no `NaN`/`undefined`/`Invalid Date`/spinner-forever states.

---

## 6. Prioritized fix list (by public-trust risk)

1. 🚫 **Kill JWT fallback-decode + hardcoded secret, rotate secret** (§2) — before anything else ships publicly.
2. ⚠️ **Track-record labeling** (§3.2, §3.3) — compliance/mis-selling exposure.
3. ⚠️ **Stale-data badge + producer monitoring** (§3.1).
4. ⚠️ **ACTIVE empty-state UX** (§3.4).
5. 🔎 Complete the four "not fully verified" checks (§5).
