"""Risk engine: sizing, stop-cap, liquidity/ATR down-sizing, trend-break exit,
and the guarantee that every component OFF == legacy behavior (instant rollback)."""

from __future__ import annotations

import pytest

from services import risk_engine as re


# Healthy liquidity so LIQUIDITY_ADJ never penalizes / fetches network in tests.
LIQ = dict(atr_pct=2.0, turnover_cr=10.0)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    # Deterministic capital + generous per-position weight cap for ratio maths.
    monkeypatch.setenv("PORTFOLIO_NOTIONAL_CAPITAL", "1000000")
    monkeypatch.setenv("RISK_PER_TRADE_PCT", "1.0")
    monkeypatch.setenv("RISK_MAX_WEIGHT_PCT", "100")
    for f in ("RISK_ENGINE_ENABLED", "RISK_SIZING_ENABLED", "STOP_CAP_ENABLED", "LIQUIDITY_ADJ_ENABLED"):
        monkeypatch.setenv(f, "1")


# ── (1) risk-normalized sizing ────────────────────────────────────────────────
def test_size_is_inverse_to_stop_width():
    tight = re.evaluate_promotion("A", "SWING", 100, 96, **LIQ)   # 4% stop
    wide = re.evaluate_promotion("B", "SWING", 100, 92, **LIQ)    # 8% stop
    assert tight.accepted and wide.accepted
    # risk fixed at 1% of 1e6 = 10k; shares = 10k/risk_per_share
    assert tight.shares == 2500 and wide.shares == 1250
    # tighter stop → ~2x the notional
    assert round(tight.new_position_value / wide.new_position_value, 1) == 2.0


def test_risk_amount_constant_regardless_of_stop():
    a = re.evaluate_promotion("A", "SWING", 100, 96, **LIQ)
    b = re.evaluate_promotion("B", "SWING", 500, 460, **LIQ)
    assert a.risk_amount == b.risk_amount == 10000.0  # 1% of 1e6, always


# ── (2) stop-width cap ────────────────────────────────────────────────────────
def test_stop_cap_rejects_wide_swing(monkeypatch):
    monkeypatch.setenv("MAX_STOP_PCT", "10")
    d = re.evaluate_promotion("ONMOBILE", "SWING", 78.17, 47.50, **LIQ)  # ~39% stop
    assert d.accepted is False and "stop_too_wide" in d.reason


def test_stop_cap_longterm_uses_own_threshold(monkeypatch):
    monkeypatch.setenv("MAX_STOP_PCT", "10")
    monkeypatch.setenv("MAX_STOP_LONGTERM_PCT", "15")
    # 12% stop: rejected for swing, accepted for long-term
    assert re.evaluate_promotion("X", "SWING", 100, 88, **LIQ).accepted is False
    assert re.evaluate_promotion("X", "LONGTERM", 100, 88, **LIQ).accepted is True


# ── (4) liquidity / ATR down-sizing (reduces, never rejects) ──────────────────
def test_high_atr_reduces_size(monkeypatch):
    monkeypatch.setenv("ATR_SIZE_REF_PCT", "4")
    base = re.evaluate_promotion("A", "SWING", 100, 95, atr_pct=2.0, turnover_cr=10.0)
    hot = re.evaluate_promotion("B", "SWING", 100, 95, atr_pct=8.0, turnover_cr=10.0)  # 2x ref
    assert hot.accepted and hot.new_position_value < base.new_position_value
    assert hot.atr_factor == pytest.approx(0.5, abs=0.01)   # 4/8


def test_low_turnover_reduces_size(monkeypatch):
    monkeypatch.setenv("LIQ_MIN_TURNOVER_CR", "2")
    thin = re.evaluate_promotion("B", "SWING", 100, 95, atr_pct=2.0, turnover_cr=0.5)  # < 2 Cr
    assert thin.accepted and thin.liquidity_factor < 1.0


# ── rollback guarantees: every flag OFF == legacy ─────────────────────────────
def test_engine_off_is_legacy_equal_weight(monkeypatch):
    monkeypatch.setenv("RISK_ENGINE_ENABLED", "0")
    d = re.evaluate_promotion("ONMOBILE", "SWING", 78.17, 47.50, **LIQ)  # absurd stop
    assert d.accepted is True                      # legacy accepts any valid long
    assert d.new_position_value == d.old_position_value  # equal-weight sizing


def test_stop_cap_off_accepts_wide(monkeypatch):
    monkeypatch.setenv("STOP_CAP_ENABLED", "0")
    d = re.evaluate_promotion("ONMOBILE", "SWING", 78.17, 47.50, **LIQ)
    assert d.accepted is True


def test_sizing_off_is_equal_weight(monkeypatch):
    monkeypatch.setenv("RISK_SIZING_ENABLED", "0")
    d = re.evaluate_promotion("A", "SWING", 100, 96, **LIQ)
    assert d.accepted and d.new_position_value == d.old_position_value


# ── (3) trend-break exit ──────────────────────────────────────────────────────
def test_trend_break_exits_below_dma_and_weak_rs(monkeypatch):
    monkeypatch.setenv("TREND_BREAK_EXIT_ENABLED", "1")
    monkeypatch.setattr(re, "_rs_vs_nifty", lambda s, n: -5.0)   # RS negative
    d = re.evaluate_trend_break_exit("X", cmp=90, days_held=10, dma200=100)  # 10% below DMA
    assert d.should_exit is True and "rs_negative" in d.reason


def test_trend_break_holds_when_rs_positive(monkeypatch):
    monkeypatch.setattr(re, "_rs_vs_nifty", lambda s, n: +8.0)
    d = re.evaluate_trend_break_exit("X", cmp=90, days_held=10, dma200=100)
    assert d.should_exit is False


def test_trend_break_holds_above_dma():
    d = re.evaluate_trend_break_exit("X", cmp=105, days_held=10, dma200=100)
    assert d.should_exit is False and d.reason == "above_dma200"


def test_trend_break_too_fresh(monkeypatch):
    monkeypatch.setenv("TREND_BREAK_MIN_DAYS", "3")
    d = re.evaluate_trend_break_exit("X", cmp=90, days_held=1, dma200=100)
    assert d.should_exit is False and d.reason == "too_fresh"


def test_trend_break_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("TREND_BREAK_EXIT_ENABLED", "0")
    d = re.evaluate_trend_break_exit("X", cmp=90, days_held=10, dma200=100)
    assert d.should_exit is False and d.reason == "trend_break_disabled"
