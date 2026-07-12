# 🚀 LAUNCH_CHECKLIST.md

**Single source of truth for the public launch of stockswithgaurav.com (₹1,200/mo).**
Living document — update until launch. Nothing here is "done" until it's checked and dated.

**Status legend:** ✅ done · 🟡 in progress / partial · 🔲 not started · ⏸ deferred (post-launch) · ⚠️ decision needed · 🔒 hard launch gate (cannot launch without)

**Positioning is LOCKED = Option A** (analytics/tooling, never buy/sell advice). Every copy/legal/marketing item must stay inside this lane.

---

## 🔒 Launch Gate (the non-negotiables)
Launch is blocked until every one of these is ✅:
- [ ] 🔒 Sprint 1 Validation complete + Validation Report produced
- [ ] 🔒 Payment lifecycle tested end-to-end (success · failure · retry · webhook grant/revoke · refund)
- [ ] 🔒 Auth flows solid (signup · login · forgot-password · email verify)
- [ ] 🔒 Access auto-revoke on cancel/expiry (incl. Telegram removal)
- [ ] 🔒 Legal pages live (Privacy · Terms · Refund · Disclaimer)
- [ ] 🔒 Secrets audited (no secrets in code/repo)
- [ ] 🔒 Rollback plan verified

Everything else can trail launch by up to a week (fast-follow).

---

## 1. Product Readiness
- [ ] 🟡 **Validation complete** — Sprint 1 5-trading-day window + report (`docs/validation/`). *In progress.*
- [ ] 🟡 **Critical bugs resolved** — evidence-based fixes shipping during freeze (track-record KPIs #110, radar table #112, chart zones/labels #113–115). Keep triaging via `docs/validation/sprint1-observation-log.md`.
- [ ] 🟡 **Mobile verified** — spot-verified live (Command Center, research chart, On-the-Radar). *Still needed: full responsive matrix (iPhone/Android/tablet, portrait+landscape) across all pages.*
- [ ] 🔲 **Performance verified** — build clean, but no formal **Core Web Vitals** / Lighthouse pass yet. Measure LCP/CLS/INP on Command Center, Research, Chart.
- [ ] 🔲 **Accessibility review** — WCAG basics not audited (contrast, focus states, keyboard nav, alt text, aria labels).
- [x] ✅ Command Center V1 complete + frozen (`docs/MODULE_STATUS.md`).
- [ ] 🔲 Cross-browser check (Chrome · Safari · Firefox · Edge).
- [ ] 🔲 Error/empty-state audit across every page.

## 2. Payment Readiness  *(Next phase — do NOT start until validation closes)*
- [ ] 🔲 **Razorpay integration** — Razorpay Subscriptions (recommended for India recurring). UPI AutoPay + cards + netbanking + wallets.
- [ ] 🔲 **Subscription lifecycle** — create · active · upgrade/downgrade · cancel · grace period · expiry; webhook → grant/revoke access + Telegram gating.
- [ ] 🔲 **GST invoices** — generate + downloadable; correct GST fields.
- [ ] 🔲 **Trial** — 7-day trial flow + conversion.
- [ ] 🔲 **Coupon support** — coupon + referral codes.
- [ ] 🔲 **Failed payment handling** — retry, dunning, renewal reminders, grace period, auto-suspend.
- [ ] 🔲 Payment history + receipts UI.
- [ ] 🔲 Webhook idempotency + signature verification.

## 3. Security
- [x] ✅ **Authentication** — JWT; hardcoded-secret forgery bug fixed pre-launch (PR #58). Verify `JWT_SECRET` strong + set in prod.
- [x] ✅ **Authorization** — PIL/holdings private (login-only, PR #100); `/health` + product-analytics admin-gated.
- [ ] 🟡 **Rate limiting** — limiter exists (`dashboard/backend`); verify coverage on auth, ingest, and public endpoints; tune limits for launch traffic.
- [ ] 🔲 🔒 **Secrets** — full audit: no secrets in repo; all in `.env`/Redis; rotate any exposed; confirm `.gitignore`.
- [ ] 🔲 **Backup** — DB (Railway volume) backup + restore drill; document RPO/RTO.
- [ ] 🔲 **Monitoring** — uptime + error alerting (engine heartbeat, backend 5xx, deploy failures). Currently only in-app health statuses.
- [ ] 🔲 HTTPS/headers review (CSP, HSTS), dependency/vuln scan.

## 4. Legal
- [x] ✅ **Disclaimer** — in-app footer ("educational, not SEBI-registered, not advice") — aligns with Option A.
- [ ] 🔲 🔒 **Privacy Policy** — data collected (analytics, account, journal), storage, third parties (GA4/Clarity/Razorpay), user rights.
- [ ] 🔲 🔒 **Terms & Conditions** — subscription terms, acceptable use, liability, Option A framing.
- [ ] 🔲 🔒 **Refund Policy** — required by Razorpay + trust; clear conditions.
- [ ] 🔲 Standalone Disclaimer page (not just footer).
- [ ] 🔲 Cookie/consent notice (analytics) — assess for compliance.

## 5. Analytics
- [x] ✅ **Product Dashboard** — `/health` (KPIs + activation funnel + feature adoption + sessions), admin-only.
- [x] ✅ **Business Dashboard** — `/health` Business Health (MRR/ARPU estimates + Coming-Soon until billing).
- [ ] 🟡 **GA4** — code deployed but **INERT until `NEXT_PUBLIC_GA4_ID` set in Vercel** (see `docs/validation/analytics-setup.md`). *Owner action.*
- [ ] 🟡 **Clarity** — code deployed but **INERT until `NEXT_PUBLIC_CLARITY_ID` set in Vercel**. *Owner action.*
- [ ] 🔲 Verify events firing post-config (login, signup, nba_clicked, watchlist_stock_added, page views).
- [ ] ⏸ PostHog (optional).

## 6. Customer Support
- [ ] 🔲 **Contact page** — email/form + response-time expectation.
- [ ] 🔲 **FAQ** — pricing, what you get, "is this advice?" (Option A), cancellation/refund, Telegram access.
- [ ] 🔲 **Feedback form** — in-app.
- [ ] 🔲 **Issue reporting** — bug report path (could route to the observation log/queue).
- [ ] 🔲 Support inbox + SLA defined.

## 7. Marketing
- [ ] 🟡 **Landing page** — exists (public `/`) but audit flagged: rebuild as conversion page (value → proof → pricing → signup); persistent login. Currently "educational research" framing.
- [ ] 🔲 🔒-ish **Pricing page** — ₹1,200/mo plan, what's included, trial, CTA. (Needed before charging.)
- [ ] 🔲 **Demo video** — 60–90s product walkthrough.
- [ ] 🔲 **Screenshots** — Command Center, chart with SMC zones/labels, On-the-Radar, Track Record.
- [ ] 🔲 **Testimonials** — collect from beta users (validation window).
- [ ] 🔲 SEO basics (meta, OG, sitemap already present), social handles.

## 8. Launch Day
- [ ] 🔲 **Production checklist** — all env vars set (GA4, Clarity, JWT, Razorpay keys, DASHBOARD_URL); flags reviewed (`MORNING_BRIEF_ENABLED` decision; PIL flags).
- [ ] 🔲 **Smoke tests** — signup → pay → access granted → Telegram added → cancel → access revoked; core pages load; auth flows.
- [ ] 🔲 **Monitoring** — dashboards open, alerting armed, on-call plan for launch window.
- [ ] 🔲 **Rollback plan** — deploys from `main`; document: revert-PR path, feature-flag kill switches (no-redeploy), DB-safe rollback. Verify Vercel/Railway rollback works.
- [ ] 🔲 Status page / incident comms.
- [ ] 🔲 Load sanity check for expected launch traffic.

## 9. Post Launch
- [ ] 🔲 **First 100 users** — watch activation funnel (`/health`); fix top drop-offs.
- [ ] 🔲 **First 100 paid users** — watch conversion + churn/refund; validate MRR becomes real (Business Health).
- [ ] 🔲 **Weekly KPI review** — retention (D1/D7), NBA CTR, feature adoption, MRR, churn — from `/health` + GA4/Clarity.
- [ ] 🔲 **Sprint planning** — next roadmap driven ONLY by launch evidence (deferred candidates: Personalization/Memory engines, Portfolio consolidation, Market Intelligence v1, MF platform).

---

## Deferred (explicitly NOT for launch)
Personalization/Memory/Decision engines (founder blueprint), Portfolio consolidation, full Market Intelligence rebuild, Mutual-Fund platform, broker-connect (user's own holdings), SEBI-RA advisory tier. Revisit post-launch on evidence.

---
*Owner: Gaurav · Last updated: 2026-07-12 · Update this file until launch.*
