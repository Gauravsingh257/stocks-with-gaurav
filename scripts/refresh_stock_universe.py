"""
scripts/refresh_stock_universe.py — weekly rebuild of the researchable universe.

Populates `stock_universe`: every NSE symbol with its company name, sector (from
the layered classifier) and the headline ratios — PE, PB, ROE, debt/equity,
revenue growth, promoter holding — plus price, market cap, turnover, distance
from the 52-week high and 1-year return.

    python -m scripts.refresh_stock_universe                # full refresh
    python -m scripts.refresh_stock_universe --limit 50     # smoke test
    python -m scripts.refresh_stock_universe --dry-run

RUN OUT OF BAND. Scheduled for Saturday, when the market is closed and nothing
competes for the provider. Never call this from a request handler or a FastAPI
startup hook — that is the data-volume work that caused the 2026-08-02
healthcheck outage.

A ratio the provider does not give is stored as NULL, never 0: a 0 PE would
screen as "cheap" and a 0 debt/equity as "unlevered", which is exactly backwards.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("refresh_stock_universe")


def _num(v):
    """Finite float or None — the provider returns strings and NaN."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def fetch_one(symbol: str) -> dict:
    """Provider snapshot for one symbol. Never raises."""
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    out = {"symbol": symbol}
    try:
        info = yf.Ticker(f"{symbol}.NS").info or {}
    except Exception as exc:
        log.debug("info failed %s: %s", symbol, exc)
        return out

    price = _num(info.get("currentPrice")) or _num(info.get("previousClose"))
    mcap = _num(info.get("marketCap"))
    hi52 = _num(info.get("fiftyTwoWeekHigh"))
    de = _num(info.get("debtToEquity"))
    if de is not None and de > 10:          # provider sometimes reports % not ratio
        de = de / 100.0
    roe = _num(info.get("returnOnEquity"))
    rev = _num(info.get("revenueGrowth"))
    prom = _num(info.get("heldPercentInsiders"))

    out.update({
        "price": price,
        "market_cap_cr": round(mcap / 1e7, 1) if mcap else None,
        "pe": _num(info.get("trailingPE")) or _num(info.get("forwardPE")),
        "pb": _num(info.get("priceToBook")),
        "roe_pct": round(roe * 100, 2) if roe is not None else None,
        "debt_to_equity": round(de, 2) if de is not None else None,
        "revenue_growth_pct": round(rev * 100, 2) if rev is not None else None,
        "net_margin_pct": (
            round(_num(info.get("profitMargins")) * 100, 2)
            if _num(info.get("profitMargins")) is not None else None
        ),
        "promoter_pct": round(prom * 100, 2) if prom else None,
        "pct_from_52w_high": (
            round((hi52 - price) / hi52 * 100, 2) if (hi52 and price and hi52 > 0) else None
        ),
        "sector_raw": info.get("sector"),
        "industry_raw": info.get("industry"),
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=2200)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--map", default="data/sector_map_FULL.csv")
    args = ap.parse_args()

    import csv

    from dashboard.backend.db.universe import upsert_universe
    from services.instrument_type import EQUITY, classify
    from services.universe_manager import load_nse_universe

    # Sector map is authoritative; rebuild it first if it is missing.
    smap = {}
    if os.path.exists(args.map):
        for r in csv.DictReader(open(args.map, encoding="utf-8-sig")):
            smap[r["symbol"].strip().upper()] = r
    else:
        log.warning("%s missing — sectors will fall back to the live classifier", args.map)

    symbols = [s.replace("NSE:", "").strip().upper()
               for s in load_nse_universe(args.universe).symbols]
    if args.limit:
        symbols = symbols[: args.limit]
    log.info("refreshing %d symbols with %d workers", len(symbols), args.workers)

    started = time.time()
    fetched: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, res in enumerate(pool.map(fetch_one, symbols), start=1):
            fetched[res["symbol"]] = res
            if i % 200 == 0:
                log.info("  %d/%d", i, len(symbols))

    # ROE from OUR OWN quarterly filings where the provider omits it — which it
    # does for most NSE names (`returnOnEquity` is absent from the fast payload,
    # while profitMargins/debtToEquity are present). TTM net income over the most
    # recent shareholders' equity is the real calculation, not a proxy.
    roe_db: dict[str, float] = {}
    try:
        from dashboard.backend.db.schema import get_connection
        conn = get_connection()
        try:
            for r in conn.execute("""
                SELECT symbol,
                       SUM(net_income) OVER (PARTITION BY symbol) AS ni,
                       total_equity, period_end,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY period_end DESC) rn
                FROM fundamentals_quarterly WHERE net_income IS NOT NULL
            """):
                if r["rn"] == 1 and r["total_equity"] and r["total_equity"] > 0 and r["ni"]:
                    roe_db[r["symbol"].replace("NSE:", "").upper()] = round(
                        r["ni"] / r["total_equity"] * 100, 2)
        finally:
            conn.close()
        log.info("ROE computed from quarterly filings for %d symbols", len(roe_db))
    except Exception as exc:
        log.info("quarterly ROE unavailable (%s)", exc)

    # Price history for turnover / 1y return, from the universe OHLC snapshot
    # when the scanner has published one (no extra network), else skipped.
    bars = {}
    try:
        from services.universe_ohlc import load_universe_ohlc
        bars = load_universe_ohlc(symbols) or {}
        log.info("universe OHLC snapshot supplied %d symbols", len(bars))
    except Exception as exc:
        log.info("no OHLC snapshot (%s) — turnover/1y left null", exc)

    rows = []
    for sym in symbols:
        f = fetched.get(sym, {})
        m = smap.get(sym, {})
        sector = m.get("sector") or "Unassigned"
        kind = classify(sym, m.get("company_name"))
        if sector in {"Sovereign Gold Bond", "Government Security", "Corporate Bond / NCD",
                      "SME Board", "SME Trading", "Trade-to-Trade", "Rights Entitlement",
                      "ETF", "Delisted / Renamed", "Non-EQ Series"}:
            kind, sector = sector, "Unassigned"

        turnover = ret1y = None
        b = bars.get(sym)
        if b:
            tail = b[-20:]
            if tail:
                turnover = round(sum((x.get("close") or 0) * (x.get("volume") or 0)
                                     for x in tail) / len(tail) / 1e7, 2)
            if len(b) > 250 and b[-251].get("close"):
                ret1y = round((b[-1]["close"] - b[-251]["close"]) / b[-251]["close"] * 100, 2)

        rows.append({
            "symbol": sym,
            "company_name": m.get("company_name") or "",
            "sector": sector,
            "sector_source": m.get("source") or "",
            "instrument": EQUITY if kind == EQUITY else kind,
            "price": f.get("price"),
            "market_cap_cr": f.get("market_cap_cr"),
            "turnover_cr": turnover,
            "pe": f.get("pe"),
            "pb": f.get("pb"),
            "roe_pct": f.get("roe_pct") if f.get("roe_pct") is not None else roe_db.get(sym),
            "roe_source": ("provider" if f.get("roe_pct") is not None
                           else ("filings" if sym in roe_db else None)),
            "net_margin_pct": f.get("net_margin_pct"),
            "debt_to_equity": f.get("debt_to_equity"),
            "revenue_growth_pct": f.get("revenue_growth_pct"),
            "promoter_pct": f.get("promoter_pct"),
            "pct_from_52w_high": f.get("pct_from_52w_high"),
            "ret_1y_pct": ret1y,
        })

    have = lambda k: sum(1 for r in rows if r[k] is not None)  # noqa: E731
    summary = {
        "symbols": len(rows),
        "equities": sum(1 for r in rows if r["instrument"] == EQUITY),
        "with_pe": have("pe"), "with_roe": have("roe_pct"),
        "with_de": have("debt_to_equity"), "with_mcap": have("market_cap_cr"),
        "with_margin": have("net_margin_pct"),
        "with_turnover": have("turnover_cr"),
        "elapsed_sec": round(time.time() - started, 1),
    }
    if args.dry_run:
        summary["dry_run"] = True
        summary["sample"] = rows[:3]
    else:
        summary["written"] = upsert_universe(rows)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
