"""
services/outcome_labeling.py — forward-outcome labels for the signals_log corpus.

WHY THIS EXISTS
---------------
The teardown established that the system has 903,481 scan rows and no idea which
of its conditions predict anything, because no row is joined to what the stock
actually did next. This module turns the corpus into a supervised dataset: for
each scanned (symbol, date) it computes forward return, MFE and MAE at +5/+10/
+20/+60 trading days, plus time-to-target and the benchmark's move over the same
window so excess return is available without a second pass.

TWO DESIGN DECISIONS THAT MATTER
--------------------------------
1. LABELS KEY ON (symbol, date), NOT ON signals_log.id.
   A forward return is a property of the stock and the day — nothing about the
   scan changes it. The corpus has ~903k rows but only ~185k distinct
   (symbol, date) pairs, because every scan of a day and both horizons repeat the
   same symbol. Labelling per row would store the same number ~6x over and force
   a rewrite of the largest table on a volume that has already hit 84%. Labels
   live in their own compact table and are JOINed.

2. A WINDOW THAT DOES NOT EXIST YET IS NULL, NEVER TRUNCATED.
   The corpus spans 2026-04-25 to 2026-08-21 — about 82 trading days. A +60
   trading-day label therefore only exists for signals from roughly the first
   five weeks. Silently computing "+60d" from 30 available bars would fabricate
   an outcome and quietly bias every calibration built on it. `bars_available`
   is recorded so a consumer can always see what a NULL means.

Pure functions only — no I/O, no database, no network. The backfill runner
(scripts/backfill_forward_returns.py) supplies the price series.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Forward windows in TRADING days (bars), not calendar days.
HORIZONS = (5, 10, 20, 60)

# The move the teardown benchmarked the whole system against: roughly 45% of
# random NSE names touch +10% within six weeks. `days_to_target` measures how
# fast a name got there, so "probability x magnitude x TIME" is computable.
TARGET_PCT = 10.0


@dataclass(slots=True)
class OutcomeLabel:
    """Forward outcome for one (symbol, date). Every window is independently
    nullable — a short series produces partial labels, never fabricated ones."""

    symbol: str
    date: str
    base_close: float
    bars_available: int
    fwd_pct: dict[int, float | None]
    mfe_pct: dict[int, float | None]
    mae_pct: dict[int, float | None]
    days_to_target: int | None
    bench_fwd_pct: dict[int, float | None]

    def excess_pct(self, horizon: int) -> float | None:
        """Forward return minus the benchmark's over the same window."""
        own = self.fwd_pct.get(horizon)
        bench = self.bench_fwd_pct.get(horizon)
        if own is None or bench is None:
            return None
        return round(own - bench, 4)

    def to_row(self) -> dict:
        """Flatten to the column layout of the `forward_returns` table."""
        row: dict = {
            "symbol": self.symbol,
            "date": self.date,
            "base_close": round(self.base_close, 4),
            "bars_available": self.bars_available,
            "days_to_target": self.days_to_target,
        }
        for horizon in HORIZONS:
            row[f"fwd_{horizon}d_pct"] = self.fwd_pct.get(horizon)
            row[f"mfe_{horizon}d_pct"] = self.mfe_pct.get(horizon)
            row[f"mae_{horizon}d_pct"] = self.mae_pct.get(horizon)
            row[f"bench_fwd_{horizon}d_pct"] = self.bench_fwd_pct.get(horizon)
            row[f"excess_{horizon}d_pct"] = self.excess_pct(horizon)
        return row

    def to_dict(self) -> dict:
        return asdict(self)


def _pct(value: float, base: float) -> float:
    return round((value - base) / base * 100.0, 4)


def _forward_slice(bars: list[dict], base_date: str) -> tuple[float, list[dict]] | None:
    """(close on/just before base_date, bars strictly after it).

    Uses the last bar at or before `base_date` as the base so a scan dated on a
    holiday still anchors to a real traded price. Returns None when the symbol
    has no bar at or before the scan date (i.e. it had not listed yet).
    """
    prior = [b for b in bars if str(b.get("date", ""))[:10] <= base_date]
    if not prior:
        return None
    base_close = float(prior[-1].get("close") or 0.0)
    if base_close <= 0:
        return None
    forward = [b for b in bars if str(b.get("date", ""))[:10] > base_date]
    return base_close, forward


def _days_to_target(forward: list[dict], base_close: float, target_pct: float) -> int | None:
    """Trading days until the high first touches `target_pct` above base.

    None means "not within the bars we have" — which is not the same as "never",
    so consumers must read it alongside `bars_available`.
    """
    threshold = base_close * (1.0 + target_pct / 100.0)
    for index, bar in enumerate(forward, start=1):
        high = bar.get("high")
        if high is not None and float(high) >= threshold:
            return index
    return None


def compute_label(
    symbol: str,
    base_date: str,
    bars: list[dict],
    *,
    benchmark_bars: list[dict] | None = None,
    horizons: tuple[int, ...] = HORIZONS,
    target_pct: float = TARGET_PCT,
) -> OutcomeLabel | None:
    """Label one (symbol, date) from its daily bar series.

    `bars` must be ascending by date and carry at least close/high/low. Returns
    None only when the symbol has no usable price at or before `base_date`.
    """
    sliced = _forward_slice(bars or [], base_date)
    if sliced is None:
        return None
    base_close, forward = sliced

    fwd: dict[int, float | None] = {}
    mfe: dict[int, float | None] = {}
    mae: dict[int, float | None] = {}
    for horizon in horizons:
        if len(forward) < horizon:
            # The window has not elapsed yet. Recording NULL keeps a partially
            # observed corpus honest instead of scoring a 60-day rule on 30 days.
            fwd[horizon] = mfe[horizon] = mae[horizon] = None
            continue
        window = forward[:horizon]
        fwd[horizon] = _pct(float(window[-1]["close"]), base_close)
        highs = [float(b["high"]) for b in window if b.get("high") is not None]
        lows = [float(b["low"]) for b in window if b.get("low") is not None]
        mfe[horizon] = _pct(max(highs), base_close) if highs else None
        mae[horizon] = _pct(min(lows), base_close) if lows else None

    bench_fwd: dict[int, float | None] = {}
    if benchmark_bars:
        bench_sliced = _forward_slice(benchmark_bars, base_date)
        if bench_sliced is not None:
            bench_base, bench_forward = bench_sliced
            for horizon in horizons:
                bench_fwd[horizon] = (
                    _pct(float(bench_forward[horizon - 1]["close"]), bench_base)
                    if len(bench_forward) >= horizon
                    else None
                )
    if not bench_fwd:
        bench_fwd = {horizon: None for horizon in horizons}

    # Time-to-target is only meaningful inside the longest window we can see.
    observable = min(len(forward), max(horizons))
    return OutcomeLabel(
        symbol=symbol,
        date=base_date,
        base_close=base_close,
        bars_available=len(forward),
        fwd_pct=fwd,
        mfe_pct=mfe,
        mae_pct=mae,
        days_to_target=_days_to_target(forward[:observable], base_close, target_pct),
        bench_fwd_pct=bench_fwd,
    )


def label_coverage(labels: list[OutcomeLabel], horizons: tuple[int, ...] = HORIZONS) -> dict:
    """How many labels each window actually produced.

    The corpus is younger than the 60-day window, so this is the number that
    tells you which horizons a calibration may legitimately use.
    """
    total = len(labels)
    out: dict = {"total": total, "by_horizon": {}}
    for horizon in horizons:
        have = sum(1 for label in labels if label.fwd_pct.get(horizon) is not None)
        out["by_horizon"][f"{horizon}d"] = {
            "labelled": have,
            "coverage_pct": round(have / total * 100, 2) if total else 0.0,
        }
    reached = sum(1 for label in labels if label.days_to_target is not None)
    out["target_touch"] = {
        "target_pct": TARGET_PCT,
        "reached": reached,
        "reached_pct": round(reached / total * 100, 2) if total else 0.0,
    }
    return out
