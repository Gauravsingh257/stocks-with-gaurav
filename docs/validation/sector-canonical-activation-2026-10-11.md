# Sector canonical taxonomy — activation validation

> **STATUS: LIVE** · workstream: `selection` · validated 2026-10-11 · flag **OFF** throughout
> Subject: should `SECTOR_STRENGTH_CANONICAL=1` (PR #219) be switched on?
> **Verdict: GO for the flag, but it is NOT the fix for the producer race.**

## 1. The three sector systems (durable architecture)

| # | Module | Taxonomy | Consumers | Coverage |
|---|---|---|---|---|
| 1 | `sector_rotation_engine.py` | 22 sectors, no "Banking" | public `/sectors`, homepage widget | n/a |
| 2 | `sector_strength.compute_sector_strength()` — **yfinance index path** | 11 keys incl. orphan **"Banking"/"Insurance"** | cold-cache callers | **~13%** |
| 2b | `sector_strength.compute_sector_strength_from_index_snapshot()` — **canonical index path** (PR #219) | 13 canonical keys | cold-cache callers when flag ON | **~46%** |
| 3 | `sector_strength.compute_sector_strength_from_candles()` — **constituent path** | canonical, every sector in the scanned universe | the scan itself | **~99%** |

All of them write the **same** Redis key `sector:strength:<date>` (6h TTL). Last writer wins.

## 2. Activation path, traced end to end

```
SECTOR_STRENGTH_CANONICAL ─► compute_sector_strength()
                              └─► classify_symbol(symbol, strength) ─► band + rel_20d_pct
                                   ├─► exceptionalism.score_and_qualify()
                                   │     ├─ sector_rel20 ─► adds the `rs_sector` SCORE dimension
                                   │     └─ sector_band  ─► qualifies()
                                   └─► regime_governor.sector_multiplier()  (ranking_engine)
```

Two mechanics decide everything:

- **`qualifies()`** ([exceptionalism.py:169](../../services/exceptionalism.py#L169)) — `unknown`, `neutral` and `leading` behave **identically**. Only `lagging` is penalised: it needs score ≥ `EXC_OVERRIDE_MIN` (90). **A band can only help by avoiding `lagging`, never by gaining `leading`.**
- **`sector_multiplier()`** ([regime_governor.py:483](../../services/regime_governor.py#L483)) — `leading` ×1.15, `neutral`/`unknown` ×1.0, **`lagging` ×0.6**, applied to `final_score` in `ranking_engine` (`SECTOR_LEADERSHIP_SCORING_ENABLED=1`).

## 3. The finding that changes the recommendation

`run_validation_scan` writes the **constituent** result to the cache at
[validation_engine.py:782](../../services/validation_engine.py#L782) **before** reading
`compute_sector_strength()` at :816. That read is a cache hit, so **in-scan the constituent
path always wins**.

Measured from `signals_log.exc_sector_band` (exposed by PR #221), 8 consecutive scan days:

| date | rows | unknown | neutral | lagging | leading | coverage |
|---|---:|---:|---:|---:|---:|---:|
| 09-30 | 2200 | 8 | 1495 | 509 | 188 | **99%** |
| 10-01 | 2200 | 10 | 1471 | 509 | 210 | **99%** |
| 10-02 | 2200 | 6 | 1711 | 272 | 211 | **99%** |
| 10-05 | 2200 | 11 | 1707 | 272 | 210 | **99%** |
| 10-06 | 2200 | 8 | 2033 | 123 | 36 | **99%** |
| 10-07 | 2200 | 8 | 1638 | 366 | 188 | **99%** |
| 10-08 | 2200 | 8 | 2069 | 123 | 0 | **99%** |
| 10-09 | 2200 | 11 | 2109 | 80 | 0 | **99%** |

**This corrects the Phase-5 audit claim that the sector signal was dead.** It was never dead in
the selection path. The earlier reading came from inspecting the Redis key at a moment the index
path had overwritten it, joined against universe-table labels rather than `get_sector()`.

## 4. Shadow comparison (offline, flag OFF)

Model: hold `exc_score` fixed, re-run `qualifies()` under each band set, re-rank, take the top
`EXCEPTIONALISM_SOFT_CEILING` (20).

**Model validation first** — scan `VAL-SWING-2026-10-09-133565b8`, gate pool 121:
reproduced the real selected set **5/5, zero false positives, zero misses**. The model is sound.

| variant | qualified | selected | delta |
|---|---:|---:|---|
| legacy bands (as production ran) | 5 | 5 | baseline (= actual) |
| canonical index bands | 4 | 4 | **−1** (SKYGOLD removed, nothing added) |

Band transitions over the full 2,200-row scan: `neutral→unknown` 1155, `neutral→lagging` 519,
`neutral→neutral` 248, `neutral→leading` 187, `lagging→lagging` 49, `lagging→unknown` 31.

**This models a counterfactual that cannot occur**, because of the cache precedence in §3 —
included only to bound the risk if precedence ever changed.

## 5. Empirical precedence test (flag ON)

| cache state | `_served_from` | sectors |
|---|---|---|
| warm (in-scan, constituent already written) | `redis_cache` | constituent, **unchanged** |
| cold (first caller of the day) | `index_snapshot_canonical` | 13 canonical, orphans gone |

**So the flag cannot alter selection.** It only replaces the orphan taxonomy for cold-cache
consumers.

## 6. Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | No invalid mappings | ✅ orphans `Banking`/`Insurance` dropped + logged; every emitted key is a real stock label |
| 2 | No data leakage / look-ahead | ✅ bands derive from the same snapshot the scan reads; no forward data |
| 3 | Expected coverage | ⚠️ canonical index path 46% — **below** the constituent path's 99%; 9 sectors unmapped, reported explicitly |
| 4 | Explainable ranking changes | ✅ mechanics isolated to two functions; model reproduces production 5/5 |
| 5 | Risk controls preserved | ✅ thresholds, `EXC_OVERRIDE_MIN`, multipliers, cap, stop logic untouched |
| 6 | Reproducible | ✅ `tests/test_sector_taxonomy.py` (12) + `scripts/ceiling_cohort_study.py`; mapping asserted deterministic |
| 7 | Rollback verified | ✅ unset the env var; merge is inert with it unset (confirmed live: key still shows `leading=['Banking']`) |
| 8 | Stale data | ✅ snapshot age not asserted by this path — inherits `index_ohlc` freshness; flagged below |

## 7. Risks

1. **The producer race is NOT fixed.** Three writers, one key, last-writer-wins. The flag improves
   what the index path emits; it does not make the winner deterministic.
2. **Canonical index coverage (46%) < constituent coverage (99%).** If precedence ever inverted,
   cold-cache consumers would see worse coverage than the scan. Do **not** "promote" the canonical
   index path over the constituent one.
3. **`lagging` is the only band that bites.** Any change that converts `unknown`→`lagging` silently
   tightens selection (×0.6 and a 90-score bar). The 519 `neutral→lagging` transitions in §4 are
   exactly this hazard, and are the reason the counterfactual removes a stock rather than adding one.
4. **Stale-snapshot handling is inherited, not asserted** — the canonical path does not check
   `index_ohlc` age; a stale snapshot yields stale bands rather than `unknown`.

## 8. GO / NO-GO

**GO — switch `SECTOR_STRENGTH_CANONICAL=1` on `web`.** It is a correctness fix with a bounded
blast radius: it cannot touch selection (§5), and it removes a taxonomy that can never attach to a
stock. Expected visible effect: `/api/research/*`, `ranking_engine`, `regime_governor` and
`market_health` stop seeing `leading=['Banking']` for zero stocks.

**NO-GO on treating this as the sector fix.** The real defect is the shared-key race (§7.1). The
follow-up — make the constituent path authoritative and stop other producers overwriting it — is
a separate, larger change requiring its own validation.

### Activation
```
railway variables --service web --set SECTOR_STRENGTH_CANONICAL=1   # outside market hours
```
Then confirm the next cold-cache write: `sector:strength:<date>` should show
`_served_from: index_snapshot_canonical`, 13 canonical keys, no `Banking`.

### Rollback
```
railway variables --service web --unset SECTOR_STRENGTH_CANONICAL
```
Next cold cache reverts to the legacy path. No redeploy required; no data migration either way.
