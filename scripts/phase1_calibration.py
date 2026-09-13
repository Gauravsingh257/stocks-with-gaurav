"""
scripts/phase1_calibration.py — measure Phase 1 before enabling any of it.

Answers, on the REAL logged corpus rather than on intuition:

  1. What does the strict funnel (L1 AND L2 AND L3) actually remove?
  2. Do the rows it removes have WORSE forward outcomes than the rows it keeps?
     If they do not, the change is not justified and should not ship enabled.
  3. How many currently-final picks are unfillable at the tighter entry gap?
  4. How much does the sector-unknown fix bind, at each PHASE0_REAL_SECTORS state?

Read-only. Touches no production state, enables no flag, writes no table.

    python -m scripts.phase1_calibration
    python -m scripts.phase1_calibration --horizon LONGTERM --json out.json

Requires the `forward_returns` table to be populated for the outcome comparison
(scripts/backfill_forward_returns.py). Without it the funnel counts still print
and the outcome section reports that it has no evidence — which is itself the
correct answer, not a reason to guess.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("phase1_calibration")

_HORIZON_METRIC = {"5d": "fwd_5d_pct", "10d": "fwd_10d_pct", "20d": "fwd_20d_pct"}


def _median(values: list[float]) -> float | None:
    return round(st.median(values), 3) if values else None


def funnel_comparison(horizon: str | None, days: int) -> dict:
    """Legacy vs strict selection over the logged scans, with forward outcomes.

    Legacy: final_selected = layer3_pass
    Strict: final_selected = layer1_pass AND layer2_pass AND layer3_pass

    Both are recomputed from the stored per-layer flags, so this compares the
    two rules on identical inputs rather than on two different scans.
    """
    from dashboard.backend.db.schema import get_connection

    conn = get_connection()
    try:
        # Qualify with the alias: `date` and `symbol` exist on BOTH signals_log
        # and forward_returns, so an unqualified name is ambiguous in the join.
        where = ["s.date >= date('now', ?)"]
        params: list = [f"-{int(days)} day"]
        if horizon:
            where.append("s.horizon = ?")
            params.append(horizon.upper())
        clause = " AND ".join(where)

        rows = conn.execute(
            f"""
            SELECT s.symbol, s.date, s.layer1_pass, s.layer2_pass, s.layer3_pass,
                   s.confidence, s.cmp, s.entry,
                   f.fwd_5d_pct, f.fwd_10d_pct, f.fwd_20d_pct,
                   f.mfe_10d_pct, f.mae_10d_pct, f.days_to_target
              FROM signals_log s
              LEFT JOIN forward_returns f
                     ON REPLACE(f.symbol, 'NSE:', '') = REPLACE(s.symbol, 'NSE:', '')
                    AND f.date = s.date
             WHERE {clause}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"available": False, "reason": "no signals_log rows in window"}

    legacy, strict, dropped = [], [], []
    for r in rows:
        l3 = bool(r["layer3_pass"])
        if not l3:
            continue
        legacy.append(r)
        if bool(r["layer1_pass"]) and bool(r["layer2_pass"]):
            strict.append(r)
        else:
            dropped.append(r)

    def outcomes(bucket: list) -> dict:
        out: dict = {"n": len(bucket)}
        for label, col in _HORIZON_METRIC.items():
            vals = [float(r[col]) for r in bucket if r[col] is not None]
            out[label] = {
                "labelled": len(vals),
                "median_pct": _median(vals),
                "win_rate_pct": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 2) if vals else None,
            }
        touched = [r for r in bucket if r["days_to_target"] is not None]
        have_label = [r for r in bucket if r["fwd_10d_pct"] is not None]
        out["target_touch_pct"] = (
            round(len(touched) / len(have_label) * 100, 2) if have_label else None
        )
        maes = [float(r["mae_10d_pct"]) for r in bucket if r["mae_10d_pct"] is not None]
        out["median_mae_10d_pct"] = _median(maes)
        return out

    return {
        "available": True,
        "window_days": days,
        "horizon": horizon or "ALL",
        "layer3_pass_rows": len(legacy),
        "counts": {
            "legacy_final": len(legacy),
            "strict_final": len(strict),
            "dropped_by_strict": len(dropped),
            "dropped_pct": round(len(dropped) / len(legacy) * 100, 2) if legacy else 0.0,
            "dropped_for_layer1": sum(1 for r in dropped if not r["layer1_pass"]),
            "dropped_for_layer2": sum(1 for r in dropped if not r["layer2_pass"]),
        },
        "outcomes": {
            "legacy_final": outcomes(legacy),
            "strict_final_kept": outcomes(strict),
            "dropped_by_strict": outcomes(dropped),
        },
    }


def entry_gap_impact(horizon: str | None, days: int, gap_pct: float) -> dict:
    """How many currently-final picks are unfillable at the tighter gap."""
    from dashboard.backend.db.schema import get_connection

    conn = get_connection()
    try:
        where = ["date >= date('now', ?)", "final_selected = 1",
                 "entry IS NOT NULL", "cmp IS NOT NULL", "entry > 0"]
        params: list = [f"-{int(days)} day"]
        if horizon:
            where.append("horizon = ?")
            params.append(horizon.upper())
        rows = conn.execute(
            f"SELECT symbol, date, cmp, entry FROM signals_log WHERE {' AND '.join(where)}",
            params,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"available": False, "reason": "no final_selected rows with entry+cmp"}

    gaps = [(float(r["cmp"]) - float(r["entry"])) / float(r["entry"]) * 100.0 for r in rows]
    within = sum(1 for g in gaps if abs(g) <= gap_pct)
    return {
        "available": True,
        "n": len(gaps),
        "gap_threshold_pct": gap_pct,
        "median_gap_pct": _median(gaps),
        "within_threshold": within,
        "within_threshold_pct": round(within / len(gaps) * 100, 2),
        "beyond_15pct": sum(1 for g in gaps if g > 15),
        "beyond_15pct_share": round(sum(1 for g in gaps if g > 15) / len(gaps) * 100, 2),
    }


def sector_bypass_impact(horizon: str | None, days: int) -> dict:
    """How many final picks currently ride the Unknown/Others exemption, with
    real sectors OFF and ON. This is the number that says whether the F-10 fix
    is safe to enable on its own (it is not) or needs PHASE0_REAL_SECTORS too."""
    from dashboard.backend.db.schema import get_connection

    conn = get_connection()
    try:
        where = ["date >= date('now', ?)", "final_selected = 1"]
        params: list = [f"-{int(days)} day"]
        if horizon:
            where.append("horizon = ?")
            params.append(horizon.upper())
        rows = conn.execute(
            f"SELECT DISTINCT symbol FROM signals_log WHERE {' AND '.join(where)}",
            params,
        ).fetchall()
    finally:
        conn.close()

    symbols = [str(r["symbol"]) for r in rows]
    if not symbols:
        return {"available": False, "reason": "no final_selected symbols"}

    result: dict = {"available": True, "n_symbols": len(symbols)}
    for state in ("0", "1"):
        prev = os.environ.get("PHASE0_REAL_SECTORS")
        os.environ["PHASE0_REAL_SECTORS"] = state
        try:
            import services.sector_classification as sc
            sc._memo = None
            from engine.swing import get_sector

            sectors = [get_sector(s) for s in symbols]
        finally:
            if prev is None:
                os.environ.pop("PHASE0_REAL_SECTORS", None)
            else:
                os.environ["PHASE0_REAL_SECTORS"] = prev
        exempt = sum(1 for s in sectors if s in ("Unknown", "Others", ""))
        result[f"real_sectors_{state}"] = {
            "exempt_from_cap": exempt,
            "exempt_pct": round(exempt / len(symbols) * 100, 2),
            "classified": len(symbols) - exempt,
        }
    return result


# Commit 4fe80ae (2026-08-23) stopped the exceptionalism final gate readmitting
# stocks the funnel had rejected at Layer 1. From that date an L1-failed stock is
# never selected however exceptional, so its outcome never reaches the portfolio
# journal. Scan-level forward returns are the only place the effect of that
# exclusion can be measured.
L1_READMISSION_FIX_DATE = "2026-08-23"


def l1_counterfactual(horizon: str | None, days: int, min_labelled: int = 30) -> dict:
    """Stocks Layer 1 now EXCLUDES despite qualifying as exceptional, vs selected.

    Why it exists: matching 69 closed positions to their scan rows (2026-09-13),
    stocks whose row FAILED Layer 1 — the set exceptionalism used to readmit —
    returned +5.44% mean against +0.86% for Layer-1 passes, at identical median
    holding period. But p=0.109, and removing two trades (SCANSTL +51%, BI +42%)
    collapsed it to +1.32%. A hypothesis, not a finding — and one portfolio trades
    can no longer test, because excluded stocks never become trades.

    Rules that keep it honest:
      * One observation per (symbol, date). A stock scanned six times in a day is
        one opportunity; undeduplicated counts would overweight busy days.
      * A stock selected in ANY scan that day counts as selected — so a degraded
        early scan that excluded it cannot put it in the excluded bucket.
      * Outcomes come only from rows WITH a 20-day forward-return label, and each
        bucket is withheld below `min_labelled`. `forward_returns` is filled by a
        manual backfill, so the report states how recent its labels are; newer
        rows are unmeasured, not neutral.
      * Split at the fix date: pre-fix "readmitted and selected" and post-fix
        "excluded" are never pooled.
    Read-only. Decides nothing.
    """
    from dashboard.backend.db.schema import get_connection

    conn = get_connection()
    try:
        params: list = [f"-{int(days)} day"]
        hz = ""
        if horizon:
            hz = "AND s.horizon = ?"
            params.append(horizon.upper())
        rows = conn.execute(
            f"""
            SELECT REPLACE(s.symbol, 'NSE:', '') AS sym, s.date AS date,
                   MAX(s.final_selected) AS selected,
                   MAX(CASE WHEN s.final_selected = 1 AND s.layer1_pass = 0 THEN 1 ELSE 0 END)
                       AS selected_l1_fail,
                   MAX(CASE WHEN s.layer1_pass = 0 AND s.final_selected = 0 AND s.entry IS NOT NULL
                             AND (CASE WHEN json_valid(s.layer_details)
                                       THEN json_extract(s.layer_details, '$.exceptionalism.qualifies')
                                  END) = 1
                            THEN 1 ELSE 0 END) AS excluded_exceptional,
                   MAX(f.fwd_10d_pct) AS fwd_10d, MAX(f.fwd_20d_pct) AS fwd_20d,
                   MAX(f.excess_20d_pct) AS excess_20d
              FROM signals_log s
              LEFT JOIN forward_returns f
                     ON REPLACE(f.symbol, 'NSE:', '') = REPLACE(s.symbol, 'NSE:', '')
                    AND f.date = s.date
             WHERE s.date >= date('now', ?) {hz}
             GROUP BY sym, s.date
            """,
            params,
        ).fetchall()
        labelled_through = conn.execute(
            "SELECT MAX(date) FROM forward_returns WHERE fwd_20d_pct IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    if not rows:
        return {"available": False, "reason": "no signals_log rows in window"}

    pre_pass: list = []
    pre_readmitted: list = []
    post_selected: list = []
    post_excluded: list = []
    for r in rows:
        pre = str(r["date"])[:10] < L1_READMISSION_FIX_DATE
        if r["selected"]:
            if pre:
                (pre_readmitted if r["selected_l1_fail"] else pre_pass).append(r)
            else:
                post_selected.append(r)
        elif r["excluded_exceptional"] and not pre:
            post_excluded.append(r)

    def bucket(group: list) -> dict:
        f20 = [float(r["fwd_20d"]) for r in group if r["fwd_20d"] is not None]
        out: dict = {"n": len(group), "labelled_20d": len(f20)}
        if len(f20) < min_labelled:
            out["outcome"] = None
            out["withheld"] = f"{len(f20)} labelled 20d outcomes (need >= {min_labelled})"
            return out
        f10 = [float(r["fwd_10d"]) for r in group if r["fwd_10d"] is not None]
        ex20 = [float(r["excess_20d"]) for r in group if r["excess_20d"] is not None]
        out["outcome"] = {
            "median_fwd_10d_pct": _median(f10),
            "median_fwd_20d_pct": _median(f20),
            "win_rate_20d_pct": round(sum(1 for v in f20 if v > 0) / len(f20) * 100, 2),
            "median_excess_20d_pct": _median(ex20),
        }
        return out

    return {
        "available": True,
        "window_days": days,
        "horizon": horizon or "ALL",
        "fix_date": L1_READMISSION_FIX_DATE,
        "forward_returns_labelled_through": labelled_through,
        "min_labelled": min_labelled,
        "pre_fix": {
            "selected_l1_pass": bucket(pre_pass),
            "selected_l1_fail_readmitted": bucket(pre_readmitted),
        },
        "post_fix": {
            "selected": bucket(post_selected),
            "excluded_l1_fail_exceptional": bucket(post_excluded),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 calibration (read-only)")
    parser.add_argument("--horizon", choices=("SWING", "LONGTERM"), default=None)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--gap-pct", type=float, default=8.0)
    parser.add_argument("--min-labelled", type=int, default=30)
    parser.add_argument("--json", dest="json_out", default=None)
    args = parser.parse_args()

    report = {
        "funnel": funnel_comparison(args.horizon, args.days),
        "entry_gap": entry_gap_impact(args.horizon, args.days, args.gap_pct),
        "sector_bypass": sector_bypass_impact(args.horizon, args.days),
        "l1_counterfactual": l1_counterfactual(args.horizon, args.days, args.min_labelled),
    }
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
