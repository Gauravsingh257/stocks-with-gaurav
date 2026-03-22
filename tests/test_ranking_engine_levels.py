"""Tests for OHLC-backed research trade levels (no live broker required)."""

from __future__ import annotations

import os

import pandas as pd
import pytest

os.environ.setdefault("TECH_SCANNER_USE_HASH_ONLY", "1")

from services.research_levels import (
    atr_fallback_levels,
    build_longterm_trade_levels,
    build_swing_trade_levels,
    daily_candles_to_weekly,
    df_to_candles,
    entry_vs_close_sane,
    long_swing_geometry_ok,
)


def _synthetic_uptrend_days(n: int = 120, start: float = 100.0) -> list[dict]:
    rows = []
    p = start
    for i in range(n):
        o = p
        p = p * 1.0015
        h = p * 1.01
        lo = o * 0.995
        rows.append(
            {
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                "open": o,
                "high": h,
                "low": lo,
                "close": p,
                "volume": 1_000_000 + i * 100,
            }
        )
    return rows


def test_entry_vs_close_sane():
    # Default RESEARCH_MAX_ENTRY_VS_CLOSE_PCT = 0.30
    assert entry_vs_close_sane(100.0, 100.0) is True
    assert entry_vs_close_sane(135.0, 100.0) is False  # 35% > 30%
    assert entry_vs_close_sane(102.0, 100.0) is True   # 2%
    assert entry_vs_close_sane(72.0, 100.0) is True    # 28% — within 30%
    assert entry_vs_close_sane(69.0, 100.0) is False   # 31% > 30%


def test_df_to_candles_roundtrip():
    raw = _synthetic_uptrend_days(40)
    df = pd.DataFrame(raw)
    candles = df_to_candles(df)
    assert len(candles) == 40
    assert abs(candles[-1]["close"] - raw[-1]["close"]) < 1e-6


def test_daily_candles_to_weekly():
    raw = _synthetic_uptrend_days(200)
    w = daily_candles_to_weekly(raw)
    assert len(w) >= 10
    assert w[-1]["close"] > 0


def test_atr_fallback_produces_cmp_entry():
    raw = _synthetic_uptrend_days(80)
    out = atr_fallback_levels("NSE:TEST", raw)
    assert out is not None
    entry, sl, targets, setup = out
    assert setup.startswith("ATR_FALLBACK_")
    assert abs(entry - raw[-1]["close"]) < 0.02
    assert len(targets) == 2
    if "LONG" in setup:
        assert sl < entry < targets[1]


def test_atr_fallback_force_long_always_long_setup():
    raw = _synthetic_uptrend_days(80)
    out = atr_fallback_levels("NSE:TEST", raw, force_long=True)
    assert out is not None
    entry, sl, targets, _setup = out
    assert long_swing_geometry_ok(entry, sl, targets)


def test_long_swing_geometry_ok():
    assert long_swing_geometry_ok(100.0, 95.0, [110.0, 120.0]) is True
    assert long_swing_geometry_ok(100.0, 100.0, [110.0]) is False  # SL not below entry
    assert long_swing_geometry_ok(100.0, 95.0, [90.0]) is False   # target below entry (short-like)


def test_swing_short_smc_rejected_for_long_only_research(monkeypatch):
    """SHORT SMC must not appear as swing-long — exclude symbol (no fake long substitute)."""
    from services import research_levels as rl

    raw = _synthetic_uptrend_days(80)
    df = pd.DataFrame(raw)

    def fake_short(symbol, daily, weekly, nifty):
        return {
            "symbol": symbol,
            "direction": "SHORT",
            "entry": daily[-1]["close"],
            "sl": daily[-1]["close"] * 1.05,
            "target": daily[-1]["close"] * 0.9,
            "weekly_trend": "STRONG_BEAR",
            "daily_structure": "BEARISH_BOS",
        }

    monkeypatch.setattr(rl, "score_swing_candidate", fake_short)
    out = build_swing_trade_levels("NSE:TEST", df, [])
    assert out is None


def test_swing_atr_fallback_respects_env(monkeypatch):
    """RESEARCH_SWING_ATR_FALLBACK=0 → no ATR when SMC None. =1 (default) → ATR long."""
    from services import research_levels as rl

    raw = _synthetic_uptrend_days(80)
    df = pd.DataFrame(raw)
    monkeypatch.setattr(rl, "score_swing_candidate", lambda *a, **k: None)

    monkeypatch.setenv("RESEARCH_SWING_ATR_FALLBACK", "0")
    out = build_swing_trade_levels("NSE:TEST", df, [])
    assert out is None

    monkeypatch.setenv("RESEARCH_SWING_ATR_FALLBACK", "1")
    out2 = build_swing_trade_levels("NSE:TEST", df, [])
    assert out2 is not None
    entry, sl, targets, setup, smc_meta = out2
    assert smc_meta is None
    assert "ATR_FALLBACK_LONG" in setup
    assert long_swing_geometry_ok(entry, sl, targets)
    monkeypatch.delenv("RESEARCH_SWING_ATR_FALLBACK", raising=False)


def test_build_longterm_trade_levels():
    raw = _synthetic_uptrend_days(120)
    df = pd.DataFrame(raw)
    lt = build_longterm_trade_levels(df)
    assert lt is not None
    entry, stop, targets, long_target, zone, setup = lt
    assert setup.startswith("LONGTERM_")
    # Entry is a demand zone or pullback BELOW CMP (not necessarily at CMP)
    close = raw[-1]["close"]
    assert entry <= close, "Long-term entry must be at or below current price"
    assert stop < entry, "Stop must be below entry"
    assert long_target > close, "Long-term target must be above current price"
    assert len(zone) == 2
    assert targets[0] == long_target


def test_build_swing_trade_levels_with_monkeypatched_swing(monkeypatch):
    from services import research_levels as rl

    raw = _synthetic_uptrend_days(80)
    df = pd.DataFrame(raw)

    def fake_score(symbol, daily, weekly, nifty):
        return {
            "symbol": symbol,
            "direction": "LONG",
            "entry": daily[-1]["close"],
            "sl": daily[-1]["close"] * 0.95,
            "target": daily[-1]["close"] * 1.1,
            "weekly_trend": "BULLISH",
            "daily_structure": "BULLISH_BOS",
        }

    monkeypatch.setattr(rl, "score_swing_candidate", fake_score)
    out = rl.build_swing_trade_levels("NSE:TEST", df, [])
    assert out is not None
    entry, sl, targets, setup, smc_meta = out
    assert "SMC_SWING" in setup
    assert len(targets) == 2
    assert sl < entry
    assert smc_meta is not None
