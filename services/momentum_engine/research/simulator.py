"""
services/momentum_engine/research/simulator.py
===============================================
Deterministic forward trade simulation for the backtest. Given an entry plan
(trigger/base) and the FORWARD candles, it models the full lifecycle:

    arm at trigger → tap (fill at trigger, or open on a gap) → initial stop
    → per-bar: breakeven, trailing, failed-breakout, stop-hit, time-stop

and returns a SimTrade with the realised R-multiple + MFE/MAE. Pure (no I/O),
so every stop/trail methodology is measured under identical, replayable rules.
"""

from __future__ import annotations

from typing import Any

from . import stops, trailing
from .models import SimConfig, SimTrade


def _atr(candles: list[dict], n: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"]); l = float(candles[i]["low"]); pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    k = min(n, len(trs))
    return sum(trs[-k:]) / k if k else 0.0


def simulate_trade(symbol: str, trigger: float, base_low: float, atr: float,
                   forward: list[dict], config: SimConfig) -> SimTrade:
    """`forward` = candles from the arm bar onward. `atr` = ATR at arm time."""
    if not forward or trigger <= 0 or atr <= 0:
        return SimTrade(symbol, False, None, None, "NOT_TRIGGERED", None, 0, None, None, None)

    # 1) Arm-on-tap: find the first bar whose high reaches the trigger.
    fill_i = None
    fill = None
    for i, b in enumerate(forward[: config.max_arm_bars]):
        if float(b["high"]) >= trigger:
            fill_i = i
            fill = max(trigger, float(b["open"]))  # gap-up fills at the open
            break
    if fill_i is None:
        return SimTrade(symbol, False, None, None, "NOT_TRIGGERED", None, 0, None, None, None)

    init_stop = stops.initial_stop(config.stop_method, fill, base_low, atr, config.stop_params)
    risk = fill - init_stop
    if risk <= 0:
        return SimTrade(symbol, False, None, None, "NOT_TRIGGERED", None, 0, None, None, None)

    current_stop = init_stop
    breakeven_done = False
    mfe_r = 0.0
    mae_r = 0.0
    path = forward[fill_i:]

    for j, b in enumerate(path):
        hi = float(b["high"]); lo = float(b["low"]); cl = float(b["close"])
        mfe_r = max(mfe_r, (hi - fill) / risk)
        mae_r = min(mae_r, (lo - fill) / risk)

        # Stop-hit (intrabar low breaches the current stop).
        if lo <= current_stop:
            reason = "BREAKEVEN" if (breakeven_done and abs(current_stop - fill) < 1e-9) \
                else ("TRAIL" if current_stop > init_stop else "STOP")
            return SimTrade(symbol, True, round(fill, 2), round(current_stop, 2), reason,
                            round((current_stop - fill) / risk, 3), j + 1,
                            round(mfe_r, 3), round(mae_r, 3), round(init_stop, 2))

        # Failed breakout: close back below the base after entry.
        if cl < base_low:
            return SimTrade(symbol, True, round(fill, 2), round(cl, 2), "FAILED_BREAKOUT",
                            round((cl - fill) / risk, 3), j + 1,
                            round(mfe_r, 3), round(mae_r, 3), round(init_stop, 2))

        # Time stop.
        if j + 1 >= config.max_hold_bars:
            return SimTrade(symbol, True, round(fill, 2), round(cl, 2), "TIME",
                            round((cl - fill) / risk, 3), j + 1,
                            round(mfe_r, 3), round(mae_r, 3), round(init_stop, 2))

        # Breakeven, then trailing (only ever raises the stop).
        if not breakeven_done and (hi - fill) / risk >= config.breakeven_at_r:
            current_stop = max(current_stop, fill)
            breakeven_done = True
        bars_so_far = path[: j + 1]
        proposed = trailing.trail_stop(config.trail_method, bars_so_far, atr, config.trail_params)
        if proposed is not None and proposed > current_stop:
            current_stop = min(proposed, cl * 0.9999)  # never above current price

    # Ran out of forward data → exit at last close.
    last = float(path[-1]["close"])
    return SimTrade(symbol, True, round(fill, 2), round(last, 2), "TIME",
                    round((last - fill) / risk, 3), len(path),
                    round(mfe_r, 3), round(mae_r, 3), round(init_stop, 2))
