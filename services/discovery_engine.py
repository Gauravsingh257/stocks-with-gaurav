"""
services/discovery_engine.py

LAYER 1 of the Opportunity Engine — pure momentum / volume / breakout discovery.

Goal: never let the Research UI show "0 ideas". This module scans the full NSE
universe with a fast OHLCV-only signal extractor and returns the top N candidates
ranked by a discovery score that combines:

    discovery_score = 0.30 * momentum_score
                    + 0.30 * volume_score
                    + 0.40 * breakout_score   (52W proximity + 50d high break)

It does NOT enforce SMC (no OB / FVG / BOS) so it always returns *something*
actionable. Higher tiers (LAYER 2 quality filter, LAYER 3 SMC) sit downstream
in `services/ranking_engine.py`. When those layers reject everything, the
Research routes fall back to discovery output to avoid an empty UI.

Author note: kept dependency-light and synchronous-friendly (uses thread pool)
because `data.ingestion.DataIngestion.fetch_historical` is itself blocking.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass
from typing import Iterable

import pandas as pd

from data.ingestion import DataIngestion

log = logging.getLogger("services.discovery_engine")


# ── Tunables (env-overridable) ────────────────────────────────────────────────
DISCOVERY_LOOKBACK_DAYS = int(os.getenv("DISCOVERY_LOOKBACK_DAYS", "260"))     # ~52w of trading days
DISCOVERY_MIN_CANDLES = int(os.getenv("DISCOVERY_MIN_CANDLES", "60"))          # need >=60d for 50d momentum
DISCOVERY_VOL_AVG_WINDOW = int(os.getenv("DISCOVERY_VOL_AVG_WINDOW", "20"))    # 20d avg volume
DISCOVERY_BREAKOUT_PROXIMITY = float(os.getenv("DISCOVERY_BREAKOUT_PROX", "5.0"))  # within 5% of 52w high = breakout proximity
DISCOVERY_TOP_K_DEFAULT = int(os.getenv("DISCOVERY_TOP_K", "100"))
DISCOVERY_CONCURRENCY = int(os.getenv("DISCOVERY_CONCURRENCY", "16"))


@dataclass(slots=True)
class DiscoveryCandidate:
    """A momentum/volume/breakout candidate from LAYER 1."""

    symbol: str
    cmp: float
    # raw components (already 0..100 scaled)
    momentum_5d_pct: float
    momentum_20d_pct: float
    momentum_50d_pct: float
    momentum_score: float       # 0..100 composite of the three windows
    volume_spike_pct: float     # last day vol vs 20d avg, e.g. +85%
    volume_score: float         # 0..100 (capped & scaled)
    pct_below_52w_high: float   # 0..100 = at high; 100 = far below
    is_at_52w_high: bool
    is_50d_breakout: bool
    breakout_score: float       # 0..100
    discovery_score: float      # final weighted 0..100
    avg_turnover_cr: float      # for liquidity gate downstream
    reason_tags: list[str]      # human-readable badges

    def to_dict(self) -> dict:
        return asdict(self)


# ── Per-symbol feature extraction ─────────────────────────────────────────────

def _safe_pct(curr: float, prev: float) -> float:
    if prev is None or prev == 0:
        return 0.0
    return (curr - prev) / prev * 100.0


def _scale(value: float, lo: float, hi: float) -> float:
    """Clamp+scale a value to 0..100 against a [lo, hi] band."""
    if hi == lo:
        return 50.0
    pct = (value - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, pct))


def _compute_features(symbol: str, df: pd.DataFrame) -> DiscoveryCandidate | None:
    """Convert OHLCV DataFrame into a DiscoveryCandidate. Returns None on bad data."""
    if df is None or df.empty or len(df) < DISCOVERY_MIN_CANDLES:
        return None

    # Normalize column case (Kite returns lowercase, yfinance returns Capitalized)
    cols = {c.lower(): c for c in df.columns}
    close_col = cols.get("close")
    high_col = cols.get("high")
    low_col = cols.get("low")
    vol_col = cols.get("volume")
    if not all((close_col, high_col, low_col, vol_col)):
        return None

    closes = df[close_col].astype(float).to_numpy()
    highs = df[high_col].astype(float).to_numpy()
    vols = df[vol_col].astype(float).to_numpy()

    cmp = float(closes[-1])
    if cmp <= 0:
        return None

    # ── Momentum (price returns over rolling windows) ──
    mom_5 = _safe_pct(cmp, float(closes[-6])) if len(closes) >= 6 else 0.0
    mom_20 = _safe_pct(cmp, float(closes[-21])) if len(closes) >= 21 else 0.0
    mom_50 = _safe_pct(cmp, float(closes[-51])) if len(closes) >= 51 else 0.0
    # weighted: shorter windows reflect "fresh" momentum
    momentum_blend = (0.5 * mom_5) + (0.3 * mom_20) + (0.2 * mom_50)
    # Map -10..+15% blend -> 0..100 score (most stocks live in this band)
    momentum_score = _scale(momentum_blend, -10.0, 15.0)

    # ── Volume spike (today vs 20d avg) ──
    vol_window = min(DISCOVERY_VOL_AVG_WINDOW, len(vols) - 1)
    avg_vol = float(vols[-vol_window - 1:-1].mean()) if vol_window > 0 else float(vols[:-1].mean())
    last_vol = float(vols[-1])
    vol_spike_pct = _safe_pct(last_vol, avg_vol) if avg_vol > 0 else 0.0
    # Map 0..200% spike -> 0..100 score (anything >200% is just "extreme")
    volume_score = _scale(vol_spike_pct, 0.0, 200.0)

    # ── Liquidity (₹Cr/day, used by LAYER 2 quality filter) ──
    avg_turnover_cr = (avg_vol * cmp) / 1e7 if avg_vol > 0 else 0.0

    # ── Breakout proximity (52w high) ──
    lookback = min(252, len(highs))
    hi52 = float(highs[-lookback:].max())
    pct_below_52w = max(0.0, (hi52 - cmp) / hi52 * 100.0) if hi52 > 0 else 100.0
    is_at_52w_high = pct_below_52w <= DISCOVERY_BREAKOUT_PROXIMITY

    # ── 50d breakout check ──
    if len(highs) >= 51:
        hi50 = float(highs[-51:-1].max())
        is_50d_breakout = cmp > hi50
    else:
        is_50d_breakout = False

    # Breakout score: 0% below high -> 100; 20% below -> 0
    proximity_score = _scale(20.0 - min(pct_below_52w, 20.0), 0.0, 20.0)
    breakout_bonus = (40.0 if is_at_52w_high else 0.0) + (30.0 if is_50d_breakout else 0.0)
    breakout_score = min(100.0, 0.5 * proximity_score + 0.5 * breakout_bonus)

    # ── Final composite ──
    discovery_score = round(
        0.30 * momentum_score + 0.30 * volume_score + 0.40 * breakout_score, 2
    )

    tags: list[str] = []
    if mom_5 >= 3:
        tags.append("MOMENTUM_5D")
    if mom_20 >= 8:
        tags.append("MOMENTUM_20D")
    if vol_spike_pct >= 50:
        tags.append("VOL_SPIKE")
    if is_at_52w_high:
        tags.append("AT_52W_HIGH")
    if is_50d_breakout:
        tags.append("50D_BREAKOUT")

    return DiscoveryCandidate(
        symbol=symbol,
        cmp=round(cmp, 2),
        momentum_5d_pct=round(mom_5, 2),
        momentum_20d_pct=round(mom_20, 2),
        momentum_50d_pct=round(mom_50, 2),
        momentum_score=round(momentum_score, 2),
        volume_spike_pct=round(vol_spike_pct, 2),
        volume_score=round(volume_score, 2),
        pct_below_52w_high=round(pct_below_52w, 2),
        is_at_52w_high=is_at_52w_high,
        is_50d_breakout=is_50d_breakout,
        breakout_score=round(breakout_score, 2),
        discovery_score=discovery_score,
        avg_turnover_cr=round(avg_turnover_cr, 2),
        reason_tags=tags,
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def scan_discovery(
    symbols: Iterable[str],
    *,
    top_k: int = DISCOVERY_TOP_K_DEFAULT,
    min_turnover_cr: float = 1.0,
    source: str = "yfinance",
) -> list[DiscoveryCandidate]:
    """
    LAYER 1 entry point.

    Args:
        symbols: NSE symbols (e.g. ["NSE:HDFCBANK", "NSE:RELIANCE", ...]).
        top_k: maximum number of candidates to return.
        min_turnover_cr: liquidity gate (₹Cr/day average). Default ₹1Cr.
        source: data source for `DataIngestion` ("kite" or "yfinance").

    Returns:
        List of `DiscoveryCandidate`, sorted by `discovery_score` descending.
        Empty list only if every fetch failed.
    """
    symbols = list(symbols)
    if not symbols:
        return []

    ingestion = DataIngestion(source=source)
    sem = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
    loop = asyncio.get_running_loop()

    async def _scan_one(sym: str) -> DiscoveryCandidate | None:
        async with sem:
            try:
                df = await loop.run_in_executor(
                    None,
                    lambda: ingestion.fetch_historical(
                        sym, interval="day", days=DISCOVERY_LOOKBACK_DAYS
                    ),
                )
                cand = _compute_features(sym, df)
                if cand is None:
                    return None
                if cand.avg_turnover_cr < min_turnover_cr:
                    return None
                return cand
            except Exception as exc:
                log.debug("discovery: %s failed: %s", sym, exc)
                return None

    results = await asyncio.gather(*(_scan_one(s) for s in symbols))
    candidates = [c for c in results if c is not None]
    candidates.sort(key=lambda c: c.discovery_score, reverse=True)

    log.info(
        "[discovery] scanned %d symbols, %d passed (min_turnover=₹%.1fCr), returning top %d",
        len(symbols), len(candidates), min_turnover_cr, min(top_k, len(candidates)),
    )
    return candidates[:top_k]


def synthesize_swing_levels(cand: DiscoveryCandidate) -> dict:
    """
    Convert a `DiscoveryCandidate` into an actionable LONG swing card payload.

    Used by the Research routes as a *guaranteed* fallback when the strict SMC
    pipeline returns zero ideas. ATR-style geometry: tight SL below CMP, two
    targets at 1.5R and 3R. Confidence is the discovery_score capped at 80
    so it never out-ranks a real SMC pick.
    """
    cmp = cand.cmp
    # Use 4% as a proxy for ATR-based stop in absence of intraday ATR.
    risk_pct = 0.04
    sl = round(cmp * (1 - risk_pct), 2)
    risk = max(cmp - sl, 0.01)
    t1 = round(cmp + 1.5 * risk, 2)
    t2 = round(cmp + 3.0 * risk, 2)

    return {
        "symbol": cand.symbol,
        "setup": "MOMENTUM_FALLBACK",
        "entry_price": cmp,
        "stop_loss": sl,
        "targets": [t1, t2],
        "risk_reward": round((t2 - cmp) / risk, 2),
        "confidence_score": min(80.0, cand.discovery_score),
        "scan_cmp": cmp,
        "entry_type": "MARKET",
        "expected_holding_period": "1-4 weeks",
        "discovery": cand.to_dict(),
        "reasoning": (
            f"Discovery: 5D {cand.momentum_5d_pct:+.1f}%, 20D {cand.momentum_20d_pct:+.1f}%, "
            f"vol spike {cand.volume_spike_pct:+.0f}%, "
            f"{cand.pct_below_52w_high:.1f}% below 52W high. "
            f"Tags: {', '.join(cand.reason_tags) or 'none'}."
        ),
        "technical_signals": {
            "momentum": (
                f"5D {cand.momentum_5d_pct:+.1f}% / 20D {cand.momentum_20d_pct:+.1f}% / "
                f"50D {cand.momentum_50d_pct:+.1f}%"
            ),
            "volume": f"Last day +{cand.volume_spike_pct:.0f}% vs 20D avg",
            "breakout": (
                f"{cand.pct_below_52w_high:.1f}% below 52W high"
                + (" — AT 52W HIGH" if cand.is_at_52w_high else "")
                + (" — 50D breakout" if cand.is_50d_breakout else "")
            ),
        },
    }
