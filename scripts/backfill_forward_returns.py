"""
scripts/backfill_forward_returns.py — PHASE 0, build the evidence base.

Labels every distinct (symbol, date) in `signals_log` with what the stock
actually did next: forward return, MFE, MAE at +5/+10/+20/+60 trading days,
trading-days-to-+10%, and NIFTY's move over the same window.

    python -m scripts.backfill_forward_returns --dry-run
    python -m scripts.backfill_forward_returns
    python -m scripts.backfill_forward_returns --limit 5000 --source kite

RUN THIS OUT OF BAND. Never from a FastAPI startup handler and never on a
request path — a bulk write against dashboard.db is exactly the data-volume work
that caused the 2026-08-02 healthcheck timeout and 502'd the web service.

Idempotent. Re-running refreshes rows whose windows have since elapsed, so a
NULL +60d becomes a real number once 60 trading days have actually passed. That
is the intended operating mode: schedule it, don't run it once.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill_forward_returns")

BENCHMARK = "^NSEI"
# Longest horizon (60 trading days) plus slack, so the newest scan date in the
# corpus can still see a full forward window once one exists.
_PAD_CALENDAR_DAYS = 130


def _bare(symbol: str) -> str:
    return str(symbol or "").replace("NSE:", "").replace(".NS", "").strip().upper()


def _fetch_yfinance(symbols: list[str], start: str, end: str) -> dict[str, list[dict]]:
    """Bulk daily bars. yfinance batches internally, so this is one wire trip per
    chunk rather than per symbol — the right tool for a one-shot historical
    backfill even though the live scan path is moving to Kite."""
    import warnings

    warnings.filterwarnings("ignore")
    import pandas as pd
    import yfinance as yf

    out: dict[str, list[dict]] = {}
    chunk = int(os.getenv("BACKFILL_YF_CHUNK", "200"))
    for index in range(0, len(symbols), chunk):
        batch = symbols[index : index + chunk]
        tickers = [s + ".NS" for s in batch]
        try:
            frame = yf.download(
                tickers, start=start, end=end, progress=False,
                group_by="ticker", threads=True, auto_adjust=False,
            )
        except Exception as exc:
            log.warning("yfinance batch %d failed: %s", index // chunk, exc)
            continue
        multi = isinstance(frame.columns, pd.MultiIndex)
        for symbol in batch:
            try:
                df = frame[symbol + ".NS"] if multi else frame
                df = df.dropna(subset=["Close"])
            except Exception:
                continue
            if df is None or df.empty:
                continue
            out[symbol] = [
                {
                    "date": str(idx)[:10],
                    "close": float(row["Close"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                }
                for idx, row in df.iterrows()
            ]
        log.info("fetched %d/%d symbols", len(out), len(symbols))
    return out


def _fetch_kite(symbols: list[str], lookback_days: int) -> dict[str, list[dict]]:
    """Same series via the production Kite fetcher (throttled to 3 req/sec).

    Slower than the yfinance bulk path for a one-shot backfill, but it is the
    same data the live scan will use once PHASE0_KITE_OHLC is on, so labels and
    live features come from one provider.
    """
    from services.scanners.data_layer import KiteOHLCFetcher

    fetcher = KiteOHLCFetcher()
    log.info("kite token verified for: %s", fetcher.verify_token())
    log.info("instrument map: %d NSE-EQ symbols", fetcher.load_instruments())
    candles, errors = fetcher.fetch_universe(symbols, "day", lookback_days)
    log.info("kite fetch: %d symbols, %d errors/missing", len(candles), errors)
    return candles


def run(*, limit: int | None, source: str, dry_run: bool, chunk: int) -> dict:
    from dashboard.backend.db.outcomes import (
        distinct_scan_keys,
        forward_return_stats,
        upsert_forward_returns,
    )
    from services.outcome_labeling import compute_label, label_coverage

    started = time.time()
    keys = distinct_scan_keys(limit=limit)
    if not keys:
        log.warning("signals_log has no rows to label — nothing to do")
        return {"labelled": 0, "reason": "empty_corpus"}

    by_symbol: dict[str, list[str]] = defaultdict(list)
    for symbol, scan_date in keys:
        by_symbol[_bare(symbol)].append(scan_date)
    dates = sorted({d for _s, d in keys})
    log.info(
        "corpus: %d distinct (symbol,date) pairs | %d symbols | %s .. %s",
        len(keys), len(by_symbol), dates[0], dates[-1],
    )

    if dry_run:
        return {
            "dry_run": True,
            "distinct_pairs": len(keys),
            "symbols": len(by_symbol),
            "date_range": [dates[0], dates[-1]],
        }

    import datetime as _dt

    start = (_dt.date.fromisoformat(dates[0]) - _dt.timedelta(days=10)).isoformat()
    end = (_dt.date.fromisoformat(dates[-1]) + _dt.timedelta(days=_PAD_CALENDAR_DAYS)).isoformat()
    today = _dt.date.today().isoformat()
    end = min(end, today)

    symbols = sorted(by_symbol)
    log.info("fetching prices for %d symbols (%s → %s) via %s", len(symbols), start, end, source)
    if source == "kite":
        span = (_dt.date.fromisoformat(end) - _dt.date.fromisoformat(start)).days
        prices = _fetch_kite(symbols, span)
        bench = _fetch_kite(["NIFTY 50"], span).get("NIFTY 50", [])
    else:
        prices = _fetch_yfinance(symbols, start, end)
        bench = _fetch_yfinance([BENCHMARK.replace("^", "")], start, end).get(
            BENCHMARK.replace("^", ""), []
        )
        if not bench:
            import warnings

            warnings.filterwarnings("ignore")
            import yfinance as yf

            df = yf.download(BENCHMARK, start=start, end=end, progress=False, auto_adjust=False)
            close = df["Close"]
            close = close.iloc[:, 0] if hasattr(close, "columns") else close
            bench = [
                {"date": str(i)[:10], "close": float(v), "high": float(v), "low": float(v)}
                for i, v in close.items()
            ]

    if not bench:
        log.warning("no benchmark series — excess-return columns will be NULL")

    labels = []
    no_price = 0
    for symbol, scan_dates in by_symbol.items():
        bars = prices.get(symbol)
        if not bars:
            no_price += len(scan_dates)
            continue
        for scan_date in scan_dates:
            label = compute_label(symbol, scan_date, bars, benchmark_bars=bench)
            if label is not None:
                labels.append(label)

    coverage = label_coverage(labels)
    log.info("labelled %d pairs (%d had no price series) | coverage=%s",
             len(labels), no_price, json.dumps(coverage["by_horizon"]))

    written = upsert_forward_returns([label.to_row() for label in labels], chunk=chunk)
    stats = forward_return_stats()
    return {
        "distinct_pairs": len(keys),
        "labelled": len(labels),
        "written": written,
        "no_price_series": no_price,
        "coverage": coverage,
        "table_stats": stats,
        "elapsed_sec": round(time.time() - started, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill forward-return labels for signals_log")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap distinct (symbol,date) pairs — newest first")
    parser.add_argument("--source", choices=("yfinance", "kite"), default="yfinance")
    parser.add_argument("--dry-run", action="store_true",
                        help="report corpus shape without fetching or writing")
    parser.add_argument("--chunk", type=int, default=2000, help="rows per committed batch")
    args = parser.parse_args()

    result = run(limit=args.limit, source=args.source, dry_run=args.dry_run, chunk=args.chunk)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
