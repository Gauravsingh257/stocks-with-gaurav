# PHASE G — System Mind Alignment Audit

Research only. No implementation. Every claim is code-cited (file:line).
Scope: the full system — the live SMC engine (SETUP A–E in
`smc_mtf_engine_v4.py`) **and** the research/watchlist pipeline
(Phases E/F). Index-only setups are noted but not deep-audited per
instruction.

---

## 0. THE CENTRAL FINDING (read this first)

**You have two opposed architectures, and the one that matches your mind
is switched off for stocks while the one that violates it is the
"product".**

| | A — Live SMC engine (`smc_mtf_engine_v4.py`) | B — Research/Watchlist pipeline (Phases E/F) |
|---|---|---|
| Entry model | **Planned state machine**: zone → tap → rejection → fire | **Instant**: user clicks, position ACTIVE immediately |
| Code | SETUP A `:1628`, D `:2379`, E `:2778`; `STRUCTURE_STATE :645` | `auth.py:take_entry:440` |
| Matches your vision? | **YES — almost exactly** | **NO — at nearly every point** |
| Trades stocks? | **NO** — `INDEX_ONLY=True :1043` (NIFTY/BANKNIFTY options only) | YES (the stock-facing system) |
| Alpha proven? | untested on stocks (dormant) | proven none (F-FORENSICS/XRAY) |

The corrective for Phase G is therefore **not** "build planned
execution". Planned execution already exists and is well-built — it is
just confined to index options. The corrective is to **extend engine A
to stocks and retire/Demote engine B to its genuinely good parts
(F-Risk + OS/workflow).**

---

## TASK 3 — ENTRY ACTIVATION AUDIT (answered first; it's the crux)

### Engine A (live SMC): **A — WAITS for planned levels.** ✅ matches vision

`detect_setup_a` (`smc_mtf_engine_v4.py:1628`) is a strict per-symbol
state machine keyed `{symbol}_{bias}` in `STRUCTURE_STATE` (`:645`):

```
FORMED  →  price taps FVG zone (is_price_inside_fvg :1371)  →  TAPPED
TAPPED  →  fvg_rejection(:1399) confirmation               →  FIRE entry+SL+target
```
- Entry only computed at the FVG zone (`:1718 entry = (fvg[0]+fvg[1])/2`).
- Late entries rejected (docstring `:1633-1634`); invalidation
  (`invalidate_structure :1406`, called `:1693`); state expiry 1800s
  (`:1687`).
- SETUP D (`SETUP_D_STATE :700`, `stage:"FORMED"`) and SETUP E
  (`SETUP_E_STATE :703`, `stage:"BOS_WAIT"/"WAIT"/"TAPPED"`) follow the
  same wait-for-zone pattern.

This is **exactly your SBI-at-95 example**: structure formed → wait →
price reaches zone → confirmation → activate. Your mind is already in
this code.

### Engine B (research/watchlist): **B — INSTANT activation.** ❌ violates vision

`auth.py:take_entry:440`: validates `entry_price>0` (`:447`), rejects a
duplicate ACTIVE (`:453`), then **immediately `INSERT … status='ACTIVE'`
(`:462`, `:482`)**. There is **no comparison of live CMP to the planned
entry price** anywhere in the path. The position is LIVE the instant the
button is pressed, at whatever price is passed. `_classify_position
(:523)` only computes a *post-hoc display label* (Running/SL Risk) from
CMP — it never gates activation. This is precisely the "instant fake
entries" anti-pattern you explicitly rejected.

---

## TASK 1 — MINDSET GAP ANALYSIS

| Your intent | Engine A | Engine B | Verdict |
|---|---|---|---|
| Planned execution (wait for zone) | ✅ state machine | ❌ instant `take_entry` | Split; stock side wrong |
| Dynamic count (0/3/12/25, quality-only) | ✅ fires 0..N organically | ❌ `MAX_*_SLOTS=25` + `MIN_SCAN_K=15` floor | Engine B forces inventory |
| No fake opportunities | ✅ no fill-the-slot logic | ❌ ungated fallback fabricates 92% of fills at −0.39% | Engine B contaminated |
| No dead setups forever | ✅ 1800s expiry + invalidation | ⚠️ slot-saturation (Phase D2) | Engine B leaks |
| Sector strength drives picks | ❌ none in A | ❌ valuation proxy mislabeled "sector_strength" | Missing both |
| Regime gates entries | ⚠️ implicit (HTF bias) | ❌ `market_regime.py` orphaned | Largely missing |
| One never-ending loop | ❌ 3 disconnected systems | — | Fragmented |
| Long-term = macro/sector/flow | — | ❌ daily SMC stretched | Wrong by design |

Headline mismatches: **(1) instant vs planned entry on the stock side,
(2) forced fixed inventory + ungated fallback contamination, (3) orphaned
regime engine, (4) absent real sector intelligence, (5) fragmented
3-system architecture, (6) long-term is daily-SMC-stretched.**

---

## TASK 2 — CURRENT ARCHITECTURE MAP (3 disconnected systems)

```
SYSTEM 1  Live SMC (smc_mtf_engine_v4.py)  — INDEX_ONLY=True :1043
   scan loop → scan_symbol:3605 → detect_setup_a/d/e (state machines)
   → Telegram. Stocks: load_stock_universe returns [] (:1059). DORMANT for equities.

SYSTEM 2  Scheduled research agents (weekly)
   swing_alpha_agent / longterm_investment_agent (MAX_*_SLOTS=25)
   → generate_rankings (ranking_engine:751) → run_validation_scan
   → build_*_trade_levels OR ungated _scored_smc_levels fallback (92%)
   → stock_recommendations DB

SYSTEM 3  User watchlist / positions
   watchlist_intel_service monitor_state (DISPLAY only)
   → user clicks Take Entry → auth.py:take_entry (INSTANT ACTIVE)
   → user_positions table → manual close_position

NO bridge between 1↔2↔3. The watchlist state machine is cosmetic; the
position lifecycle is manual; the live engine is index-only. There is no
single FILTER→QUALIFY→MONITOR→ENTRY→MANAGE→COMPLETE→loop.
```

vs **your vision: ONE continuous loop**. Reality: three pipelines that
don't talk.

---

## SETUP A–E AUDIT (the live engine, per your added scope)

| Setup | Code | Pattern | Stock-capable? | Status |
|---|---|---|---|---|
| A | `:1628` | FORMED→TAPPED→rejection→fire; OB+FVG; 1800s expiry | yes (`is_index` gate `:1652`) | **ACTIVE config `:393`**, but dormant (INDEX_ONLY) |
| B | `:1783` | similar state logic | yes (`:1806`) | **DISABLED** (`_DISABLED_SETUPS={"B",…} :1046`) |
| C | `:1945` | index ATR-filtered | index-leaning (`:1974`) | config off `:393` |
| D | `:2379` | `SETUP_D_STATE :700` FORMED state machine | index-only (`:2384`, expiry 14400s index) | ACTIVE, index-only |
| E | `:2778` | `SETUP_E_STATE :703` BOS_WAIT/WAIT/TAPPED | index-only (`:2793`) | ACTIVE, index-only |

**What's strong:** A/D/E are genuine planned-execution state machines
with expiry + invalidation — the *correct* architecture for your vision.
**What's wrong:** the only equity-capable one (A, and dormant B) is
gated off by `INDEX_ONLY=True`. The proven planned-execution machinery
exists but never sees a stock.

---

## TASK 4 — SECTOR + REGIME AUDIT — your suspicion is CORRECT

- **Regime:** `services/market_regime.py` is a real engine
  (`detect_regime:160`, `get_regime_adjustments:247`, NIFTY ADX/EMA).
  **It is imported by NOTHING in the selection/entry path.** Grep of
  `ranking_engine.py` + `engine/swing.py` for "regime" → only
  `format_swing_report(market_regime="UNKNOWN") :444` — a Telegram
  *display string*. `get_regime_adjustments` has no consumer. **The
  regime brain exists and is unplugged.**
- **Sector:** `get_sector` is a static dict lookup
  (`engine/swing.py:66-68`, `SECTOR_MAP`); F-FORENSICS showed 233/260
  trades resolved to `"Unknown"`. `fundamental_analysis.sector_strength`
  is `(pb_score+pe_score)/2` — a per-stock **valuation** proxy, not
  relative sector momentum/rotation. There is **no leadership-sector
  engine**. Your "Banking bullish → SBI strongest" step has no code.

---

## TASK 5 — LIFECYCLE AUDIT — fragmented (confirmed)

Your intended `FILTERED→MONITORING→ENTRY ACTIVE→LIVE→ANALYTICS→HISTORY`
exists only in **pieces, across the 3 disconnected systems**:
- `monitor_state` (WATCH/BUILDING/BREAKOUT SOON/GOOD ENTRY/ACTIVE) is
  computed for display in `watchlist_intel_service` but **drives no
  transition** — it never moves a stock from MONITORING to ENTRY ACTIVE.
- Position lifecycle (`user_positions`: ACTIVE/TARGET_HIT/SL_HIT/CLOSED)
  is **manual** (button → `take_entry` / `close_position`).
- `stock_recommendations` has its own slot/expiry logic, disconnected
  from both of the above.
There is no rotation engine. The lifecycle is three half-machines.

---

## TASK 6 — DYNAMIC ENGINE AUDIT — NOT dynamic (confirmed)

`agents/swing_alpha_agent.py:10 MAX_SWING_SLOTS=25`,
`longterm_investment_agent.py:11 MAX_LONGTERM_SLOTS=25`. `empty_slots
<= 0 → "slots occupied — scan skipped" :27-30`. `scan_top_k =
max(empty_slots, MIN_SWING_SCAN_K=15) :35`. So inventory is **hard-capped
at 25 and floor-pressured toward filling slots** — and the ungated
fallback exists *specifically to fill slots the gated scorer can't*
(ENGINE_XRAY: 92% of fills, −0.39%). This is the exact "force fixed
count / produce fake opportunities" failure you named. Engine A, by
contrast, is naturally dynamic (fires only on real zone+rejection).

---

## TASK 7 — LONGTERM ENGINE AUDIT — fundamentally wrong (confirmed)

`score_longterm_candidate (engine/swing.py:716)` is **daily+weekly SMC**;
it even *accepts NEUTRAL weekly trend* (`:760-763`). Real fundamentals
(`fundamental_analysis.py`, live yfinance) feed only cross-sectional
*ranking*, never the long-term levels/entry/exit. There is **no macro
engine, no sector-cycle engine, no institutional-flow engine**. It is
exactly "daily SMC logic stretched longer" — your words, confirmed in
code — and F-FORENSICS proved it loses money (−2.36%, −6.48% early-2025).

---

## FINAL DELIVERABLE

**1. Matches your vision**
- SETUP A/D/E planned-execution state machines (zone→tap→rejection→fire,
  expiry, invalidation). This is your mind, in code.
- F-Risk portfolio engine (deterministic sizing; robust across all
  windows).
- The OS/workflow + trust layer (watchlist UI, positions, ACK).

**2. Became disconnected**
- `market_regime.py` (built, unplugged from selection/entry).
- The 3 systems (live / research / watchlist) never bridged.
- `monitor_state` machine (cosmetic; drives no transition).

**3. Overengineered**
- Research pipeline: decision/maturity/calibration layers (already
  stripped from UI in Phases C/D); ungated `_scored_smc_levels`
  fallback; the whole hash-noise→quality-gate→percentile stack feeding
  a scorer that's either inert or junk.

**4. Missing**
- Planned-entry activation on the **stock** side (CMP-reaches-zone gate).
- Real sector-leadership + regime gating in the decision path.
- A genuine long-term macro/sector/flow engine.
- A single continuous lifecycle loop bridging the 3 systems.

**5. Actually strong**
- SETUP A/D/E state-machine design.
- F-Risk engine.
- OS/workflow/trust layer + the F8 backtest harness (now honest).

**6. Should be removed**
- Ungated `_scored_smc_levels` fallback (−0.39%, 92% of fills — actively
  harmful).
- LONGTERM-SMC in its current form.
- Fixed slot caps + MIN_SCAN_K forced floor.

**7. Should be redesigned**
- Stock entry activation → adopt SETUP-A's state-machine model
  (zone→tap→rejection) instead of instant `take_entry`.
- Long-term → macro/sector-cycle/institutional engine (not SMC).
- Lifecycle → one rotation engine consuming a dynamic, quality-only
  inventory.

**8. Canonical architecture (recommendation)**
> Make **Engine A's planned-execution state machine the canonical entry
> model for all horizons**, fed by a **dynamic quality-only universe**
> (F2) and **gated by the now-orphaned `market_regime` engine**, with
> **F-Risk** as the position layer and the **OS/workflow** as the
> surface. Demote Engine B to a *research feed* only (candidate ideas
> that must still pass Engine A's zone→tap→rejection before going live).
> Delete the ungated fallback and LONGTERM-SMC. Extend `INDEX_ONLY`
> handling so equities run through the same state machine.

The single most important sentence: **the architecture you want already
exists as the live SMC state machine — it was just never pointed at
stocks, and a second, vision-violating pipeline became the product.**

No code changed in this phase. Recommendation only; implementation
sequencing is the next decision and is yours to direct.
