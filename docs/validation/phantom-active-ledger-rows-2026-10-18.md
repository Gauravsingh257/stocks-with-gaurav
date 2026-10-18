# Phantom ACTIVE rows on the public track record

**STATUS: LIVE** · 2026-10-18 · fix on branch `fix/phantom-active-ledger-rows`

## The report

The `/research/track-record` page listed **201 positions as Active**. The three books together
hold **32**. Every "% resolved" and maturity figure computed over the ledger was therefore
computed over a denominator that was ~84% wrong.

## Measured, production, 2026-10-18

| Book | Live open | Ledger `status='ACTIVE'` | Phantom |
|------|----------:|-------------------------:|--------:|
| Swing | 11 | 85 | 74 |
| Long-term | 19 | 48 | 29 |
| Momentum | **2** | **68** | **66** |
| **Total** | **32** | **201** | **169** |

Sources: `/api/portfolio/summary` (swing 11 ACTIVE + 7 PENDING, LT 19 ACTIVE + 1 PENDING),
`/api/momentum-portfolio/summary` (`count: 2`, `pending: 7`, `used: 9`),
`/api/lifecycle/trades?status=ACTIVE` (201, all `is_legacy=1`, sourced only from
`portfolio_positions` 133 and `momentum_positions` 68).

Pending rows are *in sync* (ledger swing 7 / LT 1 match the live books exactly), which localises
the defect to the ACTIVE transition rather than to the mapper as a whole.

## Independent confirmation from the owner's own export

Cross-matching the owner's two Google-Sheet exports on `symbol + entry_price`, **~70 of the 200
"Active" rows also appear in his closed sheet** as Target Hit or Stop Hit. Examples:

| Symbol | Entry | "Active" row | Closed sheet |
|---|---|---|---|
| RAYMOND (swing) | ₹593.42 | Active, +41.45%, 62d | Target Hit ₹851, +43.41%, 62d |
| RAYMOND (swing) | ₹1240.80 | Active, −3.37%, 1d | Stop Hit ₹1177.70, −5.09%, 1d |
| SBCL | ₹748 | Active, +32.71% | Target Hit ₹1023.60, +36.84% |
| OCCLLTD | ₹225.96 | Active, −1.62% | Stop Hit ₹207.78, −8.05% |
| BLUSPRING (LT) | ₹127.37 | Active, −4.96% | Stop Hit ₹112.75, −11.48% |

The phantom's P&L is consistently *short* of the real exit, and several show `0d` while dated
August — the fingerprint of a value frozen at the last sync before the position left scope.

## Root cause

`trade_lifecycle_migrate.backfill()` maps live positions with

```sql
SELECT ... FROM portfolio_positions WHERE status IN ('ACTIVE','PENDING','EXPIRED')
```

and upserts every row that matches. **Nothing retired the rows it had already written.** When a
position closes, its source row leaves that `WHERE`, the live book correctly drops it, and the
lifecycle row it created is orphaned — frozen at `status='ACTIVE'` with the `pnl_pct` and
`holding_days` of its last sync. The genuine close then arrives from `portfolio_journal` under a
*different* deterministic UUID (`make_uuid(source, source_table, source_id)`), so the same trade
exists twice: once closed and correct, once permanently open and stale.

`NSE:BLUSPRING` carries five ledger rows, including `STOP_HIT pnl=-11.48`, `TARGET_HIT pnl=15.79`
and two phantom `ACTIVE`s.

**Nothing is running in parallel.** There is one selection engine; the ledger simply never
forgot.

## The fix

`_reconcile_departed()` in `dashboard/backend/db/trade_lifecycle_migrate.py`. Each positions
mapper now collects the deterministic UUID of every row it saw in scope; anything in the ledger
with that `source_table` and `record_state='ACTIVE'` that is *not* in that set has left the source
table and is moved to **`record_state='ARCHIVED'`**.

Deliberately **not** done:

- not deleted — the audit trail stays;
- **no terminal status, exit_reason, exit_at or exit P&L is written.** We know the source row is
  gone; we do not know how it ended. Inventing an outcome would fabricate a trade result on a
  public page. The real outcome already exists as the journal row.

`record_state='ACTIVE'` is already the filter on every read path
(`trade_lifecycle_query._build_where` default, `lifecycle_analytics._closed_rows`), and
`/api/lifecycle/record-state/{id}` already supported `ARCHIVED` manually — so this reuses the
existing retirement mechanism rather than adding a concept.

### Two guards against erasing the track record

1. **An entirely empty source table archives nothing.** A `portfolio_positions` with zero rows
   while the ledger references it is the fingerprint of a fresh or ephemeral database — the
   2026-09-02 duplicate-Railway-project failure mode. Crucially this is *not* the same as an empty
   scope: a table holding rows of which none are ACTIVE/PENDING/EXPIRED is a book that genuinely
   closed everything, which Momentum did for three weeks in July 2026, and that case **must** still
   reconcile.
2. **A cap**, `LIFECYCLE_RECONCILE_MAX_ARCHIVE` (default 2000), refuses implausibly large runs. The
   expected first run archives ~170–270 rows, well inside it.

Either guard trips → archive nothing and log loudly. A visible phantom is recoverable; a silently
emptied ledger is not.

## Tests

`tests/test_lifecycle_phantom_reconcile.py`, 10 cases: the defect itself, no invented outcome,
disappearance from `query()`/`stats()` with retention under `record_state="ALL"`,
PENDING/EXPIRED kept in scope, idempotency, dry-run, both guards, and — the important one — that a
genuinely open Momentum book survives, which fails the moment the re-derived `make_uuid` drifts
from what the mapper upserts (the failure mode that would archive an entire book).

119 existing lifecycle tests still pass; `ruff` clean.

## What this invalidates

- The **"44% resolved"** cohort figure and the **December–January maturity estimate** derived from
  it. Both were computed over this ledger. Void until recomputed after the fix lands.
- Any `/api/lifecycle/stats` `open_trades` figure published before the fix.

## Separate defects found in the same pass — not fixed here

1. **Duplicate rows for one position.** BRIGADE appears 7× at entry ₹644.43 with `0d`, PNB 3× at
   ₹111.76, DCBBANK and SONAL 2× each. Re-seed duplicates. Re-measure after reconciliation — most
   are expected to archive out, since the duplicates are departed source rows.
2. **The `DATE` column means two things.** Entry date on Active rows, exit date on closed rows
   (ONWARDTEC: Active 2026-10-13 / 3d, closed 2026-10-16). One header, two meanings.
3. **Headline totals are sums of per-trade percentages.** The owner's sheet shows Target Achieved
   `729.11%`, Stop Hit `−466.89%`, Overall `262.22%`. With 20 slots per book that overstates the
   book by roughly the slot count — the exact failure `db/perf_stats.py` exists to prevent. The
   site must publish book-weighted return.
4. **Unrounded float on a user-visible surface.** `EVEREADY` SL renders `₹309.22499999999997`.
5. **Momentum exit labels.** `/api/momentum-portfolio/summary` reports `target_hits: 0`,
   `stop_hits: 1`, `cut_early: 90` of 91. Momentum has no fixed target by design (TARGET is `—`),
   so `target_hits: 0` is expected — but labelling a trailing-stop exit `cut_early` is misleading
   and makes the book look undisciplined.
