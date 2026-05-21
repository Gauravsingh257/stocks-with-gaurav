"""
scripts/fvg_tap_diagnose.py
============================
Read-only diagnostic: for a given date and index, walk every 5m bar and
print exactly *which strict condition* of the FVG-Tap rule passed or
failed — and **by how much**. This makes the gap between visual reading
and the strict algorithm concrete (not theoretical).

For each bar of the day, evaluates the 4 gates independently:
    1) CHoCH   — was the most recent close strictly above the last
                 confirmed lower-high (for LONG)?
    2) FVG     — does a strict 3-candle bullish gap exist near the CHoCH
                 leg? (candle1.high < candle3.low)
    3) Tap     — did price first re-enter the FVG zone before continuation?
    4) Engulf  — did a strict bullish engulfing candle print at/after tap?

Reports: time, direction, pass/fail per gate, *and the precise miss
margin* for failed gates (e.g. 'FVG fail by 1 tick: 23,495.0 vs 23,494.5').

Pure observation — no scoring, no tuning. Helps the disciplined
enhancement loop: gather near-miss evidence, score forward outcomes,
relax only with data. Run locally:
    python scripts/fvg_tap_diagnose.py --date 2026-05-20
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smc_detectors as smc
from engine.fvg_tap_setup import (
    SWING, CONFIRM_WIN, TAP_WIN, CHOCH_RECENCY, SL_BUF_ATR,
    R_MULT, _atr_at, _bull_engulf, _bear_engulf,
)

# Diagnostic-only — defines the "pre-continuation" gate width in ATR
# multiples beyond the broken CHoCH level. Mirrors the same constant in
# scripts/fresh_phase_research.py; intentionally NOT exported from the
# production detector (engine.fvg_tap_setup) since the live rule does not
# evaluate this gate — it exists here only to flag near-misses where price
# extended past the CHoCH break before the first FVG tap (= late entry).
CONT_ATR = 1.0

YF = {"NIFTY 50 (^NSEI)": "^NSEI", "BANKNIFTY (^NSEBANK)": "^NSEBANK"}


def _load(ticker: str, period: str = "5d"):
    import yfinance as yf
    df = yf.download(ticker, interval="5m", period=period,
                     progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return []
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df = df.droplevel(1, axis=1)
    try:
        idx = df.index.tz_convert("Asia/Kolkata")
    except (TypeError, AttributeError):
        idx = df.index
    out = []
    for ts, row in zip(idx, df.itertuples(index=False)):
        o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
        if not (o > 0 and h > 0 and l > 0 and c > 0):
            continue
        out.append({"date": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                    "open": o, "high": h, "low": l, "close": c})
    return out


def diagnose_bar(candles: list, direction: str) -> dict | None:
    """Evaluate every gate independently on the last closed bar."""
    n = len(candles)
    if n < 80:
        return None
    long = direction == "LONG"
    c = candles
    i = n - 1
    sh, sl = smc.detect_swing_points(c[:i + 1], SWING, SWING)
    if len(sh) < 2 or len(sl) < 2:
        return None
    atr = _atr_at(c, i)
    if atr <= 0:
        return None

    diag = {"time": c[i]["date"], "direction": direction,
            "atr": round(atr, 2), "gates": {}}

    # Gate 1: CHoCH
    if long:
        piv_idx, _ = sl[-1]
        lh = next(((hx, hp) for hx, hp in reversed(sh) if hx < piv_idx), None)
    else:
        piv_idx, _ = sh[-1]
        lh = next(((lx, lp) for lx, lp in reversed(sl) if lx < piv_idx), None)
    if not lh:
        diag["gates"]["choch"] = {"pass": False, "reason": "no prior pivot"}
        return diag
    choch = None
    for j in range(piv_idx + 1, i + 1):
        if long and c[j]["close"] > lh[1]:
            choch = j; break
        if not long and c[j]["close"] < lh[1]:
            choch = j; break
    if choch is None:
        diag["gates"]["choch"] = {"pass": False, "reason": f"no close beyond {lh[1]:.2f}"}
        return diag
    if choch < i - CHOCH_RECENCY:
        diag["gates"]["choch"] = {"pass": False,
            "reason": f"CHoCH at idx {choch} too old (>{CHOCH_RECENCY} bars ago)"}
        return diag
    diag["gates"]["choch"] = {"pass": True, "at": c[choch]["date"],
                              "broken_level": round(lh[1], 2)}

    # Gate 2: FVG
    fvg = None; fvg_fail_reason = None
    for a in range(max(piv_idx, choch - 8), choch + 1):
        if a + 2 > i:
            break
        c0, c2 = c[a], c[a + 2]
        if long:
            if c0["high"] < c2["low"]:
                fvg = (c0["high"], c2["low"], a + 2); break
            else:
                fvg_fail_reason = (f"overlap at idx {a}: c0.high={c0['high']:.2f} "
                                   f"≥ c2.low={c2['low']:.2f} "
                                   f"(by {c0['high']-c2['low']:.2f})")
        else:
            if c0["low"] > c2["high"]:
                fvg = (c2["high"], c0["low"], a + 2); break
            else:
                fvg_fail_reason = (f"overlap at idx {a}: c0.low={c0['low']:.2f} "
                                   f"≤ c2.high={c2['high']:.2f} "
                                   f"(by {c2['high']-c0['low']:.2f})")
    if fvg is None:
        diag["gates"]["fvg"] = {"pass": False,
            "reason": fvg_fail_reason or "no 3-candle gap in displacement window"}
        return diag
    fvg_lo, fvg_hi, fvg_done = fvg
    diag["gates"]["fvg"] = {"pass": True,
                            "zone": [round(fvg_lo, 2), round(fvg_hi, 2)],
                            "completed_at": c[fvg_done]["date"]}

    # Gate 3: first tap
    tap = None
    for k in range(fvg_done + 1, min(fvg_done + TAP_WIN, i + 1)):
        if (long and c[k]["low"] <= fvg_hi) or (not long and c[k]["high"] >= fvg_lo):
            tap = k; break
    if tap is None:
        diag["gates"]["tap"] = {"pass": False, "reason": "no retest into FVG yet"}
        return diag
    diag["gates"]["tap"] = {"pass": True, "at": c[tap]["date"]}

    # Gate 3b: pre-continuation
    cont_level = (lh[1] + CONT_ATR * atr) if long else (lh[1] - CONT_ATR * atr)
    pre_cont_pass = True; ext_run = 0.0
    for k in range(choch + 1, tap + 1):
        if long:
            ext_run = max(ext_run, c[k]["high"])
            if c[k]["high"] >= cont_level:
                pre_cont_pass = False; break
        else:
            ext_run = c[k]["low"] if ext_run == 0 else min(ext_run, c[k]["low"])
            if c[k]["low"] <= cont_level:
                pre_cont_pass = False; break
    if not pre_cont_pass:
        diag["gates"]["pre_cont"] = {"pass": False,
            "reason": f"continuation ran past {cont_level:.2f} before first tap (LATE)"}
        return diag
    diag["gates"]["pre_cont"] = {"pass": True,
                                  "cont_level_was": round(cont_level, 2)}

    # Gate 4: engulfing confirmation (on the LAST closed bar = i)
    conf = None
    for k in range(tap, min(tap + CONFIRM_WIN, i + 1)):
        if k < 1:
            continue
        if (long and _bull_engulf(c[k - 1], c[k])) or \
           (not long and _bear_engulf(c[k - 1], c[k])):
            conf = k; break
    if conf is None:
        # Show how close — body comparison of the last candle vs prior
        prev, cur = c[i - 1], c[i]
        if long:
            cond_bull = cur["close"] > cur["open"]
            cond_prev_bear = prev["close"] < prev["open"]
            cond_close = cur["close"] >= prev["open"]
            cond_open = cur["open"] <= prev["close"]
            why = []
            if not cond_bull: why.append("cur not bullish")
            if not cond_prev_bear: why.append("prev not bearish")
            if not cond_close: why.append(f"cur.close {cur['close']:.2f} < prev.open {prev['open']:.2f}")
            if not cond_open: why.append(f"cur.open {cur['open']:.2f} > prev.close {prev['close']:.2f}")
            diag["gates"]["engulf"] = {"pass": False, "reason": "; ".join(why)
                                       or "no bullish engulf within window"}
        else:
            cond_bear = cur["close"] < cur["open"]
            cond_prev_bull = prev["close"] > prev["open"]
            cond_close = cur["close"] <= prev["open"]
            cond_open = cur["open"] >= prev["close"]
            why = []
            if not cond_bear: why.append("cur not bearish")
            if not cond_prev_bull: why.append("prev not bullish")
            if not cond_close: why.append(f"cur.close {cur['close']:.2f} > prev.open {prev['open']:.2f}")
            if not cond_open: why.append(f"cur.open {cur['open']:.2f} < prev.close {prev['close']:.2f}")
            diag["gates"]["engulf"] = {"pass": False, "reason": "; ".join(why)
                                       or "no bearish engulf within window"}
        return diag
    diag["gates"]["engulf"] = {"pass": True, "at": c[conf]["date"]}
    diag["fires"] = True
    return diag


def _print_diag(diag: dict, only_misses: bool = False) -> None:
    fires = diag.get("fires", False)
    if only_misses and fires:
        return
    gates = diag["gates"]
    passed = [k for k, v in gates.items() if v.get("pass")]
    failed = [k for k, v in gates.items() if not v.get("pass")]
    tag = "FIRE" if fires else f"MISS by {failed[0] if failed else '?'}"
    print(f"\n[{diag['time'][11:16]}] {diag['direction']:<5} → {tag}  "
          f"(passed: {','.join(passed) or '—'}; ATR={diag['atr']})")
    for gate in ("choch", "fvg", "tap", "pre_cont", "engulf"):
        if gate not in gates:
            continue
        g = gates[gate]
        sym = "✓" if g.get("pass") else "✗"
        if g.get("pass"):
            extras = " ".join(f"{k}={v}" for k, v in g.items() if k != "pass")
            print(f"    {sym} {gate}: {extras}")
        else:
            print(f"    {sym} {gate} FAILED: {g.get('reason', '')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="FVG-Tap strict-gate diagnostic")
    ap.add_argument("--date", default=str(date.today()),
                    help="YYYY-MM-DD (default: today)")
    ap.add_argument("--only-misses", action="store_true",
                    help="hide fires; show only near-misses (most useful)")
    ap.add_argument("--bars-back", type=int, default=12,
                    help="for each bar of the day, eval the last N (default 12)")
    args = ap.parse_args()

    target = args.date
    print("=" * 72)
    print(f"  FVG-Tap STRICT-GATE DIAGNOSTIC  ·  date={target}")
    print(f"  For each bar of the day, evaluates which of the 4 gates")
    print(f"  passed/failed — and by how much.")
    print("=" * 72)

    for label, ticker in YF.items():
        try:
            bars = _load(ticker)
        except Exception as exc:
            print(f"\n  {label}: data fetch failed: {exc}")
            continue
        if not bars:
            print(f"\n  {label}: no data")
            continue
        idxs = [i for i, b in enumerate(bars) if b["date"].startswith(target)]
        if not idxs:
            print(f"\n  {label}: no bars on {target}")
            continue
        print(f"\n  ━━━━━ {label} ━━━━━")
        any_fire = False
        n_misses = 0
        for i in idxs:
            window = bars[: i + 1]
            for direction in ("LONG", "SHORT"):
                diag = diagnose_bar(window, direction)
                if diag is None:
                    continue
                fires = diag.get("fires", False)
                if fires:
                    any_fire = True
                # Show: fires + near-misses (3 of 4 gates passed)
                gates = diag["gates"]
                passed = sum(1 for g in gates.values() if g.get("pass"))
                if fires or passed >= 2:
                    _print_diag(diag, only_misses=args.only_misses)
                    if not fires:
                        n_misses += 1
        if not any_fire:
            print(f"\n  → No FVG-Tap fires on {target}.")
        print(f"\n  Near-misses (≥2 gates passed): {n_misses}")

    print("\n" + "=" * 72)
    print("  READ: a 'MISS by FVG (overlap 1 tick)' is the visual-vs-strict")
    print("  gap. Collect these across 2 weeks of live data + score forward")
    print("  outcomes → THEN decide if relaxing that specific gate is")
    print("  evidence-supported. Don't relax from one hindsight chart.")
    print("=" * 72)


if __name__ == "__main__":
    main()
