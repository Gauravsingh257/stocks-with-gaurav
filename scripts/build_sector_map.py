"""
scripts/build_sector_map.py — the authoritative sector map for the whole universe.

Resolution order, highest priority first:

    1. manual        data/sector_overrides.csv     hand-assigned
    2. nse_official  NIFTY constituent industry
    3. provider      cached industry -> canonical  (industry, not the coarse sector)
    4. Unassigned    a real bucket, still fully eligible for selection

Rows that are not equities at all (ETF / G-Sec / SGB / NCD / rights / stale
tickers) are typed by services.instrument_type and excluded from the tradeable
universe rather than being dropped into Unassigned.

Writes data/sector_map_FULL.csv. Read-only with respect to production.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NON_EQUITY_KINDS = {
    "Sovereign Gold Bond", "Government Security", "Corporate Bond / NCD",
    "SME Board", "SME Trading", "Trade-to-Trade", "Rights Entitlement",
    "ETF", "Delisted / Renamed", "Non-EQ Series", "Unknown Instrument",
}


def load_live_equities(path: str) -> dict:
    """NSE's own equity list — the authority on whether a ticker still exists."""
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=2200)
    ap.add_argument("--out", default="data/sector_map_FULL.csv")
    args = ap.parse_args()

    import services.sector_classification as sc
    from services.industry_map import canon_from_provider
    from services.instrument_type import EQUITY, classify
    from services.universe_manager import load_nse_universe

    live = load_live_equities("data/_nse_live_equities.json")
    manual = {r["symbol"].strip().upper(): r["sector"].strip()
              for r in csv.DictReader(open("data/sector_overrides.csv", encoding="utf-8-sig"))}
    nse = sc.load_nse_industry_map()
    nse_canon = {k: sc._NSE_TO_LEGACY.get(v, v) for k, v in nse.items()}

    provider: dict[str, dict] = {}
    for p in ("data/_unassigned_yf.json", "data/_provider_sectors.json"):
        if os.path.exists(p):
            for k, v in json.load(open(p, encoding="utf-8")).items():
                provider.setdefault(k.upper(), v if isinstance(v, dict) else {"sector": v})

    rows = []
    for raw in load_nse_universe(args.universe).symbols:
        s = raw.replace("NSE:", "").strip().upper()
        info = live.get(s)
        company = info["name"] if info else ""
        kind = classify(s, company) if info else ("Delisted / Renamed" if live else EQUITY)
        if info and kind == EQUITY and info.get("series") not in ("EQ", "BE", "SM", "ST", "BZ", "SZ"):
            kind = "Non-EQ Series"
        if kind != EQUITY:
            rows.append({"symbol": s, "company_name": company, "sector": kind,
                         "source": "non_equity",
                         "note": info.get("series", "") if info else "not on NSE list"})
            continue
        if s in manual:
            rows.append({"symbol": s, "company_name": company, "sector": manual[s],
                         "source": "manual", "note": ""})
            continue
        if s in nse_canon:
            rows.append({"symbol": s, "company_name": company, "sector": nse_canon[s],
                         "source": "nse_official", "note": nse[s]})
            continue
        p = provider.get(s) or {}
        canon = canon_from_provider(p.get("industry"), p.get("sector"))
        if canon:
            rows.append({"symbol": s, "company_name": company, "sector": canon,
                         "source": "provider", "note": p.get("industry") or p.get("sector") or ""})
            continue
        rows.append({"symbol": s, "company_name": company, "sector": "Unassigned",
                     "source": "none", "note": ""})

    rows.sort(key=lambda r: (r["sector"], r["symbol"]))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "company_name", "sector", "source", "note"])
        w.writeheader()
        w.writerows(rows)

    by_sec = Counter(r["sector"] for r in rows)
    by_src = Counter(r["source"] for r in rows)
    eq = [r for r in rows if r["sector"] not in NON_EQUITY_KINDS]
    cl = [r for r in eq if r["sector"] != "Unassigned"]
    print(f"universe    : {len(rows)}  -> {args.out}")
    print(f"  equities  : {len(eq)}   CLASSIFIED {len(cl)} ({len(cl)/max(len(eq),1)*100:.1f}%)"
          f"   Unassigned {by_sec.get('Unassigned', 0)}")
    print(f"  non-equity: {len(rows)-len(eq)}")
    print(f"  sources   : {dict(by_src)}")
    print("\nSECTORS")
    for s, n in by_sec.most_common():
        tag = "   [non-equity]" if s in NON_EQUITY_KINDS else ""
        print(f"   {s:<24}{n:>5}{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
