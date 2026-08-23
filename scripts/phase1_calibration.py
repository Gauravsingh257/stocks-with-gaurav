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


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 calibration (read-only)")
    parser.add_argument("--horizon", choices=("SWING", "LONGTERM"), default=None)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--gap-pct", type=float, default=8.0)
    parser.add_argument("--json", dest="json_out", default=None)
    args = parser.parse_args()

    report = {
        "funnel": funnel_comparison(args.horizon, args.days),
        "entry_gap": entry_gap_impact(args.horizon, args.days, args.gap_pct),
        "sector_bypass": sector_bypass_impact(args.horizon, args.days),
    }
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
