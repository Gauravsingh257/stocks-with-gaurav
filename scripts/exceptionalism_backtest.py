"""
scripts/exceptionalism_backtest.py — validate Exceptionalism on HISTORY (offline).

The live shadow dataset needs weeks to mature (20 trading days of forward returns
per scan). This backtest gives the same verdict in DAYS by reconstructing the
exceptionalism verdict POINT-IN-TIME on past candles and measuring the REAL
forward returns that already happened — across many dates / multiple market
cycles. Read-only; touches no engine code, tables, or flags.

For each (symbol, historical as-of date) it computes — using ONLY data up to that
date — the same inputs the live engine uses (discovery momentum/breakout/volume,
SMC band, RS vs Nifty, RS vs sector, entry state) via the SAME functions, scores
exceptionalism, then measures forward returns from that date's close. Results are
emitted through the SAME calibration report as the live harness
(`scripts.exceptionalism_calibration`), so the metrics are identical and
comparable.

    python -m scripts.exceptionalism_backtest --symbols data/stock_universe_500.json \
        --start 2025-01-01 --end 2026-06-30 --cadence 5 --out reports/exc_backtest.md

Nothing is fabricated: symbols/dates without enough trailing history (<60 bars)
or enough forward bars for a horizon are simply excluded from what they can't
support.
"""

from __future__ import annotations

import argparse
import json
import os
from bisect import bisect_right

import pandas as pd

from scripts.exceptionalism_calibration import (
    HORIZONS, ShadowRow, build_report, render_markdown,
)

MIN_TRAILING_BARS = 60


# ── point-in-time verdict (reuses the real engine functions) ──────────────────

def _bars_from_df(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    frame = df.copy()
    if "date" not in frame.columns and isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.reset_index()
        frame.rename(columns={frame.columns[0]: "date"}, inplace=True)
    frame.columns = [str(c).lower() for c in frame.columns]
    if "close" not in frame.columns:
        return []
    dt = pd.to_datetime(frame["date"], errors="coerce", utc=False)
    try:
        if getattr(dt.dt, "tz", None) is not None:
            dt = dt.dt.tz_convert(None)
    except (AttributeError, TypeError):
        pass
    frame["date"] = dt
    frame = frame.dropna(subset=["date"]).sort_values("date")
    out = []
    for _, r in frame.iterrows():
        out.append({"date": r["date"].date().isoformat(),
                    "open": _f(r.get("open")), "high": _f(r.get("high")),
                    "low": _f(r.get("low")), "close": _f(r.get("close")),
                    "volume": _f(r.get("volume")) or 0.0})
    return out


def _f(v):
    try:
        return float(v) if v is not None and not pd.isna(v) else None
    except (TypeError, ValueError):
        return None


def _slice_df(bars: list[dict], upto_idx: int) -> pd.DataFrame:
    return pd.DataFrame(bars[: upto_idx + 1])


def _pit_health(nifty_bars: list[dict], upto_idx: int, sample_pct_above_200: float | None) -> float | None:
    """A point-in-time Market Health proxy for the backtest: Nifty trend (vs its
    50/200-DMA) blended with sample breadth. Not the full production health model
    (which also uses VIX/rotation), but it captures regime for the threshold."""
    closes = [b["close"] for b in nifty_bars[: upto_idx + 1] if b["close"] is not None]
    if len(closes) < 50:
        return None
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else sum(closes) / len(closes)
    last = closes[-1]
    trend = 0.0
    trend += 50 if last > sma50 else 0
    trend += 50 if last > sma200 else 0           # 0/50/100
    if sample_pct_above_200 is None:
        return round(trend, 1)
    return round(0.5 * trend + 0.5 * sample_pct_above_200, 1)


def backtest(
    hist: dict[str, list[dict]],
    nifty_bars: list[dict],
    *,
    as_of_dates: list[str],
    horizons=HORIZONS,
) -> list[ShadowRow]:
    """Reconstruct verdicts + forward returns across `as_of_dates`. `hist` is
    {symbol: bars(list[dict])}; `nifty_bars` the Nifty series."""
    from services.discovery_engine import _compute_features
    from services.entry_state import classify_entry_state
    from services.exceptionalism import score_and_qualify
    from services.sector_strength import classify_symbol, compute_sector_strength_from_candles
    from services.validation_engine import _scored_smc_levels, _smc_confirmation, _smc_score

    # date → index per symbol (and nifty), for O(log n) point-in-time lookups.
    sym_dates = {s: [b["date"] for b in bars] for s, bars in hist.items()}
    nifty_dates = [b["date"] for b in nifty_bars]

    rows: list[ShadowRow] = []
    for as_of in as_of_dates:
        n_idx = bisect_right(nifty_dates, as_of) - 1
        if n_idx < 50:
            continue
        nifty_slice = [b["close"] for b in nifty_bars[: n_idx + 1] if b["close"] is not None]
        nifty_ret20 = ((nifty_slice[-1] - nifty_slice[-21]) / nifty_slice[-21] * 100.0
                       if len(nifty_slice) >= 21 and nifty_slice[-21] else None)

        # Point-in-time sample slices → constituent sector strength + breadth.
        pit_candles: dict[str, list[dict]] = {}
        above200 = total200 = 0
        for s, bars in hist.items():
            i = bisect_right(sym_dates[s], as_of) - 1
            if i < MIN_TRAILING_BARS:
                continue
            sl = bars[: i + 1]
            pit_candles[s] = sl
            closes = [b["close"] for b in sl if b["close"] is not None]
            if len(closes) >= 200:
                total200 += 1
                if closes[-1] > sum(closes[-200:]) / 200:
                    above200 += 1
        if not pit_candles:
            continue
        pct_above_200 = round(above200 / total200 * 100, 1) if total200 else None
        health = _pit_health(nifty_bars, n_idx, pct_above_200)
        try:
            strength = compute_sector_strength_from_candles(pit_candles, cache=False)
        except Exception:
            strength = {}

        for s, sl in pit_candles.items():
            i = len(sl) - 1
            df = _slice_df(hist[s], i)
            cand = _compute_features(s, df)
            if cand is None:
                continue
            conf = _smc_confirmation(df)
            smc_band = _smc_score(conf, "SWING") / 10.0
            levels = None
            try:
                levels = _scored_smc_levels(s, df, "SWING", conf)
            except Exception:
                levels = None
            entry = stop = None
            targets: list[float] = []
            rr = None
            if levels:
                entry, stop, targets, _setup, _meta = levels
                risk = abs(entry - stop) if (entry and stop) else 0
                rr = (abs(max(targets) - entry) / risk) if (risk > 0 and targets) else None
            cmp = cand.cmp
            es = classify_entry_state(cmp, entry, stop, targets)
            sc = classify_symbol(s, strength)
            verdict = score_and_qualify(
                discovery=cand.to_dict(), smc_band=smc_band, rr=rr,
                nifty_ret20=nifty_ret20, sector_rel20=sc.get("rel_20d_pct"),
                sector_band=sc.get("band"), entry_state=es.get("state"), market_health=health,
            )
            row = ShadowRow(
                scan_id=f"BT-{as_of}", symbol=s, date=as_of, cmp=cmp,
                final_selected=(smc_band >= 5.0),   # legacy engine's "final" gate
                exceptionalism=verdict["exceptionalism"], threshold=verdict["threshold"],
                qualifies=verdict["qualifies"], market_health=health, sector_band=sc.get("band"),
            )
            # Forward returns from the FULL series (the future already happened).
            _attach_forward(row, hist[s], sym_dates[s], as_of, horizons)
            rows.append(row)
    return rows


def _attach_forward(row: ShadowRow, bars: list[dict], dates: list[str], as_of: str, horizons) -> None:
    idx = bisect_right(dates, as_of) - 1
    if idx < 0 or not row.cmp or row.cmp <= 0:
        return
    base = row.cmp
    for h in horizons:
        j = idx + h
        if j >= len(bars):
            continue
        window = bars[idx + 1: j + 1]
        close_h = bars[j].get("close")
        if close_h is None:
            continue
        lows = [b["low"] for b in window if b.get("low") is not None]
        highs = [b["high"] for b in window if b.get("high") is not None]
        row.forward[h] = {
            "ret": round((close_h - base) / base * 100.0, 2),
            "mae": round((min(lows) - base) / base * 100.0, 2) if lows else None,
            "mfe": round((max(highs) - base) / base * 100.0, 2) if highs else None,
        }


# ── data fetch + date selection (production path) ─────────────────────────────

def _fetch_full_history(symbol: str, days: int, *, retries: int = 3) -> list[dict]:
    from data.ingestion import DataIngestion
    src = os.getenv("RESEARCH_DATA_SOURCE", "yfinance")
    last_exc = None
    for _ in range(max(1, retries)):
        try:
            df = DataIngestion(source=src).fetch_historical(symbol, interval="day", days=days)
            bars = _bars_from_df(df)
            if bars:
                return bars
        except Exception as exc:   # intermittent yfinance tz/SSL flakiness → retry
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    return []


def _pick_as_of_dates(nifty_bars: list[dict], start: str, end: str, cadence: int) -> list[str]:
    dates = [b["date"] for b in nifty_bars if start <= b["date"] <= end]
    return dates[::max(1, cadence)]


def _load_symbols(path: str, limit: int) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    syms = data if isinstance(data, list) else (data.get("symbols") or data.get("stocks") or [])
    out = []
    for s in syms:
        s = s if isinstance(s, str) else (s.get("symbol") if isinstance(s, dict) else None)
        if s:
            out.append(s if s.startswith("NSE:") else f"NSE:{s}")
    return out[:limit]


def main() -> None:
    import time

    ap = argparse.ArgumentParser(description="Historical Exceptionalism backtest (offline, read-only).")
    ap.add_argument("--symbols", default=None, help="JSON file of NSE symbols (else use --universe)")
    ap.add_argument("--universe", type=int, default=0,
                    help="pull this many CLEAN equity symbols via load_nse_universe (recommended over a raw file)")
    ap.add_argument("--start", required=True, help="backtest start YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="backtest end YYYY-MM-DD (leave ~1mo before today for 20D fwd)")
    ap.add_argument("--cadence", type=int, default=5, help="as-of every N trading days (default 5 = weekly)")
    ap.add_argument("--limit", type=int, default=300, help="max symbols (keep runtime sane)")
    ap.add_argument("--nifty", default=os.getenv("RESEARCH_NIFTY_SYMBOL", "NSE:NIFTY 50"))
    ap.add_argument("--history-days", type=int, default=750)
    ap.add_argument("--pace", type=float, default=0.0, help="seconds to sleep between fetches (avoids yfinance throttling)")
    ap.add_argument("--cache", default=None, help="pickle path: load fetched history if it exists, else fetch + save (makes re-runs instant)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    if args.universe > 0:
        from services.universe_manager import load_nse_universe
        symbols = [s if s.startswith("NSE:") else f"NSE:{s}"
                   for s in load_nse_universe(target_size=args.universe).symbols][: args.limit]
    elif args.symbols:
        symbols = _load_symbols(args.symbols, args.limit)
    else:
        ap.error("provide --universe N (recommended) or --symbols FILE")

    hist: dict[str, list[dict]] = {}
    nifty_bars: list[dict] = []
    if args.cache and os.path.exists(args.cache):
        import pickle
        print(f"Loading cached history from {args.cache} …")
        with open(args.cache, "rb") as fh:
            blob = pickle.load(fh)
        hist, nifty_bars = blob.get("hist", {}), blob.get("nifty", [])
        print(f"Loaded {len(hist)} symbols from cache.")
    else:
        print(f"Fetching history for {len(symbols)} symbols + Nifty …")
        try:
            nifty_bars = _fetch_full_history(args.nifty, args.history_days)
        except Exception as exc:
            print(f"Could not fetch Nifty history ({exc}); aborting.")
            return
        for i, s in enumerate(symbols, 1):
            try:
                bars = _fetch_full_history(s, args.history_days)
            except Exception:
                bars = []          # one bad symbol never kills the run
            if len(bars) >= MIN_TRAILING_BARS:
                hist[s] = bars
            if args.pace:
                time.sleep(args.pace)
            if i % 50 == 0:
                print(f"  … {i}/{len(symbols)} fetched")
        if args.cache:
            import pickle
            with open(args.cache, "wb") as fh:
                pickle.dump({"hist": hist, "nifty": nifty_bars}, fh)
            print(f"Saved history cache → {args.cache} (re-runs will be instant).")

    as_of = _pick_as_of_dates(nifty_bars, args.start, args.end, args.cadence)
    print(f"Backtesting {len(hist)} symbols across {len(as_of)} as-of dates …")
    rows = backtest(hist, nifty_bars, as_of_dates=as_of)
    report = build_report(rows)
    md = render_markdown(report)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"Wrote backtest report to {args.out} ({len(rows)} observations).")
    else:
        print(md)


if __name__ == "__main__":
    main()
