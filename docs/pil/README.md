# Portfolio Intelligence Layer (PIL)

An additive, **read-only** Portfolio Management System that sits *above* the three
independent production engines — **Swing (SMC pullback)**, **Long-Term**, and
**Momentum** — and only **observes, measures, analyses and reports**. It never
feeds a decision back into any engine.

> Design invariant: PIL reads engine data exclusively through the existing DB
> getters and writes only to its own `pil_*` tables. No engine file is in its call
> graph. With `PIL_ENABLED` unset, PIL is completely inert and the platform
> behaves exactly as before.

## Why it exists

Each engine had its own siloed UI/API. PIL unifies them into one institutional
dashboard: one place to see combined performance, cross-portfolio exposure/risk,
engine scorecards, allocation, health, reports and alerts.

## The accounting layer (foundation)

Swing/LT track P&L in **% / per-share** terms only — no ₹ notional. Momentum
carries a real notional. To produce PMS-grade ₹ metrics uniformly, PIL adds a
**book-capital accounting layer** (`services/pil/accounting.py`) that reconstructs
a *virtual ledger* per book from the existing position/journal rows:

- Each book starts from a configurable initial capital (`PIL_CAPITAL_*`, defaults
  ₹10L / ₹10L / ₹5L).
- Events replay chronologically. On entry a position gets an equal share of the
  *currently available cash* across free slots (`alloc = cash / free_slots`),
  `qty = alloc / entry_price`, cash is debited. Momentum reuses its real
  `position_size` when present. On exit, proceeds return to cash and realised P&L
  is booked. Open positions are marked to `current_price`.
- Outputs: cash, invested, portfolio value, realised/unrealised P&L and a daily
  equity curve (per book + capital-weighted **COMBINED**).

This is pure accounting — deterministic given the rows, never influencing a trade.
Switching to risk-based sizing later only changes the `alloc` rule.

## Module map (`services/pil/`)

| Module | Part | Purpose |
|--------|------|---------|
| `config.py` | — | live flags + capital/thresholds/targets (env + DB override) |
| `reference_data.py` | 2 | symbol → sector/industry/mcap/theme/beta/liquidity |
| `accounting.py` | 1 | virtual ledger + equity curves per book + combined |
| `metrics.py` | 1 | full PMS metric set (returns, ratios, trade stats, turnover) |
| `exposure.py` | 2 | cross-portfolio exposure, HHI, correlation, warnings |
| `scorecard.py` | 3 | daily/monthly engine scorecards |
| `analytics.py` | 4 | contribution, diversification, what-if, optimal allocation |
| `allocation.py` | 5 | current-vs-target capital allocation + rebalancing |
| `health.py` | 6 | per-book + combined health → GREEN/YELLOW/RED |
| `reports.py` | 7,8 | daily + monthly reports (print-ready HTML) |
| `alerts.py` | 10 | stateful, self-clearing alert rule engine |
| `scheduler.py` | 7,8,10 | gated daemon: evening daily + monthly + alert ticks |
| `notify.py` | — | best-effort Telegram delivery (reuses platform bot) |

- API (Part 11): `dashboard/backend/routes/portfolio_intelligence.py` →
  `/api/intelligence/*`, guarded by `PIL_ENABLED`.
- Storage: `dashboard/backend/db/pil.py` → isolated `pil_*` tables in `dashboard.db`.
- UI (Parts 9,12): `dashboard/frontend/app/intelligence/*` (Overview, Risk,
  Scorecards, Analytics, Allocation, Health, Reports, Alerts), nav gated by
  `NEXT_PUBLIC_PIL_ENABLED`.

See [METRICS.md](METRICS.md), [DEPLOYMENT.md](DEPLOYMENT.md), [ROLLBACK.md](ROLLBACK.md).
