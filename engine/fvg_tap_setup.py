"""
engine/fvg_tap_setup.py
=======================
PRODUCTION detector for the FVG-Tap setup — index 5m only.

PROVENANCE / VALIDATION (do not change the rule without re-validating):
The rule below is byte-identical to scripts/fvg_tap_backtest.py which
cleared the pre-committed, locked-before-results index-domain gate:
  NIFTY 5m  n=232 win 50.9% expR +0.526 PF 2.07   (gate Y)
  BANKNIFTY 5m n=266 win 53.8% expR +0.613 PF 2.33 (gate Y)
Two independent indices, criteria fixed in advance. If ANY rule
parameter changes, the gate is void until re-run.

This module is PURE (no I/O, no side effects). All wiring (engine tick,
ledger, Telegram, website) lives elsewhere and is flag-gated. With
FVG_TAP_MODE unset/"off" nothing in this module is ever invoked, so the
live engine is byte-identical.

The live evaluator differs from the backtest ONLY in that it has no
future: it reports a signal exactly when the ENGULFING confirmation is
the most recent CLOSED candle, so the live entry = the next bar
(MARKET) — point-in-time, zero look-ahead, identical decision logic.
"""

from __future__ import annotations

import os

import smc_detectors as smc

# --- FIXED, PRE-COMMITTED PARAMETERS (identical to the gated backtest;
#     never tuned/swept — doing so voids the validation) ---------------
R_MULT = 2.0
SWING = 3
SL_BUF_ATR = 0.10
CONFIRM_WIN = 6           # engulfing must print within N bars of the tap
TAP_WIN = 25              # first tap must occur within N bars of the FVG
CHOCH_RECENCY = CONFIRM_WIN + 12

INDEX_SYMBOLS = ("NSE:NIFTY 50", "NSE:NIFTY BANK")  # index 5m ONLY


def fvg_tap_mode() -> str:
    """`FVG_TAP_MODE` env, lowered. ''/'off' ⟹ fully inert.

    off    → never invoked (byte-identical engine)
    shadow → log would-fire to lifecycle ledger only (no outward signal)
    alert  → shadow + isolated Telegram + website panel (NOT auto-traded)
    live   → also fire as a real auto-traded signal (only after the
             forward-shadow scorecard clears — gated operationally)
    """
    return os.getenv("FVG_TAP_MODE", "").strip().lower()


def _atr_at(c, i, p=14):
    seg = c[max(0, i - p - 1):i + 1]
    if len(seg) < p + 1:
        return 0.0
    trs = [max(seg[k]["high"] - seg[k]["low"],
               abs(seg[k]["high"] - seg[k - 1]["close"]),
               abs(seg[k]["low"] - seg[k - 1]["close"]))
           for k in range(1, len(seg))]
    return sum(trs[-p:]) / p


def _bull_engulf(prev, cur):
    return (cur["close"] > cur["open"] and prev["close"] < prev["open"]
            and cur["close"] >= prev["open"] and cur["open"] <= prev["close"])


def _bear_engulf(prev, cur):
    return (cur["close"] < cur["open"] and prev["close"] > prev["open"]
            and cur["close"] <= prev["open"] and cur["open"] >= prev["close"])


def evaluate_fvg_tap(candles: list, direction: str) -> dict | None:
    """Return a fresh FVG-Tap signal iff the ENGULFING confirmation is
    the most recently CLOSED candle (so entry = the next/current bar,
    MARKET). Else None. Pure; no look-ahead. `direction` ∈ LONG/SHORT.

    Rule (identical to the gated backtest):
      5m CHoCH gate → FVG from the displacement → FIRST tap of the FVG
      → ENGULFING confirmation → entry next bar → SL just beyond the
      FVG → fixed R = 2.0 target.
    """
    n = len(candles)
    if n < 80:
        return None
    long = direction == "LONG"
    c = candles
    i = n - 1  # decision point = the just-closed bar (the confirmation)

    sh, sl = smc.detect_swing_points(c[:i + 1], SWING, SWING)
    if len(sh) < 2 or len(sl) < 2:
        return None
    atr = _atr_at(c, i)
    if atr <= 0:
        return None

    # --- CHoCH gate ---------------------------------------------------
    if long:
        piv_idx, _ = sl[-1]
        lh = next(((hx, hp) for hx, hp in reversed(sh) if hx < piv_idx), None)
        if not lh:
            return None
        choch = next((j for j in range(piv_idx + 1, i + 1)
                      if c[j]["close"] > lh[1]), None)
    else:
        piv_idx, _ = sh[-1]
        hl = next(((lx, lp) for lx, lp in reversed(sl) if lx < piv_idx), None)
        if not hl:
            return None
        choch = next((j for j in range(piv_idx + 1, i + 1)
                      if c[j]["close"] < hl[1]), None)
    if choch is None or choch < i - CHOCH_RECENCY:
        return None

    # --- FVG from the displacement that made the CHoCH ---------------
    fvg = None
    for a in range(max(piv_idx, choch - 8), choch + 1):
        if a + 2 > i:
            break
        if long and c[a]["high"] < c[a + 2]["low"]:
            fvg = (c[a]["high"], c[a + 2]["low"], a + 2)
        elif not long and c[a]["low"] > c[a + 2]["high"]:
            fvg = (c[a + 2]["high"], c[a]["low"], a + 2)
    if fvg is None:
        return None
    fvg_lo, fvg_hi, fvg_done = fvg
    if fvg_hi <= fvg_lo:
        return None

    # --- FIRST tap of the FVG ----------------------------------------
    tap = None
    for k in range(fvg_done + 1, min(fvg_done + TAP_WIN, i + 1)):
        if (long and c[k]["low"] <= fvg_hi) or (not long and c[k]["high"] >= fvg_lo):
            tap = k
            break
    if tap is None:
        return None

    # --- ENGULFING confirmation, and it must be the LAST closed bar ---
    conf = None
    for k in range(tap, min(tap + CONFIRM_WIN, i + 1)):
        if k < 1:
            continue
        if (long and _bull_engulf(c[k - 1], c[k])) or \
           (not long and _bear_engulf(c[k - 1], c[k])):
            conf = k
            break
    if conf is None or conf != i:
        return None  # only actionable the instant confirmation closes

    # --- planned entry/SL/target (entry fills on the next/current bar)
    entry = c[i]["close"]  # best available proxy for next-bar MARKET
    if long:
        sl_px = fvg_lo - SL_BUF_ATR * atr
        risk = entry - sl_px
        if risk <= 0:
            return None
        target = entry + R_MULT * risk
    else:
        sl_px = fvg_hi + SL_BUF_ATR * atr
        risk = sl_px - entry
        if risk <= 0:
            return None
        target = entry - R_MULT * risk

    return {
        "setup": "FVG_TAP",
        "direction": direction,
        "entry": round(entry, 2),
        "sl": round(sl_px, 2),
        "target": round(target, 2),
        "rr": R_MULT,
        "fvg_low": round(fvg_lo, 2),
        "fvg_high": round(fvg_hi, 2),
        "choch_idx": choch,
        "tap_idx": tap,
        "confirm_time": c[i].get("date"),
        "reason": "5m CHoCH → FVG → first tap → engulfing confirmation",
    }
