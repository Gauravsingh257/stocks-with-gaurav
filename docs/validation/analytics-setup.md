# Analytics Setup — Sprint 1 Validation

Instrumentation is deployed but **inert until the env vars below are set in Vercel**
(Production + Preview). No IDs = silent no-op; the app renders identically.

## 1. Environment variables (Vercel → Project → Settings → Environment Variables)

| Var | Where to get it | Required |
|-----|-----------------|----------|
| `NEXT_PUBLIC_GA4_ID` | GA4 → Admin → Data Streams → Web → "Measurement ID" (`G-XXXXXXX`) | Yes |
| `NEXT_PUBLIC_CLARITY_ID` | clarity.microsoft.com → project → Settings → "Clarity ID" | Yes |
| `NEXT_PUBLIC_POSTHOG_KEY` | PostHog → Project Settings → Project API Key | Optional |
| `NEXT_PUBLIC_POSTHOG_HOST` | e.g. `https://app.posthog.com` (or EU host) | Optional |

After setting them, **redeploy** (env vars are baked at build time for `NEXT_PUBLIC_*`).

## 2. Verify it's live
- **GA4**: Reports → Realtime → open the site → you should appear; navigate pages and
  watch `command_center_viewed`, `watchlist_opened`, etc. under Realtime → Event count.
- **Clarity**: Dashboard shows sessions within ~10 min; session recordings + heatmaps.
- **PostHog** (if set): Activity → Live events.

## 3. Events being tracked

| Event | Fires when | Props |
|-------|-----------|-------|
| `landing_viewed` / `command_center_viewed` / `watchlist_opened` / `oi_intelligence_opened` / `portfolio_viewed` / `research_opened` / `screeners_opened` / `terminal_opened` / `track_record_viewed` / `market_intel_opened` / `stock_page_viewed` | Route navigation (one place: `AnalyticsScripts`) | — |
| `login` | Successful login | `role` |
| `signup` | Successful registration | `role` |
| `nba_clicked` | Next-Best-Action card clicked | `kind, severity, symbol, href, hero` |
| `watchlist_stock_added` | Stock added to watchlist | `symbol, with_setup` |
| `global_search` | Command palette result opened | `query, href, is_stock` |
| `page_view` | Every route change (GA4/PostHog native) | `page_path` |

Session duration, device, time-on-page, and navigation flow are captured automatically
by GA4 + Clarity — no code needed.

## 4. Not yet wired (Sprint 2 — helper is ready, one line each)
- `ai_research_used` — needs the Ask-AI submit handler in `app/research/page.tsx`.
- `telegram_link_clicked` — no outbound Telegram CTA exists in the UI yet; add when a
  "Join Telegram" link ships.

## 5. Backlog
- Cookie-consent banner (deferred for launch; revisit for EU traffic / compliance).
