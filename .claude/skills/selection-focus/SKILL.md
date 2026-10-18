---
name: selection-focus
description: The standing brief for improving the SWG stock-selection engine — the north star, the rules of engagement, the current priority order, and the off-track tripwires. Use at the start of any work on stock selection, the track-record page, portfolio/ledger integrity, Telegram-vs-portfolio mismatches, selection-criteria or SMC conformance, or whenever the user asks whether we are on track or drifting. Also use before proposing any new investigation, experiment, dashboard or measurement tool.
---

# /selection-focus

The standing brief for this project. Read it before starting selection work, and re-read it when
a task starts to sprawl.

## North star

Identify promising NSE stocks **early**, select better candidates than the ones we reject,
**measure forward performance honestly**, and manage downside.

> **Success = demonstrable, repeatable improvement in stock-selection quality.
> Not more infrastructure. Not more dashboards. Not more reports.**

Durable intent: [`docs/PRODUCT_VISION.md`](../../docs/PRODUCT_VISION.md).
Current state and staging: [`docs/PROJECT_STATE.md`](../../docs/PROJECT_STATE.md).

## Before starting anything, answer three questions

1. **Which decision does this unlock?**
2. **What evidence is missing right now?**
3. **What exact condition ends this task?**

If a task cannot answer all three, **do not start it.** If it unlocks no new decision, say so and
move to the next useful thing.

## Priority order (highest value first)

1. **Data integrity on surfaces the owner actually looks at.** If the track-record page, the
   portfolio, and Telegram disagree, every downstream number is suspect and no research on top of
   it means anything. Fix the surface before analysing it.
2. **Live-path correctness defects** — wrong labels, unwritten fields, phantom rows, mismatched
   alerts. These are cheap, provable, and they poison analysis silently.
3. **Conformance**: does the engine actually apply the selection criteria and the SMC model it
   claims to? A rule that is configured but unreachable is worth finding.
4. **Selection-quality evidence** — only once 1–3 are sound.
5. Everything else.

## Hard rules

- **No strategy change without explicit approval** — thresholds, weights, entry/exit rules,
  stop-losses, targets, live flags. Research and documentation are always fine.
- **No merge or production deploy without explicit approval.** Avoid deploys 09:15–16:10 IST; the
  cutoff is 16:10 (the signals window), not 15:30. After deploying, verify lock ownership and
  handover, not just heartbeat freshness.
- **Never weaken a test, a baseline or a maturity gate** to make a result look better.
- **Never call directional evidence proof.** State sample size, overlap, regime and the absence of
  a bootstrap in the same breath as the number.
- **Reuse before building.** `services/forward_eval.py`, `scripts/portfolio_quality_audit.py`,
  `scripts/ceiling_cohort_study.py`, the bar-replay counterfactual scripts and the lifecycle ledger
  already exist. No new shadow system.

## Closed — do not reopen without the documented trigger

Stop width, delayed entry, re-entry, and PULLBACK_3 are **settled**
([`docs/PROJECT_STATE.md`](../../docs/PROJECT_STATE.md), the CLOSED section). Re-running them is
the single most likely way to waste a session.

## Off-track tripwires — say so out loud when one fires

You are drifting if any of these is true:

- The last three actions produced **reports or reviews rather than a fixed defect or a decision**.
- A plan is being written for work that was already planned.
- The output is "we need more data" **again**, with no defect found and nothing fixed.
- A new measurement tool is being built while a known data-integrity defect is unfixed.
- A PR is being re-reviewed that was already reviewed and has not changed.
- Analysis is being run on numbers whose provenance has not been checked this session.

When a tripwire fires: **name it, stop, and go to the highest item in the priority order.**

## Two traps that have already produced wrong answers here

- **MAE/MFE carry no ordering, and MAE on a stopped trade is censored at the stop.** Never infer
  profitability from them. Replay bars.
- **A young cohort cannot contain long-held winners.** Always match on holding period, and respect
  `portfolio_quality_audit`'s refusal to report outcomes below 80% resolved.

And a third, learned 2026-10-18: **check the denominator's provenance.** Resolution percentages
were computed over a ledger containing ~97 phantom `ACTIVE` rows, so every "% resolved" and every
maturity estimate built on it was wrong.
