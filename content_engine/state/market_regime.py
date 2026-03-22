"""
content_engine/state/market_regime.py

Detects the current market regime (TRENDING / SIDEWAYS / VOLATILE)
using live NIFTY price data from Yahoo Finance.

Algorithm (no look-ahead, no TA-Lib dependency):
  1. Fetch last 20 daily closes for NIFTY 50 (^NSEI) from Yahoo Finance
  2. Compute:
     - ADX proxy: ratio of directional range to total range over 14 days
     - Choppiness: (High - Low of 14 days) / (sum of daily ranges)
  3. Classify:
     - ADX proxy > 0.55  → TRENDING
     - ADX proxy < 0.35  → SIDEWAYS
     - else              → MIXED (neutral)

Public API:
    get_regime() -> MarketRegime   (calls Yahoo Finance)
    get_regime_cached() -> MarketRegime  (returns cached if < 4 hours old)

MarketRegime has:
    .label    "TRENDING" | "SIDEWAYS" | "VOLATILE" | "MIXED" | "UNKNOWN"
    .bias     "BULLISH" | "BEARISH" | "NEUTRAL"
    .score    float 0–1 (directional strength)
    .preferred_strategies  list[str]  — strategy IDs that fit this regime
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("content_engine.market_regime")

_CACHE_FILE    = Path(__file__).parent / "regime_cache.json"
_CACHE_TTL_H   = 4          # re-detect every 4 hours

# ── Strategy → regime mapping ─────────────────────────────────────────────────
# Each regime gets a prioritised list of strategy IDs
_REGIME_STRATEGIES: dict[str, list[str]] = {
    "TRENDING": [
        "order_block",
        "trend_pullback",
        "consolidation_breakout",
        "momentum_scalp",
        "fvg",
        "breaker_block",
        "sr_flip",
    ],
    "SIDEWAYS": [
        "range_breakout",
        "vwap_strategy",
        "orb",
        "liquidity_grab",
        "trendline_break",
        "fvg",
        "order_block",
    ],
    "VOLATILE": [
        "liquidity_grab",
        "orb",
        "vwap_strategy",
        "momentum_scalp",
        "range_breakout",
        "order_block",
    ],
    "MIXED": [
        "order_block",
        "fvg",
        "vwap_strategy",
        "trend_pullback",
        "range_breakout",
        "liquidity_grab",
    ],
    "UNKNOWN": [
        "order_block",
        "fvg",
        "trend_pullback",
        "vwap_strategy",
        "range_breakout",
        "liquidity_grab",
        "orb",
        "momentum_scalp",
    ],
}


@dataclass
class MarketRegime:
    label:                str         = "UNKNOWN"
    bias:                 str         = "NEUTRAL"   # BULLISH | BEARISH | NEUTRAL
    score:                float       = 0.0
    nifty_price:          float       = 0.0
    nifty_change_pct:     float       = 0.0
    preferred_strategies: list[str]   = field(default_factory=list)
    detected_at:          str         = ""

    def __post_init__(self) -> None:
        if not self.preferred_strategies:
            self.preferred_strategies = _REGIME_STRATEGIES.get(self.label, _REGIME_STRATEGIES["UNKNOWN"])
        if not self.detected_at:
            self.detected_at = datetime.now().isoformat()

    def is_trending(self) -> bool:
        return self.label == "TRENDING"

    def is_sideways(self) -> bool:
        return self.label in ("SIDEWAYS", "MIXED")

    def summary(self) -> str:
        return (
            f"Regime: {self.label} ({self.bias}) | "
            f"Score: {self.score:.2f} | "
            f"NIFTY: {self.nifty_price:,.0f} ({self.nifty_change_pct:+.1f}%)"
        )


# ── Yahoo Finance fetch ────────────────────────────────────────────────────────

def _fetch_nifty_closes(n_days: int = 25) -> list[float]:
    """
    Fetch last `n_days` daily closes for NIFTY 50 from Yahoo Finance.
    Returns list in chronological order (oldest first).
    """
    url    = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
    params = {"interval": "1d", "range": f"{n_days + 5}d"}
    try:
        with httpx.Client(timeout=12) as client:
            resp = client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        closes = result["indicators"]["quote"][0].get("close", [])
        # Filter out None values
        closes = [c for c in closes if c is not None]
        return closes[-n_days:] if len(closes) >= n_days else closes
    except Exception as exc:
        log.warning("Yahoo Finance fetch failed: %s", exc)
        return []


def _fetch_nifty_ohlcv(n_days: int = 20) -> list[dict]:
    """Fetch OHLCV dicts for regime calculation."""
    url    = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
    params = {"interval": "1d", "range": f"{n_days + 5}d"}
    try:
        with httpx.Client(timeout=12) as client:
            resp = client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        quote  = result["indicators"]["quote"][0]
        length = min(
            len(quote.get("open",  [])),
            len(quote.get("high",  [])),
            len(quote.get("low",   [])),
            len(quote.get("close", [])),
        )
        bars = []
        for i in range(length):
            o = quote["open"][i]
            h = quote["high"][i]
            l = quote["low"][i]
            c = quote["close"][i]
            if None not in (o, h, l, c):
                bars.append({"open": o, "high": h, "low": l, "close": c})
        return bars[-n_days:]
    except Exception as exc:
        log.warning("OHLCV fetch failed: %s", exc)
        return []


# ── Regime detection ──────────────────────────────────────────────────────────

def _detect_from_bars(bars: list[dict]) -> MarketRegime:
    """
    Classify market regime from OHLCV bars.
    Uses a simple directional-strength proxy (no external TA library needed).
    """
    if len(bars) < 10:
        return MarketRegime(label="UNKNOWN")

    closes = [b["close"] for b in bars]
    highs  = [b["high"]  for b in bars]
    lows   = [b["low"]   for b in bars]

    n = len(bars)

    # ── Directional strength: net move / total range (ADX-like proxy) ──────
    period = min(14, n)
    net_move   = abs(closes[-1] - closes[-period])
    total_span = max(highs[-period:]) - min(lows[-period:])
    direction_score = net_move / total_span if total_span > 0 else 0.0

    # ── Choppiness: is price going anywhere? ──────────────────────────────
    daily_ranges  = [h - l for h, l in zip(highs, lows)]
    sum_ranges    = sum(daily_ranges[-period:])
    choppiness    = sum_ranges / total_span if total_span > 0 else 1.0

    # ── Bias: is net move up or down? ─────────────────────────────────────
    net_signed = closes[-1] - closes[-period]
    bias = "BULLISH" if net_signed > 0 else ("BEARISH" if net_signed < 0 else "NEUTRAL")

    # ── Today's price change ──────────────────────────────────────────────
    change_pct = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0.0

    # ── Classify ──────────────────────────────────────────────────────────
    if abs(change_pct) > 1.8:
        label = "VOLATILE"
    elif direction_score > 0.55 and choppiness < 1.8:
        label = "TRENDING"
    elif direction_score < 0.30 or choppiness > 2.5:
        label = "SIDEWAYS"
    else:
        label = "MIXED"

    return MarketRegime(
        label             = label,
        bias              = bias,
        score             = round(direction_score, 3),
        nifty_price       = round(closes[-1], 2),
        nifty_change_pct  = round(change_pct, 2),
        preferred_strategies = _REGIME_STRATEGIES.get(label, _REGIME_STRATEGIES["UNKNOWN"]),
    )


# ── Cache layer ───────────────────────────────────────────────────────────────

def _load_cache() -> MarketRegime | None:
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        ts   = datetime.fromisoformat(data["detected_at"])
        if datetime.now() - ts < timedelta(hours=_CACHE_TTL_H):
            return MarketRegime(**{k: v for k, v in data.items()})
    except Exception:
        pass
    return None


def _save_cache(regime: MarketRegime) -> None:
    try:
        payload = {
            "label":                regime.label,
            "bias":                 regime.bias,
            "score":                regime.score,
            "nifty_price":          regime.nifty_price,
            "nifty_change_pct":     regime.nifty_change_pct,
            "preferred_strategies": regime.preferred_strategies,
            "detected_at":          regime.detected_at,
        }
        _CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not save regime cache: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

def get_regime(force_refresh: bool = False) -> MarketRegime:
    """
    Detect the current market regime.
    Uses a 4-hour cache by default. Pass force_refresh=True to bypass.
    Returns MarketRegime(label="UNKNOWN") gracefully on any failure.
    """
    if not force_refresh:
        cached = _load_cache()
        if cached:
            log.info("Market regime (cached): %s", cached.summary())
            return cached

    bars = _fetch_nifty_ohlcv(n_days=20)
    if not bars:
        log.warning("No OHLCV data — defaulting to UNKNOWN regime")
        regime = MarketRegime(label="UNKNOWN")
    else:
        regime = _detect_from_bars(bars)

    _save_cache(regime)
    log.info("Market regime detected: %s", regime.summary())
    return regime
