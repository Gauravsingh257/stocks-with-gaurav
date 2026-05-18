"""
scripts/setup_a_frequency_probe.py  —  Step 1 of the Setup-A improvement plan.

PURPOSE
-------
Answer the user's core concern with DATA, not guesses:
  "If we add the quality gate, at each MIN_SCORE setting how many trades do
   we still get, and is the quality actually better?"

It does NOT touch the live engine or change any behaviour. It:
  1. Runs the EXISTING Setup-A backtest (backtest.engine.BacktestEngine) over
     historical data — the same harness behind ALPHA_BASELINE.
  2. For every Setup-A trade it took, reconstructs the POINT-IN-TIME view
     (candles sliced up to that trade's entry bar — no look-ahead) and
     computes the real SetupAFeatures using the project's OWN detectors
     (smc_detectors): liquidity sweep, HTF bias, OB, FVG, RR, entry-distance.
  3. Scores each with engine.setup_a_quality.score_setup_a.
  4. Prints the frequency / quality curve at every MIN_SCORE threshold vs the
     unfiltered baseline (= Setup-A as it trades today), plus where each
     threshold would stand against the frozen gate.

HONEST SCOPE
------------
- Feature snapshots are a faithful point-in-time RECONSTRUCTION using the
  same detectors the engine uses; the engine's internal STRUCTURE_STATE
  machine has timing nuances, so this is calibration-grade (good enough to
  choose MIN_SCORE), not a tick-perfect audit.
- `--synthetic` exists ONLY to prove the pipeline runs end to end. Synthetic
  numbers are NEVER decision-grade and are printed with a loud banner.
- Outcome labels use the trade's realised r_multiple (the dependent
  variable). Features use ONLY data up to the entry bar (no look-ahead).

Usage (where market data exists — local Kite store, or a remote run):
    python scripts/setup_a_frequency_probe.py --db path/to/store.db
    python scripts/setup_a_frequency_probe.py --symbols "NSE:NIFTY 50"
    python scripts/setup_a_frequency_probe.py --synthetic        # wiring test only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BACKTEST_MODE", "1")

import smc_detectors as smc
from backtest.engine import BacktestEngine, BacktestConfig
from backtest.data_store import DataStore
from backtest.runner import generate_synthetic_candles
from engine.setup_a_quality import SetupAFeatures, score_setup_a

DEFAULT_THRESHOLDS = [40, 50, 55, 60, 65, 70, 75, 80, 85]

# Frozen gate (the corrected, user-approved bar). Expectancy here is in R
# (the backtest's native unit); profit factor / win-rate map directly.
GATE = {"win_rate": 0.48, "profit_factor": 1.30, "expectancy_r": 0.0}


def _is_setup_a(setup: str | None) -> bool:
    """Match Setup-A across both label conventions: the backtest engine
    emits 'SETUP-A'; the live engine emits 'A-FVG-BUY' / 'A-STRUCTURE' / 'A'.
    """
    u = (setup or "").upper().replace("_", "-")
    return u == "SETUP-A" or u == "A" or u.startswith("A-")


def _parse_dt(v: str):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "").strip())
    except Exception:
        return None


def _point_in_time(candles: list, entry_time) -> list:
    """Candles up to and including the entry bar — strictly no look-ahead."""
    et = _parse_dt(entry_time)
    if et is None:
        return candles
    out = []
    for c in candles:
        cd = _parse_dt(c.get("date"))
        if cd is None or cd <= et:
            out.append(c)
        else:
            break
    return out


def _features_for(trade, data: dict) -> SetupAFeatures | None:
    tf = data.get(trade.symbol)
    if not tf:
        return None
    ltf_full = tf.get("5m") or []
    htf_full = tf.get("1h") or []
    if not ltf_full:
        return None

    ltf = _point_in_time(ltf_full, trade.entry_time)
    htf = _point_in_time(htf_full, trade.entry_time) if htf_full else []
    if len(ltf) < 30:
        return None

    direction = trade.direction
    bias = smc.detect_htf_bias(htf) if htf else smc.detect_htf_bias(ltf)
    ob = smc.detect_order_block(ltf, direction)
    fvg = smc.detect_fvg(ltf, direction)
    # liquidity_sweep_detected already requires sweep + close-back (reject),
    # i.e. swept AND reclaimed in one — the SAME helper the engine uses.
    swept = bool(smc.liquidity_sweep_detected(ltf))
    atr = smc.calculate_atr(ltf) or 0.0
    cmp_at_fire = ltf[-1]["close"]

    # ob_is_origin proxy (point-in-time, honest): an OB exists AND a sweep
    # printed recently AND the OB body sits within ~1 ATR of the swept
    # extreme — i.e. the OB that formed off the liquidity grab, not a
    # random nearest OB mid-trend.
    ob_is_origin = False
    if ob and swept and atr > 0:
        recent_low = min(c["low"] for c in ltf[-12:])
        recent_high = max(c["high"] for c in ltf[-12:])
        ref = recent_low if direction == "LONG" else recent_high
        ob_edge = ob[0] if direction == "LONG" else ob[1]
        ob_is_origin = abs(ob_edge - ref) <= atr * 1.0

    dist_atr = abs(trade.entry_price - cmp_at_fire) / atr if atr > 0 else None

    return SetupAFeatures(
        sweep_detected=swept,
        sweep_reclaimed=swept,  # the helper inherently requires the reclaim
        ob_is_origin=ob_is_origin,
        fvg_present=fvg is not None,
        regime_aligned=(bias == direction),
        rr=float(trade.rr or 0.0),
        dist_from_cmp_atr=dist_atr,
    )


def _stats(trades: list) -> dict:
    if not trades:
        return {"n": 0, "win_rate": 0.0, "expectancy_r": 0.0,
                "profit_factor": 0.0, "total_r": 0.0, "avg_rr": 0.0}
    rs = [t.r_multiple for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gp = sum(wins)
    gl = abs(sum(losses)) or 1e-9
    return {
        "n": len(trades),
        "win_rate": round(len(wins) / len(rs), 4),
        "expectancy_r": round(sum(rs) / len(rs), 4),
        "profit_factor": round(gp / gl, 3),
        "total_r": round(sum(rs), 2),
        "avg_rr": round(sum(t.rr for t in trades) / len(trades), 2),
    }


def _gate_pass(s: dict) -> bool:
    return (s["n"] > 0 and s["win_rate"] >= GATE["win_rate"]
            and s["profit_factor"] >= GATE["profit_factor"]
            and s["expectancy_r"] > GATE["expectancy_r"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Setup-A frequency/quality probe")
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--symbols", type=str, default=None)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--synthetic-days", type=int, default=120)
    ap.add_argument("--thresholds", type=str,
                    default=",".join(map(str, DEFAULT_THRESHOLDS)))
    ap.add_argument("--output", type=str, default="backtest_results/setup_a_frequency.json")
    args = ap.parse_args()

    if args.synthetic:
        print("=" * 72)
        print("  SYNTHETIC DATA — WIRING TEST ONLY. NUMBERS ARE NOT DECISION-GRADE.")
        print("=" * 72)
        candles = generate_synthetic_candles(days=args.synthetic_days)
        data = {"SYNTHETIC:INDEX": {"5m": candles}}
    else:
        store = DataStore(args.db) if args.db else DataStore()
        syms = args.symbols.split(",") if args.symbols else None
        from backtest.runner import load_data_from_store
        data = load_data_from_store(store, syms)
        store.close()
        if not data:
            print("ERROR: no market data in the store. Populate it (Kite "
                  "fetcher / CSV import) or use --synthetic for a wiring test.")
            sys.exit(1)

    trades = BacktestEngine(BacktestConfig()).run_multi(data)
    setup_a = [t for t in trades if _is_setup_a(t.setup)]
    print(f"\nTotal trades: {len(trades)}  |  Setup-A trades: {len(setup_a)}")
    if not setup_a:
        print("No Setup-A trades in this dataset — nothing to calibrate.")
        sys.exit(0)

    scored = []
    skipped = 0
    for t in setup_a:
        feats = _features_for(t, data)
        if feats is None:
            skipped += 1
            continue
        scored.append((t, score_setup_a(feats)))
    print(f"Scored {len(scored)} Setup-A trades ({skipped} skipped: short window)\n")
    if not scored:
        sys.exit(0)

    baseline = _stats([t for t, _ in scored])
    thresholds = [int(x) for x in args.thresholds.split(",")]

    rows = []
    print(f"{'MIN':>4} {'fires':>6} {'%base':>6} {'watch':>6} "
          f"{'winR%':>7} {'expR':>8} {'PF':>7} {'totR':>8} {'gate':>5}")
    print("-" * 64)
    base_row = {"min_score": "BASE", **baseline,
                "pct_base": 100.0, "watch": 0, "gate_pass": _gate_pass(baseline)}
    print(f"{'BASE':>4} {baseline['n']:>6} {'100':>6} {'-':>6} "
          f"{baseline['win_rate']*100:>6.1f} {baseline['expectancy_r']:>8.3f} "
          f"{baseline['profit_factor']:>7.2f} {baseline['total_r']:>8.1f} "
          f"{'Y' if _gate_pass(baseline) else 'N':>5}")
    for thr in sorted(thresholds):
        fired = [t for t, q in scored if q.score >= thr]
        watch = [t for t, q in scored
                 if q.score < thr and q.score >= thr - 12]
        s = _stats(fired)
        pct = round(100.0 * s["n"] / max(baseline["n"], 1), 1)
        gp = _gate_pass(s)
        rows.append({"min_score": thr, **s, "pct_base": pct,
                     "watch": len(watch), "gate_pass": gp})
        print(f"{thr:>4} {s['n']:>6} {pct:>6.1f} {len(watch):>6} "
              f"{s['win_rate']*100:>6.1f} {s['expectancy_r']:>8.3f} "
              f"{s['profit_factor']:>7.2f} {s['total_r']:>8.1f} "
              f"{'Y' if gp else 'N':>5}")

    out = {
        "generated_at": datetime.now().isoformat(),
        "synthetic": args.synthetic,
        "baseline": base_row,
        "by_threshold": rows,
        "gate": GATE,
        "note": ("SYNTHETIC — not decision-grade" if args.synthetic
                 else "real backtest data"),
    }
    op = Path(args.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {op}")
    print("\nHow to read this: pick the lowest MIN_SCORE whose row both "
          "clears the gate (gate=Y) AND keeps an acceptable trade count "
          "(fires / %base). That is your frequency-vs-quality decision, "
          "made on data — not guesses.")


if __name__ == "__main__":
    main()
