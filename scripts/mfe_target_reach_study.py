"""
scripts/mfe_target_reach_study.py — do the stocks we select ever reach their target?

READ-ONLY. Answers one question, and it is a SELECTION question, not an exit one.

The books plan ~3R at entry (stop width 5.0% median, RR-at-entry 3.00 median) and
realise ~0.20R. Targets are hit on only ~21% of closed swing trades, 0% in
momentum. Two explanations survive that evidence and they imply opposite work:

  A. the stocks we pick do not move far enough for the target to be reachable
     -> a SELECTION problem; tuning exits cannot fix it
  B. the stocks do move far enough but we exit before collecting it
     -> a CAPTURE problem; selection is not the bottleneck

Average return plus realised RR cannot separate A from B. The maximum favourable
excursion can: MFE is the best the trade was ever worth, so if MFE rarely
reaches the target then the target was never reachable and A holds.

Method — identical conventions to scripts/ceiling_cohort_study.py, whose
machinery this reuses wholesale (services/forward_eval):

- Base = close of the SCAN DATE. Scans publish pre-open, so that close is
  tradable after publication. No look-ahead.
- MFE/return measured over bars strictly AFTER the base bar, to horizon N.
- Only MATURED observations count: if the bar at D+N does not exist the
  observation is dropped and counted as immature, never treated as a zero.
- One observation per (scan_date, symbol); SELECTED wins a tie.
- Symbols absent from the universe snapshot are counted and reported as a
  survivorship caveat, not silently dropped.
- Regime is the benchmark's TRAILING return at the base date (lookback bars
  ending at the base bar), so it is knowable on the day and carries no
  look-ahead.
- Statistical claims use the day-block bootstrap (resampling scan DAYS, because
  overlapping windows make observations dependent). Threshold and target-reach
  RATES are reported descriptively with their day counts — the decision turns on
  the magnitude of the rate, not on a CI around it.

Target-reach uses each candidate's OWN published target, not a generic +15%:
required_move = (target - entry) / entry. That is the question the engine
actually poses on each row.

    python -m scripts.mfe_target_reach_study [--days 90] [--horizon SWING]

Needs REDIS_URL (universe OHLC snapshot) and reads the public signals-log export.
"""

from __future__ import annotations

import argparse
import collections
import os
import statistics as st
import sys

HORIZONS = (5, 10, 20)
MFE_THRESHOLDS = (5.0, 10.0, 15.0)
BOOTSTRAP = 5000
REGIME_LOOKBACK = 20
REGIME_BAND = 2.0          # +/- % trailing benchmark move separating the bands
COHORTS = ("SELECTED", "CEILING_CUT", "PHASE2_CUT")


def _load_redis_url() -> bool:
    """Lift REDIS_URL out of .env if the shell does not already carry it.

    Only that one key, and its value is never printed — the universe snapshot
    lives in Redis and PriceBook cannot be built without it.
    """
    if os.getenv("REDIS_URL"):
        return True
    try:
        with open(".env", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("REDIS_URL="):
                    os.environ["REDIS_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return True
    except OSError:
        pass
    return False


def _regimes(bench: list[dict], lookback: int = REGIME_LOOKBACK) -> dict[str, str]:
    """date -> RISK_ON / NEUTRAL / RISK_OFF from the benchmark's TRAILING move.

    Trailing only. A forward-looking regime label would leak the answer into the
    question.
    """
    out: dict[str, str] = {}
    for i in range(lookback, len(bench)):
        try:
            now = float(bench[i]["close"])
            then = float(bench[i - lookback]["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if not then:
            continue
        chg = (now - then) / then * 100.0
        out[str(bench[i].get("date"))[:10]] = (
            "RISK_ON" if chg >= REGIME_BAND else "RISK_OFF" if chg <= -REGIME_BAND else "NEUTRAL")
    return out


def _bench_forward(bench: list[dict], horizons) -> dict[int, dict[str, float]]:
    """{horizon: {base_date: benchmark forward return %}} — same maturity rule."""
    out: dict[int, dict[str, float]] = {h: {} for h in horizons}
    closes: list[tuple[str, float]] = []
    for b in bench:
        try:
            closes.append((str(b.get("date"))[:10], float(b["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    for i, (d, c0) in enumerate(closes):
        for h in horizons:
            if i + h < len(closes) and c0:
                out[h][d] = (closes[i + h][1] - c0) / c0 * 100.0
    return out


def _rate(hits: int, n: int) -> str:
    return f"{hits * 100.0 / n:5.1f}%" if n else "    —"


def _report_block(label: str, rows: list[tuple], days: int) -> None:
    """rows = [(mfe_pct, required_move_pct|None, excess_ret_pct|None), ...]."""
    n = len(rows)
    if not n:
        return
    mfes = [m for m, _, _ in rows]
    line = f"  {label:22s} n={n:5d} days={days:3d}"
    for t in MFE_THRESHOLDS:
        line += f"  >={t:4.0f}%:{_rate(sum(1 for m in mfes if m >= t), n)}"
    with_t = [(m, r) for m, r, _ in rows if r is not None and r > 0]
    if with_t:
        reached = sum(1 for m, r in with_t if m >= r)
        line += (f"  | own target:{_rate(reached, len(with_t))} of {len(with_t):4d}"
                 f"  req_med={st.median([r for _, r in with_t]):5.1f}%")
    line += f"  mfe_med={st.median(mfes):+6.2f}%"
    exc = [e for _, _, e in rows if e is not None]
    if exc:
        beat = sum(1 for e in exc if e > 0)
        line += f"  excess_med={st.median(exc):+6.2f}% beat_bench:{_rate(beat, len(exc))}"
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="MFE / target-reach study (read-only)")
    ap.add_argument("--api", default=os.getenv("DASHBOARD_URL",
                                               "https://web-production-2781a.up.railway.app"))
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--horizon", default="SWING", choices=["SWING", "LONGTERM", "BOTH"])
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    if not _load_redis_url():
        print("::error::REDIS_URL not set and not found in .env — cannot build the price book")
        return 2

    from scripts.ceiling_cohort_study import _cohort, _export
    from services.forward_eval import (
        Observation,
        PriceBook,
        benchmark_series,
        block_bootstrap,
        dedupe,
        evaluate,
    )

    book = PriceBook.from_universe_snapshot()
    if not book._series:
        print("::error::no universe OHLC snapshot available")
        return 2
    last = max(str(b[-1].get("date"))[:10] for b in book._series.values() if b)
    print(f"price book : {len(book._series)} symbols | last bar {last}")

    bench = benchmark_series("NIFTY 50")
    regimes = _regimes(bench)
    # The benchmark's OWN forward move from each base date. Without it a raw
    # +0.29% over 20 days cannot be read as an edge or as a shortfall.
    bench_fwd = _bench_forward(bench, HORIZONS)
    print(f"benchmark  : {len(bench)} bars | {len(regimes)} dated regime labels "
          f"(trailing {REGIME_LOOKBACK}d, band +/-{REGIME_BAND}%)")

    horizons = ("SWING", "LONGTERM") if args.horizon == "BOTH" else (args.horizon,)

    for hz in horizons:
        print(f"\n{'=' * 100}\nBOOK: {hz}   window: last {args.days} days\n{'=' * 100}")
        raw: list[Observation] = []
        meta: dict[tuple[str, str], dict] = {}
        for kind in ("selected", "discovery_rejected"):
            for row in _export(args.api, kind, args.days, hz):
                coh = _cohort(row)
                if not coh:
                    continue
                key = (str(row["date"])[:10], row["symbol"])
                # SELECTED wins the tie, matching dedupe's precedence, so the
                # entry/target we judge against is the one actually published.
                if key not in meta or coh == "SELECTED":
                    meta[key] = row
                raw.append(Observation(symbol=row["symbol"], date=row["date"], cohort=coh))

        obs = dedupe(raw, precedence=COHORTS)
        print(f"observations: {len(obs)} {dict(collections.Counter(o.cohort for o in obs))}")
        if not obs:
            print("  nothing to measure in this window")
            continue

        priced, cov = evaluate(obs, book, horizons=HORIZONS)
        c = cov.to_dict()
        print(f"coverage    : priced {c['priced']}/{c['total']} ({c['priced_pct']}%) | "
              f"no_series {c['no_series']} (survivorship) | "
              f"no_bar_on_date {c['no_bar_on_date']} | "
              f"immature {c.get('immature_by_horizon')}")

        for n in HORIZONS:
            imm = (c.get("immature_by_horizon") or {}).get(n, 0)
            bf = bench_fwd.get(n, {})
            print(f"\n--- forward {n}d, MATURED ONLY (immature dropped: {imm}) ---")
            groups: dict[str, list[tuple]] = collections.defaultdict(list)
            gdays: dict[str, set[str]] = collections.defaultdict(set)
            byregime: dict[tuple[str, str], list[tuple]] = collections.defaultdict(list)
            rdays: dict[tuple[str, str], set[str]] = collections.defaultdict(set)

            for o, res in priced:
                oc = res.get(n)
                if oc is None or oc.mfe is None:
                    continue
                row = meta.get((str(o.date)[:10], o.symbol)) or {}
                entry, target = row.get("entry"), row.get("target")
                req = None
                try:
                    if entry and target and float(entry) > 0:
                        req = (float(target) - float(entry)) / float(entry) * 100.0
                except (TypeError, ValueError):
                    req = None
                b = bf.get(str(o.date)[:10])
                exc = (oc.ret - b) if (oc.ret is not None and b is not None) else None
                groups[o.cohort].append((oc.mfe, req, exc))
                gdays[o.cohort].add(o.date)
                reg = regimes.get(str(o.date)[:10])
                if reg:
                    byregime[(o.cohort, reg)].append((oc.mfe, req, exc))
                    rdays[(o.cohort, reg)].add(o.date)

            for coh in COHORTS:
                _report_block(coh, groups[coh], len(gdays[coh]))

            if n == 20:
                print("  -- SELECTED by market regime (trailing benchmark) --")
                for reg in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                    _report_block(f"SELECTED/{reg}", byregime[("SELECTED", reg)],
                                  len(rdays[("SELECTED", reg)]))

        print("\n=== day-block bootstrap: does SELECTED run further than what we passed over? ===")
        for other in ("CEILING_CUT", "PHASE2_CUT"):
            for metric in ("mfe", "ret"):
                for n in HORIZONS:
                    r = block_bootstrap(priced, "SELECTED", other, n, metric=metric,
                                        iters=BOOTSTRAP, seed=args.seed, min_days=5)
                    if r.get("withheld"):
                        print(f"  {metric:3s} {n:2d}d vs {other:11s}: "
                              f"only {r['days']} comparable days — withheld")
                        continue
                    lo, hi = r["ci95"]
                    # block_bootstrap reports (cohort_b - cohort_a), i.e.
                    # other - SELECTED. A NEGATIVE diff therefore means SELECTED
                    # is higher. Spelled out because the sign inverts easily.
                    if not r["significant"]:
                        verdict = "no measurable difference"
                    elif r["diff"] < 0:
                        verdict = f"SELECTED higher by {abs(r['diff']):.2f}pp"
                    else:
                        verdict = f"{other} higher by {r['diff']:.2f}pp"
                    print(f"  {metric:3s} {n:2d}d vs {other:11s}: days={r['days']:3d} "
                          f"SELECTED {r['SELECTED_day_mean']:+6.2f}% {other} "
                          f"{r[f'{other}_day_mean']:+6.2f}% "
                          f"95%CI [{lo:+.2f},{hi:+.2f}] -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
