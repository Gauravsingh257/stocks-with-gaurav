# Fundamentals coverage (Yahoo `.info`)

> **STATUS: LIVE** · workstream: `platform` / `selection` data · created 2026-09-27
> How the fundamentals cache is filled, why coverage used to swing by day, and the
> `FUND_COVERAGE_FIX` flag that fixes it. No scoring, ranking or selection code is involved.

## Flow

```
08:30 Swing agent ─┐  generate_rankings + run_validation_scan
08:40 LT agent    ─┼─► analyze_fundamentals(2,187 symbols, FUND_FETCH_CONCURRENCY=8)
stock search      ─┘        └─► _fetch_snapshot: cache hit (< FUNDAMENTALS_CACHE_TTL_H=24h) or
                                 _fetch_yf_info → Yahoo .info → FundamentalSnapshot → cache
                                 (/data/fundamentals_cache, persistent volume, one JSON per symbol)
consumers: validation L2 quality gate (+ its hard filters), ranking_engine fund/growth/quality,
           LT shadow eligibility, sector_classification (cache read only). Momentum: none.
```

Swing runs first and fills the cache. The Long-Term scan at 08:40 reads the same cache, so both
books see identical coverage each day.

## Root cause (measured 2026-09-27)

1. **A burst trips Yahoo's limit.** At 8 workers the fetch runs at about 1,000 requests/minute.
   Yahoo answers roughly 900 requests, then raises `YFRateLimitError` for **every** further
   request for about 4–5 minutes. A sustained ~50/minute never tripped it: about 2,200 requests
   over 40 minutes, 0 errors.
2. **A failure was cached as data.**
   - `_fetch_yf_info` swallowed the exception and returned `{}`.
   - `_fetch_snapshot` treated that as "no fundamentals" and cached a hash placeholder for 24h.
   - Every scan that day then inherited the failure. On 2026-09-27 the production cache held
     1,095 such hash files.
3. **The loss is ordered.** The fetch walks the universe in A→Z order, so the budget runs out
   part-way through the alphabet. In production, presence among fetchable names by A→Z quintile
   was 0.92 / 0.87 / 0.47 / 0.16 / 0.40. Company size had no effect.
4. **The effect reaches the live gate.** The L2 hard filters (market cap < ₹300 Cr, PE < 0,
   PE > 200) only run when fundamentals are present. Missing data therefore let those names skip
   the filters. From 2026-08-25 to 09-25, 44 of 682 LT picks and 64 of 1,188 Swing picks lacked
   fundamentals but would have failed a hard filter on current `stock_universe` values (mostly
   PE > 200). High-coverage days pass about 7% fewer names through L2 than low-coverage days.

## Fix — `FUND_COVERAGE_FIX` (default OFF = byte-identical legacy behaviour)

- **Failures are never cached.** `_fetch_yf_info` returns a `FetchFailed` dict: still empty, so
  every existing caller behaves as before, but it records the reason. With the flag on, a
  transient failure returns the usual placeholder but writes nothing, so the next caller retries.
  Genuine "no fundamentals" answers are still cached for 24h, so no calls are wasted.
- **Paced pre-warm.** `scripts/prewarm_fundamentals.py` is run by `agents/runner.py` at **06:45 IST
  on weekdays**, as a subprocess with a 15-minute misfire grace.
  - It refreshes entries older than 12h, at 50 req/min with 2 workers.
  - The order is a deterministic date-hashed one, so it isn't alphabetical.
  - A rate-limit response pauses all workers for 300s. Failed symbols get 2 retry passes.
  - It stops hard at **08:15 IST**, so it can never overlap the scans.
  - The 08:30/08:40 scans then read a warm cache.
- Cache writes are atomic (temp file + `os.replace`).
- **Rollback:** unset `FUND_COVERAGE_FIX`. There's nothing to migrate, because the cache format
  is unchanged.

## Before vs after (local, live Yahoo, same 2,200 symbols, production code paths)

| | BEFORE (flag off, scan path @8) | AFTER (flag on: pre-warm, then scan path) |
|---|---|---|
| Real fundamentals | **658 (29.9%)** | **2,173 (98.8%)** (27 are genuinely empty at Yahoo) |
| Rate-limited requests | 1,536 | **0** (no retry pass needed) |
| A→Z quintile coverage | 0.99 / 0.50 / 0 / 0 / 0 | **0.99 / 0.98 / 0.99 / 0.99 / 0.98** |
| Provider calls during the scan | 2,200 | **0** (all served from the warm cache) |
| Time in the scan's fundamentals step | 76s | **5s** |
| Pre-warm | — | 2,200 requests in 44 min (06:45 → ~07:30), peak memory trivial, 2 threads |

Measured on a residential connection. Railway's egress IP may have a different budget. The
cooldown and retry passes cover a tighter limit, and a symbol that still fails is simply fetched
by the scan as before.

## What this changes live (why it is flag-gated)

Nothing in scoring, ranking or selection changes, but more stocks now have fundamentals. That
means:

- the L2 hard filters actually run for ~900 more names a day (see Root cause §4);
- `fundamental_score`, `growth` and `quality` percentiles cover the full universe;
- the LT shadow's eligible pool is no longer throttled.

Expect L2 to pass roughly 7% fewer names, and roughly 5–7% of recent Swing/LT picks would have
been rejected by the existing hard filters. Every high-coverage production day (for example
2026-09-17/18) already behaved this way. `FUND_COVERAGE_FIX` is recorded in the LT shadow's
`control_env`, so the switch is visible in shadow provenance.
