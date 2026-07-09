"""
services/momentum_engine/metrics.py
====================================
Pure technical-metric computation from OHLCV candles (+ NIFTY for relative
strength). No I/O, no config — deterministic and unit-testable. Everything the
eligibility, entry, and ranking stages need is derived here ONCE per candidate.

`candles` = chronological list of dicts with keys: open, high, low, close,
volume, date. `nifty` = same shape for ^NSEI (optional; RS is None without it).
"""

from __future__ import annotations

import math
from typing import Any


def _closes(candles: list[dict]) -> list[float]:
    return [float(c["close"]) for c in candles]


def _sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n or n <= 0:
        return None
    return sum(vals[-n:]) / n


def _ema(vals: list[float], n: int) -> float | None:
    if len(vals) < n or n <= 0:
        return None
    k = 2.0 / (n + 1.0)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def _atr(candles: list[dict], n: int = 14) -> float | None:
    if len(candles) <= n:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"]); l = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def _ret(vals: list[float], n: int) -> float | None:
    if len(vals) <= n or vals[-1 - n] <= 0:
        return None
    return (vals[-1] / vals[-1 - n] - 1.0) * 100.0


def _r_squared(vals: list[float]) -> float:
    """R² of close vs time — a 0..1 measure of how *linear* (smooth) the advance
    is. High = orderly institutional trend; low = choppy/erratic."""
    n = len(vals)
    if n < 5:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in vals)
    sxy = sum((xs[i] - mx) * (vals[i] - my) for i in range(n))
    if sxx <= 0 or syy <= 0:
        return 0.0
    r = sxy / math.sqrt(sxx * syy)
    return max(0.0, min(1.0, r * r))


def compute_metrics(candles: list[dict], nifty: list[dict] | None = None,
                    tq_lookback: int = 40, base_lookback: int = 15) -> dict[str, Any] | None:
    """All momentum metrics for one symbol. Returns None if too few bars."""
    if not candles or len(candles) < 30:
        return None
    closes = _closes(candles)
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    vols = [float(c.get("volume") or 0) for c in candles]
    last = closes[-1]
    if last <= 0:
        return None

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    sma200 = _sma(closes, 200)
    atr = _atr(candles, 14)
    atr_pct = (atr / last * 100.0) if atr else None

    # Relative strength vs NIFTY over 20 bars.
    rs_20d = None
    r20 = _ret(closes, 20)
    if nifty and len(nifty) > 20:
        nret = _ret(_closes(nifty), 20)
        if r20 is not None and nret is not None:
            rs_20d = r20 - nret

    # Trend quality — linearity of the recent advance, only credited when rising.
    tq_slice = closes[-tq_lookback:] if len(closes) >= tq_lookback else closes
    rising = tq_slice[-1] > tq_slice[0]
    trend_quality = _r_squared(tq_slice) if rising else 0.0

    # Volume expansion (last bar vs 20-bar average).
    avg_vol = _sma(vols, 20) or 0.0
    volume_ratio = (vols[-1] / avg_vol) if avg_vol > 0 else 0.0
    turnover_cr = (avg_vol * last) / 1e7  # ₹Cr/day

    # Extension above EMA20 (anti-chase), in ATR units and %.
    extension_pct = ((last - ema20) / ema20 * 100.0) if ema20 else 0.0
    extension_atr = ((last - ema20) / atr) if (ema20 and atr and atr > 0) else 0.0

    # Consecutive up-days (climax proxy).
    up_days = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            up_days += 1
        else:
            break

    # ATR percentile within its own ~1y range (exhaustion proxy).
    atr_series = []
    for i in range(20, len(candles)):
        a = _atr(candles[: i + 1], 14)
        if a:
            atr_series.append(a / float(candles[i]["close"]) * 100.0)
    atr_percentile = None
    if atr_series and atr_pct is not None:
        below = sum(1 for x in atr_series if x <= atr_pct)
        atr_percentile = below / len(atr_series)

    # Opening gap of the latest bar.
    gap_pct = 0.0
    if len(candles) >= 2:
        prev_close = float(candles[-2]["close"])
        if prev_close > 0:
            gap_pct = (float(candles[-1]["open"]) - prev_close) / prev_close * 100.0

    # Consolidation base (recent range) for entry models.
    win = candles[-base_lookback:] if len(candles) >= base_lookback else candles
    base_high = max(float(c["high"]) for c in win)
    base_low = min(float(c["low"]) for c in win)
    base_atr_pct = ((base_high - base_low) / last * 100.0) if last > 0 else None

    # 52-week positioning.
    lb = min(252, len(candles))
    hi52 = max(highs[-lb:]); lo52 = min(lows[-lb:])
    pos52 = ((last - lo52) / (hi52 - lo52) * 100.0) if hi52 > lo52 else 0.0
    pivot_50 = max(highs[-min(50, len(highs)):-1]) if len(highs) > 2 else base_high

    return {
        "last": last, "ema20": ema20, "ema50": ema50, "sma200": sma200,
        "above_200dma": bool(sma200 and last > sma200),
        "stack": bool(ema20 and ema50 and last > ema20 > ema50),
        "atr": atr, "atr_pct": atr_pct,
        "rs_20d": rs_20d, "ret_20d": r20,
        "trend_quality": round(trend_quality, 3),
        "volume_ratio": round(volume_ratio, 2), "turnover_cr": round(turnover_cr, 2),
        "extension_pct": round(extension_pct, 2), "extension_atr": round(extension_atr, 2),
        "consecutive_up_days": up_days,
        "atr_percentile": None if atr_percentile is None else round(atr_percentile, 3),
        "gap_pct": round(gap_pct, 2),
        "base_high": base_high, "base_low": base_low,
        "base_atr_pct": None if base_atr_pct is None else round(base_atr_pct, 2),
        "pivot_50": pivot_50, "pos52": round(pos52, 1),
    }
