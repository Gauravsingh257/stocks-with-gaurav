"""
services/sector_classification.py — THE single authoritative sector/industry source.

WHY THIS EXISTS
---------------
The Phase-0 teardown found THREE competing hardcoded sector dictionaries:

    engine/swing.py          SECTOR_MAP        96 symbols  -> "Others"
    services/portfolio_risk  _SECTOR_MAP      128 symbols  -> "OTHER"
    services/pil/reference_data                reuses engine.swing's

Against a 2,553-name NSE universe that is ~96% unclassified. Because "Others" /
"Unknown" is EXEMPT from the diversification cap and PASSES the governor's
`require_not_lagging` rule, the entire sector layer was inert for almost every
stock the system actually picks.

THE SOURCE
----------
NSE publishes its own official industry classification through the NIFTY index
constituent files. `ind_niftytotalmarket_list.csv` carries 752 symbols across 22
official industries and covers the whole investable universe (largecap 100 +
midcap 150 + smallcap 250 + microcap 250). It is free, needs no auth, and is the
same body that defines the sector indices — so it is authoritative rather than
someone's opinion.

Resolution order (first hit wins, never fabricated):
    1. NSE industry      (niftyindices constituent files)      authoritative
    2. yfinance sector   (already cached by fundamental_analysis)  best-effort
    3. legacy hardcoded  (engine.swing.SECTOR_MAP)             back-compat
    4. "Unknown"                                               honest

A symbol we genuinely cannot classify returns "Unknown" — it is never guessed
into a bucket, because a wrong sector is worse than a missing one (it would let
a lagging-sector name through the governor under a leading-sector label).

FLAG
----
`PHASE0_REAL_SECTORS` (default OFF). When off, `resolve_sector()` is not consulted
by any caller and every engine keeps its existing hardcoded map — byte-identical
behaviour. Turning it on WIDENS sector coverage, which CHANGES live selection
under the regime governor (more names become cappable / lagging-blocked). That is
a deliberate behaviour change and is why it ships disabled.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

log = logging.getLogger("services.sector_classification")

_ROOT = Path(__file__).resolve().parents[1]
_CACHE_PATH = Path(
    os.getenv("SECTOR_CLASSIFICATION_CACHE", str(_ROOT / "data" / "nse_industry_classification.json"))
)

# NSE constituent files carrying an `Industry` column. The total-market list is a
# superset of the other four; they are kept as fallbacks in case NSE reshuffles
# the file names (it has before), not because they add symbols today.
_NSE_CONSTITUENT_FILES = (
    "ind_niftytotalmarket_list.csv",
    "ind_nifty500list.csv",
    "ind_niftymicrocap250_list.csv",
    "ind_niftysmallcap250list.csv",
    "ind_niftymidcap150list.csv",
)
_NSE_BASE_URL = "https://niftyindices.com/IndexConstituent/"

UNKNOWN = "Unknown"

# NSE's 22 official industries -> the sector vocabulary the existing engines and
# `SECTOR_INDEX_TICKER` already speak. Mapping (rather than renaming everything)
# keeps sector_strength's index-ticker path working for the majors while the
# constituent-derived path handles the rest. Industries with no clean legacy
# equivalent keep their NSE name — `compute_sector_strength_from_candles` derives
# bands from constituents, so a new name is fully supported.
_NSE_TO_LEGACY: dict[str, str] = {
    "Financial Services": "Finance",
    "Information Technology": "IT",
    "Healthcare": "Pharma",
    "Automobile and Auto Components": "Auto",
    "Metals & Mining": "Metal",
    "Oil Gas & Consumable Fuels": "Energy",
    "Fast Moving Consumer Goods": "FMCG",
    "Realty": "Realty",
    "Construction": "Infra",
    "Construction Materials": "Cement",
    "Chemicals": "Chemicals",
    "Telecommunication": "Telecom",
    "Power": "Power",
    "Utilities": "Utilities",
    "Capital Goods": "Capital Goods",
    "Consumer Durables": "Consumer Durables",
    "Consumer Services": "Consumer Services",
    "Services": "Services",
    "Textiles": "Textiles",
    "Media Entertainment & Publication": "Media",
    "Diversified": "Diversified",
    "Forest Materials": "Forest Materials",
}

# yfinance's global GICS-ish sector labels -> the same vocabulary, so tier-2
# lookups land in the same buckets as tier-1 rather than creating parallel ones.
_YF_TO_LEGACY: dict[str, str] = {
    "Financial Services": "Finance",
    "Technology": "IT",
    "Healthcare": "Pharma",
    "Consumer Cyclical": "Consumer Services",
    "Consumer Defensive": "FMCG",
    "Basic Materials": "Chemicals",
    "Energy": "Energy",
    "Industrials": "Capital Goods",
    "Real Estate": "Realty",
    "Utilities": "Utilities",
    "Communication Services": "Telecom",
}

_memo: dict[str, str] | None = None


def real_sectors_enabled() -> bool:
    """Master flag. Default OFF -> callers keep their legacy hardcoded maps."""
    return os.getenv("PHASE0_REAL_SECTORS", "0").strip().lower() in ("1", "true", "yes", "on")


def _bare(symbol: str) -> str:
    return str(symbol or "").replace("NSE:", "").replace(".NS", "").strip().upper()


_OVERRIDE_PATH = Path(
    os.getenv("SECTOR_OVERRIDES_PATH", str(_ROOT / "data" / "sector_overrides.csv"))
)
_overrides: dict[str, str] | None = None


def load_overrides(*, refresh: bool = False) -> dict[str, str]:
    """Hand-assigned sectors — the highest-priority tier.

    A two-column CSV (`symbol,sector`) maintained by the owner. It exists because
    reference-data coverage is a limitation of our sources, not a property of the
    company: a stock outside the NIFTY Total Market list is often exactly the
    small/micro cap worth finding. Rather than let a missing row decide what the
    engine may consider, anything identifiable can be assigned by hand here.

    Unknown/blank rows are ignored, so a partially-filled sheet is safe to ship.
    """
    global _overrides
    if _overrides is not None and not refresh:
        return _overrides
    out: dict[str, str] = {}
    if _OVERRIDE_PATH.exists():
        try:
            with _OVERRIDE_PATH.open(encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    sym = _bare(row.get("symbol") or row.get("SYMBOL") or "")
                    sec = str(row.get("sector") or row.get("SECTOR") or "").strip()
                    if sym and sec and sec.lower() not in ("unknown", "unassigned", "na", "-"):
                        out[sym] = sec
        except Exception as exc:
            log.warning("sector overrides unreadable (%s): %s", _OVERRIDE_PATH, exc)
    if out:
        log.info("sector overrides: %d hand-assigned symbols", len(out))
    _overrides = out
    return out


# ── tier 1: NSE official industry ─────────────────────────────────────────────

def _cache_is_fresh(path: Path, max_age_days: int = 7) -> bool:
    """NSE reconstitutes indices twice a year, so a week-old cache is fine and
    avoids hammering niftyindices on every worker boot."""
    if not path.exists():
        return False
    try:
        age = date.today() - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()
    except OSError:
        return False
    return age.days <= max_age_days


def _read_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = payload.get("industries") if isinstance(payload, dict) else payload
    if not isinstance(rows, dict):
        return {}
    return {str(k).upper(): str(v) for k, v in rows.items() if k and v}


def _write_cache(path: Path, mapping: dict[str, str], source: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": source,
                    "count": len(mapping),
                    "industries": mapping,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("sector classification cache write failed (%s): %s", path, exc)


def _fetch_constituent_file(filename: str, timeout: int = 20) -> dict[str, str]:
    request = Request(
        _NSE_BASE_URL + filename,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/csv,application/csv,text/plain,*/*",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public NSE URL
        raw = response.read().decode("utf-8-sig", errors="ignore")
    out: dict[str, str] = {}
    for row in csv.DictReader(raw.splitlines()):
        symbol = str(row.get("Symbol") or "").strip().upper()
        industry = str(row.get("Industry") or "").strip()
        if symbol and industry:
            out[symbol] = industry
    return out


def load_nse_industry_map(*, refresh: bool = False) -> dict[str, str]:
    """{SYMBOL: NSE industry} from the NIFTY constituent files, disk-cached.

    Falls back to a stale cache when the network fails — a classification that is
    six months old is still correct for almost every symbol, whereas an empty map
    would silently collapse every stock to "Unknown".
    """
    cached = _read_cache(_CACHE_PATH)
    if cached and not refresh and _cache_is_fresh(_CACHE_PATH):
        return cached

    merged: dict[str, str] = {}
    errors: list[str] = []
    for filename in _NSE_CONSTITUENT_FILES:
        try:
            merged.update(_fetch_constituent_file(filename))
        except Exception as exc:
            errors.append(f"{filename}: {str(exc)[:120]}")

    if merged:
        _write_cache(_CACHE_PATH, merged, source=_NSE_BASE_URL)
        log.info("NSE industry classification: %d symbols loaded", len(merged))
        return merged

    if cached:
        log.warning("NSE industry fetch failed (%s) — using stale cache of %d symbols",
                    "; ".join(errors)[:200], len(cached))
        return cached
    log.error("NSE industry classification unavailable: %s", "; ".join(errors)[:300])
    return {}


# ── tier 2: yfinance sector (already fetched + cached for fundamentals) ────────

def _yfinance_sector(symbol: str) -> str | None:
    """Read the sector yfinance already gave us. Cache-only: this never makes a
    network call, so classification can't stall a scan."""
    try:
        from services.fundamental_analysis import _load_cache

        cached = _load_cache(f"NSE:{_bare(symbol)}") or _load_cache(_bare(symbol))
    except Exception:
        return None
    if not cached:
        return None
    raw = cached.get("sector") or cached.get("industry")
    if not raw:
        return None
    return _YF_TO_LEGACY.get(str(raw).strip(), str(raw).strip())


# ── tier 3: legacy hardcoded maps (back-compat only) ──────────────────────────

def _legacy_sector(symbol: str) -> str | None:
    try:
        from engine.swing import SECTOR_MAP

        value = SECTOR_MAP.get(_bare(symbol))
    except Exception:
        return None
    if not value or value in ("Others", "OTHER", UNKNOWN):
        return None
    return value


# ── public API ────────────────────────────────────────────────────────────────

def resolve_sector(symbol: str) -> str:
    """Authoritative sector for one symbol, or "Unknown" when genuinely unknown.

    Never raises and never guesses. Memoised per process — the underlying maps
    change at most twice a year.
    """
    global _memo
    key = _bare(symbol)
    if not key:
        return UNKNOWN
    if _memo is None:
        try:
            nse = load_nse_industry_map()
        except Exception as exc:
            log.warning("industry map load failed (%s) — falling back per-symbol", exc)
            nse = {}
        _memo = {sym: _NSE_TO_LEGACY.get(ind, ind) for sym, ind in nse.items()}

    hand = load_overrides().get(key)
    if hand:
        return hand

    hit = _memo.get(key)
    if hit:
        return hit
    for resolver in (_yfinance_sector, _legacy_sector):
        try:
            value = resolver(key)
        except Exception:
            value = None
        if value:
            return value
    return UNKNOWN


def coverage_report(symbols: list[str]) -> dict:
    """How much of a universe we can actually classify, by tier. Used by the
    Phase-0 validation check — a coverage number nobody measures is a coverage
    number nobody can trust."""
    tiers = {"manual": 0, "nse_official": 0, "yfinance": 0, "legacy": 0, "unknown": 0}
    sectors: dict[str, int] = {}
    if _memo is None:
        resolve_sector("RELIANCE")  # prime the memo
    memo = _memo or {}
    hand = load_overrides()
    for symbol in symbols:
        key = _bare(symbol)
        if key in hand:
            tiers["manual"] += 1
            sector = hand[key]
        elif key in memo:
            tiers["nse_official"] += 1
            sector = memo[key]
        elif _yfinance_sector(key):
            tiers["yfinance"] += 1
            sector = _yfinance_sector(key) or UNKNOWN
        elif _legacy_sector(key):
            tiers["legacy"] += 1
            sector = _legacy_sector(key) or UNKNOWN
        else:
            tiers["unknown"] += 1
            sector = UNKNOWN
        sectors[sector] = sectors.get(sector, 0) + 1
    total = max(len(symbols), 1)
    classified = total - tiers["unknown"]
    return {
        "total": len(symbols),
        "classified": classified,
        "coverage_pct": round(classified / total * 100, 2),
        "by_tier": tiers,
        "by_sector": dict(sorted(sectors.items(), key=lambda kv: -kv[1])),
    }
