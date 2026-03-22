"""
tests/test_chart_trade_levels.py — Swing-based ENTRY/SL/TARGET vs legacy spread.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _zigzag_ohlc(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    t = np.linspace(0, 3 * np.pi, n)
    close = 24000.0 + 90.0 * np.sin(t) + np.linspace(0, 100, n)
    high = close + 12.0
    low = close - 12.0
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    vol = np.ones(n) * 1_000_000.0
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


def test_trade_levels_bullish_ordering():
    from content_engine.services import chart_engine as ce

    df = _zigzag_ohlc()
    entry, sl, tgt = ce.trade_levels_from_swings(df, "order_block")
    assert sl < entry < tgt, (sl, entry, tgt)


def test_trade_levels_bearish_ordering():
    from content_engine.services import chart_engine as ce

    df = _zigzag_ohlc()
    entry, sl, tgt = ce.trade_levels_from_swings(df, "sr_flip")
    assert tgt < entry < sl, (tgt, entry, sl)


def test_overlay_fracs_monotonic_with_price():
    from content_engine.services import chart_engine as ce

    df = _zigzag_ohlc()
    entry, sl, tgt = ce.trade_levels_from_swings(df, "order_block")
    fr = ce.trade_levels_to_overlay_fracs(entry, sl, tgt, df)
    # Top of image = high price → lower frac; bullish: tgt > entry > sl in price
    assert fr["target_frac"] < fr["entry_frac"] < fr["sl_frac"]


def test_legacy_fallback_tiny_df():
    from content_engine.services import chart_engine as ce

    idx = pd.date_range("2024-01-01", periods=5, freq="5min")
    df = pd.DataFrame(
        {
            "Open": [100, 100, 100, 100, 100],
            "High": [101, 101, 101, 101, 101],
            "Low": [99, 99, 99, 99, 99],
            "Close": [100, 100, 100, 100, 100],
            "Volume": [1e6] * 5,
        },
        index=idx,
    )
    e, s, t = ce.trade_levels_from_swings(df, "order_block")
    assert s < e < t
