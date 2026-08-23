"""
scripts/export_unclassified_sectors.py — produce the hand-assign worksheet.

Writes a CSV of every universe symbol we could NOT classify automatically, with
its registered company name, ready to be filled in and saved as
`data/sector_overrides.csv` (read as the highest-priority tier by
services.sector_classification).

    python -m scripts.export_unclassified_sectors --out data/sector_overrides_TODO.csv

Read-only with respect to production. Sorted by liquidity so the names that can
actually be traded are at the top and a partial pass still buys most of the
coverage.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def company_names() -> dict[str, str]:
    """SYMBOL -> registered company name, from NSE's own equity list."""
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv",
                                "Referer": "https://www.nseindia.com/"})
    raw = urlopen(req, timeout=25).read().decode("utf-8-sig", errors="ignore")  # noqa: S310
    out = {}
    for row in csv.DictReader(raw.splitlines()):
        sym = (row.get("SYMBOL") or "").strip().upper()
        name = (row.get("NAME OF COMPANY") or "").strip()
        if sym:
            out[sym] = name
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/sector_overrides_TODO.csv")
    ap.add_argument("--universe", type=int, default=2200)
    args = ap.parse_args()

    from services.sector_classification import UNKNOWN, resolve_sector
    from services.universe_manager import load_nse_universe

    symbols = load_nse_universe(args.universe).symbols
    names = company_names()

    # Liquidity, so a partial pass covers the names that matter most.
    turnover: dict[str, float] = {}
    try:
        from services.universe_ohlc import load_universe_ohlc
        for sym, bars in (load_universe_ohlc(symbols) or {}).items():
            tail = bars[-20:]
            if tail:
                turnover[sym] = sum((b.get("close") or 0) * (b.get("volume") or 0)
                                    for b in tail) / len(tail) / 1e7
    except Exception:
        pass

    rows = []
    for s in symbols:
        bare = s.replace("NSE:", "").strip().upper()
        if resolve_sector(bare) != UNKNOWN:
            continue
        rows.append({"symbol": bare, "company_name": names.get(bare, ""),
                     "avg_turnover_cr": round(turnover.get(bare, 0.0), 2), "sector": ""})
    rows.sort(key=lambda r: -r["avg_turnover_cr"])

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "company_name", "avg_turnover_cr", "sector"])
        w.writeheader()
        w.writerows(rows)

    tradeable = sum(1 for r in rows if r["avg_turnover_cr"] >= 1.0)
    print(f"unclassified: {len(rows)} of {len(symbols)}")
    print(f"  of those, >= Rs1Cr/day turnover : {tradeable}  <- filling only these covers most of what is tradeable")
    print(f"  worksheet written: {args.out}")
    print("  fill the `sector` column, save as data/sector_overrides.csv, redeploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
