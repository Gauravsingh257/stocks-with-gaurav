"""
services/market_intelligence.py
External API integrations for market intelligence data.

5 Safe APIs:
1. NSE Holiday Calendar — hardcoded official NSE holidays (Nager.Date doesn't support India)
2. QuickChart  — Chart image generation
3. Frankfurter — USD/INR (free, no key, ECB data)
4. FRED        — US macro indicators (free key)
5. Indian Mutual Fund API — MF flow data (mfapi.in)

All calls are async-safe and cached. NEVER called inside the trading loop.
Designed for pre-market agent (08:45 IST) and dashboard on-demand.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Optional

import requests

log = logging.getLogger("services.market_intelligence")

# ── Cache TTLs ─────────────────────────────────────────────────────────────
_HOLIDAY_CACHE_TTL = 86400      # 24h
_FX_CACHE_TTL = 3600            # 1h
_FRED_CACHE_TTL = 86400         # 24h
_MF_CACHE_TTL = 86400           # 24h
_REQUEST_TIMEOUT = 10           # seconds

# ── Thread-safe cache ──────────────────────────────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()


def _cache_get(key: str) -> Optional[dict]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() < entry["expires"]:
            return entry["data"]
    return None


def _cache_set(key: str, data, ttl: int):
    with _cache_lock:
        _cache[key] = {"data": data, "expires": time.time() + ttl}


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class Holiday:
    date: str
    name: str
    country_code: str = "IN"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FXSnapshot:
    usd_inr: float
    usd_inr_prev: Optional[float] = None
    chg_pct: float = 0.0
    source: str = "frankfurter.dev"
    fetched_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FREDMacro:
    fed_funds_rate: Optional[float] = None
    us_10y_yield: Optional[float] = None
    dxy_index: Optional[float] = None
    us_cpi_yoy: Optional[float] = None
    source: str = "FRED"
    fetched_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class MFFlowData:
    """Indian Mutual Fund flow data — latest NAV movements as a proxy for flows."""
    top_equity_funds: list = field(default_factory=list)
    fetched_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class MarketIntelSnapshot:
    holidays: list = field(default_factory=list)
    is_holiday_today: bool = False
    next_holiday: Optional[dict] = None
    fx: Optional[dict] = None
    macro: Optional[dict] = None
    mf_flows: Optional[dict] = None
    fetched_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. NSE HOLIDAY CALENDAR (hardcoded — Nager.Date does not support India)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Official NSE trading holidays (equity segment). Source: NSE circulars.
_NSE_HOLIDAYS: dict[int, list[tuple[str, str]]] = {
    2025: [
        ("2025-02-26", "Mahashivratri"),
        ("2025-03-14", "Holi"),
        ("2025-03-31", "Id-Ul-Fitr (Ramadan)"),
        ("2025-04-10", "Shri Mahavir Jayanti"),
        ("2025-04-14", "Dr. Baba Saheb Ambedkar Jayanti"),
        ("2025-04-18", "Good Friday"),
        ("2025-05-01", "Maharashtra Day"),
        ("2025-08-15", "Independence Day"),
        ("2025-08-27", "Ganesh Chaturthi"),
        ("2025-10-02", "Mahatma Gandhi Jayanti / Dussehra"),
        ("2025-10-21", "Diwali (Laxmi Pujan)"),
        ("2025-10-22", "Diwali (Balipratipada)"),
        ("2025-11-05", "Prakash Gurpurb Sri Guru Nanak Dev"),
        ("2025-12-25", "Christmas"),
    ],
    2026: [
        ("2026-01-26", "Republic Day"),
        ("2026-02-17", "Mahashivratri"),
        ("2026-03-03", "Holi"),
        ("2026-03-20", "Id-Ul-Fitr (Ramadan)"),
        ("2026-03-30", "Shri Mahavir Jayanti"),
        ("2026-04-03", "Good Friday"),
        ("2026-04-14", "Dr. Baba Saheb Ambedkar Jayanti"),
        ("2026-05-01", "Maharashtra Day"),
        ("2026-05-25", "Buddha Purnima"),
        ("2026-06-29", "Id-Ul-Adha (Bakri Id)"),
        ("2026-08-15", "Independence Day"),
        ("2026-08-17", "Ganesh Chaturthi"),
        ("2026-10-02", "Mahatma Gandhi Jayanti"),
        ("2026-10-09", "Dussehra"),
        ("2026-10-20", "Diwali (Laxmi Pujan) / Muharram"),
        ("2026-11-25", "Prakash Gurpurb Sri Guru Nanak Dev"),
        ("2026-12-25", "Christmas"),
    ],
}


def fetch_holidays(year: Optional[int] = None) -> list[Holiday]:
    """
    Return official NSE trading holidays for the given year.
    Uses hardcoded calendar (Nager.Date API does not support India).
    """
    if year is None:
        year = date.today().year

    cache_key = f"holidays_{year}"
    cached = _cache_get(cache_key)
    if cached:
        return [Holiday(**h) for h in cached]

    raw = _NSE_HOLIDAYS.get(year, [])
    holidays = [Holiday(date=d, name=n, country_code="IN") for d, n in raw]
    if holidays:
        _cache_set(cache_key, [h.as_dict() for h in holidays], _HOLIDAY_CACHE_TTL)
    log.info("Loaded %d NSE holidays for %d", len(holidays), year)
    return holidays


def is_holiday_today() -> bool:
    """Check if today is an NSE trading holiday."""
    today_str = date.today().isoformat()
    holidays = fetch_holidays()
    return any(h.date == today_str for h in holidays)


def next_holiday() -> Optional[Holiday]:
    """Return the next upcoming NSE holiday, or None."""
    today_str = date.today().isoformat()
    holidays = fetch_holidays()
    upcoming = [h for h in holidays if h.date > today_str]
    return upcoming[0] if upcoming else None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. QUICKCHART — Chart Image URLs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_chart_url(
    chart_type: str = "line",
    labels: Optional[list[str]] = None,
    datasets: Optional[list[dict]] = None,
    title: str = "",
    width: int = 600,
    height: int = 300,
) -> str:
    """
    Generate a QuickChart URL for embedding chart images.
    https://quickchart.io/chart?c={...}&w=600&h=300

    Returns a URL string (no API call — URL encodes the chart config).
    """
    import json as _json
    from urllib.parse import quote

    if labels is None:
        labels = []
    if datasets is None:
        datasets = []

    config = {
        "type": chart_type,
        "data": {
            "labels": labels,
            "datasets": datasets,
        },
        "options": {
            "title": {"display": bool(title), "text": title},
            "legend": {"display": len(datasets) > 1},
        },
    }
    encoded = quote(_json.dumps(config))
    return f"https://quickchart.io/chart?c={encoded}&w={width}&h={height}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. FRANKFURTER — USD/INR (free, no key, ECB reference rates)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_usd_inr() -> FXSnapshot:
    """
    Fetch latest USD/INR rate from Frankfurter API (free, no key).
    GET https://api.frankfurter.dev/v1/latest?base=USD&symbols=INR
    """
    cached = _cache_get("fx_usd_inr")
    if cached:
        return FXSnapshot(**cached)

    url = "https://api.frankfurter.dev/v1/latest"
    params = {"base": "USD", "symbols": "INR"}

    try:
        resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        rate = data.get("rates", {}).get("INR")
        if rate is None:
            log.warning("Frankfurter: no INR rate in response")
            return FXSnapshot(usd_inr=0.0, fetched_at=datetime.now().isoformat())

        snapshot = FXSnapshot(
            usd_inr=round(float(rate), 4),
            source="frankfurter.dev",
            fetched_at=datetime.now().isoformat(),
        )
        _cache_set("fx_usd_inr", snapshot.as_dict(), _FX_CACHE_TTL)
        log.info("USD/INR rate: %.4f (Frankfurter)", snapshot.usd_inr)
        return snapshot
    except Exception as exc:
        log.warning("Frankfurter USD/INR fetch failed: %s", exc)
        return FXSnapshot(usd_inr=0.0, fetched_at=datetime.now().isoformat())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. FRED — Federal Reserve Economic Data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",    # Federal Funds Effective Rate
    "us_10y_yield":   "DGS10",       # 10-Year Treasury Constant Maturity
    "dxy_index":      "DTWEXBGS",    # Trade Weighted U.S. Dollar Index
    "us_cpi_yoy":     "CPIAUCSL",    # Consumer Price Index (monthly)
}


def _fetch_fred_series(series_id: str, api_key: str) -> Optional[float]:
    """Fetch the latest observation from a FRED series."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 1,
    }
    try:
        resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        if obs and obs[0].get("value", ".") != ".":
            return float(obs[0]["value"])
    except Exception as exc:
        log.warning("FRED series %s fetch failed: %s", series_id, exc)
    return None


def fetch_fred_macro() -> FREDMacro:
    """
    Fetch key US macro indicators from FRED.
    Requires FRED_API_KEY env var (free at https://fred.stlouisfed.org/docs/api/api_key.html).
    """
    cached = _cache_get("fred_macro")
    if cached:
        return FREDMacro(**cached)

    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        log.info("FRED_API_KEY not set — skipping FRED macro fetch")
        return FREDMacro(fetched_at=datetime.now().isoformat())

    macro = FREDMacro(fetched_at=datetime.now().isoformat())
    for field_name, series_id in FRED_SERIES.items():
        val = _fetch_fred_series(series_id, api_key)
        if val is not None:
            setattr(macro, field_name, round(val, 4))

    _cache_set("fred_macro", macro.as_dict(), _FRED_CACHE_TTL)
    log.info("FRED macro: FFR=%.2f, 10Y=%.2f, DXY=%.2f",
             macro.fed_funds_rate or 0, macro.us_10y_yield or 0, macro.dxy_index or 0)
    return macro


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. INDIAN MUTUAL FUND API — MF Flow Data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Large-cap equity fund codes (Direct Growth) for flow proxy
# Verified against api.mfapi.in on 2026-03-28
_MF_FUND_CODES = [
    "120586",  # ICICI Prudential Large Cap Fund - Direct Plan - Growth
    "119018",  # HDFC Large Cap Fund - Direct Plan - Growth
    "118825",  # Mirae Asset Large Cap Fund - Direct Plan - Growth
    "118632",  # Nippon India Large Cap Fund - Direct Plan - Growth
    "120465",  # Axis Large Cap Fund - Direct Plan - Growth
]


def fetch_mf_flows() -> MFFlowData:
    """
    Fetch latest NAV data for top equity MFs as a proxy for institutional flows.
    Uses https://api.mfapi.in/mf/{scheme_code}/latest
    """
    cached = _cache_get("mf_flows")
    if cached:
        return MFFlowData(**cached)

    funds = []
    for code in _MF_FUND_CODES:
        # Use full endpoint (not /latest) to get at least 2 data points for change %
        url = f"https://api.mfapi.in/mf/{code}"
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            meta = data.get("meta", {})
            nav_data = data.get("data", [])
            if nav_data:
                latest = nav_data[0]
                prev = nav_data[1] if len(nav_data) > 1 else latest
                nav_now = float(latest.get("nav", 0))
                nav_prev = float(prev.get("nav", nav_now))
                chg_pct = ((nav_now - nav_prev) / nav_prev * 100) if nav_prev else 0
                funds.append({
                    "scheme_code": code,
                    "scheme_name": meta.get("scheme_name", "Unknown"),
                    "fund_house": meta.get("fund_house", "Unknown"),
                    "nav": nav_now,
                    "nav_date": latest.get("date", ""),
                    "nav_prev": nav_prev,
                    "chg_pct": round(chg_pct, 2),
                })
        except Exception as exc:
            log.warning("MF API fetch failed for code %s: %s", code, exc)

    result = MFFlowData(
        top_equity_funds=funds,
        fetched_at=datetime.now().isoformat(),
    )
    _cache_set("mf_flows", result.as_dict(), _MF_CACHE_TTL)
    log.info("Fetched NAV data for %d mutual funds", len(funds))
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPOSITE SNAPSHOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_market_intel_snapshot() -> MarketIntelSnapshot:
    """
    Build a full Market Intelligence snapshot.
    Called by pre-market agent and dashboard API.
    """
    holidays = fetch_holidays()
    holiday_today = is_holiday_today()
    nxt = next_holiday()
    fx = fetch_usd_inr()
    macro = fetch_fred_macro()
    mf = fetch_mf_flows()

    return MarketIntelSnapshot(
        holidays=[h.as_dict() for h in holidays[:10]],  # next 10 holidays
        is_holiday_today=holiday_today,
        next_holiday=nxt.as_dict() if nxt else None,
        fx=fx.as_dict(),
        macro=macro.as_dict(),
        mf_flows=mf.as_dict(),
        fetched_at=datetime.now().isoformat(),
    )
