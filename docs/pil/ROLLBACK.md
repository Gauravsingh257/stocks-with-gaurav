# PIL Rollback Plan

PIL is designed so it can be disabled instantly and completely, with no engine
impact and no redeploy required.

## Instant disable (no redeploy)

1. **Unset `PIL_ENABLED`** (or set `0`) on the backend service.
   - The entire `/api/intelligence/*` surface returns 404 (guard dependency).
   - The scheduler loop reads the flag live each tick and goes dormant — no
     reports, no alerts, no reconstruction.
2. **Unset `NEXT_PUBLIC_PIL_ENABLED`** on Vercel and redeploy the frontend to hide
   the nav entry and section. (Until redeploy, the pages simply show a
   "PIL is disabled" error because the API 404s — harmless.)
3. If the homepage redirect was enabled, unset `NEXT_PUBLIC_PIL_HOMEPAGE` to
   restore the public landing page at `/`.

Because no engine code path ever calls into PIL, disabling it cannot affect
Swing/LT/Momentum trading in any way.

## Partial rollback

- Turn off only automation: unset `PIL_REPORTS_ENABLED` / `PIL_ALERTS_ENABLED` /
  `PIL_TELEGRAM_ENABLED` — the dashboards/API stay up, no background writes/sends.
- Revert a capital or threshold change: reset the env var, or clear the DB
  override row in `pil_config` (`DELETE FROM pil_config WHERE key='...'`).

## Full removal

- Revert the PIL commits (all additive) — the only shared files touched are
  `dashboard/backend/main.py` (router import + registration + gated scheduler
  start) and the three nav components; reverting restores them exactly.
- The `pil_*` tables are isolated and can be dropped without affecting any engine
  table:
  ```sql
  DROP TABLE IF EXISTS pil_equity_curve;
  DROP TABLE IF EXISTS pil_scorecards;
  DROP TABLE IF EXISTS pil_reports;
  DROP TABLE IF EXISTS pil_alerts;
  DROP TABLE IF EXISTS pil_config;
  ```

## Safety invariants (verify after any change)

- `git grep -n "portfolio_positions\|momentum_positions" services/pil` shows only
  **reads** (SELECT) — PIL never writes an engine table.
- No module under `services/pil/` imports an engine loop
  (`smc_mtf_engine_v4`, `services/momentum_engine/engine`, trackers).
- With flags off, `pytest` is green and the platform is byte-for-byte unchanged.
