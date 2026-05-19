"""
scripts/live_vs_ideal_replay.py
================================
Concrete answer to: "at best effort, how CLOSE does the real live engine
get to the hindsight-ideal picture — and how far?"

Pure observation. No tuning, no wiring, scorer frozen. It:
  1. Replays real NIFTY 5m through the ACTUAL Setup-A backtest logic
     (point-in-time by construction — the engine never sees the future).
  2. Takes the engine's REAL Setup-A LONG entry on the target day
     (entry / SL / target / time it actually produced). If the harness
     produced none that day, it falls back to the GROUND-TRUTH production
     Telegram signal the user screenshotted (clearly labelled).
  3. Overlays the HINDSIGHT-ideal read (same levels as the Remotion
     SmcNifty0518 picture).
  4. Honestly checks whether the engine's LIMIT entry would even have
     FILLED in real life (did price trade back to it after the signal?).
  5. Prints + draws the quantified gap (points later, time later, extra
     ATR extension, RR, fill?).

Run locally:
    python scripts/live_vs_ideal_replay.py --yf
    python scripts/live_vs_ideal_replay.py --db path\\to\\store.db
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

import smc_detectors as smc
from backtest.engine import BacktestEngine, BacktestConfig
import setup_a_frequency_probe as probe

TARGET_DAY = "2026-05-18"
OUT = Path("backtest_results/live_vs_ideal.png")

# Hindsight-ideal read — identical to the Remotion SmcNifty0518 picture.
IDEAL = {"sweep": 23318, "ob_lo": 23318, "ob_hi": 23360,
         "entry": 23360, "sl": 23300, "t1": 23560, "t2": 23660}

# GROUND TRUTH: the actual production Telegram the user screenshotted on
# 18 May (the real live engine output — no hindsight). Used only as a
# labelled fallback if the backtest harness produced no Setup-A LONG
# that day (the live state-machine and the backtest harness are not
# byte-identical; the screenshot is the realest possible reference).
PROD_SIGNAL = {"time": "2026-05-18T11:37:00", "entry": 23499.65,
               "sl": 23405.24, "target": 23679.48, "src": "production Telegram"}


def _atr(c, i, p=14):
    seg = c[max(0, i - p - 1):i + 1]
    if len(seg) < p + 1:
        return 0.0
    t = [max(seg[k]["high"] - seg[k]["low"],
             abs(seg[k]["high"] - seg[k - 1]["close"]),
             abs(seg[k]["low"] - seg[k - 1]["close"])) for k in range(1, len(seg))]
    return sum(t[-p:]) / p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--yf", action="store_true")
    ap.add_argument("--day", type=str, default=TARGET_DAY)
    args = ap.parse_args()

    if args.yf:
        print(f"REAL yfinance ^NSEI 5m. Target day {args.day}.")
        data = probe._load_yfinance()
        c5 = next(iter(data.values()))["5m"]
        sym = "NSE:NIFTY 50"
    else:
        from backtest.data_store import DataStore
        from backtest.runner import load_data_from_store
        store = DataStore(args.db) if args.db else DataStore()
        raw = load_data_from_store(store, ["NSE:NIFTY 50"])
        store.close()
        if not raw:
            print("ERROR: no data. Use --yf or --db.")
            sys.exit(1)
        sym, c5 = next(iter(raw.items()))
        c5 = c5["5m"]

    day = [x for x in c5 if str(x["date"]).startswith(args.day)]
    if len(day) < 20:
        print(f"ERROR: only {len(day)} 5m candles for {args.day} "
              f"(outside yfinance ~60d window?). Try --db.")
        sys.exit(1)

    # --- the engine's REAL point-in-time Setup-A LONG that day ---------
    trades = BacktestEngine(BacktestConfig()).run_multi({sym: {"5m": c5}})
    eng = [t for t in trades if probe._is_setup_a(t.setup)
           and t.direction == "LONG"
           and str(t.entry_time).startswith(args.day)]
    if eng:
        e = min(eng, key=lambda t: str(t.entry_time))
        live = {"time": str(e.entry_time), "entry": float(e.entry_price),
                "sl": float(e.sl), "target": float(e.target),
                "src": "backtest Setup-A (point-in-time)"}
    else:
        live = dict(PROD_SIGNAL)
        print("  (harness produced no Setup-A LONG that day — using the "
              "ground-truth production Telegram signal you screenshotted.)")

    # --- did the live LIMIT actually FILL in real life? ---------------
    et = probe._parse_dt(live["time"])
    after = [x for x in day if probe._parse_dt(x["date"]) and
             probe._parse_dt(x["date"]) >= et]
    filled = any(x["low"] <= live["entry"] <= x["high"] or x["low"] <= live["entry"]
                 for x in after)
    # outcome if filled: first touch of SL/target after fill
    outcome = "no-fill (price never returned to the LIMIT)"
    if filled:
        risk = live["entry"] - live["sl"]
        for x in after:
            if x["low"] <= live["sl"]:
                outcome = f"FILLED → SL hit (-1.0R)"
                break
            if x["high"] >= live["target"]:
                rr = (live["target"] - live["entry"]) / risk if risk > 0 else 0
                outcome = f"FILLED → target hit (+{rr:.1f}R)"
                break
        else:
            outcome = "FILLED → open at day end (unresolved)"

    # --- quantify the gap --------------------------------------------
    di = probe._parse_dt(live["time"])
    ideal_t = probe._parse_dt("2026-05-18T10:15:00")
    mins_late = (di - ideal_t).total_seconds() / 60.0 if di and ideal_t else float("nan")
    pts_worse = live["entry"] - IDEAL["entry"]
    atr_ref = _atr(day, min(len(day) - 1, 30)) or 1.0
    extra_atr = pts_worse / atr_ref
    rr_live = (live["target"] - live["entry"]) / max(live["entry"] - live["sl"], 1e-9)

    print("\n" + "=" * 68)
    print(f"  LIVE ENGINE (real, no hindsight)  [{live['src']}]")
    print(f"    fired {live['time'][11:16]}  entry {live['entry']:.2f}  "
          f"SL {live['sl']:.2f}  target {live['target']:.2f}  RR {rr_live:.1f}")
    print(f"    real-world: {outcome}")
    print(f"  HINDSIGHT IDEAL (the picture)")
    print(f"    ~10:15  entry {IDEAL['entry']}  SL {IDEAL['sl']}  "
          f"T1 {IDEAL['t1']}  T2 {IDEAL['t2']}  RR ~3.3–5.0")
    print(f"  THE GAP")
    print(f"    entry {pts_worse:+.0f} pts worse (~{extra_atr:.1f} ATR more "
          f"extended) · ~{mins_late:.0f} min later")
    print(f"    + the honest one: the live LIMIT was {outcome.split(' ')[0].lower()}")
    print("=" * 68)

    # --- draw it ------------------------------------------------------
    fig, ax = plt.subplots(figsize=(15, 7.2))
    for i, x in enumerate(day):
        col = "#16a34a" if x["close"] >= x["open"] else "#dc2626"
        ax.plot([i, i], [x["low"], x["high"]], color=col, lw=0.7, zorder=2)
        ax.add_patch(Rectangle((i - 0.3, min(x["open"], x["close"])), 0.6,
                               max(abs(x["close"] - x["open"]), 0.01),
                               color=col, zorder=3))
    n = len(day)

    def _xt(ts):
        t = probe._parse_dt(ts)
        if not t:
            return n // 2
        for i, x in enumerate(day):
            xt = probe._parse_dt(x["date"])
            if xt and xt >= t:
                return i
        return n - 1

    # ideal
    ax.add_patch(Rectangle((0, IDEAL["ob_lo"]), n, IDEAL["ob_hi"] - IDEAL["ob_lo"],
                           color="#16a34a", alpha=0.10, zorder=1))
    for lvl, cstyle, lab in [(IDEAL["entry"], ("#19c3ff", "-"), "IDEAL entry 23,360"),
                             (IDEAL["sl"], ("#dc2626", ":"), "ideal SL 23,300"),
                             (IDEAL["t1"], ("#16a34a", ":"), "T1 23,560"),
                             (IDEAL["t2"], ("#16a34a", ":"), "T2 23,660")]:
        ax.axhline(lvl, color=cstyle[0], ls=cstyle[1], lw=1.0, alpha=0.55, zorder=4)
        ax.annotate(lab, (2, lvl), color=cstyle[0], fontsize=7, va="bottom")
    ax.scatter([_xt("2026-05-18T10:15:00")], [IDEAL["entry"]], color="#19c3ff",
               s=70, marker="*", zorder=6, label="hindsight ideal entry")

    # live engine (real)
    lx = _xt(live["time"])
    ax.axvline(lx, color="#f59e0b", lw=1.2, ls="--", zorder=5)
    ax.scatter([lx], [live["entry"]], color="#f59e0b", s=90, marker="D",
               zorder=7, label="LIVE engine entry (real)")
    ax.axhline(live["entry"], color="#f59e0b", lw=1.0, alpha=0.7, zorder=4)
    ax.axhline(live["sl"], color="#dc2626", lw=0.9, ls="--", alpha=0.6, zorder=4)
    ax.axhline(live["target"], color="#16a34a", lw=0.9, ls="--", alpha=0.6, zorder=4)
    ax.annotate(f"LIVE engine: {live['time'][11:16]} @ {live['entry']:.0f}"
                f"  ({outcome})", (lx + 1, live["entry"]),
                color="#f59e0b", fontsize=9, fontweight="bold", va="bottom")

    ax.set_title(f"NIFTY 50 · 5m · {args.day} — LIVE engine (real, no "
                 f"hindsight)  vs  the hindsight picture", fontsize=12)
    ax.set_xticks([])
    ax.legend(loc="lower right", fontsize=8)
    ax.text(0.5, -0.04,
            f"gap: entry {pts_worse:+.0f} pts worse · ~{extra_atr:.1f} ATR more "
            f"extended · ~{mins_late:.0f} min later · live LIMIT {outcome}",
            transform=ax.transAxes, ha="center", fontsize=9, color="#475569")
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=120)
    plt.close(fig)
    print(f"\nWrote {OUT}  — open it: LIVE (orange ◆) vs IDEAL (blue ★).")
    print("DIRECTIONAL, point-in-time, no tuning, scorer frozen, no wiring.")


if __name__ == "__main__":
    main()
