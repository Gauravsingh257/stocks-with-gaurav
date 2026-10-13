"""
services/forward_eval.py — the ONE forward-return evaluator.

Computes what a (symbol, date) did next **directly from OHLC bars**, deliberately
NOT from the `forward_returns` table. That table is filled only by the manual
`scripts/backfill_forward_returns.py`, which is scheduled nowhere (confirmed
2026-10-12: no GitHub workflow, no `agents/runner.py` job) and has been stale
since 2026-09-13 — which silently stalled LT-shadow evaluation, exceptionalism
calibration, the Phase-1 counterfactual and stagnation evaluation at once.

The method is the one proven in `scripts/ceiling_cohort_study.py` (PR #220),
generalised so every consumer shares the same bias controls:

* **No look-ahead.** The base price is the close of the observation date itself.
  Scans publish pre-open, so that close is tradable *after* the row exists.
  Nothing from after the horizon is ever read.
* **Maturity gating.** A horizon is reported only when bar `n` after the
  observation genuinely exists. A short series yields `None` — never a guess,
  never a partial window silently treated as complete.
* **Survivorship is reported, not hidden.** Symbols missing from the price book
  (delisted, renamed, dropped from the universe) and dates that were not trading
  days are counted in `Coverage` and surfaced by every caller.
* **Overlapping observations.** Daily scans produce overlapping windows, so
  significance must come from `block_bootstrap`, which resamples *dates*, not
  individual observations.

Consumers: `scripts/ceiling_cohort_study.py`, `scripts/exceptionalism_calibration.py`,
`scripts/lt_shadow_report.py`, `scripts/phase1_calibration.py`,
`scripts/stagnation_eval.py`.
"""

from __future__ import annotations

import random
import statistics as st
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 20)


# ── data ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Observation:
    """One thing that happened on one day, to be judged by what followed."""
    symbol: str
    date: str                       # YYYY-MM-DD, the scan/entry date
    cohort: str = ""                # grouping label (e.g. SELECTED / CEILING_CUT)
    base: float | None = None       # entry price; default = close of `date`
    meta: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Outcome:
    """What the symbol did over one horizon. `None` means not measurable."""
    ret: float | None = None        # close-to-close %, base -> bar n
    mae: float | None = None        # worst intraday excursion vs base, %
    mfe: float | None = None        # best intraday excursion vs base, %


@dataclass
class Coverage:
    """Why observations dropped out. Reported by every consumer, never silent."""
    total: int = 0
    priced: int = 0
    no_series: int = 0              # symbol absent from the book  -> survivorship
    no_bar_on_date: int = 0         # date was not a trading day for this symbol
    market_closed: int = 0          # of those, the market itself was shut (subset)
    immature: dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        pct = round(self.priced * 100.0 / self.total, 1) if self.total else 0.0
        return {
            "total": self.total,
            "priced": self.priced,
            "priced_pct": pct,
            "unpriceable": self.total - self.priced,
            "no_series": self.no_series,
            "no_bar_on_date": self.no_bar_on_date,
            "no_bar_market_closed": self.market_closed,
            "no_bar_symbol_gap": self.no_bar_on_date - self.market_closed,
            "immature_by_horizon": dict(sorted(self.immature.items())),
            "note": "unpriceable rows are reported, not dropped silently (survivorship)",
        }


def _f(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None        # NaN-safe: NaN != NaN
    except (TypeError, ValueError):
        return None


# ── price book ───────────────────────────────────────────────────────────────

class PriceBook:
    """Bars per symbol, ascending by date. The only place prices are read."""

    def __init__(self, series: dict[str, list[dict]] | None = None,
                 fetcher: Callable[[str, str], list[dict]] | None = None):
        self._series = series or {}
        self._fetcher = fetcher
        self._fetch_cache: dict[tuple[str, str], list[dict]] = {}
        self._calendar: set[str] | None = None

    def market_closed_on(self, date: str) -> bool:
        """True when NO symbol in the book traded on `date` — a holiday or weekend.

        Distinguishes a market-wide closure from a per-symbol gap, which is a data
        defect. Always False for a fetcher-backed book, which has no calendar.
        """
        if self._fetcher is not None or not self._series:
            return False
        if self._calendar is None:
            self._calendar = {str(b.get("date"))[:10] for bars in self._series.values() for b in bars}
        return str(date)[:10] not in self._calendar

    # -- constructors ---------------------------------------------------------
    @classmethod
    def from_mapping(cls, mapping: dict[str, list[dict]]) -> PriceBook:
        """{symbol: [{date, open?, high?, low?, close}, ...]} ascending."""
        return cls(series={str(k): list(v or []) for k, v in (mapping or {}).items()})

    @classmethod
    def from_universe_snapshot(cls, symbols: Sequence[str] | None = None,
                               day: str | None = None) -> PriceBook:
        """The production `ohlc:universe` snapshot — needs REDIS_URL."""
        from services.universe_ohlc import load_universe_ohlc

        return cls.from_mapping(load_universe_ohlc(list(symbols) if symbols else None, day=day) or {})

    @classmethod
    def from_fetcher(cls, fetch_bars: Callable[[str, str], list[dict]]) -> PriceBook:
        """Lazy source: `fetch_bars(symbol, from_date)` -> bars ON/AFTER from_date."""
        return cls(fetcher=fetch_bars)

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _bare(symbol: str) -> str:
        return str(symbol or "").replace("NSE:", "").strip()

    def _bars_from(self, symbol: str, date: str) -> list[dict] | None:
        """Bars from `date` onward (bar 0 == `date`), or None if unavailable.

        Returns an empty list when the symbol exists but `date` is not one of its
        trading days — the caller distinguishes that from a missing symbol.
        """
        key = self._bare(symbol)
        if self._fetcher is not None:
            ck = (key, date)
            if ck not in self._fetch_cache:
                try:
                    self._fetch_cache[ck] = list(self._fetcher(symbol, date) or [])
                except Exception:
                    self._fetch_cache[ck] = []
            bars = self._fetch_cache[ck]
            return bars if bars else []
        bars = self._series.get(key) or self._series.get(str(symbol))
        if bars is None:
            return None
        for i, b in enumerate(bars):
            if str(b.get("date"))[:10] == date:
                return bars[i:]
        return []

    def has_symbol(self, symbol: str) -> bool:
        if self._fetcher is not None:
            return True
        key = self._bare(symbol)
        return key in self._series or str(symbol) in self._series

    def outcome(self, symbol: str, date: str, horizon: int,
                base: float | None = None) -> Outcome | None:
        """Outcome over `horizon` trading days. None when not measurable.

        Bar 0 is the observation date. The return uses the CLOSE of bar
        `horizon`; MAE/MFE are the worst/best intraday excursions over bars
        1..horizon. Fewer than `horizon`+1 bars ⇒ immature ⇒ None.
        """
        bars = self._bars_from(symbol, date)
        if not bars or len(bars) <= horizon:
            return None
        b0 = base if base is not None else _f(bars[0].get("close"))
        if b0 is None or b0 <= 0:
            return None
        close_h = _f(bars[horizon].get("close"))
        if close_h is None:
            return None
        window = bars[1:horizon + 1]
        lows = [x for x in (_f(b.get("low")) for b in window) if x is not None]
        highs = [x for x in (_f(b.get("high")) for b in window) if x is not None]
        # Deliberately UNROUNDED: rounding is a presentation concern and the
        # error compounds through means/bootstraps. `cohort_stats` rounds for
        # display; callers that persist may round at their own precision.
        return Outcome(
            ret=(close_h - b0) / b0 * 100.0,
            mae=((min(lows) - b0) / b0 * 100.0) if lows else None,
            mfe=((max(highs) - b0) / b0 * 100.0) if highs else None,
        )


# ── evaluation ───────────────────────────────────────────────────────────────

def dedupe(observations: Iterable[Observation],
           precedence: Sequence[str] = ()) -> list[Observation]:
    """One observation per (date, symbol). `precedence` wins ties, first = highest."""
    rank = {c: i for i, c in enumerate(precedence)}
    best: dict[tuple[str, str], Observation] = {}
    for o in observations:
        k = (o.date, PriceBook._bare(o.symbol))
        cur = best.get(k)
        if cur is None or rank.get(o.cohort, len(rank)) < rank.get(cur.cohort, len(rank)):
            best[k] = o
    return list(best.values())


def evaluate(observations: Iterable[Observation], book: PriceBook, *,
             horizons: Sequence[int] = DEFAULT_HORIZONS,
             ) -> tuple[list[tuple[Observation, dict[int, Outcome]]], Coverage]:
    """Price every observation. Returns (priced rows, coverage)."""
    cov = Coverage(immature={h: 0 for h in horizons})
    out: list[tuple[Observation, dict[int, Outcome]]] = []
    for o in observations:
        cov.total += 1
        if not book.has_symbol(o.symbol):
            cov.no_series += 1
            continue
        bars = book._bars_from(o.symbol, o.date)
        if bars is None:
            cov.no_series += 1
            continue
        if not bars:
            cov.no_bar_on_date += 1
            cov.market_closed += int(book.market_closed_on(o.date))
            continue
        res: dict[int, Outcome] = {}
        for h in horizons:
            oc = book.outcome(o.symbol, o.date, h, base=o.base)
            if oc is None or oc.ret is None:
                cov.immature[h] = cov.immature.get(h, 0) + 1
            else:
                res[h] = oc
        if res:
            cov.priced += 1
            out.append((o, res))
    return out, cov


def cohort_stats(priced: Sequence[tuple[Observation, dict[int, Outcome]]],
                 horizon: int, *, metric: str = "ret") -> dict[str, dict]:
    """Per-cohort distribution for one horizon. Matured observations only."""
    buckets: dict[str, list[float]] = {}
    for obs, res in priced:
        oc = res.get(horizon)
        v = getattr(oc, metric, None) if oc else None
        if v is not None:
            buckets.setdefault(obs.cohort, []).append(v)
    stats: dict[str, dict] = {}
    for cohort, vals in buckets.items():
        s = sorted(vals)
        wins = sum(1 for x in vals if x > 0)
        stats[cohort] = {
            "n": len(vals),
            "win_rate_pct": round(wins * 100.0 / len(vals), 1),
            "mean": round(st.mean(vals), 2),
            "median": round(st.median(vals), 2),
            "p10": round(s[int(len(s) * 0.10)], 2),
            "p90": round(s[int(len(s) * 0.90)], 2),
        }
    return stats


def block_bootstrap(priced: Sequence[tuple[Observation, dict[int, Outcome]]],
                    cohort_a: str, cohort_b: str, horizon: int, *,
                    metric: str = "ret", iters: int = 5000, seed: int = 7,
                    min_days: int = 5) -> dict:
    """CI for (cohort_b − cohort_a), resampling DAYS not observations.

    Daily scans overlap, so observations are not independent; the scan day is the
    natural independent block.
    """
    byday: dict[str, dict[str, list[float]]] = {}
    for obs, res in priced:
        oc = res.get(horizon)
        v = getattr(oc, metric, None) if oc else None
        if v is not None:
            byday.setdefault(obs.date, {}).setdefault(obs.cohort, []).append(v)
    days = [d for d, g in byday.items() if g.get(cohort_a) and g.get(cohort_b)]
    if len(days) < min_days:
        return {"days": len(days), "withheld": True,
                "reason": f"only {len(days)} comparable days (<{min_days})"}
    diffs = [st.mean(byday[d][cohort_b]) - st.mean(byday[d][cohort_a]) for d in days]
    rng = random.Random(seed)
    boots = sorted(st.mean([rng.choice(diffs) for _ in diffs]) for _ in range(iters))
    lo, hi = boots[int(iters * 0.025)], boots[int(iters * 0.975)]
    return {
        "days": len(days),
        "withheld": False,
        f"{cohort_a}_day_mean": round(st.mean([st.mean(byday[d][cohort_a]) for d in days]), 2),
        f"{cohort_b}_day_mean": round(st.mean([st.mean(byday[d][cohort_b]) for d in days]), 2),
        "diff": round(st.mean(diffs), 2),
        "ci95": [round(lo, 2), round(hi, 2)],
        "days_b_higher": sum(1 for d in diffs if d > 0),
        "significant": not (lo <= 0 <= hi),
    }


# ── row enrichment (for consumers that previously read `forward_returns`) ────

def benchmark_series(name: str = "NIFTY 50", day: str | None = None) -> list[dict]:
    """Benchmark bars from the index snapshot, for excess-return calculations."""
    from services.index_ohlc import load_index_ohlc

    return (load_index_ohlc([name], day=day) or {}).get(name) or []


def merge_coverage(reports: Sequence[dict], *, label: str = "queries_enriched") -> dict:
    """Combine several `Coverage.to_dict()`s without losing the breakdown.

    A consumer that enriches more than one query used to sum only total/priced/
    unpriceable, which threw away exactly the survivorship detail the coverage
    exists to report.
    """
    reports = [r for r in reports if r]
    if not reports:
        return {}
    immature: dict[int, int] = {}
    for r in reports:
        for h, n in (r.get("immature_by_horizon") or {}).items():
            immature[int(h)] = immature.get(int(h), 0) + int(n)
    total = sum(r["total"] for r in reports)
    priced = sum(r["priced"] for r in reports)
    return {
        label: len(reports),
        "total": total,
        "priced": priced,
        "priced_pct": round(priced * 100.0 / total, 1) if total else 0.0,
        "unpriceable": sum(r["unpriceable"] for r in reports),
        "no_series": sum(r.get("no_series", 0) for r in reports),
        "no_bar_on_date": sum(r.get("no_bar_on_date", 0) for r in reports),
        "no_bar_market_closed": sum(r.get("no_bar_market_closed", 0) for r in reports),
        "no_bar_symbol_gap": sum(r.get("no_bar_symbol_gap", 0) for r in reports),
        "immature_by_horizon": dict(sorted(immature.items())),
        "note": reports[0].get("note", ""),
    }


def enrich_rows(rows: Sequence[dict], book: PriceBook, *,
                windows: Sequence[int] = (20, 60),
                benchmark: list[dict] | None = None,
                symbol_key: str = "symbol", date_key: str = "date",
                base_key: str | None = None,
                mae_windows: Sequence[int] = ()) -> Coverage:
    """Populate `fwd_{w}d_pct` (and `excess_{w}d_pct`) on each row, in place.

    Drop-in replacement for the `LEFT JOIN forward_returns` those consumers used,
    so their downstream stats code is untouched. A join yields its columns on
    EVERY row (NULL when unmatched), so every key is seeded to `None` up front —
    a consumer indexing `r["mae_10d_pct"]` must not meet a KeyError just because
    that row's horizon has not matured.

    `mae_windows` adds horizons for which only `mae_`/`mfe_` are wanted, without
    adding a forward-return series to the report.

    Rows that cannot be priced get `None` — exactly what an unlabelled join
    produced — and are counted in the returned `Coverage` so the gap is reported
    rather than silently absorbed.
    """
    bench = PriceBook.from_mapping({"__BENCH__": list(benchmark)}) if benchmark else None
    bench_dates = [str(b.get("date"))[:10] for b in (benchmark or [])]
    cov = Coverage(immature={w: 0 for w in windows})
    all_w = list(dict.fromkeys(list(windows) + list(mae_windows)))
    for r in rows:
        cov.total += 1
        sym, date = str(r.get(symbol_key) or ""), str(r.get(date_key) or "")[:10]
        base = _f(r.get(base_key)) if base_key else None
        priced_any = False
        # A LEFT JOIN always produces its columns. Seed them before any branch so
        # no consumer can KeyError on an unmatured horizon.
        r.setdefault("base_close", None)
        for w in all_w:
            r.setdefault(f"fwd_{w}d_pct", None)
            r.setdefault(f"excess_{w}d_pct", None)
            r.setdefault(f"mae_{w}d_pct", None)
            r.setdefault(f"mfe_{w}d_pct", None)
        if not book.has_symbol(sym):
            cov.no_series += 1
        else:
            bars = book._bars_from(sym, date)
            if bars is None:
                cov.no_series += 1
            elif not bars:
                cov.no_bar_on_date += 1
                cov.market_closed += int(book.market_closed_on(date))
            else:
                if r.get("base_close") is None:
                    r["base_close"] = base if base is not None else _f(bars[0].get("close"))
                for w in all_w:
                    oc = book.outcome(sym, date, w, base=base)
                    if oc is None or oc.ret is None:
                        if w in windows:
                            cov.immature[w] = cov.immature.get(w, 0) + 1
                        continue
                    r[f"mae_{w}d_pct"] = round(oc.mae, 4) if oc.mae is not None else None
                    r[f"mfe_{w}d_pct"] = round(oc.mfe, 4) if oc.mfe is not None else None
                    if w not in windows:
                        continue        # mae/mfe only — no forward series wanted
                    r[f"fwd_{w}d_pct"] = round(oc.ret, 4)
                    priced_any = True
                    if bench is not None and date in bench_dates:
                        b = bench.outcome("__BENCH__", date, w)
                        r[f"excess_{w}d_pct"] = (round(oc.ret - b.ret, 4)
                                                 if b is not None and b.ret is not None else None)
        if priced_any:
            cov.priced += 1
    return cov
