"""Tests for services/regime_governor.py (PR1 — Regime Governor)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from services import regime_governor as gov
from services.regime_governor import (
    BEAR,
    CORRECTION,
    SIDEWAYS,
    STRONG_BULL,
    UNKNOWN,
    WEAK_BULL,
    MarketStateResult,
    apply_to_ideas,
    apply_to_records,
    classify_market_state,
    exposure_state,
    get_policy,
    governor_enabled,
)

ORDER = [STRONG_BULL, WEAK_BULL, SIDEWAYS, CORRECTION, BEAR]


# ── fixtures / helpers ────────────────────────────────────────────────────────

@dataclass
class Idea:
    symbol: str
    confidence_score: float
    entry_price: float = 100.0
    stop_loss: float = 95.0
    targets: tuple[float, ...] = (115.0,)  # RR = 15/5 = 3.0


@dataclass
class Rec:
    symbol: str
    confidence_score: float
    entry: float = 100.0
    stop_loss: float = 95.0
    targets: tuple[float, ...] = (115.0,)
    smc: dict | None = None


def _regime(**kw):
    base = dict(
        regime="TRENDING_UP", confidence=0.8, nifty_close=100.0,
        ema_short=98.0, ema_long=95.0, adx=30.0, trend_slope=0.1,
        above_200dma=True, pct_from_52w_high=3.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── flag ──────────────────────────────────────────────────────────────────────

def test_governor_disabled_by_default(monkeypatch):
    monkeypatch.delenv("REGIME_GOVERNOR_ENABLED", raising=False)
    assert governor_enabled() is False


def test_governor_flag_truthy(monkeypatch):
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("REGIME_GOVERNOR_ENABLED", v)
        assert governor_enabled() is True
    monkeypatch.setenv("REGIME_GOVERNOR_ENABLED", "0")
    assert governor_enabled() is False


# ── policy monotonicity (graduated, not binary) ───────────────────────────────

def test_policy_is_graduated(monkeypatch):
    # Clear any env overrides so we test the shipped defaults.
    for st in ORDER:
        for pfx in ("MAX_IDEAS", "MIN_CONF", "MIN_RR", "MIN_SMC", "EXPOSURE"):
            monkeypatch.delenv(f"GOVERNOR_{pfx}_{st}", raising=False)
    pols = [get_policy(st) for st in ORDER]
    # As the market worsens: caps shrink, bars rise, exposure drops.
    assert [p.max_ideas for p in pols] == sorted([p.max_ideas for p in pols], reverse=True)
    assert [p.min_confidence for p in pols] == sorted([p.min_confidence for p in pols])
    assert [p.min_rr for p in pols] == sorted([p.min_rr for p in pols])
    assert [p.exposure_pct for p in pols] == sorted([p.exposure_pct for p in pols], reverse=True)
    # Bear is never a hard zero cap (graduated model allows a rare exceptional name).
    assert get_policy(BEAR).max_ideas >= 1
    assert get_policy(BEAR).cash_pct >= 80


def test_policy_env_override(monkeypatch):
    monkeypatch.setenv("GOVERNOR_MAX_IDEAS_BEAR", "0")
    monkeypatch.setenv("GOVERNOR_EXPOSURE_BEAR", "0")
    p = get_policy(BEAR)
    assert p.max_ideas == 0
    assert p.exposure_pct == 0 and p.cash_pct == 100


def test_unknown_policy_is_permissive():
    p = get_policy(UNKNOWN)
    assert p.min_confidence == 0.0 and p.min_rr == 0.0
    assert p.max_ideas >= 1000  # effectively uncapped — never blocks on data outage


# ── classification ────────────────────────────────────────────────────────────

def test_classify_strong_bull():
    assert classify_market_state(_regime()).state == STRONG_BULL


def test_classify_bear_below_200_deep():
    r = _regime(above_200dma=False, pct_from_52w_high=25.0, ema_short=90, ema_long=95, trend_slope=-0.2)
    assert classify_market_state(r).state == BEAR


def test_classify_correction_below_200_shallow():
    r = _regime(above_200dma=False, pct_from_52w_high=12.0, ema_short=99, ema_long=98, trend_slope=0.05)
    assert classify_market_state(r).state == CORRECTION


def test_classify_correction_above_200_pullback():
    r = _regime(above_200dma=True, pct_from_52w_high=12.0, nifty_close=100, ema_short=101, trend_slope=-0.1)
    assert classify_market_state(r).state == CORRECTION


def test_classify_weak_bull():
    # above 200, uptrend intact, but soft momentum (low adx, flat slope) & not near high
    r = _regime(above_200dma=True, pct_from_52w_high=6.0, adx=15.0, trend_slope=0.0)
    assert classify_market_state(r).state == WEAK_BULL


def test_classify_sideways():
    r = _regime(above_200dma=True, pct_from_52w_high=6.0, nifty_close=100, ema_short=101,
                ema_long=102, adx=12.0, trend_slope=0.0)
    assert classify_market_state(r).state == SIDEWAYS


def test_classify_unknown_when_regime_unknown():
    assert classify_market_state(_regime(regime="UNKNOWN")).state == UNKNOWN


def test_force_state_override(monkeypatch):
    monkeypatch.setenv("GOVERNOR_FORCE_STATE", "BEAR")
    res = classify_market_state(_regime())  # would be STRONG_BULL, but forced
    assert res.state == BEAR and res.source == "forced"


# ── apply_to_ideas ────────────────────────────────────────────────────────────

def test_apply_ideas_filters_confidence_and_caps(monkeypatch):
    monkeypatch.delenv("GOVERNOR_MAX_IDEAS_STRONG_BULL", raising=False)
    monkeypatch.setenv("GOVERNOR_MAX_IDEAS_STRONG_BULL", "2")
    ideas = [
        Idea("A", 90), Idea("B", 80), Idea("C", 70),   # all pass conf(55)+rr(3)
        Idea("LOW", 40),                                 # below conf floor 55
    ]
    state = MarketStateResult(state=STRONG_BULL)
    kept, diag = apply_to_ideas(ideas, "SWING", state)
    assert [i.symbol for i in kept] == ["A", "B"]        # LOW dropped, capped to 2
    assert diag.killed_confidence == 1
    assert diag.capped == 1


def test_apply_ideas_filters_rr(monkeypatch):
    monkeypatch.setenv("GOVERNOR_MIN_RR_SIDEWAYS", "2.5")
    # RR = (target-entry)/(entry-stop). Give one below 2.5 and one above.
    bad = Idea("BAD", 90, entry_price=100, stop_loss=90, targets=(110,))   # RR 1.0
    good = Idea("GOOD", 90, entry_price=100, stop_loss=95, targets=(120,))  # RR 4.0
    state = MarketStateResult(state=SIDEWAYS)
    kept, diag = apply_to_ideas([good, bad], "SWING", state)
    assert [i.symbol for i in kept] == ["GOOD"]
    assert diag.killed_rr == 1


# ── apply_to_records (SMC band + sector) ──────────────────────────────────────

def test_apply_records_smc_band(monkeypatch):
    monkeypatch.setenv("GOVERNOR_MIN_SMC_SIDEWAYS", "5.5")
    recs = [Rec("HI", 90, smc={"band": 6.0}), Rec("LO", 90, smc={"band": 5.0})]
    band_of = lambda r: float(r.smc["band"])
    state = MarketStateResult(state=SIDEWAYS)
    kept, diag = apply_to_records(recs, "SWING", band_of, state)
    assert [r.symbol for r in kept] == ["HI"]
    assert diag.killed_smc == 1


def test_apply_records_sector_leading_in_bear(monkeypatch):
    # Force BEAR (sector_requirement=require_leading). Avoid network by stubbing the band.
    bands = {"LEAD": "leading", "LAG": "lagging", "MID": "neutral"}
    monkeypatch.setattr(gov, "_sector_band", lambda sym, strength: bands[sym])
    recs = [Rec("LEAD", 90, targets=(130,)), Rec("LAG", 90, targets=(130,)), Rec("MID", 90, targets=(130,))]
    band_of = lambda r: 9.0  # pass SMC comfortably
    state = MarketStateResult(state=BEAR)
    kept, diag = apply_to_records(recs, "SWING", band_of, state)
    assert [r.symbol for r in kept] == ["LEAD"]
    assert diag.killed_sector == 2


# ── exposure payload ──────────────────────────────────────────────────────────

def test_exposure_state_shape_and_labels():
    payload = exposure_state(MarketStateResult(state=BEAR), strength={"leading": []})
    assert payload["market_state"] == BEAR
    assert payload["exposure_pct"] == 15 and payload["cash_pct"] == 85
    assert payload["exposure_label"] == "🔴 Risk-Off"
    for key in ("suggested_max_ideas", "min_confidence", "advisory", "as_of"):
        assert key in payload

    bull = exposure_state(MarketStateResult(state=STRONG_BULL), strength={"leading": []})
    assert bull["exposure_pct"] == 100 and bull["exposure_label"] == "🟢 Aggressive"
