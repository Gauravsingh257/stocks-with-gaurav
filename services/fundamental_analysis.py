from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

log = logging.getLogger("services.fundamental_analysis")

# ---------------------------------------------------------------------------
# Disk cache so we don't hammer yfinance on every scan
# ---------------------------------------------------------------------------
_CACHE_DIR = Path(os.getenv("FUNDAMENTALS_CACHE_DIR", "/tmp/fundamentals_cache"))
_CACHE_TTL = int(os.getenv("FUNDAMENTALS_CACHE_TTL_H", "24")) * 3600  # 24 h default


def _cache_path(symbol: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{symbol}.json"


def _load_cache(symbol: str) -> dict | None:
    p = _cache_path(symbol)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        if time.time() - data.get("_ts", 0) < _CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _save_cache(symbol: str, payload: dict) -> None:
    try:
        payload["_ts"] = time.time()
        _cache_path(symbol).write_text(json.dumps(payload))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hash fallback (used when yfinance fails or FUND_USE_HASH_ONLY=1)
# ---------------------------------------------------------------------------

def _stable_unit(symbol: str, salt: str) -> float:
    raw = sha256(f"{symbol}:{salt}".encode()).hexdigest()
    return int(raw[8:16], 16) / 0xFFFFFFFF


def _hash_snapshot(symbol: str) -> "FundamentalSnapshot":
    earnings = 0.35 + (_stable_unit(symbol, "earnings") * 0.65)
    revenue = 0.35 + (_stable_unit(symbol, "revenue") * 0.65)
    sector = 0.40 + (_stable_unit(symbol, "sector") * 0.60)
    inst = 0.35 + (_stable_unit(symbol, "inst") * 0.65)
    promoter = 0.45 + (_stable_unit(symbol, "promoter") * 0.55)
    delivery = 0.35 + (_stable_unit(symbol, "delivery") * 0.65)
    roce = 0.35 + (_stable_unit(symbol, "roce") * 0.65)
    roe = 0.35 + (_stable_unit(symbol, "roe") * 0.65)
    debt_quality = 0.35 + (_stable_unit(symbol, "debt") * 0.65)
    mgmt = 0.35 + (_stable_unit(symbol, "mgmt") * 0.65)
    lt_growth = _weighted_score([(earnings, 0.4), (revenue, 0.3), (sector, 0.3)])
    score = _weighted_score([
        (earnings, 0.14), (revenue, 0.14), (sector, 0.10), (inst, 0.10),
        (promoter, 0.08), (delivery, 0.08), (roce, 0.12), (roe, 0.10),
        (debt_quality, 0.08), (mgmt, 0.06),
    ])
    return FundamentalSnapshot(
        symbol=symbol, earnings_growth=earnings, revenue_growth=revenue,
        sector_strength=sector, institutional_accumulation=inst,
        promoter_holdings=promoter, delivery_volume_trend=delivery,
        roce=roce, roe=roe, debt_quality=debt_quality, management_quality=mgmt,
        long_term_growth=lt_growth, fundamental_score=score,
        # raw fields empty for hash snapshot
        raw_pe=None, raw_pb=None, raw_roe_pct=None, raw_roce_pct=None,
        raw_revenue_growth_pct=None, raw_earnings_growth_pct=None,
        raw_debt_equity=None, raw_promoter_pct=None,
        raw_institutional_pct=None, raw_market_cap_cr=None,
        data_source="hash",
    )


# ---------------------------------------------------------------------------
# Clamp / normalise helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _clamp01(v: float) -> float:
    return _clamp(v, 0.0, 1.0)


def _weighted_score(parts: list[tuple[float, float]]) -> float:
    total_w = sum(w for _, w in parts) or 1.0
    return sum(v * w for v, w in parts) / total_w


def _norm_pe(pe: float | None) -> float:
    """High PE = lower value score (growth already priced in)."""
    if pe is None or pe <= 0:
        return 0.50
    # PE 8 → 0.85, PE 25 → 0.55, PE 60+ → 0.20
    return _clamp01(1.05 - (pe / 70.0))


def _norm_pb(pb: float | None) -> float:
    if pb is None or pb <= 0:
        return 0.50
    # PB 1 → 0.90, PB 5 → 0.55, PB 15 → 0.20
    return _clamp01(1.00 - (pb / 18.0))


def _norm_roe(roe_pct: float | None) -> float:
    """ROE: 25%+ = great, <8% = weak."""
    if roe_pct is None:
        return 0.50
    return _clamp01((roe_pct - 5.0) / 30.0)


def _norm_revenue_growth(g: float | None) -> float:
    """yfinance revenueGrowth is a ratio (0.12 = 12%). 30%+ = great."""
    if g is None:
        return 0.50
    pct = g * 100
    return _clamp01((pct + 5.0) / 40.0)


def _norm_earnings_growth(g: float | None) -> float:
    if g is None:
        return 0.50
    pct = g * 100
    return _clamp01((pct + 5.0) / 50.0)


def _norm_debt_equity(de: float | None) -> float:
    """Lower D/E = better quality debt. 0 = 1.0, 2.0 = 0.5, 4+ = 0.0."""
    if de is None:
        return 0.55
    return _clamp01(1.0 - (de / 4.0))


def _norm_promoter(pct: float | None) -> float:
    """50%+ is high promoter; 25% is low."""
    if pct is None:
        return 0.55
    return _clamp01(pct / 75.0)


def _norm_institutional(pct: float | None) -> float:
    """20%+ institutional = strong accumulation signal."""
    if pct is None:
        return 0.50
    return _clamp01(pct / 40.0)


# ---------------------------------------------------------------------------
# yfinance fetch
# ---------------------------------------------------------------------------

def _yf_symbol(nse_symbol: str) -> str:
    """Convert NSE:RELIANCE or RELIANCE to RELIANCE.NS."""
    s = nse_symbol.replace("NSE:", "").replace("BSE:", "").strip()
    if not s.endswith(".NS") and not s.endswith(".BO"):
        s = s + ".NS"
    return s


def _fetch_yf_info(symbol: str) -> dict:
    """Fetch .info from yfinance with a lightweight timeout guard."""
    try:
        import yfinance as yf  # noqa: PLC0415
        ticker = yf.Ticker(_yf_symbol(symbol))
        info = ticker.info or {}
        return info
    except Exception as exc:
        log.debug("yfinance .info failed for %s: %s", symbol, exc)
        return {}


def _build_snapshot_from_info(symbol: str, info: dict) -> "FundamentalSnapshot":
    """Map yfinance info dict → FundamentalSnapshot with normalised 0-1 scores."""
    pe = info.get("trailingPE") or info.get("forwardPE")
    pb = info.get("priceToBook")
    roe_raw = info.get("returnOnEquity")  # ratio, e.g. 0.22 = 22%
    roe_pct = (roe_raw * 100) if roe_raw is not None else None
    roce_pct = roe_pct  # yfinance doesn't expose ROCE; use ROE as proxy

    rev_g = info.get("revenueGrowth")
    earn_g = info.get("earningsGrowth")
    de = info.get("debtToEquity")  # can be in %, e.g. 45.3 means 0.453
    # yfinance sometimes gives debtToEquity as 45.3 (%) instead of 0.453
    if de is not None and de > 10:
        de = de / 100.0

    promoter_pct = (info.get("heldPercentInsiders") or 0.0) * 100
    inst_pct = (info.get("heldPercentInstitutions") or 0.0) * 100
    mktcap = info.get("marketCap")
    mktcap_cr = round(mktcap / 1e7, 1) if mktcap else None  # INR → crores

    earnings = _norm_earnings_growth(earn_g)
    revenue = _norm_revenue_growth(rev_g)
    roe_score = _norm_roe(roe_pct)
    roce_score = roe_score  # same proxy
    debt_score = _norm_debt_equity(de)
    inst_score = _norm_institutional(inst_pct)
    promoter_score = _norm_promoter(promoter_pct)

    # sector_strength: blend PB and PE quality score
    pb_score = _norm_pb(pb)
    pe_score = _norm_pe(pe)
    sector = _clamp01((pb_score + pe_score) / 2)

    # delivery_volume_trend & management_quality: use available proxies
    delivery = _clamp01((earnings + revenue) / 2)
    mgmt = _clamp01((roe_score + debt_score) / 2)
    lt_growth = _weighted_score([(earnings, 0.4), (revenue, 0.3), (sector, 0.3)])

    score = _weighted_score([
        (earnings, 0.14), (revenue, 0.14), (sector, 0.10), (inst_score, 0.10),
        (promoter_score, 0.08), (delivery, 0.08), (roce_score, 0.12),
        (roe_score, 0.10), (debt_score, 0.08), (mgmt, 0.06),
    ])

    return FundamentalSnapshot(
        symbol=symbol,
        earnings_growth=earnings,
        revenue_growth=revenue,
        sector_strength=sector,
        institutional_accumulation=inst_score,
        promoter_holdings=promoter_score,
        delivery_volume_trend=delivery,
        roce=roce_score,
        roe=roe_score,
        debt_quality=debt_score,
        management_quality=mgmt,
        long_term_growth=lt_growth,
        fundamental_score=score,
        # real values for display
        raw_pe=round(pe, 1) if pe else None,
        raw_pb=round(pb, 2) if pb else None,
        raw_roe_pct=round(roe_pct, 1) if roe_pct is not None else None,
        raw_roce_pct=round(roce_pct, 1) if roce_pct is not None else None,
        raw_revenue_growth_pct=round(rev_g * 100, 1) if rev_g is not None else None,
        raw_earnings_growth_pct=round(earn_g * 100, 1) if earn_g is not None else None,
        raw_debt_equity=round(de, 2) if de is not None else None,
        raw_promoter_pct=round(promoter_pct, 1) if promoter_pct else None,
        raw_institutional_pct=round(inst_pct, 1) if inst_pct else None,
        raw_market_cap_cr=mktcap_cr,
        sector=info.get("sector"),
        industry=info.get("industry"),
        data_source="yfinance",
    )


def _fetch_snapshot(symbol: str) -> "FundamentalSnapshot":
    cached = _load_cache(symbol)
    if cached:
        try:
            return FundamentalSnapshot(**{k: v for k, v in cached.items() if k != "_ts"})
        except Exception:
            pass

    info = _fetch_yf_info(symbol)
    has_data = bool(info.get("trailingPE") or info.get("returnOnEquity") or info.get("revenueGrowth"))

    if has_data:
        snap = _build_snapshot_from_info(symbol, info)
    else:
        log.debug("yfinance returned no fundamentals for %s — using hash fallback", symbol)
        snap = _hash_snapshot(symbol)

    _save_cache(symbol, {
        "symbol": snap.symbol, "earnings_growth": snap.earnings_growth,
        "revenue_growth": snap.revenue_growth, "sector_strength": snap.sector_strength,
        "institutional_accumulation": snap.institutional_accumulation,
        "promoter_holdings": snap.promoter_holdings, "delivery_volume_trend": snap.delivery_volume_trend,
        "roce": snap.roce, "roe": snap.roe, "debt_quality": snap.debt_quality,
        "management_quality": snap.management_quality, "long_term_growth": snap.long_term_growth,
        "fundamental_score": snap.fundamental_score,
        "raw_pe": snap.raw_pe, "raw_pb": snap.raw_pb,
        "raw_roe_pct": snap.raw_roe_pct, "raw_roce_pct": snap.raw_roce_pct,
        "raw_revenue_growth_pct": snap.raw_revenue_growth_pct,
        "raw_earnings_growth_pct": snap.raw_earnings_growth_pct,
        "raw_debt_equity": snap.raw_debt_equity, "raw_promoter_pct": snap.raw_promoter_pct,
        "raw_institutional_pct": snap.raw_institutional_pct,
        "raw_market_cap_cr": snap.raw_market_cap_cr, "data_source": snap.data_source,
    })
    return snap


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FundamentalSnapshot:
    symbol: str
    earnings_growth: float
    revenue_growth: float
    sector_strength: float
    institutional_accumulation: float
    promoter_holdings: float
    delivery_volume_trend: float
    roce: float
    roe: float
    debt_quality: float
    management_quality: float
    long_term_growth: float
    fundamental_score: float
    # Real values (None when only hash is available)
    raw_pe: float | None = None
    raw_pb: float | None = None
    raw_roe_pct: float | None = None
    raw_roce_pct: float | None = None
    raw_revenue_growth_pct: float | None = None
    raw_earnings_growth_pct: float | None = None
    raw_debt_equity: float | None = None
    raw_promoter_pct: float | None = None
    raw_institutional_pct: float | None = None
    raw_market_cap_cr: float | None = None
    sector: str | None = None
    industry: str | None = None
    data_source: str = "hash"

    def as_factors(self) -> dict[str, Any]:
        return {
            "earnings_growth": round(self.earnings_growth, 3),
            "revenue_growth": round(self.revenue_growth, 3),
            "sector_strength": round(self.sector_strength, 3),
            "institutional_accumulation": round(self.institutional_accumulation, 3),
            "promoter_holdings": round(self.promoter_holdings, 3),
            "delivery_volume_trend": round(self.delivery_volume_trend, 3),
            "roce": round(self.roce, 3),
            "roe": round(self.roe, 3),
            "debt_quality": round(self.debt_quality, 3),
            "management_quality": round(self.management_quality, 3),
            "long_term_growth": round(self.long_term_growth, 3),
            "fundamental_score": round(self.fundamental_score, 3),
        }


# ---------------------------------------------------------------------------
# Public async API (unchanged signature — drop-in replacement)
# ---------------------------------------------------------------------------

_FETCH_CONCURRENCY = int(os.getenv("FUND_FETCH_CONCURRENCY", "8"))


async def analyze_fundamentals(symbols: list[str]) -> dict[str, FundamentalSnapshot]:
    """
    Fetch real fundamentals from yfinance for each symbol in parallel.
    Falls back to deterministic hash scores if yfinance has no data or
    FUND_USE_HASH_ONLY=1 is set (useful for CI/testing).
    Results are disk-cached for FUNDAMENTALS_CACHE_TTL_H hours (default 24h).
    """
    if os.getenv("FUND_USE_HASH_ONLY", "0") == "1":
        return {s: _hash_snapshot(s) for s in symbols}

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
    loop = asyncio.get_running_loop()

    async def _fetch(sym: str) -> tuple[str, FundamentalSnapshot]:
        async with sem:
            snap = await loop.run_in_executor(None, _fetch_snapshot, sym)
            return sym, snap

    results = await asyncio.gather(*[_fetch(s) for s in symbols], return_exceptions=True)
    output: dict[str, FundamentalSnapshot] = {}
    for r in results:
        if isinstance(r, Exception):
            log.warning("fundamentals gather error: %s", r)
            continue
        sym, snap = r
        output[sym] = snap

    # fill any gaps with hash fallback
    for s in symbols:
        if s not in output:
            output[s] = _hash_snapshot(s)
    return output
