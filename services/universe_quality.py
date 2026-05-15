"""
PHASE F2 — Quality Universe Engine.

Reduces the raw ~2,400-symbol NSE equity list down to an elite tradable
universe BEFORE any scanner logic runs. This is the missing selection layer
the Phase E audit + F8 baseline backtest identified: the SMC validation is
real, but applied to an unfiltered junk universe it produces no edge.

Design rules (per Phase F constraints):
  * Score ONLY on factors computable from real OHLCV. No synthetic /
    hash / placeholder inputs.
  * Delivery% and bid/ask spread are deliberately EXCLUDED — there is no
    real data source for them in this repo, and faking them would violate
    the no-synthetic-data rule. Documented, not synthesized.
  * Read-only and additive. Nothing consumes this by default; existing
    scanners are unchanged. A later gated phase can switch scanners to
    consume the filtered universe behind a flag.

Real factors scored (all from daily OHLCV):
  1. Liquidity        — avg daily traded value (close × volume), log-scaled
  2. Vol consistency  — inverse coefficient of variation of volume
                         (erratic volume = operator / manipulation risk)
  3. Movement         — ATR% (tradeable range; rejects dead charts)
  4. Trendability     — Kaufman efficiency ratio (trend vs chop)
  5. Price quality    — penny/operator floor

Output: Redis `universe:quality:{YYYY-MM-DD}` (24h TTL), ranked, tiered.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

log = logging.getLogger("services.universe_quality")

# ── Hard-reject thresholds (instant disqualify, score = 0) ────────────────
MIN_CANDLES = int(os.getenv("UQ_MIN_CANDLES", "60"))
MIN_PRICE = float(os.getenv("UQ_MIN_PRICE", "50"))
MIN_AVG_TRADED_VALUE_CR = float(os.getenv("UQ_MIN_ATV_CR", "1.0"))   # ₹1 Cr/day floor
MIN_ATR_PCT = float(os.getenv("UQ_MIN_ATR_PCT", "0.8"))             # dead-chart floor

# ── Qualification gate + tiers (0-100 composite) ──────────────────────────
QUALIFY_GATE = float(os.getenv("UQ_QUALIFY_GATE", "50"))
TIER_ELITE = 80.0
TIER_STRONG = 68.0
TIER_GOOD = 56.0
TIER_WATCHLIST = 46.0

REDIS_KEY_PREFIX = "universe:quality:"
REDIS_TTL_SEC = int(os.getenv("UQ_REDIS_TTL_SEC", "86400"))  # 24h


@dataclass(slots=True)
class TradabilityScore:
    symbol: str
    score: float
    tier: str
    qualified: bool
    reject_reason: str | None = None
    sub_scores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 1),
            "tier": self.tier,
            "qualified": self.qualified,
            "reject_reason": self.reject_reason,
            "sub_scores": {k: round(v, 3) for k, v in self.sub_scores.items()},
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
        }


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _tier_for(score: float) -> str:
    if score >= TIER_ELITE:
        return "Elite"
    if score >= TIER_STRONG:
        return "Strong"
    if score >= TIER_GOOD:
        return "Good"
    if score >= TIER_WATCHLIST:
        return "Watchlist"
    return "Reject"


def _normalize_df(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or getattr(df, "empty", True):
        return None
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    need = {"high", "low", "close", "volume"}
    if not need.issubset(set(out.columns)):
        return None
    for c in ("high", "low", "close", "volume"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["high", "low", "close"])
    return out if len(out) >= MIN_CANDLES else None


def _efficiency_ratio(close: pd.Series, period: int = 30) -> float:
    """Kaufman Efficiency Ratio: |net move| / sum(|daily moves|) over `period`.

    1.0 = perfectly trending, ~0 = pure chop. A real, well-known measure of
    trendability — distinguishes names that actually go somewhere from
    names that thrash sideways (untradeable for swing/positional).
    """
    if len(close) < period + 1:
        period = max(5, len(close) - 1)
    window = close.iloc[-(period + 1):]
    net = abs(float(window.iloc[-1]) - float(window.iloc[0]))
    path = float(window.diff().abs().sum())
    if path <= 0:
        return 0.0
    return _clamp01(net / path)


def _atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    last = float(close.iloc[-1])
    if pd.isna(atr) or last <= 0:
        return 0.0
    return float(atr) / last * 100.0


def _score_liquidity(atv_cr: float) -> float:
    """Avg daily traded value in ₹ Cr → 0-1. ₹1Cr≈0.15, ₹10Cr≈0.6, ₹50Cr+≈1.0."""
    if atv_cr <= 0:
        return 0.0
    import math
    # log10 scaled: 1Cr→0.0, 10Cr→0.5, 100Cr→1.0
    return _clamp01(math.log10(max(atv_cr, 0.1)) / 2.0)


def _score_vol_consistency(cv: float) -> float:
    """Inverse coefficient of variation of volume. Low CV = steady,
    institutionally-participated. High CV = erratic/operator. CV 0.5≈0.9,
    CV 1.0≈0.6, CV 1.8≈0.15."""
    return _clamp01(1.15 - cv / 1.8)


def _score_movement(atr_pct: float) -> float:
    """Tradeable range. Bell-ish: ~2.5% ideal, <0.8% dead, >8% too wild."""
    if atr_pct < MIN_ATR_PCT:
        return 0.0
    if atr_pct <= 2.5:
        return _clamp01(0.4 + 0.6 * (atr_pct - MIN_ATR_PCT) / (2.5 - MIN_ATR_PCT))
    # taper above 2.5% — high vol is tradeable but riskier
    return _clamp01(1.0 - (atr_pct - 2.5) / 9.0)


def _score_price(price: float) -> float:
    if price < MIN_PRICE:
        return 0.0
    if price < 100:
        return 0.5
    if price < 250:
        return 0.8
    return 1.0


# Composite weights — liquidity dominates (it's the strongest real signal
# of institutional participation and the best junk filter).
_W = {
    "liquidity": 0.32,
    "vol_consistency": 0.20,
    "movement": 0.20,
    "trendability": 0.20,
    "price_quality": 0.08,
}


def score_tradability(symbol: str, daily_df: pd.DataFrame | None) -> TradabilityScore:
    """Score one symbol's tradability from real daily OHLCV. Pure function."""
    df = _normalize_df(daily_df)
    if df is None:
        return TradabilityScore(symbol, 0.0, "Reject", False, "insufficient_ohlc")

    close = df["close"]
    vol = df["volume"].fillna(0.0)
    price = float(close.iloc[-1])
    recent = df.tail(20)
    avg_vol = float(recent["volume"].mean() or 0.0)
    avg_close = float(recent["close"].mean() or price)
    atv_cr = (avg_vol * avg_close) / 1e7  # ₹ crore/day
    vol_mean = float(vol.tail(20).mean() or 0.0)
    vol_std = float(vol.tail(20).std() or 0.0)
    vol_cv = (vol_std / vol_mean) if vol_mean > 0 else 9.9
    atrp = _atr_pct(df)
    er = _efficiency_ratio(close, 30)

    metrics = {
        "price": price,
        "avg_traded_value_cr": atv_cr,
        "volume_cv": vol_cv,
        "atr_pct": atrp,
        "efficiency_ratio": er,
        "candles": float(len(df)),
    }

    # ── Hard rejects ──────────────────────────────────────────────────────
    if price < MIN_PRICE:
        return TradabilityScore(symbol, 0.0, "Reject", False, f"price<{MIN_PRICE:.0f}", {}, metrics)
    if atv_cr < MIN_AVG_TRADED_VALUE_CR:
        return TradabilityScore(symbol, 0.0, "Reject", False, f"illiquid<{MIN_AVG_TRADED_VALUE_CR:.1f}Cr", {}, metrics)
    if atrp < MIN_ATR_PCT:
        return TradabilityScore(symbol, 0.0, "Reject", False, f"dead_chart_atr<{MIN_ATR_PCT}%", {}, metrics)

    sub = {
        "liquidity": _score_liquidity(atv_cr),
        "vol_consistency": _score_vol_consistency(vol_cv),
        "movement": _score_movement(atrp),
        "trendability": er,
        "price_quality": _score_price(price),
    }
    composite = sum(sub[k] * _W[k] for k in _W) * 100.0
    tier = _tier_for(composite)
    return TradabilityScore(
        symbol=symbol,
        score=composite,
        tier=tier,
        qualified=composite >= QUALIFY_GATE,
        reject_reason=None if composite >= QUALIFY_GATE else "below_quality_gate",
        sub_scores=sub,
        metrics=metrics,
    )


async def build_quality_universe(
    symbols: list[str],
    *,
    source: str = "yfinance",
    days: int = 120,
    concurrency: int | None = None,
) -> dict[str, Any]:
    """Concurrently fetch daily OHLC + score every symbol. Returns ranked
    qualified list + funnel stats. Pure analysis — no production mutation."""
    from data.ingestion import DataIngestion

    ingestion = DataIngestion(source=source)
    conc = concurrency or max(1, int(os.getenv("UQ_FETCH_CONCURRENCY", "8")))
    sem = asyncio.Semaphore(conc)
    loop = asyncio.get_running_loop()

    async def _one(sym: str) -> TradabilityScore:
        async with sem:
            try:
                df = await loop.run_in_executor(
                    None,
                    lambda: ingestion.fetch_historical(sym, interval="day", days=days),
                )
            except Exception as exc:
                log.debug("UQ fetch failed %s: %s", sym, exc)
                df = None
            return score_tradability(sym, df)

    scores = await asyncio.gather(*(_one(s) for s in symbols))
    qualified = sorted(
        (s for s in scores if s.qualified),
        key=lambda s: s.score,
        reverse=True,
    )
    tier_counts: dict[str, int] = {}
    reject_reasons: dict[str, int] = {}
    for s in scores:
        tier_counts[s.tier] = tier_counts.get(s.tier, 0) + 1
        if not s.qualified and s.reject_reason:
            reject_reasons[s.reject_reason] = reject_reasons.get(s.reject_reason, 0) + 1

    return {
        "generated_for": date.today().isoformat(),
        "input_count": len(symbols),
        "qualified_count": len(qualified),
        "qualify_gate": QUALIFY_GATE,
        "tier_counts": tier_counts,
        "reject_reasons": reject_reasons,
        "qualified": [s.to_dict() for s in qualified],
    }


def _redis_key() -> str:
    return f"{REDIS_KEY_PREFIX}{date.today().isoformat()}"


async def get_quality_universe(
    target_size: int = 1800,
    *,
    refresh: bool = False,
    source: str = "yfinance",
) -> dict[str, Any]:
    """Redis read-through (24h). Builds from the live NSE universe on miss.

    NOTE: building is heavy (N yfinance fetches). Intended to run as a
    daily background job; the endpoint accepts a bounded `limit` for
    interactive verification without timeouts.
    """
    from dashboard.backend.cache import get as cache_get
    from dashboard.backend.cache import set as cache_set

    key = _redis_key()
    if not refresh:
        cached = cache_get(key)
        if isinstance(cached, dict) and cached.get("qualified") is not None:
            cached["_served_from"] = "redis_cache"
            return cached

    from services.universe_manager import load_nse_universe

    universe = load_nse_universe(target_size)
    result = await build_quality_universe(universe.symbols, source=source)
    result["_served_from"] = "fresh_build"
    try:
        cache_set(key, result, ttl_seconds=REDIS_TTL_SEC)
    except Exception as exc:
        log.warning("UQ cache write failed: %s", exc)
    return result
