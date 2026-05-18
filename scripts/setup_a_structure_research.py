"""
scripts/setup_a_structure_research.py
=====================================
PHASE A of the structure-first edge investigation.

PURE RESEARCH. No scoring, no weights, no curve-fitting, no production
wiring. engine/setup_a_quality.py is FROZEN and is NOT imported here.

Question being investigated (NOT "does SMC have edge?" — that is not what
the scorer probe tested):
  Does planned execution on FRESH structure have real edge BEFORE
  continuation becomes obvious — i.e. are the winners the quiet/early
  ones and the losers the late continuation chases?

It reuses the proven data + backtest path from the frequency probe, then
for every Setup-A trade computes a STRUCTURE-FIRST feature set (only the
user's list), and reports:
  1. Winners vs losers: per-feature distribution + separation.
  2. A composite "setup maturity" index → EARLY vs LATE outcome split
     (the core hypothesis test).

EXCLUDED on purpose (per direction): liquidity-sweep bonus,
displacement-excitement bias, late-continuation bias, and constant /
non-discriminating features.

HONEST SCOPE: point-in-time reconstruction with the project's own
detectors; small samples are DIRECTIONAL, not conclusive. Decide nothing
from a 40-trade run — that is what the frozen-window study (Phase C) is
for.

Run locally (reliable path):
    python scripts/setup_a_structure_research.py --yf
    python scripts/setup_a_structure_research.py --db path/to/store.db
    python scripts/setup_a_structure_research.py --synthetic   # wiring only
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import smc_detectors as smc
from backtest.engine import BacktestEngine, BacktestConfig
import setup_a_frequency_probe as probe  # reuse proven loaders (no scorer)


# --------------------------------------------------------------------------
# STRUCTURE-FIRST FEATURES  (point-in-time; no look-ahead; no sweep/displace)
# --------------------------------------------------------------------------
def _structural_features(trade, data: dict) -> dict | None:
    tf = data.get(trade.symbol)
    if not tf:
        return None
    ltf = probe._point_in_time(tf.get("5m") or [], trade.entry_time)
    htf = probe._point_in_time(tf.get("1h") or [], trade.entry_time)
    if len(ltf) < 40:
        return None

    direction = trade.direction
    atr = smc.calculate_atr(ltf) or 0.0
    if atr <= 0:
        return None
    entry = float(trade.entry_price)
    closes = [c["close"] for c in ltf]
    highs = [c["high"] for c in ltf]
    lows = [c["low"] for c in ltf]

    sh, sl = smc.detect_swing_points(ltf, left=3, right=3)

    # --- expansion origin: last swing in trade direction's favour before
    # entry (LONG -> last swing low; SHORT -> last swing high) ----------
    if direction == "LONG":
        origin_idx, origin_px = (sl[-1] if sl else (max(0, len(ltf) - 20), min(lows[-20:])))
    else:
        origin_idx, origin_px = (sh[-1] if sh else (max(0, len(ltf) - 20), max(highs[-20:])))
    bars_from_origin = len(ltf) - 1 - origin_idx
    dist_from_origin_atr = abs(entry - origin_px) / atr

    # --- bars since BOS/CHoCH: first close beyond the prior opposite
    # swing after the origin (structure break that started this leg) ----
    bos_idx = None
    if direction == "LONG":
        prior_high = max((p for i, p in sh if i < origin_idx), default=None)
        if prior_high is not None:
            for j in range(origin_idx + 1, len(ltf)):
                if closes[j] > prior_high:
                    bos_idx = j
                    break
    else:
        prior_low = min((p for i, p in sl if i < origin_idx), default=None)
        if prior_low is not None:
            for j in range(origin_idx + 1, len(ltf)):
                if closes[j] < prior_low:
                    bos_idx = j
                    break
    bars_since_bos = (len(ltf) - 1 - bos_idx) if bos_idx is not None else -1

    # --- imbalance freshness: bars since the (dir) FVG, + first-tap ----
    fvg = smc.detect_fvg(ltf, direction)
    imbalance_fresh_bars = -1
    fvg_first_tap = 0
    if fvg:
        f_lo, f_hi = fvg
        formed = None
        for j in range(len(ltf) - 3, 2, -1):
            a, c = ltf[j - 1], ltf[j + 1]
            gap = (a["high"] < c["low"]) if direction == "LONG" else (a["low"] > c["high"])
            if gap:
                formed = j
                break
        if formed is not None:
            imbalance_fresh_bars = len(ltf) - 1 - formed
            taps = sum(1 for k in range(formed + 1, len(ltf))
                       if ltf[k]["low"] <= f_hi and ltf[k]["high"] >= f_lo)
            fvg_first_tap = 1 if taps <= 1 else 0

    # --- fresh origin-OB reclaim: OB exists & mitigated <=1x ----------
    ob = smc.detect_order_block(ltf, direction)
    ob_fresh = 0
    if ob:
        o_lo, o_hi = ob
        mit = sum(1 for c in ltf[-15:] if c["low"] <= o_hi and c["high"] >= o_hi)
        ob_fresh = 1 if mit <= 2 else 0

    # --- compression before expansion: range of the 10 bars before the
    # leg vs ATR (tight coil -> small ratio) ---------------------------
    pre = ltf[max(0, origin_idx - 10):origin_idx + 1]
    compression = ((max(c["high"] for c in pre) - min(c["low"] for c in pre)) / atr
                   if pre else 99.0)

    # --- HTF alignment QUALITY (not boolean): aligned * trend strength
    htf_bias = smc.detect_htf_bias(htf) if htf else None
    htf_quality = 0.0
    if htf_bias == direction and len(htf) >= 20:
        hatr = smc.calculate_atr(htf) or atr
        hmean = sum(x["close"] for x in htf[-20:]) / 20
        htf_quality = max(0.0, min(1.0, abs(htf[-1]["close"] - hmean) / (hatr * 2 or 1)))

    # --- entry location efficiency: 0 = deep discount(L)/premium(S)
    # (good), 1 = chasing. Range = recent dealing range. -------------
    rng_hi = max(highs[-30:])
    rng_lo = min(lows[-30:])
    span = max(rng_hi - rng_lo, 1e-9)
    pos = (entry - rng_lo) / span
    entry_eff = pos if direction == "LONG" else (1 - pos)  # lower = better

    # --- move maturity / exhaustion: legs since BOS + extension ------
    legs = 0
    if direction == "LONG":
        legs = sum(1 for i in range(max(1, len(sh) - 6), len(sh))
                   if sh[i][1] > sh[i - 1][1]) if len(sh) > 1 else 0
    else:
        legs = sum(1 for i in range(max(1, len(sl) - 6), len(sl))
                   if sl[i][1] < sl[i - 1][1]) if len(sl) > 1 else 0
    extension_atr = abs(closes[-1] - origin_px) / atr
    maturity_raw = (dist_from_origin_atr * 0.4 + extension_atr * 0.3
                    + max(0, bars_since_bos) * 0.02 + legs * 0.5
                    + entry_eff * 2.0)

    # --- reaction quality after tap: confirmation candle body/wick ---
    last = ltf[-1]
    body = abs(last["close"] - last["open"])
    rng = max(last["high"] - last["low"], 1e-9)
    if direction == "LONG":
        rej_wick = (min(last["open"], last["close"]) - last["low"]) / rng
        directional = 1.0 if last["close"] > last["open"] else 0.0
    else:
        rej_wick = (last["high"] - max(last["open"], last["close"])) / rng
        directional = 1.0 if last["close"] < last["open"] else 0.0
    reaction_quality = round(directional * (0.5 + 0.5 * rej_wick), 3)

    # --- session bucket (IST) ----------------------------------------
    dt = probe._parse_dt(trade.entry_time)
    hm = (dt.hour * 60 + dt.minute) if dt else 0
    if hm < 600:
        sess = "OPEN(9:15-10)"
    elif hm < 720:
        sess = "MORN(10-12)"
    elif hm < 810:
        sess = "MID(12-13:30)"
    elif hm < 900:
        sess = "NOON(13:30-15)"
    else:
        sess = "CLOSE(15+)"

    # ==================================================================
    # INSTITUTIONAL DEEPENING — measurable, transparent rules. NOT fitted
    # weights, NOT a scorer; these are fixed definitional classifiers for
    # observation only (per directive: no optimization).
    # ==================================================================
    imp_start = bos_idx if bos_idx is not None else origin_idx
    impulse = ltf[imp_start:] if imp_start < len(ltf) - 1 else ltf[-3:]

    # (1) STRUCTURE LIFECYCLE — fresh / mid-cycle / exhausted-late.
    # HTF equilibrium = midpoint of HTF dealing range; how far price has
    # drifted from it (premium/discount of the larger structure).
    if htf and len(htf) >= 20:
        h_hi = max(x["high"] for x in htf[-40:])
        h_lo = min(x["low"] for x in htf[-40:])
        h_eq = (h_hi + h_lo) / 2
        hatr = smc.calculate_atr(htf) or atr
        dist_htf_eq_atr = abs(entry - h_eq) / (hatr or atr)
    else:
        dist_htf_eq_atr = 0.0
    if (bars_since_bos != -1 and bars_since_bos <= 8 and extension_atr <= 2.0
            and legs <= 1):
        lifecycle = "FRESH"
    elif (extension_atr >= 4.0 or legs >= 3 or bars_since_bos >= 25
          or dist_htf_eq_atr >= 4.0):
        lifecycle = "LATE"
    else:
        lifecycle = "MID"

    # (2) EXPANSION QUALITY — clean vs messy displacement.
    if len(impulse) >= 3:
        body_ratio = sum(abs(c["close"] - c["open"])
                         / max(c["high"] - c["low"], 1e-9)
                         for c in impulse) / len(impulse)
        overlaps = 0
        for i in range(1, len(impulse)):
            a, b = impulse[i - 1], impulse[i]
            ov = min(a["high"], b["high"]) - max(a["low"], b["low"])
            if ov > 0.5 * (a["high"] - a["low"] + 1e-9):
                overlaps += 1
        overlap_frac = overlaps / max(1, len(impulse) - 1)
    else:
        body_ratio, overlap_frac = 0.0, 1.0
    expansion_clean = 1 if (body_ratio >= 0.55 and overlap_frac <= 0.40) else 0
    # follow-through right after BOS (consecutive dir closes)
    ft = 0
    if bos_idx is not None:
        for j in range(bos_idx, min(bos_idx + 6, len(ltf) - 1)):
            up = ltf[j + 1]["close"] > ltf[j]["close"]
            if (up and direction == "LONG") or (not up and direction == "SHORT"):
                ft += 1
            else:
                break
    follow_through = ft

    # (3) RE-ENTRY vs FIRST-TOUCH — count distinct entries into the OB
    # zone (a "touch" = a fresh crossing into the zone from outside).
    touch_count = 1
    if ob:
        o_lo, o_hi = ob
        if o_hi >= o_lo:
            crossings = 0
            for k in range(1, len(ltf)):
                now_in = ltf[k]["low"] <= o_hi and ltf[k]["high"] >= o_lo
                prev_in = ltf[k - 1]["low"] <= o_hi and ltf[k - 1]["high"] >= o_lo
                if now_in and not prev_in:
                    crossings += 1
            touch_count = max(1, crossings)
    touch_bucket = "1st" if touch_count <= 1 else ("2nd" if touch_count == 2 else "3rd+")

    # (4) VOLATILITY REGIME — current ATR vs ~1-session trailing ATR.
    atr_ref = smc.calculate_atr(ltf[-90:-15]) if len(ltf) >= 105 else atr
    vol_ratio = atr / atr_ref if atr_ref > 0 else 1.0
    vol_regime = ("LOW" if vol_ratio < 0.8
                  else "HIGH" if vol_ratio > 1.3 else "NORMAL")

    # (5) HTF CONTEXT QUALITY — structural, not just "trend bullish".
    htf_trend_clean = 0
    htf_fvg_fresh = 0
    htf_liq_atr = 0.0
    if htf and len(htf) >= 20:
        hsh, hsl = smc.detect_swing_points(htf, 2, 2)
        if direction == "LONG" and len(hsl) >= 2 and len(hsh) >= 2:
            htf_trend_clean = 1 if (hsl[-1][1] > hsl[-2][1]
                                    and hsh[-1][1] >= hsh[-2][1]) else 0
        elif direction == "SHORT" and len(hsh) >= 2 and len(hsl) >= 2:
            htf_trend_clean = 1 if (hsh[-1][1] < hsh[-2][1]
                                    and hsl[-1][1] <= hsl[-2][1]) else 0
        hfvg = smc.detect_fvg(htf, direction)
        if hfvg:
            for j in range(len(htf) - 3, max(2, len(htf) - 12), -1):
                a, c = htf[j - 1], htf[j + 1]
                if ((a["high"] < c["low"]) if direction == "LONG"
                        else (a["low"] > c["high"])):
                    htf_fvg_fresh = 1
                    break
        tgt = (max(p for _, p in hsh) if direction == "LONG" and hsh
               else min(p for _, p in hsl) if hsl else entry)
        htf_liq_atr = abs(tgt - entry) / (hatr or atr)
    htf_ctx_score = htf_trend_clean + htf_fvg_fresh + (1 if 0 < htf_liq_atr <= 6 else 0)

    # (6) TOO-LATE DETECTOR — the key concept. 3 measurable booleans.
    extended = 1 if extension_atr >= 3.0 else 0
    max_body = max((abs(c["close"] - c["open"]) for c in impulse), default=0.0)
    displaced_already = 1 if max_body >= 1.8 * atr else 0
    streak = 0
    for k in range(len(ltf) - 1, 0, -1):
        up = ltf[k]["close"] > ltf[k]["open"]
        if (up and direction == "LONG") or (not up and direction == "SHORT"):
            streak += 1
        else:
            break
    retail_obvious = 1 if streak >= 4 else 0
    too_late_count = extended + displaced_already + retail_obvious
    too_late_bucket = ("EARLY" if too_late_count == 0
                       else "DEVELOPING" if too_late_count == 1
                       else "TOO_LATE")

    return {
        "r": float(trade.r_multiple),
        "win": 1 if trade.r_multiple > 0 else 0,
        "dist_from_origin_atr": round(dist_from_origin_atr, 2),
        "bars_from_origin": bars_from_origin,
        "bars_since_bos": bars_since_bos,
        "imbalance_fresh_bars": imbalance_fresh_bars,
        "fvg_first_tap": fvg_first_tap,
        "ob_fresh": ob_fresh,
        "compression": round(compression, 2),
        "htf_quality": round(htf_quality, 2),
        "entry_eff": round(entry_eff, 2),
        "legs_since_bos": legs,
        "extension_atr": round(extension_atr, 2),
        "maturity_raw": round(maturity_raw, 2),
        "reaction_quality": reaction_quality,
        "session": sess,
        # institutional deepening
        "lifecycle": lifecycle,
        "dist_htf_eq_atr": round(dist_htf_eq_atr, 2),
        "body_ratio": round(body_ratio, 2),
        "overlap_frac": round(overlap_frac, 2),
        "expansion_clean": expansion_clean,
        "follow_through": follow_through,
        "touch_count": touch_count,
        "touch_bucket": touch_bucket,
        "vol_ratio": round(vol_ratio, 2),
        "vol_regime": vol_regime,
        "htf_trend_clean": htf_trend_clean,
        "htf_fvg_fresh": htf_fvg_fresh,
        "htf_liq_atr": round(htf_liq_atr, 2),
        "htf_ctx_score": htf_ctx_score,
        "extended": extended,
        "displaced_already": displaced_already,
        "retail_obvious": retail_obvious,
        "too_late_count": too_late_count,
        "too_late_bucket": too_late_bucket,
    }


NUMERIC = ["dist_from_origin_atr", "bars_from_origin", "bars_since_bos",
           "imbalance_fresh_bars", "compression", "htf_quality",
           "entry_eff", "legs_since_bos", "extension_atr",
           "maturity_raw", "reaction_quality", "dist_htf_eq_atr",
           "body_ratio", "overlap_frac", "follow_through",
           "touch_count", "vol_ratio", "htf_liq_atr",
           "htf_ctx_score", "too_late_count"]
BINARY = ["fvg_first_tap", "ob_fresh", "expansion_clean",
          "htf_trend_clean", "htf_fvg_fresh", "extended",
          "displaced_already", "retail_obvious"]


def _wl(rows):
    return ([r for r in rows if r["win"] == 1], [r for r in rows if r["win"] == 0])


def _mean(xs):
    return round(st.mean(xs), 3) if xs else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description="Setup-A structure-first research (Phase A)")
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--symbols", type=str, default=None)
    ap.add_argument("--yf", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--synthetic-days", type=int, default=90)
    ap.add_argument("--output", type=str,
                    default="backtest_results/setup_a_structure.json")
    args = ap.parse_args()

    if args.synthetic:
        print("=" * 72)
        print("  SYNTHETIC — WIRING TEST ONLY. NOT decision-grade.")
        print("=" * 72)
        from backtest.runner import generate_synthetic_candles
        data = {"SYNTHETIC:INDEX": {"5m": generate_synthetic_candles(days=args.synthetic_days)}}
    elif args.yf:
        print("=" * 72)
        print("  REAL yfinance ^NSEI (trailing ~60d 5m). DIRECTIONAL, small sample.")
        print("=" * 72)
        data = probe._load_yfinance()
    else:
        from backtest.data_store import DataStore
        from backtest.runner import load_data_from_store
        store = DataStore(args.db) if args.db else DataStore()
        data = load_data_from_store(store, args.symbols.split(",") if args.symbols else None)
        store.close()
        if not data:
            print("ERROR: no market data. Use --db / --yf / --synthetic.")
            sys.exit(1)

    trades = BacktestEngine(BacktestConfig()).run_multi(data)
    sa = [t for t in trades if probe._is_setup_a(t.setup)]
    rows = [f for f in (_structural_features(t, data) for t in sa) if f]
    print(f"\nSetup-A trades: {len(sa)}  |  with structure: {len(rows)}")
    if len(rows) < 8:
        print("Too few rows for even directional read.")
        sys.exit(0)

    win, los = _wl(rows)
    print(f"Winners: {len(win)}  Losers: {len(los)}  "
          f"(baseline win {100*len(win)/len(rows):.1f}%, "
          f"expR {_mean([r['r'] for r in rows]):+.3f})\n")

    print("WINNERS vs LOSERS — structural traits (mean):")
    print(f"  {'feature':<24} {'winners':>9} {'losers':>9} {'separation':>11}")
    print("  " + "-" * 55)
    seps = []
    for k in NUMERIC:
        wv, lv = [r[k] for r in win], [r[k] for r in los]
        mw, ml = _mean(wv), _mean(lv)
        allv = [r[k] for r in rows]
        sd = st.pstdev(allv) if len(allv) > 1 else 0.0
        sep = round((mw - ml) / sd, 2) if sd > 0 else 0.0
        seps.append((k, sep, mw, ml))
        print(f"  {k:<24} {mw:>9.3f} {ml:>9.3f} {sep:>11.2f}")
    for k in BINARY:
        pw = round(100 * _mean([r[k] for r in win]), 1)
        pl = round(100 * _mean([r[k] for r in los]), 1)
        print(f"  {k:<24} {pw:>8.1f}% {pl:>8.1f}% {'(present %)':>11}")

    seps.sort(key=lambda x: -abs(x[1]))
    print("\n  Strongest separators (|winner-loser| / stdev):")
    for k, s, mw, ml in seps[:5]:
        tilt = "winners higher" if s > 0 else "losers higher"
        print(f"    {k:<24} sep={s:+.2f}  ({tilt})")

    # ---- SETUP MATURITY: the core hypothesis test --------------------
    mats = sorted(r["maturity_raw"] for r in rows)
    med = mats[len(mats) // 2]
    early = [r for r in rows if r["maturity_raw"] <= med]
    late = [r for r in rows if r["maturity_raw"] > med]
    print("\n" + "=" * 72)
    print("  SETUP MATURITY  (EARLY = quiet/fresh/first-leg; LATE = chase)")
    print("=" * 72)
    for lab, grp in (("EARLY (<=median maturity)", early),
                     ("LATE  (> median maturity)", late)):
        if grp:
            rs = [r["r"] for r in grp]
            wr = 100 * sum(1 for r in grp if r["win"]) / len(grp)
            print(f"  {lab:<28} n={len(grp):>3}  win={wr:>5.1f}%  "
                  f"expR={_mean(rs):+.3f}  totR={sum(rs):+.1f}")
    print("\n  Hypothesis: if EARLY expR >> LATE expR, the edge is in fresh")
    print("  structure BEFORE obvious continuation — exactly the thesis.")
    print("  Small sample => DIRECTIONAL signal to confirm on frozen windows.")

    # session breakdown
    print("\n  By session (expR):")
    for s in ["OPEN(9:15-10)", "MORN(10-12)", "MID(12-13:30)",
              "NOON(13:30-15)", "CLOSE(15+)"]:
        g = [r for r in rows if r["session"] == s]
        if g:
            print(f"    {s:<16} n={len(g):>3}  win={100*sum(r['win'] for r in g)/len(g):>5.1f}%"
                  f"  expR={_mean([r['r'] for r in g]):+.3f}")

    def _by(title, key, order):
        print("\n" + "=" * 72)
        print(f"  {title}")
        print("=" * 72)
        seen = {}
        for cat in order:
            g = [r for r in rows if r.get(key) == cat]
            if not g:
                continue
            rs = [r["r"] for r in g]
            wr = 100 * sum(r["win"] for r in g) / len(g)
            thin = "  (thin)" if len(g) < 8 else ""
            print(f"  {str(cat):<22} n={len(g):>3}  win={wr:>5.1f}%  "
                  f"expR={_mean(rs):+.3f}  totR={sum(rs):+.1f}{thin}")
            seen[cat] = {"n": len(g), "win": round(wr, 1),
                         "expR": _mean(rs), "totR": round(sum(rs), 1)}
        return seen

    # (1) Structure lifecycle — THE headline hypothesis test
    lc = _by("STRUCTURE LIFECYCLE  (fresh accumulation vs late chase)",
             "lifecycle", ["FRESH", "MID", "LATE"])
    print("\n  Thesis: if FRESH expR >> LATE expR, the edge is early")
    print("  institutional behaviour BEFORE expansion is obvious.")

    # (6) Too-late detector — the key concept
    tl = _by("TOO-LATE DETECTOR  (extended + displaced + retail-obvious)",
             "too_late_bucket", ["EARLY", "DEVELOPING", "TOO_LATE"])
    print("  EARLY=0 flags · DEVELOPING=1 · TOO_LATE=2-3. Monotonic decay")
    print("  EARLY->TOO_LATE would confirm 'enters after the move is obvious'.")

    # (2) Expansion quality
    eq = _by("EXPANSION QUALITY  (clean displacement vs messy/choppy)",
             "expansion_clean", [1, 0])
    # (3) Re-entry vs first-touch
    tb = _by("RE-ENTRY vs FIRST-TOUCH (OB zone)", "touch_bucket",
             ["1st", "2nd", "3rd+"])
    # (4) Volatility regime
    vr = _by("VOLATILITY REGIME (ATR now vs trailing)", "vol_regime",
             ["LOW", "NORMAL", "HIGH"])
    # (5) HTF context quality
    hc = _by("HTF CONTEXT QUALITY (trend-clean + fresh-imbalance + liq-target)",
             "htf_ctx_score", [0, 1, 2, 3])

    op = Path(args.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "synthetic": args.synthetic,
        "n": len(rows), "winners": len(win), "losers": len(los),
        "separators": [{"feature": k, "sep": s, "win_mean": mw, "los_mean": ml}
                       for k, s, mw, ml in seps],
        "maturity": {
            "early": {"n": len(early),
                      "expR": _mean([r["r"] for r in early]) if early else 0},
            "late": {"n": len(late),
                     "expR": _mean([r["r"] for r in late]) if late else 0},
        },
        "lifecycle": lc, "too_late": tl, "expansion_quality": eq,
        "touch": tb, "vol_regime": vr, "htf_context": hc,
        "rows": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {op}  (per-trade rows included for Phase B chart replay)")


if __name__ == "__main__":
    main()
