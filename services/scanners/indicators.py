"""
services/scanners/indicators.py — Pure, deterministic technical indicators.

No I/O, no globals, no randomness. Same candles in → identical output out.
These are the accuracy-critical core; they are unit-tested in
tests/test_scanner_indicators.py against known reference values.

Conventions:
  - Inputs are plain python lists of floats (highs, lows, closes), oldest first.
  - Supertrend uses Wilder's ATR (the same definition charting platforms use for
    the default "Supertrend 10,3"), so results match TradingView's indicator.
"""

from __future__ import annotations


def ema(values: list[float], period: int) -> float | None:
    """Exponential moving average of the full series; returns the LAST EMA value.

    Seeded with values[0] (standard recursive EMA). Returns None if there is not
    at least `period` data points (so a too-short series never yields a bogus EMA).
    """
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = float(values[0])
    for v in values[1:]:
        e = float(v) * k + e * (1.0 - k)
    return e


def ema_series(values: list[float], period: int) -> list[float | None]:
    """Full EMA series (same length as input); leading entries before the seed are None."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n == 0:
        return out
    k = 2.0 / (period + 1.0)
    e = float(values[0])
    out[0] = e
    for i in range(1, n):
        e = float(values[i]) * k + e * (1.0 - k)
        out[i] = e
    return out


def atr_wilder(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Wilder's ATR series (index-aligned with input; entries before `period` are 0.0)."""
    n = len(closes)
    atr = [0.0] * n
    if n <= period:
        return atr
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    # Seed = simple average of first `period` true ranges
    atr[period] = sum(tr[1:period + 1]) / period
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def supertrend(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[list[int], list[float]] | None:
    """Supertrend(period, multiplier).

    Returns (direction, line) where:
      direction[i] == +1  → uptrend (GREEN, supertrend below price)
      direction[i] == -1  → downtrend (RED, supertrend above price)
      line[i]            → the supertrend stop line value at bar i
    Indices before `period` are not meaningful (warm-up). Returns None if the
    series is too short to seed (need > period+2 bars).

    Matches the canonical TradingView "Supertrend" using Wilder ATR.
    """
    n = len(closes)
    if n <= period + 2:
        return None

    atr = atr_wilder(highs, lows, closes, period)

    upper = [0.0] * n
    lower = [0.0] * n
    final_upper = [0.0] * n
    final_lower = [0.0] * n
    line = [0.0] * n
    direction = [0] * n

    for i in range(period, n):
        hl2 = (highs[i] + lows[i]) / 2.0
        upper[i] = hl2 + multiplier * atr[i]
        lower[i] = hl2 - multiplier * atr[i]

    # Seed at first computable bar
    final_upper[period] = upper[period]
    final_lower[period] = lower[period]
    direction[period] = 1
    line[period] = lower[period]

    for i in range(period + 1, n):
        final_upper[i] = (
            upper[i]
            if (upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            lower[i]
            if (lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )
        if direction[i - 1] == 1:
            direction[i] = -1 if closes[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if closes[i] > final_upper[i] else -1
        line[i] = final_upper[i] if direction[i] == -1 else final_lower[i]

    return direction, line


def supertrend_flip(direction: list[int], confirm_window: int = 2) -> str | None:
    """Classify a fresh red→green flip on the most recent bars.

    Returns:
      "this_bar"  → direction flipped -1→+1 on the LAST bar
      "last_bar"  → flipped on the previous bar and is still +1 on the last bar
      None        → no fresh flip within the window (or still red)

    `confirm_window` = how many bars back a flip still counts as "fresh".
    """
    n = len(direction)
    if n < 3:
        return None
    if direction[-1] != 1:
        return None
    if direction[-1] == 1 and direction[-2] == -1:
        return "this_bar"
    if confirm_window >= 2 and direction[-1] == 1 and direction[-2] == 1 and direction[-3] == -1:
        return "last_bar"
    return None
