# Sprint 1 Validation Report

> **Produced at the END of the 5-trading-day validation window — not before.**
> Every cell must be backed by evidence (dashboard number, GA4/Clarity figure, or a
> beta quote). Empty is better than guessed. Sources in brackets per section.
>
> **Window:** ____ → ____ (5 trading days) · **Report date:** ____
> **Data sources:** `/health` (Product Health + Business + Adoption + Funnel + Sessions) ·
> GA4 · Microsoft Clarity · Vercel/Railway logs · beta feedback sheet.

---

## 1. Product KPIs
_[Source: /health top grid, window = 7d]_

| KPI | Value | Notes |
|-----|-------|-------|
| Total Users | | |
| Active Users | | |
| New Signups | | |
| Returning Users | | |
| Command Center Views | | |
| NBA Clicks | | |
| **NBA CTR** | | key engagement signal |
| Watchlist Adds | | |
| Watchlist Opens | | |
| Research Searches | | |
| AI Research Usage | | (0 until wired) |
| Avg Session Duration | | |
| Pages / Session | | |
| Telegram Link Clicks | | (0 — no CTA yet) |
| **Day-1 Retention** | | + cohort size |
| Day-7 Retention | | if ≥7d data |

## 2. Business KPIs
_[Source: /health → Business Health]_

| KPI | Value | Real / Estimate / Coming Soon |
|-----|-------|-------------------------------|
| MRR (est.) | | estimate (Premium × price) |
| Paid / Premium accounts | | real (role) |
| Registered Users | | real |
| ARPU (est.) | | estimate |
| Active Subscribers | | Coming Soon |
| Trial Users | | Coming Soon |
| Renewal Rate | | Coming Soon |
| Churn Rate | | Coming Soon |
| Refund Rate | | Coming Soon |
| LTV / CAC | | Coming Soon |

## 3. Activation Funnel
_[Source: /health → Activation Funnel]_

| Stage | Users | Conversion vs prev | % of visitors |
|-------|-------|--------------------|---------------|
| Visitor | | — | 100% |
| Signup | | | |
| Login | | | |
| Command Center | | | |
| NBA Clicked | | | |
| Research Opened | | | |
| Watchlist Add | | | |
| Returned Next Day | | | |

**Biggest drop-off stage:** ____  **Hypothesis (evidence-backed):** ____

## 4. Feature Adoption
_[Source: /health → Feature Adoption, sorted by adoption]_

| Feature | Unique | Daily | Weekly | Avg Time | Repeat % | Adoption % |
|---------|--------|-------|--------|----------|----------|------------|
| | | | | | | |

**Most adopted:** ____  **Least adopted (cut/hide/promote?):** ____

## 5. Session Analytics
_[Source: /health → Session Endings + GA4/Clarity]_

- Avg session duration: ____  · Pages/session: ____  · Device split (mobile/desktop/tablet): ____
- Session endings by reason: logout ___ · closed_or_left ___ · token_expired ___ · other ___
- Clarity: top rage-click / dead-click areas: ____
- Clarity: common exit page: ____

## 6. Beta Feedback
_[Source: beta-testing-checklist.md responses]_

- Testers: ____  · Would pay ₹1,200: ___ / ___
- Recurring confusion: ____
- Most-used page: ____  · Never-opened page: ____
- Did Command Center help? ____
- "Nearly left" moments: ____

## 7. Top 10 Bugs
_[Source: observation log · error logs · Clarity]_

| # | Bug | Severity | Frequency | Evidence | Status |
|---|-----|----------|-----------|----------|--------|
| 1 | | | | | |

## 8. Top 10 UX Problems
_[Source: observation log · beta · Clarity heatmaps]_

| # | UX problem | Severity | Frequency | Evidence |
|---|-----------|----------|-----------|----------|
| 1 | | | | |

## 9. Top 10 Opportunities
_Rank each on all four axes (High/Med/Low). Do NOT include anything without evidence from §1–8._

| # | Opportunity | User Impact | Business Impact | Dev Effort | Revenue Impact | Evidence (which finding) |
|---|-------------|-------------|-----------------|------------|----------------|--------------------------|
| 1 | | | | | | |
| 2 | | | | | | |

---

## Sprint 2 Roadmap (evidence-only)
_Derived strictly from the opportunities above. Every item cites the §ref that justifies it.
No speculative features._

| Priority | Item | Why (evidence §) | Success metric |
|----------|------|------------------|----------------|
| P0 | | | |
| P1 | | | |
| P2 | | | |

**Go/No-go:** retention signal ____ · willingness-to-pay ____ · verdict ____
