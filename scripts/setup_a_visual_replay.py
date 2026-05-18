"""
scripts/setup_a_visual_replay.py  —  Phase B, items 1-4 (forensic visual)
=========================================================================
Renders every Setup-A trade as an annotated chart so the structural
discovery ("Setup-A enters LATE — after BOS, after expansion, on 3rd+
taps, in high vol") is visible, not just tabulated.

Per trade it marks: expansion ORIGIN, BOS, OB zone, FVG zone, the entry,
SL/target, how extended price already was (ATR from origin), and the
lifecycle / too-late / regime labels (reused from the Phase-A logic so
labels never diverge). Plus two comparison contact-sheets:
  - WINNERS vs LOSERS
  - calm-regime winners vs HIGH-vol losers   (the regime bleed)

PURE RESEARCH. No scoring, no optimization, no production wiring. Reads
real data the same way as Phase A. Small sample => illustrative.

Run locally (writes PNGs you open):
    python scripts/setup_a_visual_replay.py --yf
    python scripts/setup_a_visual_replay.py --db path\\to\\store.db
"""

from __future__ import annotations

import argparse
import sys
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
import setup_a_structure_research as sar

LOOKBACK = 80
FORWARD = 45
OUTDIR = Path("backtest_results/replay")


def _idx_at(candles, entry_time):
    et = probe._parse_dt(entry_time)
    if et is None:
        return None
    last = None
    for i, c in enumerate(candles):
        cd = probe._parse_dt(c.get("date"))
        if cd is None or cd <= et:
            last = i
        else:
            break
    return last


def _geometry(ltf, direction):
    """Drawing geometry (independent of the metric logic): origin swing,
    BOS bar, OB zone, FVG zone — for annotation only."""
    sh, sl = smc.detect_swing_points(ltf, 3, 3)
    if direction == "LONG":
        origin = sl[-1] if sl else (max(0, len(ltf) - 20), min(c["low"] for c in ltf[-20:]))
    else:
        origin = sh[-1] if sh else (max(0, len(ltf) - 20), max(c["high"] for c in ltf[-20:]))
    bos_idx = None
    if direction == "LONG":
        ph = max((p for i, p in sh if i < origin[0]), default=None)
        if ph is not None:
            for j in range(origin[0] + 1, len(ltf)):
                if ltf[j]["close"] > ph:
                    bos_idx = j
                    break
    else:
        pl = min((p for i, p in sl if i < origin[0]), default=None)
        if pl is not None:
            for j in range(origin[0] + 1, len(ltf)):
                if ltf[j]["close"] < pl:
                    bos_idx = j
                    break
    ob = smc.detect_order_block(ltf, direction)
    fvg = smc.detect_fvg(ltf, direction)
    return origin, bos_idx, ob, fvg


def _draw(ax, window, eidx_local, trade, lab):
    o = [c["open"] for c in window]
    h = [c["high"] for c in window]
    lo = [c["low"] for c in window]
    cl = [c["close"] for c in window]
    for i in range(len(window)):
        col = "#16a34a" if cl[i] >= o[i] else "#dc2626"
        ax.plot([i, i], [lo[i], h[i]], color=col, linewidth=0.6, zorder=2)
        ax.add_patch(Rectangle((i - 0.3, min(o[i], cl[i])), 0.6,
                               max(abs(cl[i] - o[i]), 1e-6),
                               color=col, zorder=3))
    # entry / SL / target
    ax.axvline(eidx_local, color="#2563eb", lw=1.0, ls="--", zorder=4)
    ax.scatter([eidx_local], [trade.entry_price], color="#2563eb",
               s=28, zorder=6)
    ax.axhline(trade.sl, color="#dc2626", lw=0.8, ls=":", zorder=4)
    ax.axhline(trade.target, color="#16a34a", lw=0.8, ls=":", zorder=4)
    # geometry from the point-in-time window (up to entry)
    pit = window[:eidx_local + 1]
    if len(pit) >= 30:
        (oi, opx), bos, ob, fvg = _geometry(pit, trade.direction)
        ax.scatter([oi], [opx], marker="^", color="#a855f7", s=46,
                   zorder=6)
        ax.annotate("origin", (oi, opx), color="#a855f7", fontsize=6,
                    xytext=(0, -10), textcoords="offset points")
        if bos is not None:
            ax.axvline(bos, color="#f59e0b", lw=0.8, zorder=4)
            ax.annotate("BOS", (bos, h[min(bos, len(h) - 1)]),
                        color="#f59e0b", fontsize=6)
        if ob:
            ax.add_patch(Rectangle((0, ob[0]), len(window), ob[1] - ob[0],
                                   color="#16a34a", alpha=0.10, zorder=1))
        if fvg:
            ax.add_patch(Rectangle((0, min(fvg)), len(window),
                                   abs(fvg[1] - fvg[0]),
                                   color="#f59e0b", alpha=0.12, zorder=1))
    ax.set_title(lab, fontsize=7, loc="left")
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=5)
    ax.margins(x=0.01)


def main() -> None:
    ap = argparse.ArgumentParser(description="Setup-A forensic visual replay")
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--symbols", type=str, default=None)
    ap.add_argument("--yf", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--synthetic-days", type=int, default=90)
    ap.add_argument("--max-each", type=int, default=12,
                    help="max individual PNGs per outcome class")
    args = ap.parse_args()

    if args.synthetic:
        print("SYNTHETIC — wiring test only, NOT decision-grade.")
        from backtest.runner import generate_synthetic_candles
        data = {"SYNTHETIC:INDEX": {"5m": generate_synthetic_candles(days=args.synthetic_days)}}
    elif args.yf:
        print("REAL yfinance ^NSEI (~60d 5m). Illustrative, small sample.")
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

    OUTDIR.mkdir(parents=True, exist_ok=True)
    trades = [t for t in BacktestEngine(BacktestConfig()).run_multi(data)
              if probe._is_setup_a(t.setup)]
    print(f"Setup-A trades: {len(trades)}")

    items = []  # (trade, window, eidx_local, feats)
    for t in trades:
        c5 = data.get(t.symbol, {}).get("5m") or []
        ei = _idx_at(c5, t.entry_time)
        if ei is None or ei < 40:
            continue
        w0 = max(0, ei - LOOKBACK)
        w1 = min(len(c5), ei + FORWARD)
        window = c5[w0:w1]
        feats = sar._structural_features(t, data)
        if feats is None or len(window) < 30:
            continue
        items.append((t, window, ei - w0, feats))

    if not items:
        print("No drawable Setup-A trades.")
        sys.exit(0)

    wins = [x for x in items if x[3]["win"] == 1]
    loss = [x for x in items if x[3]["win"] == 0]

    def _lab(t, f):
        return (f"{t.symbol.split(':')[-1]} {t.direction} "
                f"{'WIN' if f['win'] else 'LOSS'} {t.r_multiple:+.1f}R | "
                f"{f['lifecycle']}/{f['too_late_bucket']}/{f['vol_regime']} | "
                f"ext{f['extension_atr']:.1f} taps{f['touch_count']}")

    # individual PNGs (capped)
    made = 0
    for grp, name in ((wins, "win"), (loss, "loss")):
        for k, (t, window, el, f) in enumerate(grp[:args.max_each]):
            fig, ax = plt.subplots(figsize=(7, 3.4))
            _draw(ax, window, el, t, _lab(t, f))
            fig.tight_layout()
            fig.savefig(OUTDIR / f"{name}_{k:02d}.png", dpi=110)
            plt.close(fig)
            made += 1

    # contact sheet: winners vs losers
    def _sheet(groups, fname, title):
        rows = len(groups)
        cols = max((len(g) for _, g in groups), default=1)
        cols = min(cols, 6)
        if cols == 0:
            return
        fig, axes = plt.subplots(rows, cols,
                                 figsize=(cols * 3.0, rows * 2.2),
                                 squeeze=False)
        for r, (rlabel, g) in enumerate(groups):
            for cidx in range(cols):
                ax = axes[r][cidx]
                if cidx < len(g):
                    t, window, el, f = g[cidx]
                    _draw(ax, window, el, t, _lab(t, f))
                else:
                    ax.axis("off")
            axes[r][0].set_ylabel(rlabel, fontsize=8)
        fig.suptitle(title, fontsize=10)
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        fig.savefig(OUTDIR / fname, dpi=110)
        plt.close(fig)
        print(f"  wrote {OUTDIR / fname}")

    _sheet([("WINNERS", wins[:6]), ("LOSERS", loss[:6])],
           "_sheet_win_vs_loss.png", "Setup-A: winners vs losers")

    calm_w = [x for x in wins if x[3]["vol_regime"] in ("LOW", "NORMAL")]
    hv_l = [x for x in loss if x[3]["vol_regime"] == "HIGH"]
    _sheet([("CALM WINS", calm_w[:6]), ("HIGH-VOL LOSSES", hv_l[:6])],
           "_sheet_regime.png",
           "Calm-regime winners vs HIGH-vol losers (the bleed)")

    print(f"\nWrote {made} individual PNGs + 2 contact sheets to {OUTDIR}/")
    print("Open _sheet_win_vs_loss.png and _sheet_regime.png first.")
    print("Look for: entry AFTER BOS, AFTER expansion, far from origin,")
    print("multiple taps — the 'structurally late' thesis, made visible.")


if __name__ == "__main__":
    main()
