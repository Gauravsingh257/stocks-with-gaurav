"""Regression: an anchored entry must not keep a distant structural stop.

2026-10-09 audit: with ENTRY_ANCHOR_MAX_GAP_PCT=10, ~95% of selected ideas had
their order-block entry re-anchored to CMP - 0.5*ATR while the stop stayed
under the (far) order block / 20-bar low — median stop width ~27%, up to 43%
(JAYKAY 255.78 / 148.91). The risk engine then rejected them as stop_too_wide.
"""

import pandas as pd
import pytest

import services.validation_engine as ve
from engine.indicators import calculate_atr
from services.research_levels import df_to_candles


def _frame(start: float = 100.0, end: float = 200.0, n: int = 60) -> pd.DataFrame:
    step = (end - start) / (n - 1)
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "date": pd.date_range("2026-07-01", periods=n, freq="B"),
        "open": [c - 1 for c in closes],
        "high": [c + 3 for c in closes],
        "low": [c - 3 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })


def _confirmation(ob):
    return {"confirmation_score": 100.0, "partial_hits": 3, "order_block": ob,
            "liquidity_zone": None, "tier": "HIGH_CONVICTION", "structure": "BULLISH_BOS"}


@pytest.fixture(autouse=True)
def _gap_10pct(monkeypatch):
    monkeypatch.setattr(ve, "ENTRY_ANCHOR_MAX_GAP_PCT", 0.10)
    monkeypatch.setattr(ve, "tight_entry_gap_enabled", lambda: False)
    monkeypatch.setattr(ve, "STRUCTURAL_TARGET_CAP", False)


def _atr(df):
    return float(calculate_atr(df_to_candles(df), 14))


@pytest.mark.parametrize("horizon,mult,target_mult", [("SWING", 1.3, 3.0), ("LONGTERM", 2.0, 3.5)])
def test_anchored_entry_stop_measured_from_anchored_entry(horizon, mult, target_mult):
    df = _frame()
    close, atr = 200.0, _atr(df)
    entry, stop, targets, _setup, meta = ve._scored_smc_levels("NSE:X", df, horizon, _confirmation([100.0, 110.0]))

    assert meta["anchored"] is True and meta["stop_basis"] == "volatility_from_anchored_entry"
    assert entry == round(close - atr * 0.5, 2)
    base_risk = max(atr * mult, entry * 0.03)
    assert stop == round(entry - base_risk, 2)
    # The old geometry put the stop below the order block (< 100) — a ~50% stop.
    assert stop > 110.0
    width = (entry - stop) / entry
    assert width <= base_risk / entry + 1e-4
    # RR is not manipulated: the target is the same multiple of the (new) risk.
    assert targets[-1] == pytest.approx(entry + (entry - stop) * target_mult, abs=0.02)


@pytest.mark.parametrize("ob_low", [10.0, 50.0, 150.0, 175.0])
def test_anchored_stop_width_independent_of_how_far_the_zone_is(ob_low):
    df = _frame()
    entry, stop, *_ = ve._scored_smc_levels("NSE:X", df, "SWING", _confirmation([ob_low, ob_low + 5]))
    widths = (entry - stop) / entry
    assert widths == pytest.approx(max(_atr(df) * 1.3, entry * 0.03) / entry, abs=1e-3)


def test_non_anchored_entry_keeps_structural_stop_unchanged():
    df = _frame()
    candles = df_to_candles(df)
    atr = _atr(df)
    ob = [190.0, 198.0]  # mid 194 → 3% from close: inside the 10% gap, not anchored
    entry, stop, _t, _s, meta = ve._scored_smc_levels("NSE:X", df, "SWING", _confirmation(ob))

    assert meta["anchored"] is False and meta["stop_basis"] == "structural"
    assert entry == 194.0
    recent_low = min(c["low"] for c in candles[-20:])
    base_risk = max(atr * 1.3, entry * 0.03)
    assert stop == round(min(entry - base_risk, ob[0] - atr * 0.2, recent_low - atr * 0.15), 2)
