# 🔍 PUBLIC-LAUNCH READINESS AUDIT — stockswithgaurav.com

> **STATUS: HISTORICAL** · workstream: `platform` · last substantive update: 2026-07-05
> The prompt that produced LAUNCH_AUDIT_REPORT.md. Job input, already consumed.
> Current project state lives in [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md).

## Role & Objective
You are a senior full-stack + trading-systems auditor. The site is going from private/invite to **public launch**. Your job is to verify that **every page, every data point, every filter, and every performance number is correct, truthful, and safe to show a public audience** — including skeptical retail traders and potential SEBI/compliance scrutiny. Assume nothing renders correctly until proven. Treat any unverified number shown to the public as a **liability**.

**Output a single `LAUNCH_AUDIT_REPORT.md`** with a per-component verdict: ✅ Ready / ⚠️ Fix-before-launch / 🚫 Blocker, each with file:line evidence and a concrete fix.

## Ground Rules
- **The engine is LIVE.** Read-only investigation. Do NOT restart services, mutate Redis, or push code during the audit. Propose fixes; don't apply them.
- **Evidence over assumption.** Every claim must cite either code (`file:line`) or a live response (endpoint + payload).
- **Public lens.** For each page ask: *Would a stranger who loses money acting on this have a legitimate complaint?* Flag anything misleading, stale, cherry-picked, or unlabeled.
- Verify credentials/URLs from `.env`, `dashboard/frontend/.env.production`, Railway, and prior conversation before hitting live endpoints.

## PART A — Inventory & Access Control
1. Enumerate every public route from `dashboard/frontend/app/**/page.tsx`.
2. Map each page → backend route(s) → auth gate → anonymous/empty/error states.
3. Access-control matrix: anonymous vs logged-in vs premium. Verify screener teaser/lock (screeners.py `_is_entitled`) hides full rows from anonymous users AND the API itself doesn't leak full data. Test API directly, not just UI.
4. Flag any route returning internal data/secrets/other users' data/stack traces to unauthenticated callers.

## PART B — Data Integrity
1. Freshness: every number needs a truthful "as of". Check engine:snapshot (600s), signals:today, screener snapshots. Flag stale-as-live and silent LKG fallbacks.
2. Correctness: recompute Supertrend(10,3)+EMA10 (1D/1W) for sample symbols vs scanner output; check scanner_cron OHLC/timeframe/no-lookahead.
3. Stock filtering: universe cleanliness (nse_universe_full.json), tier classification, delisted/suspended/T2T/SME/illiquid handling.
4. Performance/track-record: win-rate/R/PnL correctness, reconcile with trade_ledger_2026.csv + journal DB; real vs paper/alert vs backtest labeling; survivorship/look-ahead/open-trade inflation.
5. Market-condition consistency: sample signals vs real OHLC.
6. Cross-container sync: no partial/duplicate trades (dashboard_sync retry queue), signal dedup.

## PART C — Per-Page Deep Dive
Per page: purpose → data sources → every visible number verified → anonymous/empty/error states → mobile → verdict.

## PART D — Trust, Compliance & Presentation
1. Visible "not investment advice" disclaimer where signals/performance appear (SEBI).
2. Truthful labels: live/delayed/backtested/alert-mode explicit.
3. No blank/spinner-forever/NaN/undefined/null/Invalid Date/raw errors publicly.
4. Security: no secrets in bundles, no verbose API errors, no CORS wildcard on authed endpoints, rate-limiting.

## Deliverable: LAUNCH_AUDIT_REPORT.md
1. Executive summary (launch-ready? blocker/fix counts).
2. Blockers table (file:line + fix).
3. Per-page verdicts.
4. Data-integrity findings.
5. Prioritized fix list by public-trust risk.
