"""
scripts/backfill_fundamentals_quarterly.py — PHASE 0 real fundamental history.

Populates `fundamentals_quarterly` with per-quarter financials so the Long-Term
book can eventually score GROWTH, ACCELERATION and MARGIN TREND — none of which
a single point-in-time snapshot can express, and 66% of the universe was being
scored on sha256(ticker) instead.

    python -m scripts.backfill_fundamentals_quarterly --limit 50
    python -m scripts.backfill_fundamentals_quarterly --universe 2200

WHAT THIS DELIBERATELY DOES NOT COLLECT
---------------------------------------
Verified against the live APIs before writing this, not assumed:

  cash flow        yfinance `quarterly_cashflow` returns an EMPTY frame for every
                   NSE symbol tested (RELIANCE, KRONOX, MSTCLTD, SOTL). The
                   CFO-vs-PAT quality screen is therefore not buildable from the
                   sources currently wired in.
  promoter %       nseindia.com/api/* returns HTTP 403 without a browser cookie
  FII / DII %      handshake, even from a residential IP — and worse from a
  pledge           datacenter IP. No free alternative is wired in.

Those columns are ABSENT from the table rather than present-and-permanently-null,
so nothing can accidentally score on a field that will never be populated. They
need a paid provider (Screener.in / Tijori / Trendlyne) or BSE XBRL ingestion,
which is a Phase-1 decision and not a Phase-0 stub.

Depth is also honest: yfinance returns ~5-6 quarters of P&L and only ~3 quarters
of balance sheet. That supports YoY growth and QoQ acceleration. It does NOT
support multi-year ROCE persistence, and nothing here should claim it does.

RUN THIS OUT OF BAND — never on a request path or a startup handler.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill_fundamentals_quarterly")

# yfinance row labels vary by company; first match wins.
_REVENUE_KEYS = ("Total Revenue", "Operating Revenue")
_EBITDA_KEYS = ("EBITDA", "Normalized EBITDA")
_EBIT_KEYS = ("EBIT", "Operating Income")
_NET_INCOME_KEYS = ("Net Income", "Net Income From Continuing Operation Net Minority Interest")
_GROSS_KEYS = ("Gross Profit",)
_DEBT_KEYS = ("Total Debt", "Long Term Debt And Capital Lease Obligation")
_EQUITY_KEYS = ("Stockholders Equity", "Total Equity Gross Minority Interest")


def _bare(symbol: str) -> str:
    return str(symbol or "").replace("NSE:", "").replace(".NS", "").strip().upper()


def _pick(frame, keys: tuple[str, ...], column) -> float | None:
    """First matching row label for one period column, as a finite float."""
    if frame is None or getattr(frame, "empty", True):
        return None
    for key in keys:
        if key in frame.index:
            try:
                value = frame.loc[key, column]
            except Exception:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value == value and value not in (float("inf"), float("-inf")):
                return value
    return None


def _margin(numerator: float | None, revenue: float | None) -> float | None:
    """Margin, or None when revenue is not a positive base.

    Some NSE financial-sector filings report a NEGATIVE "Total Revenue" for a
    quarter. Dividing by it yields an arithmetically valid but meaningless
    number — e.g. 21STCENMGM produced a +132.8% "net margin" on revenue of
    -96.2 Cr. A margin needs a positive base to mean anything, so this returns
    None instead of storing a figure that would later be scored on.
    """
    if numerator is None or revenue is None or revenue <= 0:
        return None
    return round(numerator / revenue * 100.0, 4)


def _roce(ebit: float | None, debt: float | None, equity: float | None) -> float | None:
    """EBIT / (debt + equity) — capital employed.

    This is the REAL ROCE. The live scorer currently assigns `roce = roe`, which
    is why the ROCE column in production is ROE wearing a different label.
    """
    if ebit is None:
        return None
    capital = (debt or 0.0) + (equity or 0.0)
    if capital <= 0:
        return None
    return round(ebit / capital * 100.0, 4)


def extract_rows(symbol: str, financials, balance_sheet) -> list[dict]:
    """Map one symbol's yfinance quarterly frames to table rows."""
    if financials is None or getattr(financials, "empty", True):
        return []
    rows: list[dict] = []
    for column in financials.columns:
        period_end = str(column)[:10]
        revenue = _pick(financials, _REVENUE_KEYS, column)
        ebitda = _pick(financials, _EBITDA_KEYS, column)
        ebit = _pick(financials, _EBIT_KEYS, column)
        net_income = _pick(financials, _NET_INCOME_KEYS, column)
        gross = _pick(financials, _GROSS_KEYS, column)

        debt = equity = None
        if balance_sheet is not None and not getattr(balance_sheet, "empty", True):
            # Balance sheet columns are a shorter series than the P&L; only use a
            # matching period rather than the nearest one, so debt is never
            # attributed to the wrong quarter.
            for bs_col in balance_sheet.columns:
                if str(bs_col)[:10] == period_end:
                    debt = _pick(balance_sheet, _DEBT_KEYS, bs_col)
                    equity = _pick(balance_sheet, _EQUITY_KEYS, bs_col)
                    break

        if revenue is None and net_income is None:
            continue  # nothing worth storing for this period
        rows.append({
            "symbol": f"NSE:{_bare(symbol)}",
            "period_end": period_end,
            "revenue": revenue,
            "ebitda": ebitda,
            "ebit": ebit,
            "net_income": net_income,
            "gross_profit": gross,
            "total_debt": debt,
            "total_equity": equity,
            "ebitda_margin_pct": _margin(ebitda, revenue),
            "ebit_margin_pct": _margin(ebit, revenue),
            "net_margin_pct": _margin(net_income, revenue),
            "roce_pct": _roce(ebit, debt, equity),
            "source": "yfinance",
        })
    return rows


def fetch_symbol(symbol: str) -> list[dict]:
    import warnings

    warnings.filterwarnings("ignore")
    import yfinance as yf

    try:
        ticker = yf.Ticker(f"{_bare(symbol)}.NS")
        return extract_rows(symbol, ticker.quarterly_financials, ticker.quarterly_balance_sheet)
    except Exception as exc:
        log.debug("fundamentals fetch failed %s: %s", symbol, exc)
        return []


def run(*, symbols: list[str], workers: int, dry_run: bool) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from dashboard.backend.db.outcomes import fundamentals_coverage, upsert_fundamentals_quarterly

    started = time.time()
    all_rows: list[dict] = []
    with_data = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_symbol, s): s for s in symbols}
        for done, future in enumerate(as_completed(futures), start=1):
            rows = future.result()
            if rows:
                with_data += 1
                all_rows.extend(rows)
            if done % 100 == 0:
                log.info("%d/%d symbols processed, %d with data", done, len(symbols), with_data)

    result = {
        "symbols_requested": len(symbols),
        "symbols_with_data": with_data,
        "coverage_pct": round(with_data / max(len(symbols), 1) * 100, 2),
        "quarter_rows": len(all_rows),
        "elapsed_sec": round(time.time() - started, 1),
    }
    if dry_run:
        result["dry_run"] = True
        result["sample"] = all_rows[:3]
        return result

    result["written"] = upsert_fundamentals_quarterly(all_rows)
    result["table"] = fundamentals_coverage()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill real quarterly fundamentals")
    parser.add_argument("--universe", type=int, default=2200, help="NSE universe size to cover")
    parser.add_argument("--limit", type=int, default=None, help="cap symbols (smoke tests)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from services.universe_manager import load_nse_universe

    symbols = load_nse_universe(args.universe).symbols
    if args.limit:
        symbols = symbols[: args.limit]
    log.info("fundamentals backfill: %d symbols, %d workers", len(symbols), args.workers)

    result = run(symbols=symbols, workers=args.workers, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
