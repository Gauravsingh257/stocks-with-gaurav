"""Unit tests for services/scanners/indicators.py — the accuracy-critical core.

Pure deterministic checks: EMA against hand-computed values, Supertrend
invariants (direction ±1, line/price relationship), and the flip classifier.
"""

import pytest

from services.scanners.indicators import (
    ema,
    ema_series,
    atr_wilder,
    supertrend,
    supertrend_flip,
)


def test_ema_too_short_returns_none():
    assert ema([1, 2], 10) is None
    assert ema([], 5) is None


def test_ema_hand_computed():
    # EMA(3) seeded at values[0], k = 2/4 = 0.5
    vals = [10.0, 12.0, 14.0, 16.0]
    # e0=10; e1=12*.5+10*.5=11; e2=14*.5+11*.5=12.5; e3=16*.5+12.5*.5=14.25
    assert ema(vals, 3) == pytest.approx(14.25)


def test_ema_series_aligned_and_progressive():
    vals = [10.0, 12.0, 14.0, 16.0]
    s = ema_series(vals, 3)
    assert len(s) == 4
    assert s[0] == pytest.approx(10.0)
    assert s[-1] == pytest.approx(14.25)


def test_atr_wilder_basic_positive():
    highs = [float(i) + 1 for i in range(30)]
    lows = [float(i) for i in range(30)]
    closes = [float(i) + 0.5 for i in range(30)]
    atr = atr_wilder(highs, lows, closes, 14)
    assert len(atr) == 30
    assert all(a == 0.0 for a in atr[:14])
    assert atr[-1] > 0.0


def test_supertrend_too_short_returns_none():
    assert supertrend([1, 2, 3], [0, 1, 2], [1, 2, 3], 10, 3.0) is None


def test_supertrend_invariants_on_uptrend():
    # Steady uptrend → must end GREEN with the line BELOW price.
    n = 60
    closes = [100.0 + i for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    res = supertrend(highs, lows, closes, 10, 3.0)
    assert res is not None
    direction, line = res
    # All warmed-up directions are ±1
    assert all(d in (-1, 1) for d in direction[11:])
    # Uptrend ⇒ last direction green and stop below close
    assert direction[-1] == 1
    assert line[-1] < closes[-1]


def test_supertrend_invariants_on_downtrend():
    n = 60
    closes = [200.0 - i for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    res = supertrend(highs, lows, closes, 10, 3.0)
    assert res is not None
    direction, line = res
    assert direction[-1] == -1
    assert line[-1] > closes[-1]  # red ⇒ stop above price


def test_flip_this_bar():
    direction = [-1] * 10 + [1]            # flipped on the very last bar
    assert supertrend_flip(direction) == "this_bar"


def test_flip_last_bar():
    direction = [-1] * 10 + [1, 1]         # flipped one bar ago, still green
    assert supertrend_flip(direction) == "last_bar"


def test_flip_none_when_still_red():
    direction = [1] * 5 + [-1] * 5
    assert supertrend_flip(direction) is None


def test_flip_none_when_long_established_uptrend():
    direction = [-1, -1] + [1] * 10        # flip was long ago, not fresh
    assert supertrend_flip(direction) is None
