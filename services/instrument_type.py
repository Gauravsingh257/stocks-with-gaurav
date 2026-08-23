"""
services/instrument_type.py — is this row an equity at all, and if not, what?

The NSE equity list carries more than shares: sovereign gold bonds, government
securities, corporate NCD tranches, ETFs, rights entitlements and SME-board
lines all sit alongside real companies. None of them can carry a sector, and
none of them should be scanned as a stock — so they are typed and excluded
rather than being dropped into "Unassigned", which is a bucket for real
companies we merely could not classify.
"""

from __future__ import annotations

import re

EQUITY = "EQUITY"

_ETFS = frozenset({
    "ICICIB22", "SBIETFPB", "SBIETFIT", "AXISBNKETF", "MOGSEC", "ESG",
    "NIFTYBEES", "BANKBEES", "GOLDBEES", "JUNIORBEES", "LIQUIDBEES",
})

# A bond/NCD line is a coupon+issuer+tranche code, e.g. 868NHB29-N3, 94SFL28-YL.
# Requiring the tranche suffix keeps real tickers that merely start with a digit
# (3PLAND = 3P Land Holdings, 3MINDIA, 360ONE) classified as equities.
_BOND = re.compile(r"^\d.*-(N[0-9A-Z]|Y[A-Z])$", re.I)
_SUFFIX = {
    "-GB": "Sovereign Gold Bond",
    "-GS": "Government Security",
    "-SM": "SME Board",
    "-ST": "SME Trading",
    "-BZ": "Trade-to-Trade",
    "-BE": "Trade-to-Trade",
    "-RE": "Rights Entitlement",
}


def classify(symbol: str, company_name: str | None = None) -> str:
    """Return EQUITY, or the instrument type that makes this row non-tradeable."""
    s = (symbol or "").replace("NSE:", "").strip().upper()
    if not s:
        return "Unknown Instrument"
    if s.startswith("SGB"):
        return "Sovereign Gold Bond"
    for suffix, label in _SUFFIX.items():
        if s.endswith(suffix):
            return label
    if _BOND.match(s):
        return "Corporate Bond / NCD"
    if s in _ETFS or s.endswith("ETF") or s.endswith("BEES"):
        return "ETF"
    # NSE's own equity list has no registered name for this row: the ticker is
    # stale (renamed or delisted) and will never resolve to a live company.
    if company_name is not None and not str(company_name).strip():
        return "Delisted / Renamed"
    return EQUITY


def is_equity(symbol: str, company_name: str | None = None) -> bool:
    return classify(symbol, company_name) == EQUITY
