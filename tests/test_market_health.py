"""EP1 tests — Market Health Score (Layer 1)."""

from __future__ import annotations

from types import SimpleNamespace

from services import market_health as mh
from services.market_health import (
    breadth_from_candles, compute_market_health, health_to_state,
    market_health_enabled,
)


def _regime(**kw):
    base = dict(regime="TRENDING_UP", above_200dma=True, ema_short=98, ema_long=95,
                trend_slope=0.1, pct_from_52w_high=3.0)
    base.update(kw)
    return SimpleNamespace(**base)


def _candles(values):
    return [{"close": float(v)} for v in values]


# ── flag ──────────────────────────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MARKET_HEALTH_ENABLED", raising=False)
    assert market_health_enabled() is False


# ── breadth ───────────────────────────────────────────────────────────────────

def test_breadth_from_candles_strong():
    # 210 rising closes → last is above both SMAs; each stock an advancer.
    rising = list(range(100, 310))  # 210 pts, strictly increasing
    data = {f"S{i}": _candles(rising) for i in range(5)}
    b = breadth_from_candles(data, cache=False)
    assert b["pct_above_200dma"] == 100.0
    assert b["pct_above_50dma"] == 100.0
    assert b["advancers"] == 5 and b["decliners"] == 0
    assert b["breadth_score"] == 100.0
    assert b["sampled"] == 5


def test_breadth_from_candles_weak():
    falling = list(range(310, 100, -1))  # 210 pts, strictly decreasing
    data = {f"S{i}": _candles(falling) for i in range(4)}
    b = breadth_from_candles(data, cache=False)
    assert b["pct_above_200dma"] == 0.0
    assert b["decliners"] == 4
    assert b["breadth_score"] == 0.0


def test_breadth_ignores_short_series():
    data = {"A": _candles(list(range(100, 130)))}  # only 30 bars → skipped
    b = breadth_from_candles(data, cache=False)
    assert b["sampled"] == 0 and b["breadth_score"] is None


# ── sub-scores ────────────────────────────────────────────────────────────────

def test_trend_subscore_bull_beats_bear():
    hi = mh._trend_subscore(_regime())
    lo = mh._trend_subscore(_regime(above_200dma=False, ema_short=90, ema_long=95, trend_slope=-0.2, pct_from_52w_high=25))
    assert hi is not None and lo is not None and hi > lo


def test_trend_subscore_unknown_none():
    assert mh._trend_subscore(_regime(regime="UNKNOWN")) is None


def test_rotation_subscore_more_leaders_higher():
    lead = {"sectors": {"A": {"band": "leading"}, "B": {"band": "leading"}, "C": {"band": "neutral"}}}
    lag = {"sectors": {"A": {"band": "lagging"}, "B": {"band": "lagging"}, "C": {"band": "neutral"}}}
    assert mh._rotation_subscore(lead) > mh._rotation_subscore(lag)


# ── composite ─────────────────────────────────────────────────────────────────

def test_compute_health_monotonic(monkeypatch):
    monkeypatch.setattr(mh, "_volatility_subscore", lambda: None)
    monkeypatch.setattr(mh, "get_cached_breadth", lambda: None)
    strong = compute_market_health(_regime(), {"sectors": {"A": {"band": "leading"}, "B": {"band": "leading"}}})
    weak = compute_market_health(
        _regime(above_200dma=False, ema_short=90, ema_long=95, trend_slope=-0.2, pct_from_52w_high=25),
        {"sectors": {"A": {"band": "lagging"}, "B": {"band": "lagging"}}},
    )
    assert strong["available"] and weak["available"]
    assert strong["score"] > weak["score"]


def test_compute_health_renormalizes(monkeypatch):
    # Only trend present → weights renormalise to 1; score == trend subscore.
    monkeypatch.setattr(mh, "_volatility_subscore", lambda: None)
    monkeypatch.setattr(mh, "get_cached_breadth", lambda: None)
    monkeypatch.setattr(mh, "_rotation_subscore", lambda s: None)
    h = compute_market_health(_regime(), None)
    assert h["available"] and abs(sum(h["weights"].values()) - 1.0) < 1e-6
    assert h["score"] == h["subscores"]["trend"]


def test_compute_health_unavailable(monkeypatch):
    monkeypatch.setattr(mh, "_trend_subscore", lambda r: None)
    monkeypatch.setattr(mh, "_volatility_subscore", lambda: None)
    monkeypatch.setattr(mh, "get_cached_breadth", lambda: None)
    monkeypatch.setattr(mh, "_rotation_subscore", lambda s: None)
    h = compute_market_health(_regime(regime="UNKNOWN"), None)
    assert h["available"] is False and h["score"] is None


def test_opportunity_levels_and_state_map():
    assert health_to_state(80) == "STRONG_BULL"
    assert health_to_state(32) == "CORRECTION"
    assert health_to_state(10) == "BEAR"
    assert mh._opportunity_level(85) == "RICH"
    assert mh._opportunity_level(35) == "SELECTIVE"
    assert mh._opportunity_level(10) == "SCARCE"
