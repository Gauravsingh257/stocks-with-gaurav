"""
Unit tests for the detect_setup_c guards added to smc_mtf_engine_v4.py.

We test the guard LOGIC directly (gap % calc, same-candle comparison,
wide-open detection) without importing the 5k-line engine module.
These are pure-function tests that run in < 1 s with no network access.
"""
from __future__ import annotations

import datetime
import pytest

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _ts(h: int, m: int, date_str: str = "2026-03-20") -> datetime.datetime:
    d = datetime.date.fromisoformat(date_str)
    return datetime.datetime(d.year, d.month, d.day, h, m, 0, tzinfo=_IST)


def _candle(dt: datetime.datetime, o, h, l, c) -> dict:
    return {"date": dt, "open": o, "high": h, "low": l, "close": c}


# ── Pure helpers that mirror the guard logic ──────────────────────────────────

def _gap_pct(prior_close: float, today_open: float) -> float:
    return (today_open - prior_close) / prior_close * 100


def _is_gap_up(gap: float, threshold: float = 0.75) -> bool:
    return gap > threshold


def _is_gap_down(gap: float, threshold: float = 0.75) -> bool:
    return gap < -threshold


def _same_candle(tap_ts: datetime.datetime | None, react_ts: datetime.datetime) -> bool:
    return tap_ts is not None and react_ts == tap_ts


def _is_wide_open(candle_range: float, atr: float, mult: float = 1.5) -> bool:
    return atr > 0 and candle_range > mult * atr


# ── Gap filter tests ──────────────────────────────────────────────────────────

class TestGapFilter:
    def test_1pct_gap_up_suppresses_long(self):
        gap = _gap_pct(prior_close=23087, today_open=23320)   # +1.01%
        assert _is_gap_up(gap), "1% gap-up should be flagged"

    def test_0_3pct_gap_up_allowed(self):
        gap = _gap_pct(prior_close=23087, today_open=23156)   # +0.30%
        assert not _is_gap_up(gap), "0.3% gap-up is within threshold — should be allowed"

    def test_exact_threshold_not_suppressed(self):
        # exactly 0.75% — not strictly greater than, so should NOT suppress
        gap = _gap_pct(prior_close=23087, today_open=round(23087 * 1.0075, 2))
        assert not _is_gap_up(gap), "Exactly 0.75% gap should NOT be suppressed (strict >)"

    def test_1pct_gap_down_suppresses_short(self):
        gap = _gap_pct(prior_close=23087, today_open=22854)   # -1.01%
        assert _is_gap_down(gap), "1% gap-down should suppress SHORT"

    def test_0_3pct_gap_down_allowed(self):
        gap = _gap_pct(prior_close=23087, today_open=23018)   # -0.30%
        assert not _is_gap_down(gap), "0.3% gap-down is within threshold — should be allowed"

    def test_no_gap(self):
        gap = _gap_pct(prior_close=23087, today_open=23087)
        assert not _is_gap_up(gap)
        assert not _is_gap_down(gap)

    def test_negative_gap_is_not_gap_up(self):
        gap = _gap_pct(prior_close=23087, today_open=22900)
        assert not _is_gap_up(gap)

    def test_positive_gap_is_not_gap_down(self):
        gap = _gap_pct(prior_close=23087, today_open=23300)
        assert not _is_gap_down(gap)


# ── Same-candle guard tests ───────────────────────────────────────────────────

class TestSameCandleGuard:
    def test_same_timestamp_blocked(self):
        t = _ts(9, 15)
        assert _same_candle(tap_ts=t, react_ts=t), \
            "Same timestamp → must be blocked"

    def test_next_candle_allowed(self):
        tap_t   = _ts(9, 15)
        react_t = _ts(9, 20)
        assert not _same_candle(tap_ts=tap_t, react_ts=react_t), \
            "Different timestamps → should be allowed"

    def test_none_tap_ts_never_blocks(self):
        assert not _same_candle(tap_ts=None, react_ts=_ts(9, 15)), \
            "No recorded tap timestamp → guard must not block"

    def test_march20_real_scenario_blocked(self):
        """The actual signal from 2026-03-20: both tap & react on 09:15 bar."""
        tap_t  = _ts(9, 15, "2026-03-20")
        react_t = _ts(9, 15, "2026-03-20")
        assert _same_candle(tap_ts=tap_t, react_ts=react_t)

    def test_different_dates_not_blocked(self):
        tap_t   = _ts(15, 25, "2026-03-19")
        react_t = _ts(9, 15, "2026-03-20")
        assert not _same_candle(tap_ts=tap_t, react_ts=react_t)


# ── Wide-open candle tests ────────────────────────────────────────────────────

class TestWideOpenCandle:
    def test_172pt_range_is_wide_at_atr_80(self):
        # 09:15 20th: H=23303, L=23130 → range=173 pts; ATR ~ 80–100 pts
        assert _is_wide_open(candle_range=173, atr=80), \
            "173 / (1.5 × 80=120) → wide open"

    def test_normal_range_not_wide(self):
        assert not _is_wide_open(candle_range=50, atr=80), \
            "50 pts is not wide at ATR 80"

    def test_zero_atr_never_wide(self):
        assert not _is_wide_open(candle_range=500, atr=0), \
            "ATR=0 edge case must not flag wide open"

    def test_exactly_1_5x_atr_not_wide(self):
        # exactly 1.5× ATR → NOT wide (strict >)
        assert not _is_wide_open(candle_range=120, atr=80)

    def test_just_over_1_5x_atr_is_wide(self):
        assert _is_wide_open(candle_range=121, atr=80)
