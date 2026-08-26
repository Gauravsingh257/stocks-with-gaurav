# PHASE G2 — Canonical Stock Engine Architecture (DESIGN ONLY)

> **STATUS: HISTORICAL** · workstream: `engine` · last substantive update: 2026-05-17
> Target blueprint (DESIGN ONLY). Large parts have since shipped (state machine, risk engine, momentum book, PIL). Read as design intent, not as current architecture.
> Current project state lives in [`PROJECT_STATE.md`](PROJECT_STATE.md).

No implementation. This is the target blueprint the platform migrates to.
Every component names the **real existing module** it reuses or replaces,
so this is a migration map, not a greenfield fantasy.

Philosophy (now canonical): **identify high-probability opportunities and
WAIT for proper execution. Do not manufacture trades.** Inventory is
whatever passes quality — 0, 3, 11, or 25. Zero is an acceptable output.

---

## 0. CORE PRINCIPLE — one engine, one lifecycle, three horizons

Today there are 3 disconnected systems (live SMC index-only / research
agents / watchlist-positions). Canonical = **ONE pipeline**, parameterised
by horizon. The planned-execution state machine that already works
(`smc_mtf_engine_v4.py` `detect_setup_a` + `STRUCTURE_STATE`) becomes the
**single entry brain for every horizon**. Everything else feeds it or
consumes its output.

```
[1] MARKET+REGIME GATE        services/market_regime.py        (exists, wire in)
        │  pass only if regime supports longs
[2] SECTOR STRENGTH ENGINE    NEW services/sector_strength.py
        │  rank sectors by relative momentum; keep leaders
[3] STRONGEST-IN-SECTOR       reuse RS vs NIFTY (engine/swing.py)
        │  top names inside leading sectors
[4] QUALITY + LIQUIDITY       services/universe_quality.py (F2, built)
        │  tradability ≥ gate, real OHLCV only
[5] STRUCTURE / ZONE BUILD    engine/swing detectors (OB/FVG/BOS)
        │  compute the planned entry zone (no entry yet)
[6] STATE MACHINE             generalised detect_setup_a
        │  FORMED → (price taps zone) → TAPPED → (rejection) → ARMED
[7] MONITORING STATE          NEW canonical lifecycle table
        │  visible to user as "Monitoring / waiting for ₹95"
[8] ENTRY ACTIVATION          only when CMP ∈ zone ± tolerance
        │  → ENTRY_ACTIVE, SL+target armed
[9] LIVE TRACKING             F-Risk sizing + RR/structure tracking
        │
[10] COMPLETION → ANALYTICS → HISTORY → recycle to [1]
```

---

## TASK 1 — Engine designs (per horizon, ONE machine)

All three horizons use the **same state machine and lifecycle**; only the
timeframe, zone source, hold, and risk band differ.

| | Intraday/MTF | Short-term/Swing | Long-term |
|---|---|---|---|
| Timeframe | 5m/15m + 1h bias | daily + weekly | weekly + fundamental |
| Zone source | ORB / VWAP / sweep / OB-FVG (5m) | daily OB/FVG/BOS | weekly demand + accumulation base |
| Hold | 1d–2w | 1–6mo | 6mo–2y |
| Target band | 5–20% | 20–80% | 100–500% (staged) |
| SL band | structure / ~2–4% | structure / ~5–8% | 10–15% |
| Gate adds | sector momentum + RVOL | sector + RS + accumulation | macro + sector cycle + earnings quality (Task 8) |
| Entry | **same**: zone→tap→rejection→activate-at-zone | same | same |

Key: long-term is NOT SMC-stretched. It uses a different *selection*
brain (Task 8) but the **same planned-entry execution machine**.

---

## TASK 2 — Market + Sector intelligence (MANDATORY pre-gate)

New mandatory layer; nothing reaches the state machine without passing it.

**2a. Market/Regime** — promote the orphaned `services/market_regime.py`
(`detect_regime`, `get_regime_adjustments`) into the selection path.
Output: `RISK_ON | NEUTRAL | RISK_OFF` + breadth (% NSE above 50/200DMA).
Rule: longs only when `RISK_ON` or `NEUTRAL`; in `RISK_OFF` the engine
emits **zero** setups (this is correct behaviour, not a failure — the
F-FORENSICS data proved every down regime loses money).

**2b. Sector strength** — NEW `services/sector_strength.py`:
- For each sector index (NIFTY BANK, IT, AUTO, PHARMA, FMCG, METAL,
  ENERGY, REALTY, …): 20/50-day relative momentum vs NIFTY 50.
- Rank sectors; keep only the top-K *leading* sectors (dynamic K, not
  fixed — a sector qualifies on absolute+relative momentum, not rank
  quota).
- Replaces the current fake "sector_strength = (PE+PB)/2" valuation
  proxy in `fundamental_analysis.py` (that field is mislabeled and must
  not feed selection).

**2c. Strongest-in-sector** — within each leading sector, rank
constituents by RS-vs-NIFTY (reuse `calculate_relative_strength`,
engine/swing.py) + the F2 tradability score. Only the strongest few per
leading sector proceed.

This whole layer is a hard pre-filter: **regime → sectors → strongest
names → THEN structure/zone build.** No structure work is done on a
non-leading-sector name in an unsupportive regime.

---

## TASK 3 — Entry state-machine migration (INDEX_ONLY → equities)

`detect_setup_a` already has the right shape and `is_index()` gating
exists. Migration is *enablement + generalisation*, not a rewrite.

Stages, all flag-gated, shadow-first:
1. **Generalise zone source** — abstract `detect_order_block/fvg` calls
   so the same state machine accepts a daily zone (swing) or weekly zone
   (long-term), not just 5m. Pure refactor, behaviour-preserving on
   index.
2. **`EQUITY_STATE_MACHINE=shadow`** — run the machine on the
   quality+sector-filtered equity set in **shadow** (compute states,
   log transitions to the lifecycle table, fire NOTHING, alert NOTHING).
3. **Backtest the shadow stream** through the existing harness
   (`gated_only`-style) until it clears the `ALPHA_BASELINE.md` gate on
   multi-window + bear regime.
4. **`=alert`** — emit "Monitoring / Armed" notifications only, still no
   auto-action.
5. **`=live`** — equities flow end-to-end. Index path untouched
   throughout (separate flag).

Rollback at every stage = flip the flag to the previous value
(byte-identical fallback, same pattern as `ALPHA_V2`).

---

## TASK 4 — Dynamic inventory (delete the slot machine)

Remove, do not tune: `MAX_SWING_SLOTS`, `MAX_LONGTERM_SLOTS`,
`MIN_SWING_SCAN_K`, `MIN_LONGTERM_SCAN_K`, the `empty_slots<=0 → skip`
logic in both agents, and the `_scored_smc_levels` fallback that exists
only to fill slots.

Replacement rule: a name is in inventory **iff** it independently passes
regime → sector → quality → structure → state machine. Count is an
*output*, never an input. Daily cap optional only as a sanity ceiling
(e.g. 50) that should rarely bind; never a floor. **Zero qualifying =
show zero. "Nothing to do" is a valid, honest state.**

---

## TASK 5 — Monitoring engine (the SBI-at-95 layer)

When the state machine reaches `ARMED` (structure formed, zone computed,
not yet tapped), the opportunity enters **MONITORING**:
- Stored with `planned_entry`, `zone_low/high`, `sl`, `targets`,
  `expires_at`.
- User sees: *"SBI — Monitoring. Waiting for ₹95.00 (CMP ₹100.20). Zone
  94.6–95.4."*
- A lightweight price watcher (reuse the realtime LTP registry already
  in the watchlist OS) checks CMP vs zone each tick.
- It **alerts** ("approaching zone", "in zone") but does **not**
  activate.
- Auto-expire if zone not reached within horizon-appropriate window
  (intraday: same day; swing: N days; long-term: weeks) → state
  `EXPIRED` → ANALYTICS. No dead setups linger (fixes the saturation
  defect structurally).

---

## TASK 6 — Trade activation (precise rules)

Activation fires **only** when, on a confirmed candle close:
```
CMP within zone:  zone_low*(1-tol) ≤ CMP ≤ zone_high*(1+tol)
   tol = 0.5% (configurable per horizon)
AND state == ARMED
AND regime still supportive (re-checked at activation)
AND not invalidated (price hasn't broken structure / SL pre-touch)
→ transition ARMED → ENTRY_ACTIVE
→ snapshot entry = fill price, arm SL + targets
→ size via F-Risk (Task: portfolio layer)
→ enter LIVE_TRACKING
```
This **replaces** `auth.py:take_entry`'s instant `INSERT status=ACTIVE`.
`take_entry` becomes "user opts into monitoring this armed idea", not
"open a live position now". Manual override allowed but flagged as
`manual_override=true` in history (so analytics separates disciplined vs
override trades).

---

## TASK 7 — Canonical lifecycle (one state model, one table)

Single source of truth replacing the 3 fragmented half-machines:

```
FILTERED ── regime/sector/quality pass
   │ structure forms
ARMED ───── zone computed, waiting (= MONITORING to the user)
   │ price reaches zone ± tol + confirmation
ENTRY_ACTIVE ── SL/target armed, F-Risk sized
   │
LIVE_TRACKING ── RR / structure / movement tracked
   │            ┌─ target hit ─► CLOSED_WIN
   ├────────────┼─ SL hit ─────► CLOSED_LOSS
   │            └─ time/struct ─► CLOSED_EXIT
EXPIRED ─── (from ARMED) zone never reached in window
   │
ANALYTICS ── outcome, RR achieved, MFE/MAE, regime, sector logged
   │
HISTORY ──── append-only; feeds backtest + future intelligence
   └────────► recycle
```
Implementation vehicle: extend the existing `user_positions` table into
a `lifecycle` table (new states + `armed_at`, `activated_at`,
`zone_low/high`, `planned_entry`, `expires_at`, `regime_at_entry`,
`sector`, `mfe`, `mae`, `manual_override`). `monitor_state` (currently
cosmetic) becomes a *projection* of this real state, not an independent
guess.

---

## TASK 8 — Real long-term engine (replace SMC-stretched)

New `services/longterm_engine.py`, selection brain ≠ SMC:
1. **Macro/regime context** — market_regime + rates/breadth bias.
2. **Sector cycle** — which sectors are in early/mid expansion (from
   Task 2 sector momentum + multi-quarter trend, not daily structure).
3. **Institutional accumulation** — delivery-volume trend +
   volume-on-up-weeks (real OHLCV proxies; honest, no fabricated FII
   data unless a real source is added).
4. **Earnings/fundamental quality** — reuse the *real* yfinance metrics
   already in `fundamental_analysis.py` (ROE, revenue growth, D/E,
   promoter/inst holding) — these exist and are real; they were just
   never used for long-term *selection*, only ranking.
5. **Leadership** — strongest fundamental+momentum names in
   early-cycle leading sectors.
6. **Planned entry** — weekly accumulation base / demand zone → fed into
   the SAME state machine (zone→tap→rejection) with long-term SL band
   10–15%, staged targets to 100–500%.

`score_longterm_candidate` (engine/swing.py) is **deleted** from the
long-term path (kept only if some swing reuse remains).

---

## TASK 9 — Cleanup plan (migration-safe, phased removal)

| Item | Removal method | Safety |
|---|---|---|
| Ungated `_scored_smc_levels` fallback | already has `gated_only` flag → default it ON in research, then delete the branch | flag first, observe, then delete |
| Instant-entry `take_entry` | repoint to "start monitoring"; keep old behaviour behind `LEGACY_INSTANT_ENTRY=1` for 1 release | flag + 1-release grace |
| Fixed-slot forcing | delete `MAX_*_SLOTS`/`MIN_*_SCAN_K` usage after dynamic-inventory path is live in shadow & validated | shadow-validated before delete |
| Dead lifecycle states (`monitor_state` guesswork) | replace with projection of canonical table; remove old computation | parallel-run, diff, then cut |
| LONGTERM-SMC | disable via horizon flag → delete after new long-term engine clears backtest gate | gated, backtest-proven first |

Nothing is deleted before its replacement is shadow-validated and the
flag has been default-flipped with production observed.

---

## TASK 10 — Implementation roadmap (safest order, flagged, rollbackable)

Each phase: commit → push → Railway/Vercel verify → backtest/shadow
gate → only then next. Every behavioural change behind a default-OFF
flag, byte-identical when off (proven pattern from F1/F3).

**G2-1 (now, low-risk, high-value):** Default the existing `gated_only`
ON for the research/recommendation path → kills the −0.39% fallback's
production influence immediately. Pure flag flip, reversible.

**G2-2:** Build `services/sector_strength.py` + wire
`market_regime.detect_regime` into a **read-only** "would-gate" log
(shadow). Measure how many current picks the regime/sector gate would
have killed. No behaviour change.

**G2-3:** Canonical lifecycle table (additive schema) + write-path in
shadow alongside `user_positions`. No reads switched yet.

**G2-4:** Generalise `detect_setup_a` zone source (behaviour-preserving
refactor; index path regression-tested unchanged).

**G2-5:** `EQUITY_STATE_MACHINE=shadow` — equities through regime →
sector → quality → state machine, logging ARMED/EXPIRED only, firing
nothing. Backtest this stream vs `ALPHA_BASELINE.md` gate across
multi-window + bear.

**G2-6:** If (and only if) G2-5 clears the gate → `=alert` (monitoring
notifications) → soak → `=live`. Repoint `take_entry` to monitoring.

**G2-7:** New long-term engine (Task 8) in shadow → backtest gate →
enable; delete LONGTERM-SMC.

**G2-8:** Remove slot machine + ungated fallback code + legacy instant
entry (all replacements now proven). Final cleanup.

**G2-9 (Intraday / MTF equity engine — added 2026-05-17 from
[PRODUCT_VISION.md](PRODUCT_VISION.md)):** The vision mandates an
**equity** intraday/MTF engine (holding intraday → 1–2 weeks) — ORB ·
VWAP reclaim · liquidity sweeps · momentum continuation · volume
expansion, planned entries only, fed by the same MARKET→SECTOR→STOCK→
QUALITY funnel and the same canonical lifecycle + F-Risk. This did **not
exist** in the original G2-1..G2-8 plan (the only intraday system is the
NIFTY/BANKNIFTY index-options Telegram engine, `INDEX_ONLY`, separate and
untouched). G2-9 follows the identical discipline: design-only doc →
`state_machine_sim`-style intraday simulator → backtest gate
([ALPHA_BASELINE.md](ALPHA_BASELINE.md), profile-appropriate gate like
the G2-6 ruling) → shadow → alert → live, every step flag-gated
default-OFF and reversible. Sequenced **after** G2-6 (SWING activation)
proves the planned-execution pattern end-to-end in production.

Rollback plan: every phase = one flag. Revert = flip flag (no redeploy
needed if env-driven) or revert one commit. Index/live Telegram engine
is on its own independent flag and is never touched by the equity
migration.

---

## What this delivers vs the vision

| Your vision | Canonical design |
|---|---|
| Planned execution, wait for zone | State machine ARMED→activate-at-zone (Task 5/6) |
| No instant fake entries | `take_entry` repointed to monitoring (Task 6/9) |
| Dynamic 0/3/12/25, quality only | Slot machine deleted; count = output (Task 4) |
| Sector + regime mandatory | Hard pre-gate before structure (Task 2) |
| One never-ending loop | Single lifecycle, one table, recycle (Task 7) |
| Long-term ≠ daily SMC | New macro/sector/fundamental engine (Task 8) |
| Don't manufacture trades | Zero is a valid output; no fill-the-slot logic |
| Reuse what works | Engine A state machine + F-Risk + OS kept and central |

---

## Vision → Phase Traceability Map (auditable, [PRODUCT_VISION.md](PRODUCT_VISION.md) is the north-star)

Status legend: ✅ done · 🟡 designed/proven not live · ⛔ not started ·
"shadow" = built, logging only, no user-facing effect.

| Vision element (PRODUCT_VISION.md) | Delivering phase | Status (2026-05-17) |
|---|---|---|
| Kill ungated fallback systems (rule 4) | G2-1 | ✅ live |
| MARKET REGIME gate | G2-2 → enforced in G2-6 | 🟡 shadow |
| SECTOR STRENGTH gate | G2-2 → enforced in G2-6 | 🟡 shadow |
| Full lifecycle tracking (rule 10) / ANALYTICS+HISTORY | G2-3 ledger; producer in G2-6 | 🟡 ledger live (shadow) |
| Generalised planned-zone source | G2-4 | ✅ live (behaviour-preserving) |
| SWING planned-execution engine, backtest-proven | G2-5 | 🟡 proven, not live |
| Corrected activation gate (profile-aware) | G2-6 Step 0 | ✅ approved + frozen |
| SWING: FIND→PLAN→WAIT→ACTIVATE→TRACK (state machine live) | G2-6 Steps 1–4 | ⛔ Step 1 next |
| MONITORING state ("wait for 95, don't activate now") | G2-6 Rung B/C (Task 5/6) | ⛔ |
| ENTRY ACTIVATION only at zone±tol+confirmation | G2-6 Rung C (Task 6) | ⛔ |
| No instant entries (rule 3) — repoint `take_entry` | G2-6 Rung C / G2-8 | ⛔ |
| LONGTERM = macro/sector/accumulation, not stretched SMC | G2-7 (Task 8) | ⛔ |
| **INTRADAY / MTF equity engine** (ORB/VWAP/sweeps) | **G2-9 (new)** | ⛔ not started |
| Dynamic inventory — no fixed 10/20/25 slots (rule 2) | G2-8 (Task 4/9) | ⛔ slot machine still live |
| No over-engineered AI clutter (rule 5) | G2-8 cleanup | ⛔ |
| F-Risk canonical risk engine (position/sector/DD caps) | F-Risk (validated) | ✅ canonical |
| No fake recommendations / no synthetic data (rule 1) | enforced every phase | ✅ ongoing rule |
| Reuse Engine A state machine + F-Risk + OS | whole G2 thesis | ✅ design principle |

**Honest reading of this map:** the platform's *vision and design are
fully aligned*, but most of the user-facing flow (PLAN→WAIT→ACTIVATE,
dynamic inventory, real long-term, intraday) is 🟡/⛔ — designed/proven,
**not yet live**. The live site still runs the legacy instant-entry,
fixed-slot engine. G2-6→G2-9 is precisely the work of making the live
product equal the vision. This gap is acknowledged, sequenced, and
reversible — not hidden.

The migration is mostly **connecting and redirecting components that
already exist and are proven good** (Engine A state machine, F2 quality,
market_regime, F-Risk, OS) and **deleting the proven-harmful**
(fallback, slot forcing, instant entry, LONGTERM-SMC). Low net new code;
high architectural coherence.

Implementation begins only on your go-ahead, starting with **G2-1**
(the safe, immediate win). No production behaviour changed by this
document.
