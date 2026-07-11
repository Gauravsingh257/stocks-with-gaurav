# Daily Monitoring — Sprint 1 Validation (Product Freeze)

> **Freeze rule:** only bug fixes · UI · mobile · performance · copy · monitoring.
> No new features until the [Validation Report](./sprint1-validation-report.md) is done.
> Run this once per trading day (~10 min). Log findings in the
> [observation log](./sprint1-observation-log.md) — **evidence only, no assumptions.**

## The 10-minute daily loop

| # | Surface | Where | What to look for |
|---|---------|-------|------------------|
| 1 | **Product Health** | `/health` (admin) | Active users, new signups, returning — trending up or flat? |
| 2 | **Activation Funnel** | `/health` → funnel | Which stage bleeds the most? Compare to yesterday. |
| 3 | **Feature Adoption** | `/health` → adoption | Anything at ~0% adoption? Anything surprisingly high? |
| 4 | **Business** | `/health` → business | Premium/registered count moving? (MRR/ARPU are estimates.) |
| 5 | **Session Endings** | `/health` → sessions | Spike in `token_expired` or `closed_or_left`? |
| 6 | **GA4** | analytics.google.com | Realtime + Engagement: avg session, pages/session, device split, top paths. |
| 7 | **Clarity** | clarity.microsoft.com | Rage clicks, dead clicks, excessive scrolling, exit pages. Watch 2–3 replays. |
| 8 | **Error logs** | Railway (web/engine) + Vercel | Any 5xx, exceptions, failed deploys, slow endpoints. |
| 9 | **Mobile spot-check** | phone, real | Open Command Center + Watchlist on a phone; note any breakage. |
| 10 | **Log it** | observation log | Add every finding with date · feature · severity · frequency · evidence. |

## Rules of evidence
- A finding needs a **number, a screenshot, a replay, or a quote** — not a hunch.
- Tag each: **Critical / Major / Minor / Idea**. Only Critical/Major/bugs get fixed during freeze; Ideas → Sprint 2 backlog.
- If you're unsure whether something is a bug or a preference, log it as an observation and let frequency decide.

## Daily rollup (fill one row per day)

| Day | Date | Active users | Top funnel drop | New bugs | New UX issues | Notable |
|-----|------|--------------|-----------------|----------|---------------|---------|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |

## What NOT to do during freeze
- ❌ Build any new feature or page.
- ❌ Change the roadmap based on a single day / single user.
- ❌ "Fix" something with no evidence it's broken.
- ✅ Fix reproducible bugs, obvious mobile/perf breakage, and confusing copy — small, safe, evidence-backed.

## Fixes shipped during freeze (keep this list)
| Date | Fix | Category | PR |
|------|-----|----------|----|
| | | | |
