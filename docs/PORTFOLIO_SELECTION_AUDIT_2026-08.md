# Portfolio Selection & Risk Audit — August 2026

> **STATUS: LIVE** · workstream: `portfolio` · last substantive update: 2026-08-19
> Phase 0 forensics complete; decisions D1-D6 are still OPEN. Active.
> Current project state lives in [`PROJECT_STATE.md`](PROJECT_STATE.md).

**Trigger:** `NSE:VIPULLTD` sitting in the LONGTERM book at **−21.37%** (entry ₹14.93, CMP ₹11.74,
SL ₹8.93, T1 ₹35.93, 30d held) — a ₹15 micro-cap with ~₹1.1 Cr/day turnover.

**Status:** Phase 0 (forensics) COMPLETE — evidence below. Phases 1-5 pending decisions.

---

## 1. The Master Prompt

> Audit how stocks enter and leave the SWING and LONGTERM portfolios, end to end, and explain —
> with evidence from the live book, not from the code's intent — why `VIPULLTD` is held at −21%.
>
> Answer specifically:
>
> 1. **Stop-loss policy.** What is the documented SL threshold per book, where is it enforced, and
>    at what boundary? Why is a −21% open loss possible if a threshold exists? Is the threshold a
>    *promotion-time* filter, a *runtime* exit, or both?
> 2. **Universe / liquidity policy.** What price, turnover, market-cap, segment and data-quality
>    floors exist? Which are actually wired into the live path versus written but flag-gated off?
>    Could a ₹15 micro-cap legitimately pass every enabled gate?
> 3. **Bypass.** Did this position bypass the engine, or did it pass gates that were weaker at the
>    time? Prove which, from the row's own provenance fields and the risk-engine decision log.
> 4. **Runtime containment.** Once a position is open and moving against the book, what is supposed
>    to stop the bleed — hard SL, trailing stop, structure break, time stop, give-back rule? Which
>    of those are actually reachable for a position like this, and which are structurally dead?
> 5. **Full selection criteria.** Document the complete SWING and LONGTERM funnels — every gate, its
>    threshold, its env var, its default, and whether it is enforced or shadow.
>
> Then extend the audit to questions the trigger implies but does not state:
>
> 6. **Legacy cohort.** How many currently-open positions were admitted under rules that no longer
>    exist? What is the aggregate risk carried by that cohort, and what is the remediation policy?
> 7. **Give-back.** For every open position, what was peak unrealized gain versus current P&L? Is
>    there any mechanism that reacts to a winner turning into a loser?
> 8. **Exit reachability.** For each open position, simulate every exit rule against live data.
>    Which rules can actually fire today, and which are permanently unreachable given the data
>    the container can obtain?
> 9. **Position sizing vs. display.** The UI reports position P&L in %. If the risk engine sizes
>    positions unequally, is a % headline misrepresenting the book's real ₹ damage?
> 10. **Duplicate/churn integrity.** The risk-engine exit log shows the same symbol exiting many
>     times in one day. Is the close path idempotent?
>
> Constraints: the system is LIVE. Diagnose first, change nothing without an explicit decision.
> Every proposed change must be flag-gated, reversible without redeploy, and justified against
> measured outcomes (per the validation-phase rule). No curve-fitting to a single bad trade.

---

## 2. Evidence — what the live book actually shows

Data pulled 2026-08-06 from production (`/api/portfolio/{swing,longterm}`), production Redis
(`risk_engine:*` decision logs), and daily OHLCV.

### 2.1 Why VIPULLTD is at −21% and not stopped out

The row, straight from production:

| field | value |
|---|---|
| entry / SL / T1 | 14.93 / 8.93 / 35.93 |
| **stop width** | **−40.2%** |
| target upside | +140.7% (R:R 1:3.5) |
| high_since_entry | **18.60 (+24.6%)** |
| current | 11.74 (−21.37%) |
| created_at | **2026-07-06 03:55** |
| source / reasoning | `RESEARCH_AUTO` / "Promoted from Final Trade Ideas (CMP-buy — price at entry zone)" |
| position_size, risk_weight_pct, atr_pct, turnover_cr | **all NULL** |

**−21% is not a threshold breach. It is well inside a stop that was placed 40% away.** The
position is behaving exactly as configured; the configuration is the defect.

The 40% stop exists because the long-term stop is **structural, not percentage-bounded** — it is
placed at a weekly demand-zone low / swing low ([research_levels.py:328-433](services/research_levels.py#L328-L433),
[engine/swing.py:716](engine/swing.py#L716)). On a ₹15 name with ~7.0% daily ATR, "below the weekly
demand zone" is 40% away. Nothing in the level-builder caps that.

### 2.2 It did not bypass the engine — it predates the guard

`services/risk_engine.py` caps stop width at **10% swing / 15% long-term**
([risk_engine.py:74-75](services/risk_engine.py#L74-L75)) and rejects the promotion outright when
breached ([risk_engine.py:244-247](services/risk_engine.py#L244-L247)).

Three independent proofs that VIPULLTD was admitted before that guard existed:

1. **Git:** the risk engine landed in `2d4e302`; arm-on-tap in `a3e1d7f`. Both are after
   2026-07-06. The row's `reasoning` string is the pre-arm-on-tap wording (`pending=False`,
   promoted straight to ACTIVE — today the same path arms as PENDING and waits for a genuine tap).
2. **Sizing fields are NULL.** `position_size` / `risk_weight_pct` / `atr_pct` / `turnover_cr` are
   only ever written by `evaluate_promotion`. Null means the risk engine never saw this promotion.
3. **Redis decision log** (`risk_engine:promotions:*`) begins 2026-07-13 and contains **zero**
   VIPULLTD records.

**The stop cap is working, and working hard.** Across all logged promotion decisions:

```
total promotion decisions : 438
rejected: stop_too_wide   : 415   (94.7%)
accepted                  :  23
```

Confirmed in the book itself — sorted by stop width, there is a clean break at the cap:

| symbol | stop % | created | verdict |
|---|---|---|---|
| VIPULLTD | 40.2 | 2026-07-06 | legacy, pre-cap |
| THANGAMAYL | 37.1 | 2026-06-05 | legacy, pre-cap |
| PICCADIL | 14.4 | 2026-08-05 | passes 15% LT cap |
| *(all 16 others)* | ≤ 12.2 | ≥ 2026-07-08 | — |

Swing shows the same shape: 4 of 17 exceed the 10% swing cap (RAYMOND 14.3, SBCL 12.0, GRAUWEIL
12.0, JAMNAAUTO 11.5) — all admitted on or before the cap's activation.

**Conclusion: 6 open positions (2 LT + 4 swing) are a legacy cohort carrying stops the current
policy would reject outright. Nothing in the system re-audits open positions against new rules.**

### 2.3 Why an illiquid ₹15 micro-cap could pass

VIPULLTD measured: **₹1.15 Cr/day turnover, 7.0% daily ATR, ₹11-15 price.**

The only liquidity gate in the live research path is **`min_turnover_cr = 1.0`** — ₹1 Cr/day —
applied at Layer 1 ([discovery_engine.py:226](services/discovery_engine.py#L226),
[validation_engine.py:749](services/validation_engine.py#L749), default in
[research.py:1703](dashboard/backend/routes/research.py#L1703)). VIPULLTD cleared it by 15%.

**There is no minimum price floor and no market-cap floor anywhere in the enabled path.** The
floors that would have caught it exist but are not reachable:

| gate | value | where | live? |
|---|---|---|---|
| min turnover | **₹1.0 Cr/day** | discovery / validation Layer 1 | **YES — the only one** |
| min price ₹50, min ATV ₹1Cr, min ATR 0.8%, quality tiers | `UQ_MIN_PRICE=50` etc. | `services/universe_quality.py` | **NO** — only reachable behind `ALPHA_V2` (default 0), and only as a *shadow* hook in `ranking_engine.py:853-876` |
| min price ₹50, min avg vol 200k | `engine/swing.py:732,737` | inside `score_longterm_candidate` | partial — **not re-applied at the promotion boundary** |
| T2T / -BE / -BZ / -SM exclusion | `RESEARCH_EXCLUDE_T2T_SME=1` | `services/segment_filter.py` | YES (VIPULLTD has a plain EQ listing, so not excluded) |
| momentum book's floor | **₹5 Cr/day** | `MOM_MIN_TURNOVER_CR` | YES — *the momentum book is 5× stricter than swing/LT* |

Neither `select_from_final_ideas` ([idea_selector.py:423](services/idea_selector.py#L423)) nor
`promote_to_portfolio` ([portfolio_manager.py:30](services/portfolio_manager.py#L30)) re-checks
price, turnover, or market cap. The risk engine's liquidity logic **only shrinks position size, it
never rejects** ([risk_engine.py:257-270](services/risk_engine.py#L257-L270)).

**So: not a bypass. A ₹15 micro-cap at ₹1.15 Cr/day is inside policy as currently enabled.**

### 2.4 The bigger leak: nothing reacts to give-back

VIPULLTD was **+24.6% (₹18.60)** before it was −21.37%. THANGAMAYL was **+37.1%** before −8.3%.

Peak-to-current give-back across the open book:

| book | avg give-back | worst |
|---|---|---|
| SWING | 4.3 pp | GRAUWEIL 24.9 pp |
| **LONGTERM** | **11.9 pp** | VIPULLTD 46.0, THANGAMAYL 45.4, PSPPROJECT 23.1, SATIN 16.2 |

**There is no trailing stop, no partial profit-booking, and no give-back rule anywhere in the
tracking engine** ([position_tracking_service.py:143-190](services/position_tracking_service.py#L143-L190)).
The complete exit vocabulary is: fixed target, fixed SL, trend-break, structure-break, stale-exit.
A position may round-trip a +37% gain to a loss and no rule observes it.

This affects **every** position, not just the legacy cohort. It is the largest ongoing leak found.

### 2.5 The soft exits are structurally unreachable

When `TREND_BREAK_EXIT_ENABLED=1` (default) the trend-break exit **supersedes** the legacy
structure-break and stale-exit culls ([position_tracking_service.py:154-189](services/position_tracking_service.py#L154-L189)).
It fires only on `CMP < 200-DMA × 0.98` **AND** `RS vs NIFTY < 0`.

Simulated against live data for all 18 LT positions:

```
positions below 200-DMA : 0 / 18   → trend-break CANNOT fire for any of them
positions with <200d of history: 6 / 18  (AMAGI, ABMKNO, INDPRUD, ZFSTEERING, BI, NEAGI)
                                → _get_200dma returns None → "no_dma200" → no exit path at all
```

VIPULLTD is −21% from entry and RS **−28.3** vs NIFTY, yet still **+3.8% above its own 200-DMA** —
because a 200-day average of a collapsing stock collapses with it. The 200-DMA is a lagging trigger
that a fast-falling micro-cap outruns.

Net effect for VIPULLTD: trend-break blocked (above DMA); stale-exit unreachable (its band is −5%
to +3%, deliberately excluding real losers); structure-break superseded. **The only live exit is
the ₹8.93 stop — another −24% from here.** Worst case on this one position is ≈ −40%.

Note: the RS feed *is* working on Railway (exit-log rows carry real RS values, e.g. SBILIFE
rs −0.71, APTUS rs −9.72), so this is not the yfinance-index block that hit the momentum book.

### 2.6 Integrity issue found in passing

`risk_engine:exits:2026-07-23` contains **CIPLA exiting 10 times in one day** at drifting prices
with `days_held` oscillating 30/29 — i.e. the same exposure closed repeatedly. The re-seed guard
at [portfolio.py:1324-1342](dashboard/backend/db/portfolio.py#L1324-L1342) documents exactly this
loop ("CIPLA x11 in 51 minutes, APTUS x10") and claims a fix. Needs verification that the fix
holds, and that the journal was cleaned of the phantom rows.

### 2.7 The % headline may understate ₹ damage

`position_size` and `risk_weight_pct` are NULL on all legacy rows and the UI reports plain %.
Risk-sized positions are deliberately *unequal* — a wide-stop name gets a smaller notional. A book
headline built from unweighted % is therefore not the book's real return. Cross-check against
`db/perf_stats.py` (the single source of truth per project convention) before publishing any
number derived from these rows.

---

## 3. Answers to the four questions asked

1. **Why is this at −21%?** Because its stop is 40% wide. −21% is inside policy-as-configured. The
   stop is structural (weekly demand zone) and was never percentage-bounded at creation time.
2. **Why did an illiquid stock get filtered in?** Because the only enabled liquidity gate is
   ₹1 Cr/day turnover and there is **no price or market-cap floor** in the live path. The ₹50 floor
   exists in `universe_quality.py` but is behind `ALPHA_V2=0` and only as a shadow hook.
3. **Did it bypass the engine?** No. It was admitted on 2026-07-06 under the pre-risk-engine rules,
   proven by null sizing fields, absence from the promotion log, and git history. Today's cap would
   reject it (415 of 438 logged decisions were rejected for exactly this reason).
4. **How did it cross −21%?** Because every soft exit is unreachable for it: trend-break needs
   below-200-DMA (it is 3.8% above), stale-exit only fires between −5% and +3%, structure-break is
   superseded, and there is no trailing/give-back rule at all.

---

## 4. Roadmap

Phase 0 is done. Everything below is **proposed** — nothing ships without a decision, and every
item is flag-gated and reversible per the validation-phase rule.

### Phase 1 — Legacy cohort remediation (decision required, no code)
- Produce a per-position remediation sheet for the 6 over-cap positions.
- Decide per position: hold to structural stop / tighten stop to policy / close.
- Ship a **one-time** `scripts/audit_open_positions_vs_policy.py` that re-scores every open
  position against *current* policy and prints a diff. Read-only, no mutation.

### Phase 2 — Close the promotion-boundary hole (small, high value)
- Add a single **admission gate** in `promote_to_portfolio` (the one funnel all paths pass
  through), enforcing: min price, min turnover, max ATR%, max stop width.
- All four thresholds env-driven, defaults chosen to be **no-ops** initially
  (`PROMOTE_MIN_PRICE=0`, `PROMOTE_MIN_TURNOVER_CR=0`, …) so enabling is a deliberate, measured act.
- Log every rejection to the existing `risk_engine:promotions:*` audit trail.

### Phase 3 — Give-back protection — **COMPLETE: NO-GO** (2026-08-18)

Backtested before building, per the validation-phase rule. **Result: the rule does not work.
Do not build it.** Scripts: `scripts/backtest_giveback.py` (superseded),
`scripts/backtest_giveback_path.py` (authoritative).

Sample: 83 closed trades (64 SWING / 19 LONGTERM), zero duplicates, zero missing MFE.
Baseline **mean +3.96%/trade, PF 2.37, win rate 48.2%**.

**Verdict — every cell of the validated grid is worse than or equal to baseline:**

| arm / give-back | mean P&L | PF | winners hurt | trail exits |
|---|---|---|---|---|
| baseline (no rule) | **+3.96%** | **2.37** | — | — |
| 3% / 15% | +1.22% | 1.54 | 21 (−329pp) | 41 |
| 5% / 25% | +2.69% | 2.09 | 10 (−180pp) | ~30 |
| 10% / 40% | +4.03% | 2.26 | 0 | ~2 |
| 20% / 60% | +4.00% | 2.25 | 0 | 1 |

The only settings that do not lose money are the ones where the trail **never fires**. Wherever
it fires, it truncates winners: the book's edge is a +14.24% average win, and a trail on names
with 5-8% daily ATR gets whipsawed out of exactly those. Give-back is real (11.9pp on LT) but
it is the *price* of letting winners run, not a recoverable leak.

**Two methodology traps this exposed — both would have shipped a bad rule:**

1. *The journal-only backtest said the opposite* (mean +3.96% → +6.90%, PF 2.37 → **4.78**).
   It reconstructs the trail from `high_since_entry`, the trade's FINAL peak, so it can never
   stop a winner out early. A real trail **ratchets**. Its best cell sat on the grid corner at
   arm 3%/give-back 15% — which is not a give-back rule at all, it is "scalp at +2.5%".
   A grid optimum on a corner means the optimum is outside the grid; that is the tell.
2. *A control run is mandatory.* Replaying with the trail disabled MUST reproduce known
   outcomes. It initially drifted −1.84pp, from two bugs in the harness: replaying from
   `created_at` (arm time, not entry — arm-on-tap means a position can sit PENDING for days),
   and testing exits against intraday extremes. The live tracker polls **sampled CMP** on a
   2-minute cadence, so it never sees a one-tick spike through a level. With entry dates
   reconstructed (`closed_at − days_held`) and a `close` fill model, control drift is **+0.04pp
   (PASS)**; `extremes` still fails at −1.84pp. The harness now refuses to let an uncontrolled
   grid be read as a result.

**Implication for the audit's headline finding:** §2.4 called unprotected give-back "the largest
ongoing leak". That was measured correctly but diagnosed wrongly — it is not addressable by a
trailing exit. If give-back is to be attacked at all, it must be at *entry quality* (Phase 2/5),
not at exit timing.

### Phase 4 — Make the soft exits reachable
- Trend-break currently cannot fire for 18/18 LT positions. Options to evaluate:
  faster MA (50-DMA) as a second trigger; a hard max-adverse-excursion stop; or an
  RS-only deterioration exit for names with <200d history.
- Fix the `no_dma200` dead-end for the 6 short-history positions.
- Evidence-first: simulate each option on closed history before proposing a default.

### Phase 5 — Universe floors, properly
- Decide whether `ALPHA_V2` quality-universe filtering graduates from shadow to enforced.
- Reconcile the three inconsistent liquidity floors: research ₹1 Cr, risk-engine sizing ₹2 Cr,
  momentum ₹5 Cr. One documented policy per book, not three accidents.
- Extend `portfolio_risk._SECTOR_MAP` or replace it — unmapped symbols return `OTHER` and
  **skip the sector-concentration limit entirely**, which is how micro-caps dodge diversification.

### Phase 6 — Integrity
- Verify the duplicate-close guard holds; audit `portfolio_journal` for phantom rows.
- Confirm published book metrics come from `db/perf_stats.py`, not unweighted % sums.

---

## 5. Open decisions needed

| # | Decision | Default if no answer |
|---|---|---|
| D1 | VIPULLTD & THANGAMAYL: hold to structural stop, tighten to policy, or close? | hold — no action taken |
| D2 | Should the 4 over-cap swing positions be remediated the same way? | hold |
| D3 | Do open positions get re-audited against new policy when rules change, as standing policy? | no — new rules apply to new entries only (status quo) |
| D4 | Minimum price floor for both books — ₹50 (matches `universe_quality`), higher, or none? | none (status quo) |
| D5 | Raise the ₹1 Cr turnover floor toward the momentum book's ₹5 Cr? | no change |
| D6 | Proceed to build + backtest the Phase 3 give-back rule? | yes — backtest only, flag OFF |

---

## 6. STEP 1 RESULT — open book vs. policy (2026-08-18)

Tool: `scripts/audit_open_positions_vs_policy.py` (read-only). Run against production.
**8 of 29 open positions would be rejected by policy enforced today.**

### 6.1 LONGTERM — 11 PASS / 4 REJECT

| stock | price | turnover | ATR% | SL% | sector | P/L% | days | policy | why |
|---|---|---|---|---|---|---|---|---|---|
| ZFSTEERING | 679.80 | 0.44 Cr | 3.3 | 10.6 | OTHER | -3.19 | 43 | **REJECT** | `turnover_below_floor` |
| INDPRUD | 6198.00 | 0.11 Cr | 3.0 | 9.9 | OTHER | -2.32 | 40 | **REJECT** | `turnover_below_floor` |
| VIPULLTD | 14.87 | 0.34 Cr | 4.0 | 40.2 | OTHER | -0.40 | 43 | **REJECT** | `stop_too_wide` + `turnover_below_floor` |
| THANGAMAYL | 5481.50 | 178.90 Cr | 5.0 | 37.1 | OTHER | +1.49 | 75 | **REJECT** | `stop_too_wide` |
| DHANBANK | 32.24 | 6.09 Cr | 2.7 | 10.4 | OTHER | -6.22 | 41 | PASS | — |
| NELCO | 948.80 | 61.82 Cr | 6.4 | 8.0 | OTHER | -4.34 | 14 | PASS | — |
| LUPIN | 2232.00 | 279.99 Cr | 2.3 | 8.0 | PHARMA | -3.25 | 117 | PASS | — |
| IKS | 1861.60 | 29.07 Cr | 3.2 | 8.0 | OTHER | +1.35 | 43 | PASS | — |
| GRINDWELL | 2101.30 | 9.14 Cr | 2.8 | 9.8 | OTHER | +2.26 | 27 | PASS | — |
| SAKAR | 893.70 | 13.28 Cr | 5.7 | 10.6 | OTHER | +6.71 | 43 | PASS | — |
| TMB | 847.10 | 59.34 Cr | 2.7 | 7.2 | OTHER | +9.41 | 43 | PASS | — |
| PSPPROJECT | 942.35 | 10.98 Cr | 4.3 | 12.2 | OTHER | +11.57 | 78 | PASS | — |
| JINDALSAW | 268.65 | 28.96 Cr | 2.6 | 11.3 | OTHER | +12.10 | 77 | PASS | — |
| INDSWFTLAB | 346.87 | 49.37 Cr | 5.3 | 8.0 | OTHER | +18.64 | 5 | PASS | — |
| CRAFTSMAN | 10890.50 | 67.33 Cr | 3.5 | 7.1 | OTHER | +18.88 | 46 | PASS | — |

### 6.2 SWING — 10 PASS / 4 REJECT

| stock | price | turnover | ATR% | SL% | sector | P/L% | days | policy | why |
|---|---|---|---|---|---|---|---|---|---|
| GRAUWEIL | 66.43 | 2.07 Cr | 4.3 | 12.0 | OTHER | -4.96 | 78 | **REJECT** | `stop_too_wide` |
| JAMNAAUTO | 122.58 | 36.86 Cr | 4.6 | 11.5 | OTHER | -0.06 | 77 | **REJECT** | `stop_too_wide` |
| SONAL | 90.80 | **0.00 Cr** | 4.6 | 6.5 | OTHER | +0.50 | **7** | **REJECT** | `turnover_below_floor` |
| RAYMOND | 625.00 | 33.18 Cr | 3.6 | 14.3 | OTHER | +5.32 | 41 | **REJECT** | `stop_too_wide` |
| CMRGREEN | 211.99 | 18.19 Cr | 3.9 | 5.8 | OTHER | -5.33 | 8 | PASS | — |
| GARUDA | 182.88 | 19.55 Cr | 4.2 | 5.0 | OTHER | -2.83 | 6 | PASS | — |
| TATACAP | 363.15 | 81.70 Cr | 2.6 | 5.0 | OTHER | -1.46 | 11 | PASS | — |
| SUNPHARMA | 1875.10 | 472.48 Cr | 1.9 | 5.0 | PHARMA | -0.58 | 41 | PASS | — |
| AYE | 171.80 | 16.97 Cr | 4.4 | 7.4 | OTHER | -0.02 | 26 | PASS | — |
| TITAN | 5048.60 | 484.80 Cr | 2.0 | 5.0 | OTHER | +0.04 | 6 | PASS | — |
| NESTLEIND | 1462.50 | 391.83 Cr | 2.3 | 5.2 | FMCG | +1.11 | 26 | PASS | — |
| MARKOLINES | 171.62 | 3.07 Cr | 2.9 | 5.8 | OTHER | +1.27 | 41 | PASS | — |
| PNB | 116.50 | 156.63 Cr | 1.8 | 5.0 | BANKING | +4.24 | 28 | PASS | — |
| EVEREADY | 354.30 | 4.31 Cr | 2.7 | 5.0 | OTHER | +8.85 | 98 | PASS | — |

### 6.3 Reason tally

**Enforced today:**

| reason | n | symbols |
|---|---|---|
| `stop_too_wide` | 5 | VIPULLTD, THANGAMAYL, RAYMOND, JAMNAAUTO, GRAUWEIL |
| `turnover_below_floor` | 4 | INDPRUD, ZFSTEERING, VIPULLTD, SONAL |

**Proposed (not live) — evidence for Step 3/4 thresholds:**

| candidate gate | n | symbols |
|---|---|---|
| `atr_above_size_ref` (>4%) | 10 | INDSWFTLAB, NELCO, SAKAR, THANGAMAYL, PSPPROJECT, GARUDA, SONAL, AYE, JAMNAAUTO, GRAUWEIL |
| `turnover_below_5Cr` | 7 | INDPRUD, ZFSTEERING, VIPULLTD, SONAL, MARKOLINES, GRAUWEIL, EVEREADY |
| `no_200dma` (<200 bars) | 6 | INDPRUD, ZFSTEERING, SONAL, CMRGREEN, AYE, GRAUWEIL |
| `turnover_below_2Cr` | 4 | INDPRUD, ZFSTEERING, VIPULLTD, SONAL |
| `price_below_floor` (<Rs50) | 2 | DHANBANK, VIPULLTD |

No position breaches the ATR 8% hard ceiling. Highest is NELCO at 6.4%.

### 6.4 THE HEADLINE FINDING — the cohort is not all legacy

The two failure modes have completely different causes, and only one is legacy:

**`stop_too_wide` (5 positions) IS legacy.** All are 41-78 days old, all predate the stop cap.
Every position admitted since carries a stop inside policy. The cap works.

**`turnover_below_floor` is a LIVE, ACTIVE leak.** SONAL was admitted **7 days ago** with
**Rs 0.00 Cr/day turnover** — essentially untradeable. Its row proves the risk engine *did*
evaluate it: `position_size=22030.54, turnover_cr=0.0, atr_pct=4.17`. It measured zero
liquidity and **admitted it anyway**, merely sizing it down to Rs 22k against CMRGREEN's
Rs 150k. That is `risk_engine.py:257-270` behaving as written — liquidity only ever
DOWN-SIZES, it never rejects.

So this is not a historical artifact to migrate. It is a hole that is still admitting
untradeable stock, and it will keep doing so until Step 3 lands.

### 6.5 SECOND LIVE HOLE — `promote_to_portfolio` is NOT the only door

The Step 3 plan assumed one funnel. That is wrong. There are **two** doors into the book:

| door | path | risk engine? |
|---|---|---|
| 1 | `promote_to_portfolio` — idea_selector arm-on-tap **and** manual `POST /api/portfolio/add` | **YES** — stop cap + sizing + liquidity |
| 2 | `seed_portfolio_from_recommendations` — engine live-sync (`PORTFOLIO_LIVE_SYNC_ENABLED=1`) | **NO** — raw `INSERT INTO portfolio_positions` |

Door 2 (`db/portfolio.py:1272-1416`) checks only duplicate / already-exited / re-entry guard /
capacity. It applies **no stop-width cap, no sizing, no liquidity check**. GARUDA (created
2026-08-12, `position_size=None`) came through it. Any stop width and any liquidity can enter
the book here today.

**Step 3 must gate BOTH doors, or it is decorative.**

### 6.6 Sector concentration is effectively not enforced

| book | unmapped (`OTHER`) | share |
|---|---|---|
| LONGTERM | 14 / 15 | **93%** |
| SWING | 11 / 14 | **79%** |
| mapped sectors present | PHARMA 1, FMCG 1, BANKING 1 | — |

`portfolio_risk.get_sector` returns `OTHER` for anything outside a ~130-symbol hardcoded map,
and `check_sector_limit` returns `True, "ok"` immediately for `OTHER`. With 79-93% of both
books unmapped, `MAX_SECTOR_EXPOSURE=3` constrains almost nothing. This is a bigger gap than
the audit's Phase 5 note implied — the diversification limit is nominal, not real.

### 6.7 What Step 1 does NOT tell us

- Turnover/ATR here are measured TODAY, not at each position's admission date. A name can
  have been liquid at entry and dried up since (ZFSTEERING and INDPRUD are plausible cases).
  Point-in-time attribution needs the admission-date metrics, which only the Step 3 shadow
  log will capture going forward.
- `price_below_floor` and the ATR candidates are **unvalidated**. Nothing yet shows Rs 50 or
  4% is a profitable cut. That is exactly what Step 4 is for. Do not enable them on this table.
