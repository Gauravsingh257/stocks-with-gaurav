"""
scripts/ceiling_cohort_study.py — does the top-20 ceiling actually pick winners?

READ-ONLY. Compares forward returns of three cohorts from `signals_log`:

  SELECTED     final_selected=1 (the served top-20)
  CEILING_CUT  L1∧L2∧entry, survived the Phase-2 rank budget, lost at the
               exceptionalism gate (`EXCEPTIONALISM_SOFT_CEILING`, default 20)
  PHASE2_CUT   eligible but `below_rank_budget`

Method (2026-10-11 audit):
- Entry = close of the SCAN DATE for every cohort. Scans publish pre-open, so
  that day's close is tradable after publication — no look-ahead.
- Only matured observations count (the bar at D+N must exist).
- One observation per (scan_date, symbol); SELECTED wins a tie.
- Overlapping windows are handled by aggregating per scan day, then
  bootstrapping across days (a block bootstrap), not across raw observations.
- Symbols missing from the current universe snapshot are counted and reported
  as a survivorship caveat rather than silently dropped.

    python -m scripts.ceiling_cohort_study [--days 60] [--horizon SWING]

Needs REDIS_URL (universe OHLC) and reads the public signals-log export.
"""

from __future__ import annotations

import argparse
import collections
import os
import random
import statistics as st
import sys

DEFAULT_API = os.getenv("DASHBOARD_URL", "https://web-production-2781a.up.railway.app")
HORIZONS = (5, 10, 20)
BOOTSTRAP = 5000


def _export(api: str, kind: str, days: int, horizon: str, limit: int = 50000) -> list[dict]:
    import requests

    url = f"{api.rstrip('/')}/api/research/signals-log-export"
    resp = requests.get(
        url,
        params={"days": days, "kind": kind, "limit": limit, "horizon": horizon},
        timeout=300,
        headers={"User-Agent": "ceiling-study/1"},
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


def _cohort(row: dict) -> str | None:
    if row.get("final_selected"):
        return "SELECTED"
    if not (row.get("layer1_pass") and row.get("layer2_pass") and row.get("entry") is not None):
        return None
    return "PHASE2_CUT" if "below_rank_budget" in (row.get("rejection_reason") or []) else "CEILING_CUT"


def _describe(v: list[float]) -> str:
    s = sorted(v)
    wins = sum(1 for x in v if x > 0)
    return (f"n={len(v):5d} win={wins * 100 / len(v):5.1f}% mean={st.mean(v):+6.2f}% "
            f"med={st.median(v):+6.2f}% p10={s[int(len(s) * .1)]:+6.1f} p90={s[int(len(s) * .9)]:+6.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Ceiling cohort forward-return study (read-only)")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--horizon", default="SWING", choices=["SWING", "LONGTERM"])
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    from services.universe_ohlc import load_universe_ohlc

    px: dict[str, tuple[list[str], list[float]]] = {}
    for sym, candles in (load_universe_ohlc(None) or {}).items():
        dates = [str(c.get("date"))[:10] for c in candles if c.get("close") is not None]
        closes = [float(c["close"]) for c in candles if c.get("close") is not None]
        if len(closes) > max(HORIZONS):
            px[sym] = (dates, closes)
    if not px:
        print("::error::no universe OHLC snapshot available")
        return 2
    print(f"price series: {len(px)} symbols | last bar {max(d[-1] for d, _ in px.values())}")

    obs: dict[tuple[str, str], str] = {}
    for kind in ("selected", "discovery_rejected"):
        for row in _export(args.api, kind, args.days, args.horizon):
            coh = _cohort(row)
            if not coh:
                continue
            key = (row["date"], str(row["symbol"]).replace("NSE:", ""))
            if obs.get(key) == "SELECTED":
                continue
            obs[key] = coh
    print(f"deduped observations: {len(obs)} {dict(collections.Counter(obs.values()))}")

    rows: list[dict] = []
    missing = 0
    for (day, sym), coh in obs.items():
        series = px.get(sym)
        if not series:
            missing += 1
            continue
        dates, closes = series
        try:
            i = dates.index(day)
        except ValueError:
            missing += 1
            continue
        rec: dict = {"date": day, "sym": sym, "coh": coh}
        for n in HORIZONS:
            j = i + n
            rec[f"f{n}"] = (closes[j] / closes[i] - 1) * 100 if j < len(closes) and closes[i] else None
        if any(rec[f"f{n}"] is not None for n in HORIZONS):
            rows.append(rec)
        else:
            missing += 1
    pct = missing * 100 // max(1, len(obs))
    print(f"priced observations: {len(rows)} | unpriceable: {missing} ({pct}% — survivorship caveat)")

    for n in HORIZONS:
        print(f"\n--- forward {n}d (matured only) ---")
        grouped: dict[str, list[float]] = collections.defaultdict(list)
        for r in rows:
            if r[f"f{n}"] is not None:
                grouped[r["coh"]].append(r[f"f{n}"])
        for coh in ("SELECTED", "CEILING_CUT", "PHASE2_CUT"):
            if grouped[coh]:
                print(f"  {coh:12s} {_describe(grouped[coh])}")

    print("\n=== DATE-BLOCK analysis (per scan day, bootstrapped across days) ===")
    random.seed(args.seed)
    for n in HORIZONS:
        byday: dict[str, dict[str, list[float]]] = collections.defaultdict(lambda: collections.defaultdict(list))
        for r in rows:
            if r[f"f{n}"] is not None:
                byday[r["date"]][r["coh"]].append(r[f"f{n}"])
        days = [d for d, g in byday.items() if g["SELECTED"] and g["CEILING_CUT"]]
        if len(days) < 5:
            print(f"  {n}d: only {len(days)} comparable days — withheld")
            continue
        diffs = [st.mean(byday[d]["CEILING_CUT"]) - st.mean(byday[d]["SELECTED"]) for d in days]
        boots = sorted(st.mean([random.choice(diffs) for _ in diffs]) for _ in range(BOOTSTRAP))
        lo, hi = boots[int(BOOTSTRAP * .025)], boots[int(BOOTSTRAP * .975)]
        sel = st.mean([st.mean(byday[d]["SELECTED"]) for d in days])
        cut = st.mean([st.mean(byday[d]["CEILING_CUT"]) for d in days])
        verdict = "NO measurable edge" if lo <= 0 <= hi else "SIGNIFICANT"
        print(f"  {n:2d}d: days={len(days)} SELECTED {sel:+.2f}% CEILING_CUT {cut:+.2f}% "
              f"diff {st.mean(diffs):+.2f}pp 95%CI [{lo:+.2f},{hi:+.2f}] "
              f"days CUT won {sum(1 for d in diffs if d > 0)}/{len(days)} -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
