#!/usr/bin/env python3
"""
scripts/shadow_diff_phase2.py
─────────────────────────────
Shadow-diff for the Phase-2 signal-quality flags, BEFORE enabling them in
production. Compares the served Final-Trade-Ideas book under three configs and
reports how the flags change actionability / reward quality, across the last
5 / 20 / 60 trading-day windows.

    A "current"    — ENTRY_ANCHOR_MAX_GAP_PCT=30, STRUCTURAL_TARGET_CAP=off  (live today)
    B "anchor10"   — ENTRY_ANCHOR_MAX_GAP_PCT=10, STRUCTURAL_TARGET_CAP=off
    C "targetcap"  — ENTRY_ANCHOR_MAX_GAP_PCT=30, STRUCTURAL_TARGET_CAP=on

It does NOT enable anything and never writes back — read-only analysis only.

────────────────────────────────────────────────────────────────────────────
HOW THE CONFIGS ARE RECONSTRUCTED  (be honest about exactness)
────────────────────────────────────────────────────────────────────────────
Each historical final setup is stored with entry/stop/target/cmp plus, in
`layer_details.smc`, the order-block / liquidity zone it was built from.

  • Config B (entry anchoring) is reconstructed FAITHFULLY from stored fields:
    the engine pulls a far limit entry toward CMP when
    abs(entry-close)/close > threshold. ATR is not persisted, so it is
    inverted from the stored risk via the engine's own relation
    base_risk = max(ATR*1.3, entry*0.03)  ->  ATR ≈ max((entry-stop)/1.3, entry*0.03).
    The structural stop (OB/recent-low floor) is unchanged by re-anchoring, so
    it is preserved and the 3R target recomputed — mirroring _scored_smc_levels.

  • Config C (structural target cap) uses --mode proxy: APPROXIMATE (no live
    data needed). It caps the far target at the stored 52-week-high
    (reconstructed from discovery.pct_below_52w_high) as the overhead-resistance
    stand-in for the engine's swing-pivot cap. Stocks already at their 52w high
    get no cap (matches the engine: breakouts keep full target). The EXACT cap
    (real ±3 swing-pivot finder) is not reconstructable from stored fields — get
    it by running a shadow scan with STRUCTURAL_TARGET_CAP=1 and diffing the
    final book against a default run. (--mode engine is reserved and currently
    falls back to proxy with a printed note.)

So Config B numbers are faithful; Config C numbers are exact in --mode engine
and a documented proxy otherwise. The proxy is conservative for the decision
("does capping materially cut reward?") but verify with --mode engine before
flipping STRUCTURAL_TARGET_CAP on.

────────────────────────────────────────────────────────────────────────────
USAGE
────────────────────────────────────────────────────────────────────────────
  # Real multi-window run (execute where signals_log is reachable, e.g. the
  # Railway backend container or any box with the dashboard DB configured):
  python scripts/shadow_diff_phase2.py --source db --windows 5,20,60

  # Local validation / single-scan preview against a saved discovery payload:
  python scripts/shadow_diff_phase2.py --source json --path _disc.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass

# Allow running as `python scripts/shadow_diff_phase2.py` from the repo root:
# put the project root on sys.path so the lazy `data` / `services` imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── band thresholds (mirror validation_engine._entry_reachability) ────────────
ACTIONABLE_PCT = 5.0     # |gap| <= 5%  -> actionable
EXTENDED_PCT = 10.0      # gap > 10%    -> extended (per the report spec)
SWING_TARGET_MULT = 3.0
TARGET_FLOOR_R = 1.5


@dataclass
class Setup:
    symbol: str
    entry: float
    stop: float
    target: float
    cmp: float
    confidence: float
    is_at_52w_high: bool
    pct_below_52w_high: float | None
    date: str = ""
    # Exact structural cap target, filled by resolve_exact_caps() in engine mode.
    # None -> Config C uses the 52w-high proxy.
    target_cap: float | None = None


# ── reconstruction of each config's (entry, stop, target) ─────────────────────

def _atr_estimate(s: Setup) -> float:
    """ATR inverted from stored risk via base_risk = max(ATR*1.3, entry*0.03)."""
    risk = max(s.entry - s.stop, 0.0)
    return max(risk / 1.3, s.entry * 0.03)


def cfg_current(s: Setup) -> tuple[float, float, float]:
    return s.entry, s.stop, s.target


def cfg_anchor(s: Setup, max_gap_pct: float) -> tuple[float, float, float]:
    """Config B: re-anchor a far entry toward CMP at the tighter gap threshold."""
    if s.cmp <= 0 or s.entry <= 0:
        return s.entry, s.stop, s.target
    gap = abs(s.entry - s.cmp) / s.cmp * 100.0
    if gap <= max_gap_pct:
        return s.entry, s.stop, s.target
    atr = _atr_estimate(s)
    entry_b = round(s.cmp - 0.5 * atr, 2)
    stop_b = s.stop                       # structural floor unchanged
    if stop_b >= entry_b:                 # preserve original risk if inverted
        stop_b = round(entry_b - (s.entry - s.stop), 2)
    risk_b = entry_b - stop_b
    if risk_b <= 0:
        return s.entry, s.stop, s.target  # un-reconstructable -> leave as-is
    target_b = round(entry_b + risk_b * SWING_TARGET_MULT, 2)
    return entry_b, stop_b, target_b


def cfg_targetcap(s: Setup) -> tuple[float, float, float]:
    """Config C. Uses the EXACT pivot cap (s.target_cap, set by
    resolve_exact_caps in engine mode) when available; otherwise falls back to
    the documented 52w-high proxy."""
    if s.target_cap is not None:
        return s.entry, s.stop, s.target_cap
    if s.is_at_52w_high or not s.pct_below_52w_high or s.pct_below_52w_high <= 0:
        return s.entry, s.stop, s.target           # no overhead -> full target
    high52 = s.cmp / (1.0 - s.pct_below_52w_high / 100.0)
    floor = s.entry + (s.entry - s.stop) * TARGET_FLOOR_R
    capped = max(floor, min(s.target, high52))
    return s.entry, s.stop, round(capped, 2)


def resolve_exact_caps(setups: list[Setup], source: str = "yfinance", days: int = 420) -> int:
    """EXACT Config-C: fetch real candle history per symbol and run the
    PRODUCTION pivot finder (validation_engine._nearest_resistance_above) to cap
    the far target at the nearest swing-high resistance above entry, floored at
    1.5R. Mutates each Setup.target_cap in place. Returns the number resolved.

    This is the real cap the engine applies with STRUCTURAL_TARGET_CAP=1 — no
    proxy. Requires network/data access (the same yfinance path the scan uses)."""
    from data.ingestion import DataIngestion
    from services.research_levels import df_to_candles
    from services.validation_engine import _nearest_resistance_above

    ing = DataIngestion(source=source)
    cache: dict[str, float | None] = {}
    resolved = 0
    for s in setups:
        if s.symbol not in cache:
            try:
                df = ing.fetch_historical(s.symbol, interval="day", days=days)
                # Drop NaN highs (e.g. an incomplete trailing bar) so the pivot
                # finder sees only clean, completed candles.
                candles = [c for c in (df_to_candles(df) or [])
                           if c.get("high") is not None and float(c["high"]) == float(c["high"])]
                cache[s.symbol] = _nearest_resistance_above(candles, s.entry) if candles else None
            except Exception as exc:  # noqa: BLE001 — best-effort per symbol
                print(f"  [warn] candle fetch failed for {s.symbol}: {exc}", file=sys.stderr)
                cache[s.symbol] = None
        res = cache[s.symbol]
        if res is None:
            s.target_cap = s.target            # no overhead pivot -> full target (matches engine)
        else:
            floor = s.entry + (s.entry - s.stop) * TARGET_FLOOR_R
            s.target_cap = round(max(floor, min(s.target, res)), 2)
        resolved += 1
    return resolved


# ── metrics over a list of (entry, stop, target, cmp) ─────────────────────────

def _gap_from_entry(entry: float, cmp: float) -> float:
    return (cmp - entry) / entry * 100.0 if entry else 0.0


def _remaining_rr(entry: float, stop: float, target: float, cmp: float) -> float | None:
    risk = cmp - stop
    if risk <= 0:
        return None
    return (target - cmp) / risk


def metrics(levels: list[tuple[float, float, float, float]]) -> dict:
    n = len(levels)
    if n == 0:
        return {"count": 0}
    gaps = [_gap_from_entry(e, c) for e, _, _, c in levels]
    rrs = [r for e, s, t, c in levels if (r := _remaining_rr(e, s, t, c)) is not None]
    actionable = sum(1 for g in gaps if abs(g) <= ACTIONABLE_PCT)
    extended = sum(1 for g in gaps if g > EXTENDED_PCT)
    # quality score 0-100: rewards actionability + real reward left, penalises
    # extended/exhausted ideas. Purely a relative yardstick across configs.
    pos_rr = [max(r, 0.0) for r in rrs] or [0.0]
    quality = round(
        50.0 * (actionable / n)
        + 30.0 * min(statistics.median(pos_rr) / SWING_TARGET_MULT, 1.0)
        + 20.0 * (1.0 - extended / n),
        1,
    )
    return {
        "count": n,
        "actionable": actionable,
        "actionable_pct": round(actionable / n * 100, 1),
        "avg_dist_from_entry_pct": round(statistics.mean(gaps), 1),
        "median_dist_from_entry_pct": round(statistics.median(gaps), 1),
        "avg_remaining_rr": round(statistics.mean(rrs), 2) if rrs else None,
        "median_remaining_rr": round(statistics.median(rrs), 2) if rrs else None,
        "extended_gt10pct": extended,
        "extended_pct": round(extended / n * 100, 1),
        "quality_score": quality,
    }


def levels_for(setups: list[Setup], config: str, max_gap_pct: float) -> list[tuple]:
    out = []
    for s in setups:
        if config == "current":
            e, st, t = cfg_current(s)
        elif config == "anchor":
            e, st, t = cfg_anchor(s, max_gap_pct)
        elif config == "targetcap":
            e, st, t = cfg_targetcap(s)
        else:
            raise ValueError(config)
        out.append((e, st, t, s.cmp))
    return out


# ── data sources ──────────────────────────────────────────────────────────────

def _row_to_setup(r: dict) -> Setup | None:
    entry = r.get("entry") if r.get("entry") is not None else r.get("entry_price")
    stop = r.get("stop_loss")
    target = r.get("target")
    if target is None:
        target = r.get("target_1") or r.get("target_2")
        if target is None and isinstance(r.get("targets"), list) and r["targets"]:
            target = r["targets"][-1]
    cmp = r.get("cmp") if r.get("cmp") is not None else r.get("scan_cmp")
    if entry is None or stop is None or target is None or cmp is None:
        return None
    ld = r.get("layer_details") or {}
    if isinstance(ld, str):
        try:
            ld = json.loads(ld)
        except Exception:
            ld = {}
    disc = ld.get("discovery", {}) if isinstance(ld, dict) else {}
    try:
        return Setup(
            symbol=str(r.get("symbol")),
            entry=float(entry), stop=float(stop), target=float(target), cmp=float(cmp),
            confidence=float(r.get("confidence") or r.get("confidence_score") or 0),
            is_at_52w_high=bool(disc.get("is_at_52w_high")),
            pct_below_52w_high=(float(disc["pct_below_52w_high"])
                                if disc.get("pct_below_52w_high") is not None else None),
            date=str(r.get("date") or ""),
        )
    except (TypeError, ValueError):
        return None


def load_from_json(path: str) -> dict[str, list[Setup]]:
    payload = json.load(open(path, encoding="utf-8"))
    rows = payload.get("final_trades") or payload.get("items") or []
    setups = [s for r in rows if (s := _row_to_setup(r))]
    label = f"latest-scan ({payload.get('generated_at', '?')}, n={len(setups)})"
    return {label: setups}


def load_from_db(windows: list[int]) -> dict[str, list[Setup]]:
    """Pull SWING final_selected setups grouped into trailing trading-day windows."""
    from dashboard.backend.db import get_db_connection  # type: ignore

    conn = get_db_connection()
    conn.row_factory = __import__("sqlite3").Row if hasattr(conn, "row_factory") else None
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT date FROM signals_log WHERE horizon='SWING' "
                "AND final_selected=1 ORDER BY date DESC")
    all_dates = [row[0] for row in cur.fetchall()]
    out: dict[str, list[Setup]] = {}
    for w in windows:
        dates = set(all_dates[:w])
        if not dates:
            out[f"last {w} trading days (0 found)"] = []
            continue
        placeholders = ",".join("?" * len(dates))
        cur.execute(
            f"SELECT symbol, entry, stop_loss, target, cmp, confidence, layer_details, date "
            f"FROM signals_log WHERE horizon='SWING' AND final_selected=1 "
            f"AND date IN ({placeholders})", tuple(dates))
        cols = [d[0] for d in cur.description]
        setups = [s for row in cur.fetchall() if (s := _row_to_setup(dict(zip(cols, row))))]
        out[f"last {w} trading days ({len(dates)} scans, n={len(setups)})"] = setups
    return out


# ── reporting ─────────────────────────────────────────────────────────────────

CONFIGS = [("current", "A current (gap30,cap off)"),
           ("anchor",  "B anchor10 (gap10)"),
           ("targetcap", "C targetcap (on)")]


def print_window(label: str, setups: list[Setup], anchor_gap: float, mode: str):
    print(f"\n{'='*78}\nWINDOW: {label}\n{'='*78}")
    if not setups:
        print("  (no setups in this window)")
        return
    rows = {name: metrics(levels_for(setups, key, anchor_gap)) for key, name in CONFIGS}
    keys = ["count", "actionable", "actionable_pct", "avg_dist_from_entry_pct",
            "median_remaining_rr", "avg_remaining_rr", "extended_gt10pct",
            "extended_pct", "quality_score"]
    nicelabel = {
        "count": "Signal count", "actionable": "Actionable (<=5%)",
        "actionable_pct": "Actionable %", "avg_dist_from_entry_pct": "Avg dist from entry %",
        "median_remaining_rr": "Median rem. RR (CMP)", "avg_remaining_rr": "Avg rem. RR (CMP)",
        "extended_gt10pct": "Extended >10%", "extended_pct": "Extended %",
        "quality_score": "Quality score /100",
    }
    w0 = 24
    header = f"{'Metric':<{w0}}" + "".join(f"{name:>22}" for _, name in CONFIGS)
    print(header)
    print("-" * len(header))
    for k in keys:
        line = f"{nicelabel[k]:<{w0}}"
        for _, name in CONFIGS:
            v = rows[name].get(k)
            line += f"{(v if v is not None else '-')!s:>22}"
        print(line)
    cap_note = "EXACT (candles)" if mode == "engine" else "PROXY (52w-high stand-in)"
    print(f"  [Config C target cap = {cap_note}]")


def _classify(gap: float) -> str:
    if abs(gap) <= ACTIONABLE_PCT:
        return "actionable"
    if gap > EXTENDED_PCT:
        return "extended"
    return "waiting"


def print_per_symbol(setups: list[Setup], anchor_gap: float):
    """Action #5: per-recommendation diff — which CURRENT live ideas change
    under Anchor10, and how (entry, distance, remaining RR, state flip)."""
    print(f"\n{'='*112}\nPER-RECOMMENDATION CHANGE UNDER ANCHOR10 (gap={anchor_gap}%)  "
          f"— current live final book\n{'='*112}")
    hdr = (f"{'Symbol':<13}{'CMP':>9}"
           f"{'A entry':>9}{'A dist%':>8}{'A remRR':>8}{'A state':>11}   "
           f"{'B entry':>9}{'B dist%':>8}{'B remRR':>8}{'B state':>11}  {'change':>10}")
    print(hdr); print("-" * len(hdr))
    flips = 0
    for s in sorted(setups, key=lambda x: _gap_from_entry(x.entry, x.cmp), reverse=True):
        ea, sa, ta = cfg_current(s)
        eb, sb, tb = cfg_anchor(s, anchor_gap)
        ga, gb = _gap_from_entry(ea, s.cmp), _gap_from_entry(eb, s.cmp)
        ra, rb = _remaining_rr(ea, sa, ta, s.cmp), _remaining_rr(eb, sb, tb, s.cmp)
        ca, cb = _classify(ga), _classify(gb)
        changed = abs(eb - ea) > 0.01
        flip = "" if ca == cb else f"{ca[:4]}->{cb[:4]}"
        if changed:
            flips += 1
        tag = flip or ("re-anchored" if changed else "unchanged")
        print(f"{s.symbol.replace('NSE:',''):<13}{s.cmp:>9.2f}"
              f"{ea:>9.2f}{ga:>8.1f}{(round(ra,2) if ra is not None else '-')!s:>8}{ca:>11}   "
              f"{eb:>9.2f}{gb:>8.1f}{(round(rb,2) if rb is not None else '-')!s:>8}{cb:>11}  {tag:>10}")
    print("-" * len(hdr))
    print(f"  {flips}/{len(setups)} recommendations re-anchored under Anchor10; "
          f"the rest already within {anchor_gap}% of entry (unchanged).")


def main():
    ap = argparse.ArgumentParser(description="Phase-2 flag shadow-diff (read-only)")
    ap.add_argument("--source", choices=["db", "json"], default="db")
    ap.add_argument("--path", default="_disc.json", help="discovery JSON (when --source json)")
    ap.add_argument("--windows", default="5,20,60", help="trailing trading-day windows (db)")
    ap.add_argument("--anchor-gap", type=float, default=10.0,
                    help="Config-B ENTRY_ANCHOR_MAX_GAP_PCT to simulate (default 10)")
    ap.add_argument("--mode", choices=["proxy", "engine"], default="proxy",
                    help="Config-C target-cap: proxy (52w-high) or engine (EXACT pivot cap, fetches candles)")
    ap.add_argument("--per-symbol", action="store_true",
                    help="also print the per-recommendation Anchor10 change report (action #5)")
    args = ap.parse_args()

    if args.source == "json":
        windows = load_from_json(args.path)
    else:
        wins = [int(x) for x in args.windows.split(",") if x.strip()]
        windows = load_from_db(wins)

    if args.mode == "engine":
        all_setups = [s for setups in windows.values() for s in setups]
        print(f"Resolving EXACT Config-C caps via candle history for "
              f"{len({s.symbol for s in all_setups})} symbols ...", file=sys.stderr)
        resolve_exact_caps(all_setups)

    print(f"\nPhase-2 Shadow-Diff  |  Config-B anchor gap = {args.anchor_gap}%  "
          f"|  Config-C cap = {'EXACT (production pivots)' if args.mode == 'engine' else 'PROXY (52w-high)'}  "
          f"|  bands: actionable<=5%, extended>10%")
    for label, setups in windows.items():
        print_window(label, setups, args.anchor_gap, args.mode)
        if args.per_symbol:
            print_per_symbol(setups, args.anchor_gap)
    print("\nReminder: Config A reflects production today. B/C are simulations — "
          "review before setting ENTRY_ANCHOR_MAX_GAP_PCT / STRUCTURAL_TARGET_CAP.")


if __name__ == "__main__":
    main()
