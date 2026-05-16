"""
PHASE G2-2 — Sector strength engine (real NSE sector-index momentum).

Read-only. Computes relative momentum of NSE sector indices vs NIFTY 50
using the same raw-yfinance approach `services/market_regime.py` already
uses successfully in production for ^NSEI.

Sectors without a reliable NSE index ticker return band="unknown" — we do
NOT fabricate a score (honest, per the no-synthetic-data rule). Cached in
Redis 6h. Nothing consumes this for filtering yet; G2-2 only shadow-logs
what a regime/sector gate WOULD do.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

log = logging.getLogger("services.sector_strength")

# SECTOR_MAP sector name  ->  yfinance NSE sector-index ticker.
# Sectors with no dependable NSE index are intentionally absent → "unknown".
SECTOR_INDEX_TICKER: dict[str, str] = {
    "Banking": "^NSEBANK",
    "Finance": "^CNXFIN",
    "Insurance": "^CNXFIN",   # no standalone NSE insurance index; proxy to FinServ
    "IT": "^CNXIT",
    "Pharma": "^CNXPHARMA",
    "Auto": "^CNXAUTO",
    "Metal": "^CNXMETAL",
    "Energy": "^CNXENERGY",
    "FMCG": "^CNXFMCG",
    "Realty": "^CNXREALTY",
    "Infra": "^CNXINFRA",
}
NIFTY_TICKER = "^NSEI"

_LOOKBACK_DAYS = 90
_LEAD_REL20_PCT = float(os.getenv("SECTOR_LEAD_REL20", "2.0"))   # +2% rel vs NIFTY (20d) = leading
_LAG_REL20_PCT = float(os.getenv("SECTOR_LAG_REL20", "-2.0"))
_REDIS_KEY_PREFIX = "sector:strength:"
_REDIS_TTL = int(os.getenv("SECTOR_STRENGTH_TTL_SEC", "21600"))  # 6h


@dataclass(slots=True)
class SectorScore:
    sector: str
    band: str            # "leading" | "neutral" | "lagging" | "unknown"
    rel_20d_pct: float | None
    rel_50d_pct: float | None
    is_leading: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "sector": self.sector,
            "band": self.band,
            "rel_20d_pct": None if self.rel_20d_pct is None else round(self.rel_20d_pct, 2),
            "rel_50d_pct": None if self.rel_50d_pct is None else round(self.rel_50d_pct, 2),
            "is_leading": self.is_leading,
        }


def _closes(ticker: str) -> list[float] | None:
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).history(period=f"{_LOOKBACK_DAYS}d")
        if df is None or df.empty or len(df) < 55:
            return None
        return [float(x) for x in df["Close"].tolist()]
    except Exception as exc:
        log.debug("sector close fetch failed %s: %s", ticker, exc)
        return None


def _ret(closes: list[float], n: int) -> float | None:
    if len(closes) <= n:
        return None
    a, b = closes[-(n + 1)], closes[-1]
    if a <= 0:
        return None
    return (b - a) / a * 100.0


def _redis_key() -> str:
    return f"{_REDIS_KEY_PREFIX}{date.today().isoformat()}"


def compute_sector_strength(*, refresh: bool = False) -> dict[str, Any]:
    """Relative-momentum band per sector vs NIFTY 50. Redis-cached 6h."""
    if not refresh:
        try:
            from dashboard.backend.cache import get as cache_get

            cached = cache_get(_redis_key())
            if isinstance(cached, dict) and cached.get("sectors"):
                cached["_served_from"] = "redis_cache"
                return cached
        except Exception:
            pass

    nifty = _closes(NIFTY_TICKER)
    n20 = _ret(nifty, 20) if nifty else None
    n50 = _ret(nifty, 50) if nifty else None

    sectors: dict[str, dict[str, Any]] = {}
    for sector, ticker in SECTOR_INDEX_TICKER.items():
        if sector in sectors:  # Finance/Insurance share ^CNXFIN — compute once
            sectors[sector] = sectors[sector]
        cl = _closes(ticker)
        if cl is None or n20 is None:
            sectors[sector] = SectorScore(sector, "unknown", None, None, False).to_dict()
            continue
        s20 = _ret(cl, 20)
        s50 = _ret(cl, 50)
        rel20 = None if (s20 is None or n20 is None) else s20 - n20
        rel50 = None if (s50 is None or n50 is None) else s50 - n50
        if rel20 is None:
            band, lead = "unknown", False
        elif rel20 >= _LEAD_REL20_PCT and (rel50 is None or rel50 >= 0):
            band, lead = "leading", True
        elif rel20 <= _LAG_REL20_PCT:
            band, lead = "lagging", False
        else:
            band, lead = "neutral", False
        sectors[sector] = SectorScore(sector, band, rel20, rel50, lead).to_dict()

    result = {
        "generated_for": date.today().isoformat(),
        "nifty_ret_20d_pct": None if n20 is None else round(n20, 2),
        "nifty_ret_50d_pct": None if n50 is None else round(n50, 2),
        "sectors": sectors,
        "leading": sorted(
            [s for s, v in sectors.items() if v["is_leading"]],
            key=lambda s: sectors[s]["rel_20d_pct"] or 0,
            reverse=True,
        ),
        "_built_at": time.time(),
        "_served_from": "fresh",
    }
    try:
        from dashboard.backend.cache import set as cache_set

        cache_set(_redis_key(), result, ttl_seconds=_REDIS_TTL)
    except Exception as exc:
        log.debug("sector strength cache write failed: %s", exc)
    return result


def classify_symbol(symbol: str, strength: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map a stock → its sector band. Unmapped/unknown is honest, not faked."""
    try:
        from engine.swing import get_sector

        sec = get_sector(symbol)
    except Exception:
        sec = "Others"
    if not sec or sec in ("Others", "Unknown"):
        return {"symbol": symbol, "sector": sec or "Unknown", "band": "unknown", "is_leading": False}
    st = strength or compute_sector_strength()
    sv = (st.get("sectors") or {}).get(sec)
    if not sv:
        return {"symbol": symbol, "sector": sec, "band": "unknown", "is_leading": False}
    return {
        "symbol": symbol,
        "sector": sec,
        "band": sv["band"],
        "is_leading": sv["is_leading"],
        "rel_20d_pct": sv.get("rel_20d_pct"),
    }
