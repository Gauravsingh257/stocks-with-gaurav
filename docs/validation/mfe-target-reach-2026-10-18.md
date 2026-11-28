# Do the stocks we select ever reach their target?

**STATUS: LIVE** · 2026-10-18 · `scripts/mfe_target_reach_study.py` · read-only

## The question

The books plan ~3R at entry (stop width 5.0% median, RR-at-entry 3.00 median) and realise **0.20R**.
Targets are hit on ~21% of closed swing trades and 0% in momentum. Two explanations survive that
evidence and they imply opposite work:

- **A — selection:** the stocks we pick never move far enough for the target to be reachable.
- **B — capture:** they do move far enough, and we exit before collecting it.

Average return and realised RR cannot separate A from B. **Maximum favourable excursion can**: MFE
is the best the position was ever worth, so if MFE rarely reaches the target then the target was
never reachable.

## Method

Reuses `services/forward_eval.py` wholesale, with the same conventions as
`scripts/ceiling_cohort_study.py`: base = close of the **scan date** (scans publish pre-open, so
that close is tradable after publication — no look-ahead); MFE measured over bars strictly after
the base; **matured observations only** (no bar at D+N ⇒ dropped and counted, never treated as
zero); one observation per (scan_date, symbol) with SELECTED winning ties; symbols absent from the
universe snapshot counted as a survivorship caveat. Regime is the benchmark's **trailing** 20-bar
return at the base date, so it is knowable on the day.

Target-reach uses each candidate's **own published target** — `required_move = (target − entry) /
entry` — not a generic +15%.

Statistical claims use the day-block bootstrap (resampling scan *days*, since overlapping windows
make observations dependent). Threshold and target-reach **rates are descriptive** with their day
counts; no CI is claimed around them.

Window: last 90 days. Price book 2,166 symbols, last bar 2026-10-16. Benchmark NIFTY 50.

## Result 1 — the target is unreachable for ~88% of picks

**SWING, forward 20d, SELECTED** — n=2,439 over 41 scan days:

| Measure | Value |
|---|---|
| MFE ≥ +5% | 65.4% |
| MFE ≥ +10% | 38.3% |
| MFE ≥ +15% | 22.1% |
| **Reached its own target** | **11.6%** (of 1,624 rows carrying a target) |
| **Median required move** | **+21.7%** |
| Median MFE | **+7.68%** |

**LONGTERM, 20d, SELECTED** — n=2,628 over 43 days: required move median **+26.9%**, own target
reached **4.5%**, median MFE +7.73%.

The target asks for a median **+21.7%** move (swing) while the median pick's best moment is
**+7.68%**. **Explanation A holds.** The target sits roughly 3× beyond the typical best-case
excursion, so the 3R structure was never collectable on most rows — which is exactly how a planned
3.00R becomes a realised 0.20R.

## Result 2 — the harder finding: there is an MFE edge but no return edge

Day-block bootstrap, SWING. `block_bootstrap` reports (b − a), so a negative diff means SELECTED is
higher; the script now spells the direction out.

| Metric | Horizon | vs | SELECTED | Other | 95% CI | Verdict |
|---|---|---|---|---|---|---|
| MFE | 5d | CEILING_CUT | +6.34% | +5.37% | [−1.51, −0.46] | SELECTED +0.97pp |
| MFE | 10d | CEILING_CUT | +8.87% | +7.56% | [−2.22, −0.46] | SELECTED +1.32pp |
| MFE | 20d | CEILING_CUT | +11.93% | +10.73% | [−2.66, +0.18] | **none** |
| MFE | 5d | PHASE2_CUT | +6.94% | +4.34% | [−3.22, −2.03] | SELECTED +2.60pp |
| MFE | 10d | PHASE2_CUT | +9.77% | +5.79% | [−4.96, −3.03] | SELECTED +3.98pp |
| MFE | 20d | PHASE2_CUT | +13.18% | +8.04% | [−6.51, −3.85] | SELECTED +5.14pp |
| **return** | 5/10/20d | CEILING_CUT | +0.20/+0.17/+0.29% | +0.05/+0.26/+0.45% | all straddle 0 | **none** |
| return | 20d | PHASE2_CUT | −0.64% | −1.72% | [−2.01, −0.11] | SELECTED +1.08pp |

And benchmark-relative medians at 20d:

| Cohort | Median excess vs NIFTY | Beat benchmark |
|---|---|---|
| **SELECTED** | **−0.41%** | **48.1%** |
| CEILING_CUT (rank 21+) | +1.97% | 58.5% |
| PHASE2_CUT (below rank budget) | +1.04% | 55.2% |

LONGTERM is the same shape: SELECTED −0.53% / 47.9%, CEILING_CUT +1.73% / 57.8%.

**Reading.** The engine picks stocks that *travel further* (a real, bootstrapped MFE edge over the
below-budget cohort, 2.6–5.1pp) but **do not end higher**: every close-to-close comparison against
CEILING_CUT straddles zero, and SELECTED's median excess return is slightly *negative* while both
rejected cohorts' are positive. That is the signature of selecting high-excursion, high-volatility
names that spike and give it back — and it is exactly consistent with a 47.2% stop-out rate and a
realised 0.21R.

This corroborates the earlier recorded finding that the top-20 exceptionalism ceiling adds no
selection value versus rank 21+ while the Phase-2 rank budget does discriminate. It extends it:
what the rank budget discriminates on is **excursion, not return**.

## Limitations — stated, not buried

1. **Candidate-level, not trade-level.** These are scan-date observations, not entered positions.
   Entered trades are the subset whose entry tapped, and their realised target-hit rate is 21.1%
   versus 11.6% here — a pullback entry gives a better base. Candidate-level rates therefore
   *understate* trade-level target reach. The two figures are not in conflict and must not be
   conflated.
2. **The excess-return comparison is directional, not bootstrapped.** The bootstrap ran on raw
   `ret`, where SELECTED vs CEILING_CUT shows *no measurable difference*. The medians and
   beat-rates lean against SELECTED, but that lean is **not** established statistically. The honest
   claim is "no demonstrable return edge", not "selection underperforms".
3. **Coverage 80.3%** (swing). 829 symbols absent from the universe snapshot — survivorship
   caveat. 5,158 observations immature at 20d, correctly dropped.
4. **A third of SELECTED rows carry no usable target** (1,624 of 2,439), versus 99.8% of
   CEILING_CUT. That asymmetry is unexplained and is itself worth a look.
5. **The regime split is unbalanced and RISK_ON is uninformative** — n=15 over 2 days (swing),
   n=34 over 3 days (LT), because the trailing 20-day NIFTY move stayed inside ±2% for most of the
   window. RISK_OFF (n=208) shows higher MFE (+8.88%) and positive excess (+2.26%) but a required
   move of +35.6%; directional only. **Nothing should be tuned on the regime rows.**

## Decision

**Not inconclusive.** Two findings, graded:

- **FIRM:** the published target requires a median +21.7% (swing) / +26.9% (LT) move and only
  11.6% / 4.5% of selected candidates ever reach it within 20 days. The target is mis-specified
  relative to what selection currently produces.
- **FIRM (bootstrapped):** SELECTED has a genuine MFE edge over the below-budget cohort
  (2.6–5.1pp) and a small one over rank-21+ at 5–10d that vanishes by 20d.
- **NOT ESTABLISHED:** any close-to-close return edge, over the benchmark or over the rejected
  cohorts.

**The work this points to is the ranker's objective**, not the exit rules. The ranker is currently
selecting for excursion, which is close to selecting for volatility, and volatility is symmetric —
it buys both the spike and the giveback. A selection change should be judged on *realised excess
return*, not on how far a name travels.

Targets and live settings are **unchanged**; any target work needs an explicit decision plus a
pre-registered test with a disabled-rule control.
