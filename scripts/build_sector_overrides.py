"""
scripts/build_sector_overrides.py — normalise the owner's hand-assigned sectors.

The worksheet came back with a richer, more readable taxonomy than the engine's
("Banking & Financial Services" vs "Finance"). Two vocabularies would fragment
the sector-strength buckets, so this folds the hand labels onto the SAME
canonical names the automatic NSE tier already emits, and writes
`data/sector_overrides.csv`.

It also separates out rows that are NOT EQUITY at all — ETFs, government
securities, sovereign gold bonds and NCDs were sitting in the universe and can
never have a sector. Those belong in an exclusion list, not in "Unassigned".

Read-only with respect to production; regenerating is idempotent.
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Owner's label -> canonical engine sector (same vocabulary as _NSE_TO_LEGACY).
LABEL_TO_CANON = {
    "Automobile & Auto Components": "Auto",
    "Information Technology": "IT",
    "Pharmaceuticals & Healthcare": "Pharma",
    "Banking & Financial Services": "Finance",
    "FMCG & Food": "FMCG",
    "Capital Goods & Industrial Manufacturing": "Capital Goods",
    "Telecom & Communication": "Telecom",
    "Logistics & Transportation": "Services",
    "Oil, Gas & Energy": "Energy",
    "Retail & Consumer": "Consumer Services",
    "Construction & Infrastructure": "Infra",
    "Hotels, Travel & Leisure": "Consumer Services",
    "Metals & Mining": "Metal",
    "Chemicals": "Chemicals",
    "Consumer Durables & Appliances": "Consumer Durables",
    "Real Estate": "Realty",
    "Textiles": "Textiles",
    "Packaging & Paper": "Forest Materials",
    "Media & Entertainment": "Media",
    "Business Services": "Services",
    "Education & Training": "Consumer Services",
    "Agriculture & Seeds": "FMCG",
    "Trading & Distribution": "Services",
    "Diversified": "Diversified",
    "Unassigned": "Unassigned",
}

# Single-letter code used to transcribe the filled worksheet compactly.
CODE_TO_LABEL = {
    "A": "Automobile & Auto Components", "I": "Information Technology",
    "P": "Pharmaceuticals & Healthcare", "B": "Banking & Financial Services",
    "F": "FMCG & Food", "C": "Capital Goods & Industrial Manufacturing",
    "T": "Telecom & Communication", "L": "Logistics & Transportation",
    "O": "Oil, Gas & Energy", "R": "Retail & Consumer",
    "N": "Construction & Infrastructure", "H": "Hotels, Travel & Leisure",
    "M": "Metals & Mining", "K": "Chemicals",
    "D": "Consumer Durables & Appliances", "E": "Real Estate",
    "X": "Textiles", "G": "Packaging & Paper", "J": "Media & Entertainment",
    "S": "Business Services", "U": "Education & Training",
    "Q": "Agriculture & Seeds", "V": "Trading & Distribution", "W": "Diversified", "Z": "Unassigned",
}

from services.instrument_type import EQUITY, classify


def is_non_equity(symbol: str, company: str) -> bool:
    """Delegate to the shared classifier (services.instrument_type).

    The earlier local regex treated any ticker STARTING with a digit as a bond,
    which wrongly excluded real equities like 3PLAND / 3MINDIA / 360ONE. The
    shared version requires the tranche suffix that a real NCD line carries.
    """
    return classify(symbol, company) != EQUITY


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    todo = os.path.join(root, "data", "sector_overrides_TODO.csv")
    codes_path = os.path.join(root, "data", "sector_codes.txt")
    out_path = os.path.join(root, "data", "sector_overrides.csv")
    excl_path = os.path.join(root, "data", "universe_exclusions.csv")

    rows = list(csv.DictReader(open(todo, encoding="utf-8-sig")))
    # symbol -> code pairs (anything not listed is Unassigned). Keyed by symbol
    # rather than by position so a transcription slip cannot silently shift
    # every assignment by one row.
    pairs = {}
    for line in open(codes_path, encoding="utf-8"):
        parts = line.split()
        if len(parts) == 2:
            pairs[parts[0].strip().upper()] = parts[1].strip().upper()
    # A coded symbol that is not in the worksheet is still a valid override —
    # it just came from a later pass over names the first worksheet missed.
    known = {r["symbol"].strip().upper() for r in rows}
    extra = sorted(set(pairs) - known)
    for sym in extra:
        # company_name deliberately absent (None), not blank: a blank name is
        # the "stale ticker" signal and would wrongly exclude a live symbol.
        rows.append({"symbol": sym, "company_name": None, "avg_turnover_cr": ""})
    if extra:
        print(f"note: {len(extra)} coded symbols added outside the worksheet: {extra}")

    assigned, excluded, unassigned = [], [], []
    for r in rows:
        sym = r["symbol"].strip().upper()
        company = r.get("company_name")
        company = "" if company == "" else (company or None)
        label = CODE_TO_LABEL[pairs.get(sym, "Z")]
        canon = LABEL_TO_CANON[label]
        if company is not None and is_non_equity(sym, company):
            excluded.append({"symbol": sym, "company_name": company or "",
                             "reason": "not_equity_or_delisted",
                             "turnover_cr": r.get("avg_turnover_cr", "")})
        elif canon == "Unassigned":
            unassigned.append({"symbol": sym, "company_name": company or "",
                               "turnover_cr": r.get("avg_turnover_cr", "")})
        else:
            assigned.append({"symbol": sym, "sector": canon,
                             "company_name": company or "",
                             "owner_label": label,
                             "turnover_cr": r.get("avg_turnover_cr", "")})

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "sector", "company_name",
                                           "owner_label", "turnover_cr"])
        w.writeheader()
        w.writerows(assigned)
    with open(excl_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "company_name", "reason", "turnover_cr"])
        w.writeheader()
        w.writerows(excluded)

    from collections import Counter
    by_sector = Counter(a["sector"] for a in assigned)
    print(f"worksheet rows        : {len(rows)}")
    print(f"  hand-assigned       : {len(assigned)}  -> data/sector_overrides.csv")
    print(f"  genuinely Unassigned: {len(unassigned)}")
    print(f"  NOT EQUITY (excluded): {len(excluded)} -> data/universe_exclusions.csv")
    print("\ncanonical sectors from the manual tier:")
    for s, n in by_sector.most_common():
        print(f"   {s:<20}{n:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
