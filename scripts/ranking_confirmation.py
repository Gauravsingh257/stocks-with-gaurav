"""
scripts/ranking_confirmation.py — the FROZEN analysis for
docs/validation/PREREG-ranking-confirmation-2026-11.md.

READ-ONLY. Research only: nothing imports this, no production path touches it, no
flag/threshold/weight/target is read or written.

This script is the pre-registration expressed as code. Every formula, cohort
rule, exclusion, basket rule, bootstrap, seed, coverage gate and decision
threshold below is fixed by that document. **Do not edit it to make a result
pass.** If it must change, the pre-registration gets a timestamped amendment
committed BEFORE the run, and the amendment says what changed and why.

## Reproducing a run

Locally against a database copy:

    python -m scripts.ranking_confirmation --db /path/to/dashboard.db

On the service, where the database, the Redis universe snapshot and
services/forward_eval already live (`railway ssh` eats the first stdout line and
mangles nested quotes, so base64 the file and decode it remotely):

    B64=$(base64 -w0 < scripts/ranking_confirmation.py)
    railway ssh --service web -- sh -lc \\
      "echo SAC; echo $B64 | base64 -d > /tmp/rc.py; cd /app && python3 /tmp/rc.py"

The run is deterministic: given the same database and OHLC snapshot it prints
byte-identical output. Seed 7 throughout; the random comparator derives a
per-day seed by SHA-256 so it does not depend on PYTHONHASHSEED or on the order
days happen to be iterated.

## Four ambiguities in the pre-registration, resolved here and reported

The pre-registration did not specify these. Each is resolved the neutral,
deterministic way, recorded in the output, and flagged for Gaurav. None of them
can favour B over A: they apply identically to every comparator.

  R1  Basket tie-break at the Nth place. Resolved: ascending symbol. Applies to
      A, B, C and D alike.
  R2  `score_breakdown.composite` must also be present, not just F1-F3 — basket
      A is undefined without it. A row missing it is excluded from ALL baskets,
      so every comparator always chooses from an identical pool.
  R3  Scan-date maturity, and the two coverage DENOMINATORS, both fixed here.
      Maturity is judged at market level: a scan date is matured when the
      BENCHMARK has a bar 20 trading days after it.
        * ELIGIBLE POOL for a date = rows surviving dedup (R5) and the R2
          feature/incumbent requirement. The >=30-candidates-per-day gate is
          applied to THIS pool, not to the raw row count, because a basket is
          drawn from the pool and nothing else.
        * Price coverage = (eligible-pool rows with a priceable D+20 outcome) /
          (eligible-pool rows on matured days that passed the >=30 gate).
          A symbol missing its own D+20 bar is counted unpriceable in that
          denominator, never silently dropped. Gate: >=75%.
        * Feature coverage is measured against the DEDUPLICATED PRE-EXCLUSION
          candidate set, i.e. before R2 removes rows. Measuring it inside the
          eligible pool would be 100% by construction and would say nothing.
          Gate: each of F1-F3 on >=80% of that day's deduplicated candidates.
  R4  Benchmark span. "the identical bar span" is implemented as NIFTY 50's own
      20-trading-day forward return from the same base date. A symbol with
      trading gaps can therefore span slightly different calendar days from the
      index; using the index's own 20 bars is the only definition that does not
      vary per symbol.
  R5  Duplicate (scan_date, symbol). The scanner runs several times a day, so
      55% of rows in the window are repeats of a symbol already seen that day
      (57,200 of 103,400 on 2026-10-19). The pre-registration says one
      observation per (scan_date, symbol) but not WHICH. Resolved: the row with
      the LOWEST `id` — the earliest persisted scan for that date and symbol —
      because scans publish pre-open and the earliest is closest to the moment
      the ranking was actually published. Enforced with an explicit
      `ORDER BY date, symbol, id`: relying on unordered SELECT iteration made
      the choice depend on SQLite's physical row order, which is not a
      guarantee and would have broken reproducibility.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import random
import sqlite3
import statistics as st
import sys

# ── FROZEN by the pre-registration. Do not tune. ────────────────────────────
F1, F2, F3 = ("discovery.momentum_50d_pct",
              "discovery.breakout_score",
              "discovery.discovery_score")
CANDIDATE_FEATURES = (F1, F2, F3)
INCUMBENT = "score_breakdown.composite"

HORIZON = 20                     # trading days
BASKET_N = 20                    # = PORTFOLIO_MAX_SWING / EXCEPTIONALISM_SOFT_CEILING
ROBUSTNESS_N = (10, 40)          # reported only; no decision weight
COST_ROUND_TRIP_PCT = 0.30       # identical on every basket, so it cancels from differences
BOOTSTRAP_ITERS = 5000
SEED = 7

WINDOW_AFTER = "2026-09-17"      # STRICTLY after; zero overlap with prior work
MIN_MATURED_DAYS = 15
MIN_CANDIDATES_PER_DAY = 30
MIN_PRICED_PCT = 75.0
MIN_FEATURE_COVERAGE_PCT = 80.0
MIN_EFFECT_PP = 1.00

VERDICT_PASS, VERDICT_FAIL, VERDICT_INSUFFICIENT = "PASS", "FAIL", "INSUFFICIENT EVIDENCE"
VOID = "VOID (control failed — harness invalid)"


# ── primitives ───────────────────────────────────────────────────────────────

def average_ranks(values: list[float]) -> list[float]:
    """Ascending ranks, 1-based, **average ranks for ties** (pre-reg §1)."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def percentiles(values: list[float]) -> list[float]:
    """(rank - 1) / (n - 1), so the worst is 0.0 and the best 1.0 (pre-reg §1)."""
    n = len(values)
    if n <= 1:
        return [0.0] * n
    return [(r - 1.0) / (n - 1.0) for r in average_ranks(values)]


def cand_scores(day_rows: list[dict]) -> list[float]:
    """CAND = unweighted mean of the three within-day feature percentiles.

    Equal weights on purpose: nothing is estimated from data, so nothing can
    overfit."""
    cols = [percentiles([r[f] for r in day_rows]) for f in CANDIDATE_FEATURES]
    return [sum(c[i] for c in cols) / len(cols) for i in range(len(day_rows))]


def day_seed(date: str) -> int:
    """Deterministic per-day seed for comparator C.

    SHA-256 of "SEED|date" rather than hash(): Python salts string hashing per
    process, which would make the control irreproducible."""
    return int(hashlib.sha256(f"{SEED}|{date}".encode()).hexdigest()[:8], 16)


def select(day_rows: list[dict], score: list[float], n: int) -> list[dict]:
    """Top n by score, descending. **R1: ties broken by ascending symbol**, so
    selection is deterministic and the rule is identical for every comparator."""
    pairs = sorted(zip(score, day_rows, strict=True),
                   key=lambda t: (-t[0], t[1]["symbol"]))
    return [r for _, r in pairs[:n]]


def basket_return(rows: list[dict]) -> float | None:
    """Equal-weighted mean benchmark-relative return, minus the round-trip cost.

    The cost is identical for every comparator, so it cancels exactly from each
    paired difference and cannot move the primary result (pre-reg §3)."""
    vals = [r["excess"] for r in rows if r.get("excess") is not None]
    if not vals:
        return None
    return st.mean(vals) - COST_ROUND_TRIP_PCT


def partial_spearman(y: list[float], x: list[float], z: list[float]) -> float | None:
    """Rank correlation of y with x, controlling for z (pre-reg §8, secondary 1).

    rho(y,x|z) = (r_yx - r_yz*r_xz) / sqrt((1-r_yz^2)(1-r_xz^2))
    """
    if len(y) < 5:
        return None

    def pear(a, b):
        ma, mb = st.mean(a), st.mean(b)
        num = sum((p - ma) * (q - mb) for p, q in zip(a, b, strict=True))
        da = sum((p - ma) ** 2 for p in a) ** 0.5
        db = sum((q - mb) ** 2 for q in b) ** 0.5
        return (num / (da * db)) if da and db else None

    ry, rx, rz = average_ranks(y), average_ranks(x), average_ranks(z)
    r_yx, r_yz, r_xz = pear(ry, rx), pear(ry, rz), pear(rx, rz)
    if None in (r_yx, r_yz, r_xz):
        return None
    den = ((1 - r_yz ** 2) * (1 - r_xz ** 2)) ** 0.5
    return (r_yx - r_yz * r_xz) / den if den else None


def paired_bootstrap(diffs_by_day: dict[str, float]) -> dict:
    """95% CI for the mean paired per-day difference, resampling DAYS.

    Mirrors services.forward_eval.block_bootstrap's construction exactly —
    `mean(choice(diffs) for _ in diffs)` per iteration — so this study's
    uncertainty is computed the same way as every other study in the repo.
    Days are the independent unit because overlapping 20-day windows make
    individual observations dependent."""
    days = sorted(diffs_by_day)
    diffs = [diffs_by_day[d] for d in days]
    if len(diffs) < MIN_MATURED_DAYS:
        return {"days": len(diffs), "withheld": True}
    rng = random.Random(SEED)
    boots = sorted(st.mean([rng.choice(diffs) for _ in diffs])
                   for _ in range(BOOTSTRAP_ITERS))
    lo, hi = boots[int(BOOTSTRAP_ITERS * 0.025)], boots[int(BOOTSTRAP_ITERS * 0.975)]
    return {"days": len(diffs), "withheld": False, "mean": st.mean(diffs),
            "ci95": (lo, hi), "excludes_zero": not (lo <= 0 <= hi),
            "days_positive": sum(1 for v in diffs if v > 0)}


def holm(pvals: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm-Bonferroni across the pre-registered secondaries (pre-reg §8)."""
    order = sorted(pvals, key=lambda k: pvals[k])
    m = len(order)
    out, still = {}, True
    for i, k in enumerate(order):
        thresh = alpha / (m - i)
        still = still and pvals[k] <= thresh
        out[k] = still
    return out


def decide(cov_ok: bool, control_ok: bool, primary: dict) -> str:
    """The pre-registered decision table (pre-reg §9), and nothing else."""
    if not cov_ok:
        return VERDICT_INSUFFICIENT
    if not control_ok:
        return VOID
    if primary.get("withheld"):
        return VERDICT_INSUFFICIENT
    if primary["mean"] >= MIN_EFFECT_PP and primary["excludes_zero"]:
        return VERDICT_PASS
    return VERDICT_FAIL


# ── data assembly ────────────────────────────────────────────────────────────

def leaves(d, p="", out=None):
    out = {} if out is None else out
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict):
                leaves(v, p + k + ".", out)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                f = float(v)
                if f == f:
                    out[p + k] = f
    return out


def benchmark_forward(bench: list[dict], h: int) -> dict[str, float]:
    """R4: the index's OWN h-bar forward return, keyed by base date."""
    closes = []
    for b in bench:
        try:
            closes.append((str(b.get("date"))[:10], float(b["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    return {d: (closes[i + h][1] - c0) / c0 * 100.0
            for i, (d, c0) in enumerate(closes) if i + h < len(closes) and c0}


def build_panel(db: str, bench_fwd: dict[str, float],
                horizon_label: str) -> tuple[dict[str, list[dict]], dict]:
    """One matured, fully-covered row per (scan_date, symbol).

    Returns (panel, coverage). Nothing is dropped silently; every exclusion is
    counted and reported.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    # R5: ORDER BY is load-bearing, not cosmetic. With duplicates present, an
    # unordered SELECT would let SQLite's physical row order decide which scan
    # of the day is kept, so the "deterministic" run would not be.
    raw = con.execute(
        "SELECT date, symbol, layer_details FROM signals_log "
        "WHERE date > ? AND horizon = ? AND layer_details IS NOT NULL "
        "AND length(layer_details) > 40 "
        "ORDER BY date, symbol, id",
        (WINDOW_AFTER, horizon_label)).fetchall()
    con.close()

    cov = collections.Counter()
    seen: set[tuple[str, str]] = set()
    panel: dict[str, list[dict]] = collections.defaultdict(list)
    feat_present: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    per_day_total: collections.Counter = collections.Counter()

    for date, symbol, ld in raw:
        d = str(date)[:10]
        cov["rows"] += 1
        key = (d, symbol)
        if key in seen:
            cov["duplicate_symbol_day"] += 1
            continue
        seen.add(key)
        per_day_total[d] += 1
        try:
            lv = leaves(json.loads(ld))
        except Exception:
            cov["unparseable_layer_details"] += 1
            continue
        for f in CANDIDATE_FEATURES:
            if f in lv:
                feat_present[f][d] += 1
        # R2: the incumbent score is required too, else basket A is undefined.
        if any(f not in lv for f in CANDIDATE_FEATURES) or INCUMBENT not in lv:
            cov["excluded_missing_feature"] += 1
            continue
        panel[d].append({"symbol": symbol, "date": d,
                         **{f: lv[f] for f in CANDIDATE_FEATURES},
                         INCUMBENT: lv[INCUMBENT]})
    cov["candidates"] = sum(len(v) for v in panel.values())
    return panel, {"counts": cov, "feat_present": feat_present, "per_day_total": per_day_total,
                   "bench_fwd": bench_fwd}


def main() -> int:
    ap = argparse.ArgumentParser(description="FROZEN pre-registered ranking confirmation")
    ap.add_argument("--db", default=os.path.join(os.getenv("DATA_DIR", "."), "dashboard.db"))
    ap.add_argument("--horizon-label", default="SWING")
    args = ap.parse_args()

    sys.path.insert(0, "/app")
    sys.path.insert(0, os.getcwd())
    from services.forward_eval import PriceBook, benchmark_series

    print("FROZEN RUN — docs/validation/PREREG-ranking-confirmation-2026-11.md")
    print(f"  window: scan dates STRICTLY after {WINDOW_AFTER} | horizon {HORIZON}d | "
          f"N={BASKET_N} | seed {SEED} | cost {COST_ROUND_TRIP_PCT}% round trip")
    print("  resolutions: R1 tie=symbol asc | R2 composite required | "
          "R3 maturity at benchmark | R4 benchmark span = index's own 20 bars | "
          "R5 duplicate (date,symbol) -> lowest id")

    pb = PriceBook.from_universe_snapshot()
    if not pb._series:
        print(f"\nVERDICT: {VERDICT_INSUFFICIENT} — no universe OHLC snapshot")
        return 0
    last_bar = max(str(b[-1].get("date"))[:10] for b in pb._series.values() if b)
    bench_fwd = benchmark_forward(benchmark_series("NIFTY 50"), HORIZON)
    print(f"  price book: {len(pb._series)} symbols, last bar {last_bar} | "
          f"benchmark: {len(bench_fwd)} matured base dates")

    panel, meta = build_panel(args.db, bench_fwd, args.horizon_label)
    c = meta["counts"]

    # R3: matured scan dates = those where the BENCHMARK has a D+20 bar.
    matured = sorted(d for d in panel if d in bench_fwd)
    print(f"\n  rows {c['rows']} | duplicate (date,symbol) {c['duplicate_symbol_day']} | "
          f"excluded missing feature/incumbent {c['excluded_missing_feature']}")
    print(f"  scan dates seen {len(panel)} | MATURED (benchmark D+{HORIZON} exists) "
          f"{len(matured)} / required {MIN_MATURED_DAYS}")

    if len(matured) < MIN_MATURED_DAYS:
        print(f"\n  GATE 1 FAILED: {len(matured)} matured scan dates < {MIN_MATURED_DAYS}.")
        print("  No basket statistic is computed. The confirmation cohort is NOT inspected.")
        print(f"\nVERDICT: {VERDICT_INSUFFICIENT}")
        print("  Re-run when the gate is met. The ~2026-11-13 estimate is provisional and "
              "depends on the NSE calendar and snapshot availability.")
        return 0

    # From here the gate is met; price, score, select and compare.
    # R3: the >=30 gate applies to the ELIGIBLE POOL (post-dedup, post-R2),
    # because that is what a basket is drawn from. The price-coverage
    # denominator is the eligible-pool rows on days that passed that gate.
    rows_by_day: dict[str, list[dict]] = {}
    priced_n = eligible_n = 0
    dropped_small_pool = 0
    for d in matured:
        pool = panel[d]
        if len(pool) < MIN_CANDIDATES_PER_DAY:
            dropped_small_pool += 1
            continue
        keep = []
        for r in pool:
            eligible_n += 1
            oc = pb.outcome(r["symbol"], d, HORIZON)
            if oc is None or oc.ret is None:
                continue
            priced_n += 1
            keep.append(dict(r, excess=oc.ret - bench_fwd[d]))
        if len(keep) >= MIN_CANDIDATES_PER_DAY:
            rows_by_day[d] = keep
        else:
            dropped_small_pool += 1

    priced_pct = 100.0 * priced_n / eligible_n if eligible_n else 0.0
    feat_cov_ok = True
    for f in CANDIDATE_FEATURES:
        pct = [100.0 * meta["feat_present"][f][d] / meta["per_day_total"][d]
               for d in rows_by_day if meta["per_day_total"][d]]
        worst = min(pct) if pct else 0.0
        ok = worst >= MIN_FEATURE_COVERAGE_PCT
        feat_cov_ok = feat_cov_ok and ok
        print(f"  coverage {f:34s} worst day {worst:5.1f}% "
              f"({'ok' if ok else 'FAILS'} vs {MIN_FEATURE_COVERAGE_PCT}%)")
    print(f"  priceable {priced_n}/{eligible_n} = {priced_pct:.1f}% "
          f"(required {MIN_PRICED_PCT}%; denominator = eligible-pool rows on "
          f"gated matured days) | usable days {len(rows_by_day)} | "
          f"days dropped for a pool < {MIN_CANDIDATES_PER_DAY}: {dropped_small_pool}")

    cov_ok = (len(rows_by_day) >= MIN_MATURED_DAYS and priced_pct >= MIN_PRICED_PCT
              and feat_cov_ok)

    baskets: dict[int, dict[str, dict[str, float]]] = {}
    overlap_by_day: dict[str, float] = {}
    partials: list[float] = []
    for n in (BASKET_N, *ROBUSTNESS_N):
        per: dict[str, dict[str, float]] = {"A": {}, "B": {}, "C": {}, "D": {}}
        for d, rows in rows_by_day.items():
            cand = cand_scores(rows)
            rnd = random.Random(day_seed(d))
            scores = {"A": [r[INCUMBENT] for r in rows],
                      "B": cand,
                      "C": [rnd.random() for _ in rows],
                      "D": [r[F1] for r in rows]}
            picked = {k: select(rows, v, n) for k, v in scores.items()}
            for k, sel in picked.items():
                br = basket_return(sel)
                if br is not None:
                    per[k][d] = br
            if n == BASKET_N:
                a = {r["symbol"] for r in picked["A"]}
                b = {r["symbol"] for r in picked["B"]}
                overlap_by_day[d] = len(a & b)
                pc = percentiles([r[INCUMBENT] for r in rows])
                ps = partial_spearman([r["excess"] for r in rows], cand, pc)
                if ps is not None:
                    partials.append(ps)
        baskets[n] = per

    def paired(n: int, left: str, right: str) -> dict:
        """mean(right - left) per day — sign convention: positive favours `right`."""
        p = baskets[n]
        days = set(p[left]) & set(p[right])
        return paired_bootstrap({d: p[right][d] - p[left][d] for d in days})

    # ── control FIRST, per pre-reg §2: read before the primary ───────────────
    ctrl = paired(BASKET_N, "A", "C")
    control_ok = (not ctrl.get("withheld")) and (not ctrl["excludes_zero"])
    print(f"\n  CONTROL C-A: mean {ctrl.get('mean', float('nan')):+.2f}pp "
          f"CI {ctrl.get('ci95')} -> "
          f"{'valid (indistinguishable from zero)' if control_ok else 'INVALID — study VOID'}")

    primary = paired(BASKET_N, "A", "B")
    print(f"\n  PRIMARY B-A (N={BASKET_N}): mean {primary.get('mean', float('nan')):+.2f}pp "
          f"CI {primary.get('ci95')} days {primary.get('days')} "
          f"positive {primary.get('days_positive')}")
    for n in ROBUSTNESS_N:
        r = paired(n, "A", "B")
        print(f"    robustness N={n:<3d} mean {r.get('mean', float('nan')):+.2f}pp "
              f"CI {r.get('ci95')}   (no decision weight)")

    bd = paired(BASKET_N, "D", "B")
    print(f"\n  SECONDARY 1 partial Spearman(CAND | composite): "
          f"{st.mean(partials):+.4f} over {len(partials)} days" if partials else
          "\n  SECONDARY 1: insufficient days")
    print(f"  SECONDARY 2 B-D (blend vs 50d momentum alone): "
          f"mean {bd.get('mean', float('nan')):+.2f}pp CI {bd.get('ci95')}")
    print(f"  SECONDARY 3 basket overlap A&B: "
          f"{st.mean(list(overlap_by_day.values())):.1f} of {BASKET_N} names/day")

    verdict = decide(cov_ok, control_ok, primary)
    print(f"\nVERDICT: {verdict}")
    print(f"  rule: PASS needs coverage ok AND control valid AND mean >= "
          f"{MIN_EFFECT_PP:+.2f}pp AND CI excluding zero")
    if verdict == VERDICT_PASS:
        print("  A PASS authorises WRITING a flag-gated reversible proposal with a "
              "disabled-rule control. It authorises no weight, threshold or flag change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
