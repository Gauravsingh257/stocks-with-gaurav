"""
services/scanners/registry.py — Scanner catalog.

A scanner is a small declarative record: identity + timeframe + an `evaluate`
function that, given one symbol's OHLC candles, returns a "hit" metric dict or
None. The runner fetches OHLC once for the whole universe and calls `evaluate`
for every scanner — so adding scanner #N is literally appending one Scanner here.

`evaluate` returns RAW per-symbol metrics only (deterministic indicator math).
Liquidity gating and quality scoring are applied centrally by runner.py +
scoring.py, so every scanner is gated and ranked consistently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from services.scanners.indicators import ema, supertrend, supertrend_flip, atr_wilder
from services.scanners import sector_rotation


@dataclass(frozen=True)
class Scanner:
    name: str                 # stable id, e.g. "supertrend_flip"
    label: str                # human label, e.g. "Supertrend Flip"
    timeframe: str            # display tf, e.g. "1D" / "1W"
    interval: str             # Kite historical interval: "day" / "week"
    lookback_days: int        # calendar days of history to request
    bars_per_year: int        # 252 for daily, 52 for weekly — sizes the 52w window
    # evaluate(candles, bars_per_year, symbol, ctx) -> metrics | None. `symbol`
    # and `ctx` are optional; per-symbol scanners ignore them, cross-sectional
    # scanners (e.g. sector rotation) use `ctx` produced by `prepare`.
    evaluate: Callable[..., dict | None]
    description: str = ""
    # Optional cross-sectional pre-pass: prepare(universe_symbols) -> ctx dict,
    # run ONCE per scan before the per-symbol loop. None for per-symbol scanners.
    prepare: Callable[[list[str]], dict] | None = None


# ── Scanner #1: Supertrend(10,3) red→green flip AND close > EMA10 ──────────────

def _supertrend_flip_evaluate(candles: list[dict], bars_per_year: int = 252,
                              symbol: str | None = None, ctx: dict | None = None) -> dict | None:
    """Condition: Supertrend(10,3) just flipped red→green (this/last bar) AND
    close is above EMA10. Returns metric dict (raw) or None.

    `bars_per_year` sizes the 52-week high/low window (252 daily, 52 weekly).
    `symbol`/`ctx` are accepted for signature uniformity and ignored here.
    Metrics include everything scoring.py needs (trend stack, momentum, 52w
    positioning, ATR, avg volume) so the runner can gate + rank uniformly.
    """
    n = len(candles)
    if n < 40:
        return None
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    vols = [float(c.get("volume") or 0) for c in candles]

    st = supertrend(highs, lows, closes, period=10, multiplier=3.0)
    if st is None:
        return None
    direction, line = st

    flip = supertrend_flip(direction, confirm_window=2)
    if flip is None:
        return None

    ema10 = ema(closes, 10)
    if ema10 is None:
        return None

    last_close = closes[-1]
    if not (last_close > ema10):
        return None

    # Enrichment metrics for gating + scoring
    ema20 = ema(closes[-80:], 20) if n >= 20 else None
    ema50 = ema(closes[-160:], 50) if n >= 50 else None
    atr14 = atr_wilder(highs, lows, closes, 14)[-1] if n > 14 else 0.0

    win = min(max(bars_per_year, 1), n)   # 1 year of bars (252 daily / 52 weekly)
    hi_win = highs[-win:]
    lo_win = lows[-win:]
    hi = max(hi_win) if hi_win else last_close
    lo = min(lo_win) if lo_win else last_close
    pos52 = ((last_close - lo) / (hi - lo) * 100.0) if hi > lo else 0.0
    from_high = ((last_close - hi) / hi * 100.0) if hi > 0 else 0.0

    avg_vol = (sum(vols[-20:]) / min(20, len(vols))) if vols else 0.0
    flip_idx = -1 if flip == "this_bar" else -2
    flip_vol = (vols[flip_idx] / avg_vol) if avg_vol > 0 else 0.0

    def _mom(bars: int) -> float:
        if n > bars and closes[-1 - bars] > 0:
            return (closes[-1] / closes[-1 - bars] - 1.0) * 100.0
        return 0.0

    stack = bool(ema20 and ema50 and last_close > ema20 > ema50)
    st_stop = line[-1]
    risk_pct = ((last_close - st_stop) / last_close * 100.0) if last_close > 0 else 0.0

    return {
        "close": round(last_close, 2),
        "ema10": round(ema10, 2),
        "stop": round(st_stop, 2),
        "flip": "this_bar" if flip == "this_bar" else "last_bar",
        "date": str(candles[-1].get("date"))[:10],
        "avg_vol": int(avg_vol),
        "flip_vol_x": round(flip_vol, 2),
        "dist_ema_pct": round((last_close - ema10) / ema10 * 100.0, 2),
        "risk_to_stop_pct": round(risk_pct, 2),
        "pos52": round(pos52, 1),
        "from_high_pct": round(from_high, 1),
        "mom_short_pct": round(_mom(5), 1),    # ~1 week (D) / ~5 wk (W)
        "mom_long_pct": round(_mom(20), 1),    # ~1 month (D) / ~20 wk (W)
        "ema20": round(ema20, 2) if ema20 else None,
        "ema50": round(ema50, 2) if ema50 else None,
        "atr_pct": round(atr14 / last_close * 100.0, 2) if last_close > 0 else 0.0,
        "stack": stack,
    }


SCANNERS: list[Scanner] = [
    Scanner(
        name="supertrend_flip",
        label="Supertrend Flip (above 10 EMA)",
        timeframe="1D",
        interval="day",
        lookback_days=300,
        bars_per_year=252,
        evaluate=_supertrend_flip_evaluate,
        description="Supertrend(10,3) just turned red→green and price is above the 10 EMA, daily.",
    ),
    Scanner(
        name="supertrend_flip",
        label="Supertrend Flip (above 10 EMA)",
        timeframe="1W",
        interval="week",
        lookback_days=1100,
        bars_per_year=52,
        evaluate=_supertrend_flip_evaluate,
        description="Supertrend(10,3) just turned red→green and price is above the 10 EMA, weekly.",
    ),
    # ── Scanner #2: Sector Rotation — strong stock in a leading sector ─────────────
    Scanner(
        name="sector_rotation",
        label="Sector Rotation",
        timeframe="1D",
        interval="day",
        lookback_days=400,
        bars_per_year=252,
        evaluate=sector_rotation.evaluate,
        prepare=sector_rotation.prepare,
        description="Stocks in sectors leading NIFTY (real NSE sector-index momentum) with live news catalyst, in a confirmed uptrend — daily.",
    ),
    Scanner(
        name="sector_rotation",
        label="Sector Rotation",
        timeframe="1W",
        interval="week",
        lookback_days=1400,
        bars_per_year=52,
        evaluate=sector_rotation.evaluate,
        prepare=sector_rotation.prepare,
        description="Stocks in sectors leading NIFTY (real NSE sector-index momentum) with live news catalyst, in a confirmed uptrend — weekly.",
    ),
]


def get_scanners() -> list[Scanner]:
    return list(SCANNERS)


def find_scanner(name: str, timeframe: str) -> Scanner | None:
    for s in SCANNERS:
        if s.name == name and s.timeframe == timeframe:
            return s
    return None
