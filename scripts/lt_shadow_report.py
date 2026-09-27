"""
scripts/lt_shadow_report.py — read-only comparison of the Long-Term shadow selector vs the live control.

    python -m scripts.lt_shadow_report                 # every run so far
    python -m scripts.lt_shadow_report --since 2026-10-01 --json

Control   = signals_log rows with the run's scan_id and final_selected = 1 (what the
            site serves and the portfolio promotes from).
Shadow    = lt_shadow_picks with selected = 1 (budget-matched to the control).
Outcomes  = forward_returns joined on (symbol, date) — the same labels every other
            calibration reads. Labels exist only after scripts/backfill_forward_returns.py
            has run past the window, so recent runs show n_labelled < n.

Trigger/expiry is APPROXIMATE: forward_returns measures from the scan-date close and
only exposes MAE at 5/10/20/60 trading days. A plan whose entry is below that close
counts as triggered within N days when MAE_N reaches the entry; an entry at or above
it counts as immediately triggered. "Expired" = not triggered within 5 trading days
(~RESEARCH_REC_MAX_AGE_DAYS=7 calendar days). Exact fills need the intraday path.

Common support: `control_shadow_eligible` is the control picks the shadow could
rank (same liquidity/quality/plan filters AND real fundamentals that day). Shadow
vs THAT group separates "ranks differently" from "could not see the stock";
shadow vs the full control mixes the two. Fundamentals coverage is logged per
run because provider throttling moves it day to day (see docs/LT_SHADOW_PHASE4A.md).

Reports numbers only. It never changes a threshold, a weight or a flag — the
calibration decision is made on the full sample, never on a handful of runs.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.backend.db.schema import get_connection  # noqa: E402

WINDOWS = (20, 60)


def _stats(rows: list[dict], w: int) -> dict:
    fwd = [r[f"fwd_{w}d_pct"] for r in rows if r.get(f"fwd_{w}d_pct") is not None]
    exc = [r[f"excess_{w}d_pct"] for r in rows if r.get(f"excess_{w}d_pct") is not None]
    out = {"n": len(rows), "n_labelled": len(fwd), "distinct_symbols": len({r["symbol"] for r in rows})}
    if fwd:
        out |= {"mean_fwd": round(statistics.fmean(fwd), 2), "median_fwd": round(statistics.median(fwd), 2),
                "hit_rate_pos": round(sum(1 for x in fwd if x > 0) / len(fwd) * 100, 1)}
    if exc:
        out |= {"mean_excess": round(statistics.fmean(exc), 2),
                "beat_bench_pct": round(sum(1 for x in exc if x > 0) / len(exc) * 100, 1)}
    return out


def _reached(mae: float | None, need: float) -> bool:
    return mae is not None and (need >= 0 or mae <= need)


def _trigger(rows: list[dict]) -> dict:
    n = trig5 = trig20 = 0
    for r in rows:
        entry, base = r.get("entry"), r.get("base_close")
        if entry is None or base is None or r.get("mae_5d_pct") is None:
            continue
        need = (entry / base - 1) * 100            # <0 when the entry sits below the close
        trig5 += _reached(r.get("mae_5d_pct"), need)
        trig20 += _reached(r.get("mae_20d_pct"), need)
        n += 1
    if not n:
        return {"n_labelled": 0}
    return {"n_labelled": n, "triggered_5d_pct": round(trig5 / n * 100, 1),
            "triggered_20d_pct": round(trig20 / n * 100, 1), "expired_5d_pct": round((n - trig5) / n * 100, 1)}


def _rows(conn, sql: str, args: tuple) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


FR = ("f.base_close, f.fwd_20d_pct, f.excess_20d_pct, f.fwd_60d_pct, f.excess_60d_pct, "
      "f.mae_5d_pct, f.mae_20d_pct")


def build_report(conn, since: str | None = None) -> dict:
    runs = _rows(conn, "SELECT * FROM lt_shadow_runs WHERE date >= ? ORDER BY date, scan_id", (since or "",))
    groups: dict[str, list[dict]] = {"control": [], "shadow": [], "shadow_only": [], "control_only": [], "both": [],
                                     "control_shadow_eligible": [], "control_shadow_ineligible": []}
    per_run, swing_overlap = [], {"shadow": 0, "control": 0}
    latest_diff: dict = {}
    for run in runs:
        sid, d = run["scan_id"], run["date"]
        control = _rows(conn, f"SELECT s.symbol, s.date, s.entry, {FR} FROM signals_log s LEFT JOIN forward_returns f "
                              "ON f.symbol = s.symbol AND f.date = s.date WHERE s.scan_id = ? AND s.final_selected = 1",
                        (sid,))
        shadow = _rows(conn, f"SELECT p.symbol, p.date, p.entry, p.rank, {FR} FROM lt_shadow_picks p "
                             "LEFT JOIN forward_returns f ON f.symbol = p.symbol AND f.date = p.date "
                             "WHERE p.scan_id = ? AND p.selected = 1", (sid,))
        swing = {r["symbol"] for r in _rows(conn, "SELECT DISTINCT symbol FROM signals_log WHERE horizon = 'SWING' "
                                                  "AND date = ? AND final_selected = 1", (d,))}
        ranked = {r["symbol"] for r in _rows(conn, "SELECT symbol FROM lt_shadow_picks WHERE scan_id = ? "
                                                   "AND control_selected = 1 AND rank IS NOT NULL", (sid,))}
        cs, ss_ = {r["symbol"] for r in control}, {r["symbol"] for r in shadow}
        groups["control_shadow_eligible"] += [r for r in control if r["symbol"] in ranked]
        groups["control_shadow_ineligible"] += [r for r in control if r["symbol"] not in ranked]
        groups["control"] += control
        groups["shadow"] += shadow
        groups["shadow_only"] += [r for r in shadow if r["symbol"] not in cs]
        groups["control_only"] += [r for r in control if r["symbol"] not in ss_]
        groups["both"] += [r for r in shadow if r["symbol"] in cs]
        swing_overlap["shadow"] += len(ss_ & swing)
        swing_overlap["control"] += len(cs & swing)
        per_run.append({"scan_id": sid, "date": d, "universe": run["universe"], "eligible": run["eligible"],
                        "control": len(cs), "shadow": len(ss_), "overlap": len(cs & ss_),
                        "jaccard": round(len(cs & ss_) / len(cs | ss_), 3) if cs | ss_ else None,
                        "swing_overlap_shadow": len(ss_ & swing), "swing_overlap_control": len(cs & swing),
                        "swing_final_count": len(swing), "control_shadow_eligible": len(cs & ranked),
                        "coverage": json.loads(run["coverage"]) if run.get("coverage") else None})
        latest_diff = {"scan_id": sid, "shadow_only": sorted(ss_ - cs), "control_only": sorted(cs - ss_)}

    n_ctrl, n_shad = len(groups["control"]), len(groups["shadow"])
    return {
        "runs": len(runs),
        "date_range": [runs[0]["date"], runs[-1]["date"]] if runs else None,
        "selector_versions": sorted({r["selector_version"] for r in runs}),
        "selection_count": {"control_total": n_ctrl, "shadow_total": n_shad,
                            "mean_control_per_run": round(n_ctrl / len(runs), 2) if runs else None,
                            "mean_eligible_per_run": round(statistics.fmean(r["eligible"] for r in runs), 1)
                            if runs else None},
        "swing_lt_overlap": {"shadow_picks_also_swing_final": swing_overlap["shadow"],
                             "control_picks_also_swing_final": swing_overlap["control"],
                             "shadow_pct": round(swing_overlap["shadow"] / n_shad * 100, 1) if n_shad else None,
                             "control_pct": round(swing_overlap["control"] / n_ctrl * 100, 1) if n_ctrl else None},
        "forward_returns": {g: {f"{w}d": _stats(rows, w) for w in WINDOWS} for g, rows in groups.items()},
        "trigger_expiry_approx": {g: _trigger(rows) for g, rows in groups.items()},
        "latest_differences": latest_diff,
        "per_run": per_run,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = get_connection()
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'lt_shadow_runs'").fetchone()
        if not exists:
            print("lt_shadow_runs does not exist yet — the shadow selector has not run (LT_SHADOW_SELECTOR_ENABLED).")
            return 0
        rep = build_report(conn, args.since)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rep, indent=1))
        return 0
    print(json.dumps({k: v for k, v in rep.items() if k != "per_run"}, indent=1))
    print(f"per-run rows: {len(rep['per_run'])} (use --json for all)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
