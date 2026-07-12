# Module Status

Single source of truth for where each user-facing module stands. Stages:
**In Development** → **Production – Validation** (live, observing) → **Stable**.

> Change policy for anything marked **Production – Validation**: modify **only**
> on evidence — analytics, beta feedback, bug reports, or performance issues.
> Not opinions. No scheduled "improvements."

| Module | Stage | Notes |
|--------|-------|-------|
| **Command Center (homepage)** | 🔒 **Production – Validation** (FROZEN) | V1 complete 2026-07-12. See below. |
| Navigation (5-group) | Production – Validation | Phase 1 (PR #103). |
| OI Intelligence | Production – Validation | Phase 1 cleanup + zone bands/labels. |
| Research · On the Radar | Production – Validation | Table view (#112), rename (#111). |
| Research Chart (SMC zones/labels) | Production – Validation | #113/#114/#115. |
| Watchlist event feed | Production – Validation | Sprint 1 (#104). |
| Telegram Morning Brief | Gated OFF | `MORNING_BRIEF_ENABLED=0` — validate before enabling. |
| Product Health dashboard (`/health`) | Production – Validation | Internal, admin-only (#107/#108). |
| **Payment & Subscription** | ⏸ **Not started** | Next major phase — scope only AFTER Sprint 1 Validation. |

---

## Command Center V1 — COMPLETE (frozen 2026-07-12)

**Status:** Production – Validation. **Do not redesign.** Future changes evidence-only.

**What V1 includes (route `/command`, default authenticated home):**
- Value-first header ("Today's Trading Dashboard" + regime read + freshness
  "Updated X ago · Engine vN").
- **Today's Top Opportunities** — top-3 ranked SMC setups, expandable into a
  score explainer ("What builds this score?") + View Full Analysis; per-row
  sparklines; star tiers (Excellent/Strong/…).
- **Verified Track Record** card (Resolved Setups · Average Return · Last
  Updated → View Performance). Hit-rate detail lives on `/research/track-record`.
- **Adaptive:** new users get a 60-second onboarding (Search → Analyze → Add →
  Alerts) seeded with familiar names (RELIANCE/TCS/HDFCBANK/INFY); returning
  users get watchlist events + more opportunities.
- Consolidated opportunities (no duplicate tickers); collapsible daily brief;
  Option A disclaimer retained.

**Built/refined by:** PRs #104 (Sprint 1 V1), #116 (value-first redesign),
#117 (track-record focus + sparklines + freshness). Copy humanized throughout.

**Constraints honored:** UX/copy/layout only — no backend or engine changes;
reuses existing endpoints (`/api/command-center`, `/api/market/daily-brief`,
`/api/analytics/track-record`, live snapshot). Positioning stays **Option A**
(analytics, never buy/sell advice).

**Do NOT:** schedule further UX/layout changes, add features, or re-open settled
decisions on this module. Log any observation in
[`docs/validation/sprint1-observation-log.md`](./validation/sprint1-observation-log.md);
only Critical/Major/bug/perf items are actioned during the freeze.

---

## Next milestone (deferred — do not start yet)

**Payment & Subscription System.** Scope begins **only after Sprint 1 Validation
is complete** (the 5-trading-day window + Validation Report). Prior direction:
Razorpay Subscriptions (UPI AutoPay + cards), webhook → access grant/revoke +
Telegram gating, GST invoice, trial. No implementation until validation closes
and the report is produced.
