# PHASE G2-6 — Equity State-Machine Activation (DESIGN ONLY)

**Status: design. No code. No flag exists yet. Nothing in this document
ships until each step is separately approved.** This is the activation
plan for the planned-execution state machine that G2-5 proved on SWING
equities. It defines *how* a backtest-validated engine becomes a live
production path **without ever breaking the running system**, in
flag-gated, shadow-first, individually-reversible steps.

Inputs this builds on (already shipped, verified):
- G2-1 ungated fallback killed · G2-2 regime/sector shadow · G2-3
  `lifecycle_events` ledger (append-only, shadow-written) · G2-4
  generalised `detect_setup_a` zone source · G2-5 `state_machine_sim` +
  `run_state_machine_backtest` + `engine_mode=state_machine` (read-only).
- G2-5 verdict ([ALPHA_BASELINE.md](ALPHA_BASELINE.md#phase-g2-5-result--planned-execution-state-machine-vs-the-validation-engine)):
  SWING all 5 windows positive, mean **+2.01%/trade**, account DD
  1.9–5.8%; LONGTERM still negative.

---

## TASK 0 — The gate ruling that MUST precede any code (human decision)

G2-5 surfaced, and deliberately did **not** silently resolve, a
mis-specification in the F8 gate:

- F8 criterion 2 = "WR ≥48% **at payoff ≥1.5**". It was calibrated
  against Engine B's *low-WR / high-payoff* profile.
- The state machine is the **inverse profile**: WR 59–71%, payoff
  0.93–1.51. At WR ~65%, expectancy is solidly ≥+1.0% even at payoff
  ≈1.0. Mechanically 1/5 SWING windows clear all four; substantively
  **5/5** clear the three criteria that measure edge and survivability
  (expectancy, account drawdown, walk-forward consistency).

### Proposed corrected activation gate (G2-6 gate)

Replace the single fixed `payoff ≥ 1.5` sub-clause with a
**profile-agnostic expectancy/consistency gate** that does not assume a
trade shape. A SWING window "passes" iff **all** of:

| # | Criterion | Threshold | G2-5 SWING result |
|---|---|---|---|
| 1 | Net expectancy / trade | ≥ **+1.0%** | +1.37 … +2.97 (5/5 ✅) |
| 2 | Win rate | ≥ **48%** | 58.97 … 70.73 (5/5 ✅) |
| 3 | Profit factor (Σwins/Σ\|losses\|) | ≥ **1.30** | *to be recomputed per window before flip* |
| 4 | Account max drawdown (F-Risk model) | ≤ **15%** (tightened from 30% — every window was ≤7.6%) | 1.88 … 5.79 (5/5 ✅) |
| 5 | Positive walk-forward sub-windows | ≥ **3/4** | 3/3 … 3/4 (5/5 ✅) |
| 6 | Windows clearing 1–5 | ≥ **4 of 5** SWING windows | TBD on re-run |

Rationale: **profit factor** replaces the payoff proxy — it is the
correct, trade-shape-agnostic measure of "wins outweigh losses" and
naturally accommodates a high-WR engine. Expectancy + WR + PF + DD + WF
together cannot be gamed by a degenerate trade shape. Criterion 6
("4 of 5 windows", was "3 of 4") is *stricter* than F8 because G2-5 gave
us a 5-window SWING sample; we hold a higher bar than the original
single-window F-Risk mistake.

**LONGTERM is excluded entirely** (G2-5: no edge in either engine). The
state machine activation is **SWING-only**. Long-term gets its own
engine in G2-7 (Task 8 of the canonical doc), not this one.

> **Decision required from Gaurav before TASK 1 code:** approve this
> corrected gate (or amend the thresholds). Profit factor for the 5
> SWING windows must be recomputed and recorded in ALPHA_BASELINE.md as
> the frozen G2-6 baseline *before* any flag is built. No flag is
> written until this row exists and the gate is approved.

---

## TASK 1 — The production state machine (the real architectural problem)

`smc_mtf_engine_v4.detect_setup_a` cannot be reused live for equities:
it is wall-clock coupled (`(now_ist() - state.time) > 1800s`),
options-coupled (`option_strike`), `INDEX_ONLY`, and holds
`STRUCTURE_STATE` **in process memory** inside the always-on Telegram
engine. The equity agents (`SwingTradeAlphaAgent`) are a *different
process* (dashboard backend, APScheduler, `runner.py` daily 08:30 IST),
run **once per day**, and are **stateless between runs**.

A planned-execution machine is intrinsically *multi-day*: FORMED today,
TAPPED three days later, FIRE two days after that. A once-a-day stateless
agent therefore needs **durable per-symbol state** that survives across
daily runs and across container restarts (Railway redeploys).

### Design: `services/equity_state_machine.py` (new, G2-6 — not built yet)

A **persistent, daily-bar-clocked** reimplementation of the *same logic
already validated* in `services/state_machine_sim.py` (G2-5) — the sim
is the spec; this is its production sibling. Differences from the sim:

| Aspect | G2-5 sim (backtest) | G2-6 prod engine |
|---|---|---|
| Clock | bar index over a fixed array | one new daily bar per agent run |
| State | local vars in one pass | **persisted** between runs |
| Look-ahead | `_slice_to_date` guarantees none | inherently none (only past bars exist) |
| Output | list of `StateMachineFire` | lifecycle transitions + (later) a rec |

**State store:** a new `equity_sm_state` table (additive DDL via the
same idempotent `init_db()` path as G2-3's `lifecycle_events`). One row
per `(symbol, horizon)` tracking: `phase` (IDLE→FORMED→TAPPED→FIRED→
EXPIRED/INVALIDATED), `ob_low`, `fvg_low`, `fvg_high`, `formed_date`,
`tapped_date`, `bars_since_formed`, `planned_entry`, `stop_loss`,
`target`, `last_eval_date`, `updated_at`. **Not** `lifecycle_events`
(that stays a pure append-only audit log — this is mutable working
state; the two are complementary, never merged).

**Daily tick (idempotent, point-in-time):** for each F2-Good+ symbol
where weekly trend ∈ {BULLISH, STRONG_BULL}:
1. Load persisted phase. Pull daily/weekly OHLC up to *yesterday's
   close* (no intraday look-ahead — agent runs pre-market 08:30).
2. Drive exactly the `state_machine_sim` transition rules one bar
   forward: IDLE→FORMED (OB+FVG), FORMED→TAPPED (price into FVG),
   TAPPED→FIRED (confirmation candle) — or →INVALIDATED (`close <
   ob_low`) / →EXPIRED (`bars_since_formed > expiry_bars`).
3. Persist new phase. Emit the matching `lifecycle_events` row
   (FILTERED→ARMED on FORMED, EXPIRED on expiry, ENTRY_ACTIVE on FIRE).
4. **Idempotency key:** `last_eval_date` — re-running the same trading
   day is a no-op (Railway restart / manual re-trigger safe).

**Correctness anchor:** a CI/backtest assertion that, replayed over a
historical array, `equity_state_machine` produces the *identical* FIRE
set as `state_machine_sim` on the same data. The sim is the frozen
oracle; the prod engine may never silently diverge from the thing G2-5
actually validated.

---

## TASK 2 — Three-rung activation ladder (each rung = one flag, default OFF)

`EQUITY_STATE_MACHINE` env var, read at agent start. Unset/`off` ⟹ the
code path is never entered ⟹ **byte-identical to today** (proven F1/F3
pattern). Rungs are strictly ordered; each requires the previous to have
soaked and been observed in production.

### Rung A — `=shadow` (zero user-visible change)

- `equity_state_machine` ticks daily inside `SwingTradeAlphaAgent.run()`
  **after** the existing ranking/recommendation block, in a
  `try/except` that can never raise into the live path.
- Writes **only** `lifecycle_events` (ARMED / EXPIRED / would-FIRE,
  `source="g2_6_sm_shadow"`). Creates **no** `stock_recommendations`
  row, touches **no** watchlist, sends **no** alert.
- Existing instant-entry pipeline runs **unchanged in parallel**.
- **Exit cri/“soak”:** ≥4 trading weeks of shadow ARMED/FIRE events
  whose realised forward outcomes (computed read-only from later OHLC)
  reproduce the G2-6 gate live. A new read-only endpoint
  `/api/research/sm-shadow-scorecard` (additive, like G2-5's
  `/regime-sector-shadow`) renders shadow vs gate. Promote to Rung B
  only when live shadow ≈ backtest.

### Rung B — `=alert` (recommendations + notify, NO auto-position)

- On FIRE, `equity_state_machine` **does** call
  `create_stock_recommendation(...)` with `setup="SMC_STATE_MACHINE_LONG"`,
  `data_authenticity="real"`, `entry_type="LIMIT"` (planned zone), and a
  distinct `agent_type` or tag so the UI/analytics can separate
  state-machine picks from legacy instant picks.
- Lifecycle: FILTERED→ARMED on FORMED; on FIRE the rec is created in
  state **ARMED/MONITORING** (a *planned* idea), **not** an open
  position. Telegram/dashboard notify "planned setup armed — zone X,
  invalid below Y" (reuses `services/telegram_bot.py`; **the
  index/live Telegram engine is untouched** — separate code path,
  separate flag, explicitly out of scope here).
- User decides. **No auto-execution.** This is the safe value-delivery
  rung: real planned setups surface to the user, capital risk only on
  explicit human action.
- **Exit:** ≥4–8 weeks; realised expectancy of the *armed→user-acted*
  cohort tracked in analytics; must stay consistent with the gate. Any
  regression ⟹ flip back to `=shadow` (one env change, no redeploy).

### Rung C — `=live` (Task 6 activation; the only rung that auto-acts)

- Implements canonical-doc TASK 6: ARMED→ENTRY_ACTIVE fires only on
  confirmed candle close with CMP in zone±tol, regime still supportive,
  not invalidated; sized by the **already-validated F-Risk** portfolio
  layer (no new risk code — F-Risk is the proven, robust piece).
- `auth.py:take_entry` is **repointed** from instant
  `INSERT status=ACTIVE` to "user opts this armed idea into monitoring".
  Old behaviour preserved behind `LEGACY_INSTANT_ENTRY=1` for one
  release (canonical-doc TASK 9 safety), `manual_override=true` flagged
  in `lifecycle_events` so analytics separates disciplined vs override.
- **Entered only after** Rung B has demonstrated, on real forward data,
  that the armed cohort matches the gate. Highest scrutiny; smallest,
  last, most reversible-by-flag step.

No rung is skipped. No rung auto-promotes — each is a deliberate human
go-ahead after observing production.

---

## TASK 3 — Lifecycle & data-flow integration (no new state model)

The `lifecycle_events` ledger (G2-3) + canonical states
(`FILTERED→ARMED→ENTRY_ACTIVE→LIVE_TRACKING→CLOSED_*/EXPIRED`) are
**already the target model** — G2-6 is finally a *real producer* of the
non-FILTERED states instead of the shadow stubs in `auth.py`:

```
F2 Good+ universe ─► weekly-bull gate ─► equity_state_machine daily tick
  FORMED  ─► lifecycle FILTERED→ARMED         (Rung A+)
  TAPPED  ─► (internal phase; no lifecycle change)
  FIRE    ─► Rung A: log only
             Rung B: create_stock_recommendation + ARMED rec + notify
             Rung C: ARMED ─►(zone+confirm+regile)─► ENTRY_ACTIVE
                     ─► F-Risk sizing ─► LIVE_TRACKING ─► CLOSED_*
  no tap / close<ob_low / >expiry ─► EXPIRED|INVALIDATED  (no dead setups)
```

`dashboard/backend/lifecycle.py` (the read-side cosmetic classifier)
becomes a **projection** of these real events (canonical-doc TASK 7) —
deferred to G2-8 cleanup, *not* changed in G2-6 (parallel-run, diff,
then cut; nothing removed before its replacement is proven).

---

## TASK 4 — Reversibility, kill switch, blast radius

| Concern | Control |
|---|---|
| Roll back any rung | `EQUITY_STATE_MACHINE` env one level down (or unset). Env-driven ⟹ **no redeploy** needed. |
| Hard kill | unset the var ⟹ path never entered ⟹ byte-identical to today. |
| Live Telegram/index engine | **Never touched.** Separate process, separate flag, `INDEX_ONLY` unchanged. Explicitly out of scope. |
| Existing instant pipeline | Runs unchanged in parallel through Rung A & B. Only Rung C repoints `take_entry`, and that keeps `LEGACY_INSTANT_ENTRY=1` escape for one release. |
| Bad state row | `equity_sm_state` is derived; truncating it = clean re-derive from OHLC next tick (no capital state lost — positions live in `user_positions`, untouched). |
| Schema risk | Additive only, idempotent `init_db()` (same proven path as G2-3). No column drops, no backfills. |
| Data integrity | Real OHLC only (yfinance/Kite, same sources as G2-5). No synthetic/placeholder values anywhere. Point-in-time enforced (pre-market run ⟹ only closed bars). |

---

## TASK 5 — Step sequence (each = commit → push → Railway/Vercel verify → observe → STOP for go-ahead)

| Step | Deliverable | Gate to next |
|---|---|---|
| **0** | Recompute profit factor for 5 SWING windows; freeze G2-6 gate row in ALPHA_BASELINE.md; **Gaurav approves gate** | gate approved |
| **1** | `equity_state_machine.py` + `equity_sm_state` DDL + sim-equivalence CI assertion. No agent wiring yet. | equivalence test green |
| **2** | Rung A `=shadow` wiring in `SwingTradeAlphaAgent` (guarded, never raises) + `/api/research/sm-shadow-scorecard` | ≥4wk live shadow ≈ backtest gate |
| **3** | Rung B `=alert` (recs + notify, no auto-position) | ≥4–8wk armed cohort matches gate |
| **4** | Rung C `=live` (TASK 6 activation + `take_entry` repoint, `LEGACY_INSTANT_ENTRY` escape) | soak; then G2-7/G2-8 |

Steps 1–4 are **separate approvals**. This document authorises none of
them — it is the map, per the established design-only pattern
([CANONICAL_ARCHITECTURE.md](CANONICAL_ARCHITECTURE.md),
[SYSTEM_MIND_ALIGNMENT.md](SYSTEM_MIND_ALIGNMENT.md)).

---

## What G2-6 does NOT do (scope discipline)

- Does **not** change live behaviour (design only; no flag exists).
- Does **not** touch the index/live Telegram engine or `detect_setup_a`.
- Does **not** activate LONGTERM (no edge — G2-7's problem).
- Does **not** remove the instant pipeline / ungated code / slot machine
  — that is G2-8, only after replacements are proven.
- Does **not** introduce new risk-sizing logic — F-Risk is reused as-is
  (it is the validated, robust component).

## Open question for Gaurav (blocks Step 0)

Approve the **corrected G2-6 gate** in TASK 0 (expectancy + WR + profit
factor + tightened account-DD + 4-of-5 windows, replacing the
mis-specified `payoff ≥ 1.5` sub-clause), or amend the thresholds. Step 1
code does not begin until this gate is approved and its baseline row is
frozen in ALPHA_BASELINE.md.
