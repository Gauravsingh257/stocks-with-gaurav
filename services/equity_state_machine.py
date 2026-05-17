"""
PHASE G2-6 Step 1 — production daily-bar-clocked equity state machine
(ONLINE-correct).

WHY THIS EXISTS / WHAT STEP 1's TEST PROVED
-------------------------------------------
First attempt assumed "re-run the G2-5 batch simulator on all history up
to today, act on new fires" was equivalent to the batch backtest. The
equivalence test DISPROVED that: the batch oracle
(`state_machine_sim.simulate_state_machine_entries`) is a *batch*
algorithm — when a setup fires it skips its scan pointer forward, and a
setup unresolved at the array end is collapsed to "no fire". A live
engine seeing one bar at a time cannot skip with future knowledge, so a
naive driver fired MORE trades than the backtest measured (22 vs 20 on a
sample). The G2-5 numbers were measured on the *batch* trade set; a live
engine's trade set is genuinely different and must be re-backtested on
its OWN fires (this module is consumed by the re-run; see
ALPHA_BASELINE.md G2-6 ONLINE section).

THE ONLINE-CORRECT RULE
-----------------------
Identical setup logic to the oracle — detectors, confirmation candle,
weekly gate and the entry/SL/target formulas are *imported / replicated
verbatim* from `state_machine_sim` + `engine.*` (single source of truth
for the rules; this module only owns the SCAN CADENCE). The one
behavioural correction: a setup is acted on only when its outcome is
**final** — fired, invalidated, or genuinely expired by bar count. If
the scan reaches a FORMED setup whose resolution would need bars that do
not exist yet ("indeterminate"), the scan STOPS there — it does not
guess, and it does not advance past it (advancing past unresolved setups
is exactly what produced the phantom extra fires). That setup is simply
re-evaluated on a future day when more bars exist.

GUARANTEES (pinned by tests/test_equity_state_machine_equivalence.py)
---------------------------------------------------------------------
- Self-consistency (MUST hold exactly): day-by-day evaluation over
  growing prefixes, de-duplicated, == one evaluation on the full array.
  This is the property a live engine must have — no decision is
  provisional.
- Rule fidelity (MUST hold exactly): every fire this engine emits also
  appears, byte-identical (entry/SL/target/formed/tapped), in the batch
  oracle's output — i.e. online fires ⊆ oracle fires. It never invents a
  trade the validated rules would not; it correctly emits *fewer* (drops
  the oracle's batch-only phantom/overlap tail fires).

SCOPE / STATUS
--------------
Step 1 = this engine + the `equity_sm_state` DDL (additive, in
dashboard/backend/db/schema.py) + the equivalence test + the
online-engine re-backtest. NOTHING calls this in the live agent yet: no
agent wiring, no flag, no DB writes, no live behaviour. The persistent
`equity_sm_state` fast-path and the ARMED/TAPPED UI phase projection are
deferred to G2-6 Rung A (=shadow), behind a default-OFF flag, and must
still pass these same tests.

Pure / read-only. No network, no DB, no wall-clock.
"""

from __future__ import annotations

import logging

from engine.indicators import calculate_atr
from engine.swing import detect_daily_fvg, detect_daily_ob, detect_weekly_trend
from services.state_machine_sim import (
    StateMachineFire,
    _confirmation_candle_long,  # single-source rule primitive
    weekly_up_to,               # single-source point-in-time weekly slice
)

log = logging.getLogger("services.equity_state_machine")

_MIN_HISTORY_DEFAULT = 60
_EXPIRY_BARS_DEFAULT = 15


def scan_fires_online(
    daily: list[dict],
    weekly: list[dict],
    *,
    expiry_bars: int = _EXPIRY_BARS_DEFAULT,
    min_history: int = _MIN_HISTORY_DEFAULT,
) -> list[StateMachineFire]:
    """Online-correct single pass over `daily` (already point-in-time —
    only bars the live engine would have on the evaluation day).

    Mirrors `state_machine_sim.simulate_state_machine_entries`'s pointer
    logic exactly, with one correction: stop at the first FORMED setup
    whose outcome the available bars cannot yet determine, instead of
    treating it as no-fire and scanning past it. Every emitted fire is a
    decision future bars cannot reverse.
    """
    fires: list[StateMachineFire] = []
    n = len(daily)
    if n < min_history or len(weekly) < 12:
        return fires

    i = min_history
    while i < n:
        window = daily[: i + 1]
        wt = detect_weekly_trend(weekly_up_to(weekly, daily, i))
        if wt not in ("BULLISH", "STRONG_BULL"):
            i += 1
            continue

        ob = detect_daily_ob(window, "LONG")
        fvg = detect_daily_fvg(window, "LONG")
        if not ob or not fvg:
            i += 1
            continue

        formed_idx = i
        fvg_lo, fvg_hi = (fvg[0], fvg[1]) if fvg[0] <= fvg[1] else (fvg[1], fvg[0])
        ob_low = ob[0]
        tapped_idx = -1
        fired = False
        resolved = False  # outcome final & independent of future bars?

        # Inner loop mirrors state_machine_sim EXACTLY: after a tap it
        # keeps scanning, waiting for the FIRST confirmation candle
        # (it does not decide on the next bar). Only the loop-exit
        # classification below adds online-correctness.
        j = i + 1
        while j < n and (j - formed_idx) <= expiry_bars:
            c = daily[j]
            if c["close"] < ob_low:
                resolved = True  # invalidation — final
                break
            if tapped_idx < 0:
                if c["low"] <= fvg_hi and c["high"] >= fvg_lo:
                    tapped_idx = j
            else:
                if _confirmation_candle_long(c):
                    entry = round((fvg_lo + fvg_hi) / 2.0, 2)
                    atr = calculate_atr(daily[: j + 1], 14) or (entry * 0.02)
                    recent_low = min(x["low"] for x in daily[max(0, j - 9): j + 1])
                    sl = round(recent_low - atr * 0.3, 2)
                    if sl < entry:  # geometry sane (longs-only)
                        target = round(entry + 2.0 * (entry - sl), 2)
                        fires.append(StateMachineFire(
                            entry_idx=j, entry=entry, stop_loss=sl,
                            target=target, formed_idx=formed_idx,
                            tapped_idx=tapped_idx,
                        ))
                        fired = True
                    resolved = True  # first confirmation candle = final
                    break
            j += 1
        else:
            # inner loop ended on its while-condition (no break)
            if (j - formed_idx) > expiry_bars:
                resolved = True  # genuinely expired by bar count — final
            # else: j >= n within the expiry window → INDETERMINATE

        if not resolved:
            # First setup whose outcome future bars could still change.
            # A live engine must wait, not guess, and must NOT advance
            # past it — advancing past unresolved setups is exactly what
            # created the batch-only phantom fires.
            break

        i = (j + 1) if fired else (formed_idx + 1)

    return fires


# Public name the live daily tick / re-backtest call. A live tick passes
# `daily` = history up to that day; this returns every final fire, and the
# agent (Rung A, later) acts on the one whose entry bar is the latest.
def replay_fires(
    daily: list[dict],
    weekly: list[dict],
    *,
    expiry_bars: int = _EXPIRY_BARS_DEFAULT,
    min_history: int = _MIN_HISTORY_DEFAULT,
) -> list[StateMachineFire]:
    return scan_fires_online(
        daily, weekly, expiry_bars=expiry_bars, min_history=min_history
    )
