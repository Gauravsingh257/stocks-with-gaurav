# PIL Deployment Checklist

PIL is **additive and flag-gated**; every flag defaults **OFF**, so merging is a
no-op in production until you deliberately enable it.

## Feature flags

| Flag | Where | Default | Controls |
|------|-------|---------|----------|
| `PIL_ENABLED` | backend | `0` | master — mounts the API + reconstruction; nothing runs without it |
| `PIL_REPORTS_ENABLED` | backend | `0` | daily/monthly report scheduler |
| `PIL_ALERTS_ENABLED` | backend | `0` | alert rule engine on the scheduler tick |
| `PIL_TELEGRAM_ENABLED` | backend | `0` | push report/alert summaries to Telegram |
| `PIL_CAPITAL_SWING` / `_LONGTERM` / `_MOMENTUM` | backend | 1000000 / 1000000 / 500000 | book capital (₹) |
| `PIL_ALLOC_TARGET_SWING` / `_LONGTERM` / `_MOMENTUM` | backend | 0.60 / 0.25 / 0.15 | default allocation targets (DB-overridable) |
| `PIL_RISK_FREE_RATE` | backend | 0.065 | Sharpe/Sortino risk-free rate |
| `PIL_DAILY_REPORT_HOUR` | backend | 18 | IST hour for the evening daily report |
| `PIL_MAX_SECTOR_SHARE`, `PIL_MAX_SINGLE_STOCK`, `PIL_MAX_TOP10_SHARE`, `PIL_MAX_DD_WARN`, `PIL_MIN_DIVERSIFICATION`, `PIL_MAX_CAPITAL_DRIFT`, `PIL_MAX_CORRELATION`, `PIL_MIN_LIQUIDITY_CR`, `PIL_MAX_MOMENTUM_ALLOC` | backend | see `config.py` | alert/warning thresholds |
| `NEXT_PUBLIC_PIL_ENABLED` | Vercel | unset | shows the "Portfolio Intel" nav + section |
| `NEXT_PUBLIC_PIL_HOMEPAGE` | Vercel | unset | (optional) redirect `/` → `/intelligence`. Leave OFF to keep the public marketing landing page |

## Rollout sequence

1. **Merge** with all `PIL_*` flags unset → verified no-op in prod (no engine file
   changed except a 2-line router registration + a gated scheduler start).
2. **Backend**: set `PIL_ENABLED=1` on the Railway web service. Verify:
   - `GET /api/intelligence/status` → `{"enabled": true}`
   - `GET /api/intelligence/combined` → sane ₹ numbers for all four books.
3. **Frontend**: set `NEXT_PUBLIC_PIL_ENABLED=1` on Vercel; redeploy. Verify the
   Portfolio Intelligence section (Overview, Risk, Scorecards, Analytics,
   Allocation, Health, Reports, Alerts) renders and reconciles with the API.
4. **Soak**, then enable automation: `PIL_REPORTS_ENABLED=1`,
   `PIL_ALERTS_ENABLED=1`, and `PIL_TELEGRAM_ENABLED=1` for delivery.
5. **Tune** capital (`PIL_CAPITAL_*`), targets, and thresholds via env (live, no
   redeploy needed — thresholds/targets/capital are read per request; targets can
   also be edited from the Allocation page which persists to `pil_config`).
6. (Optional) `NEXT_PUBLIC_PIL_HOMEPAGE=1` to make PIL the internal homepage.

## Verification

- `pytest tests/test_pil_*.py` — 51 unit + integration tests.
- Frontend `npx tsc --noEmit` — clean.
- Non-regression: `git diff` touches only `services/pil/*`, `dashboard/**/pil*`,
  `dashboard/**/intelligence/*`, nav files, and the two `main.py` wiring lines.
