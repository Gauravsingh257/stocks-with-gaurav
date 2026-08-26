# Phase 0 — Foundation Hardening

> **STATUS: LIVE** · workstream: `selection` · last substantive update: 2026-08-23
> Phase 0 is deployed with its flags ON. Current.
> Current project state lives in [`PROJECT_STATE.md`](PROJECT_STATE.md).

Makes the inputs to stock selection trustworthy and measurable. It deliberately
changes **what the system knows**, not yet **what it decides**.

Every change ships behind a flag that defaults to the current behaviour, so
merging this is a no-op in production until a flag is deliberately flipped.

---

## Flags

| Flag | Default | Effect when ON |
|---|---|---|
| `PHASE0_KITE_OHLC` | `0` | Research reads the scanner worker's full-universe Kite OHLC snapshot instead of per-symbol yfinance |
| `PHASE0_REAL_SECTORS` | `0` | `get_sector()` resolves through the authoritative NSE industry classification |
| `PHASE0_NO_SYNTHETIC` | `0` | sha256(ticker) technical / sentiment / fundamental values are never produced |

Nothing else changed. With all three off the touched code paths are
arithmetically identical to `main` — the full test suite's failure set is
byte-identical before and after (26 pre-existing failures/errors, 0 new).

---

## 1. Reliable market data — `services/universe_ohlc.py`

**Problem.** The teardown measured the research scans pulling per-symbol
yfinance bars and getting 1,153/2,200 symbols on one scan and 1,987/2,200 on
another *the same day*. Cross-sectional ranking compares a stock against its
peers; when the peer set is a different random half of the market every morning,
a percentile rank measures nothing.

**Why not "just set `RESEARCH_DATA_SOURCE=kite`".** Three blockers:

1. `DataIngestion._resolve_token()` raises `NotImplementedError`. The Kite branch
   catches it and returns an empty frame, so flipping that env var today would
   black out the entire research feed rather than improve it.
2. Kite historical is capped at **3 req/sec** → ~12 minutes for 2,200 symbols,
   and SWING/LONGTERM scan separately, so ~25 min/day of a rate-limited key
   shared with the live trading engine.
3. The research scheduler runs **inside the web service**
   (`dashboard/backend/main.py` → `start_scheduler()`), and the existing Kite
   layer states in its own docstring that it runs "exclusively in the cron
   worker — never on the web request path."

**Design.** The fetch moves to the **scanner worker**, which already owns the
throttled Kite fetcher (`services/scanners/data_layer.py`, proven in production)
and is isolated from both the web service and the trading engine. It publishes
one snapshot per day; both horizons read it. One fetch replaces two, and every
engine compares the same universe on the same bars.

Transport is Redis, mirroring `services/scanners/snapshot_store.py`: gzip+base64
columnar shards under an isolated `ohlc:universe:*` namespace, with a write gate
that **refuses a thin snapshot** (fewer than 200 symbols) so a half-failed fetch
can never replace a good snapshot with a worse one.

Consumers fall back to their existing per-symbol fetch for anything the snapshot
misses, so coverage can only improve, never regress.

## 2. Authoritative sectors — `services/sector_classification.py`

**Problem.** Three competing hardcoded dictionaries — `engine/swing.py` (96
symbols), `services/portfolio_risk.py` (128), and `services/pil/reference_data.py`
reusing the first — against a 2,553-name universe. ~96% resolved to
`"Others"`/`"OTHER"`, which is **exempt** from the diversification cap and
**passes** the governor's `require_not_lagging` rule. The sector layer was inert
for almost every stock the system actually picks.

**Source.** NSE publishes its own industry classification through the NIFTY
constituent files. `ind_niftytotalmarket_list.csv` carries **752 symbols across
22 official industries** and covers the whole investable universe (largecap 100 +
midcap 150 + smallcap 250 + microcap 250). Free, no auth, and defined by the same
body that defines the sector indices.

`EQUITY_L.csv` — the file the universe loader already downloads — has **no**
industry column. Verified, not assumed.

**Resolution order** (first hit wins, never guessed):

1. NSE official industry — authoritative
2. yfinance sector, read from the fundamentals cache (no extra network call)
3. legacy hardcoded map — back-compat
4. `"Unknown"` — honest

Measured coverage over the 2,200-symbol scan universe: **3.8% → 29.4%** from tier
1 alone on a cold cache. Tier 2 lifts it substantially once the fundamentals
cache is warm (verified: KRONOX/SOTL → Chemicals, GARUDA → Capital Goods,
NITINSPIN → Consumer Services). The names that remain `Unknown` are sub-microcaps
outside the investable index universe — which is itself a signal.

> ⚠️ Turning `PHASE0_REAL_SECTORS=1` **changes live selection**: more names become
> cappable by the diversification rule and blockable by `require_not_lagging`.
> This is a deliberate behaviour change and is why it ships disabled.

`engine/swing.get_sector()` is the single point every consumer already routes
through (`sector_strength.classify_symbol` calls it), so widening it there widens
it everywhere without adding a fourth competing map.

## 3. No synthetic intelligence

`sha256(ticker)` values were feeding real selection weight:

| Provider | Was | Now (flag ON) |
|---|---|---|
| `news_analysis` | 100% hash, 0.16 of SWING rank weight | returns `{}` — no news API is wired, so sentiment is *unavailable* |
| `technical_scanner` | hash by default, 0.30+ of SWING rank weight | real OHLC from the universe snapshot; symbols without bars are **omitted** |
| `fundamental_analysis` | hash fallback for 66% of the universe | real yfinance only; others omitted |

**The renormalization that makes this safe.** `data_quality` soft weights sum to
0.90 against a `SCORE_PASS = 0.45` threshold, and sentiment is 0.10 of that.
Because hash sentiment is always ≥ 0.35, **every symbol has silently been
collecting that 0.10 for free**. Dropping the provider without redistributing its
weight would move every score down 0.10 against an unchanged threshold and mass-
reject the universe. Missing components now have their weight redistributed
across the components that are present, so the threshold keeps its meaning — and
when all three are present `_boost == 1.0`, so the default path is unchanged.

The same principle applies upward: `FactorRow` omits factors it has no data for
and records `available_groups`; `_score_candidates` renormalizes its weights over
the terms a row actually has; `signal_explainer` emits fewer signals rather than
narrating a hash.

## 4. Real fundamental history — `fundamentals_quarterly`

Per-quarter P&L and balance sheet so the Long-Term book can eventually score
growth, **acceleration** and **margin trend** — none of which a point-in-time
snapshot can express.

Also fixes a live mislabel: the production scorer assigns `roce = roe`. The
backfill computes real ROCE as `EBIT / (debt + equity)`.

Smoke test (first 40 universe symbols): **95% had data, avg 4.95 quarters each.**

### Deliberately not collected

Verified against the live APIs before deciding, not assumed:

| Field | Why not |
|---|---|
| cash flow | `yfinance.quarterly_cashflow` returns an **empty** frame for every NSE symbol tested (RELIANCE, KRONOX, MSTCLTD, SOTL) |
| promoter % | `nseindia.com/api/*` returns **HTTP 403** without a browser cookie handshake — even from a residential IP, worse from a datacenter IP |
| FII / DII % | same source |
| pledge | same source |

These columns are **absent** from the table rather than present-and-permanently-
null, so nothing can accidentally score on a field that will never be populated.
They need a paid provider (Screener.in / Tijori / Trendlyne) or BSE XBRL
ingestion — a Phase 1 decision, not a Phase 0 stub.

**Depth is limited and should not be overclaimed:** yfinance returns ~5–6 quarters
of P&L and only ~3 of balance sheet. That supports YoY growth and QoQ
acceleration. It does **not** support multi-year ROCE persistence.

## 5. The evidence base — `forward_returns`

Labels every distinct `(symbol, date)` in `signals_log` with what the stock
actually did next: forward return, MFE, MAE at +5/+10/+20/+60 **trading** days,
trading-days-to-+10%, and NIFTY's move over the same window.

**Keyed on `(symbol, date)`, not `signals_log.id`.** A forward return is a
property of the stock and the day; nothing about a scan changes it. Measured on a
real 69,243-row production sample: **3.4× collapse** to 20,250 distinct pairs
(higher on the full corpus, which has more scans/day). Labelling per row would
store the same number several times over and force a rewrite of the largest table
on a volume that has already hit 84% once. Consumers `JOIN` instead.

**Unelapsed windows are NULL, never truncated.** The corpus spans 2026-04-25 →
2026-08-21 (~82 trading days), so a +60 trading-day label barely exists yet.
Computing "+60d" from 30 available bars would fabricate an outcome and quietly
bias every calibration built on it. `bars_available` records what backed each row.

Validated end-to-end on the real production sample:

```
19,778 pairs labelled | 2026-04-25 → 2026-08-21
5d 94.0%   10d 87.9%   20d 81.1%   60d 0.01%
+10% touch rate: 45.44%
```

That 45.44% independently reproduces the teardown's central finding — **touching
+10% within six weeks is a market base rate, not an edge** — computed from the
corpus itself rather than a two-date sample. It is the benchmark every future
gate must beat.

---

## Running the backfills

Both are **out-of-band scripts**. Never call them from a request handler or a
FastAPI startup hook — that is exactly the data-volume work that caused the
2026-08-02 healthcheck timeout and 502'd the web service.

```bash
python -m scripts.backfill_forward_returns --dry-run     # report corpus shape
python -m scripts.backfill_forward_returns               # label everything
python -m scripts.backfill_forward_returns --limit 5000  # newest-first subset

python -m scripts.backfill_fundamentals_quarterly --limit 50   # smoke test
python -m scripts.backfill_fundamentals_quarterly              # full universe
```

Both are idempotent. Re-running the forward-return backfill refreshes rows whose
windows have since elapsed, so a NULL +60d becomes a real number once 60 trading
days have actually passed. **Schedule it; don't run it once.**

## Rollout order

1. Merge with all flags off — no behaviour change.
2. Run both backfills on the worker. Confirm `forward_return_stats()` coverage.
3. Turn on `PHASE0_KITE_OHLC` on the scanner worker first (it only *writes*),
   confirm `snapshot_status()`, then on the web service (which then *reads*).
4. Turn on `PHASE0_REAL_SECTORS` — expect selection to change; compare against
   the previous day's picks before keeping it.
5. Turn on `PHASE0_NO_SYNTHETIC` **last**, and only after step 3 is stable —
   without an OHLC snapshot it correctly yields zero technical scores.

Each is independently reversible without a redeploy.
