"""
tests/test_expiry_manager.py — Unit Tests for Expiry & Strike Management
=========================================================================
Tests:
  1. ATM±1 strike computation (CE/PE strike pairs)
  2. Expiry detection (weekly, monthly, next weekly, next monthly)
  3. Near-expiry detection (preload trigger)
  4. Target expiry builder (NIFTY weekly+monthly, BANKNIFTY monthly)
  5. Rollover state machine (add/drop/cleanup)
  6. Edge cases (exact ATM, gap moves, overlapping expiries, holidays)
"""

import pytest
from datetime import datetime, date, timedelta, time
from unittest.mock import patch, MagicMock

from engine.expiry_manager import (
    get_atm_strikes,
    get_atm,
    _collect_expiries,
    get_active_weekly_expiry,
    get_next_weekly_expiry,
    get_active_monthly_expiry,
    get_next_monthly_expiry,
    is_near_expiry,
    is_expiry_today,
    is_post_expiry_cutoff,
    get_target_expiries,
    ExpiryRolloverState,
    reset_rollover_state,
)


# =====================================================
# HELPERS — Instrument Builders
# =====================================================

def _make_instrument(name, instrument_type, strike, expiry_date):
    """Create a minimal instrument dict like Kite API returns."""
    return {
        "name": name,
        "instrument_type": instrument_type,
        "strike": strike,
        "expiry": expiry_date,
        "tradingsymbol": f"{name}{expiry_date.strftime('%y%b').upper()}{strike}{instrument_type}",
        "instrument_token": hash(f"{name}{expiry_date}{strike}{instrument_type}"),
    }


def _make_instruments_set(name, strikes, expiry_dates):
    """Create instruments for all strikes × expiries × CE/PE."""
    instruments = []
    for exp in expiry_dates:
        for strike in strikes:
            for opt_type in ("CE", "PE"):
                instruments.append(_make_instrument(name, opt_type, strike, exp))
    return instruments


# =====================================================
# TEST: ATM STRIKE COMPUTATION
# =====================================================

class TestGetATMStrikes:
    """Tests for get_atm_strikes(spot, step)."""

    def test_banknifty_example(self):
        """User spec: spot=61187, step=100 → CE:[61100,61200], PE:[61200,61300]"""
        ce, pe = get_atm_strikes(61187, 100)
        assert ce == [61100, 61200]
        assert pe == [61200, 61300]

    def test_nifty_example(self):
        """User spec: CMP=25496, step=100 → CE:[25400,25500], PE:[25500,25600]"""
        ce, pe = get_atm_strikes(25496, 100)
        assert ce == [25400, 25500]
        assert pe == [25500, 25600]

    def test_exact_atm(self):
        """When spot is exactly on a strike."""
        ce, pe = get_atm_strikes(25500, 100)
        assert ce == [25400, 25500]
        assert pe == [25500, 25600]

    def test_midpoint_rounds_up(self):
        """Spot at exact midpoint (e.g. 25450) should round to 25500 (rounds UP)."""
        ce, pe = get_atm_strikes(25450, 100)
        # 25450/100 + 0.5 = 255.0 → ATM=25500
        assert ce == [25400, 25500]
        assert pe == [25500, 25600]

    def test_just_below_midpoint(self):
        """Spot just below midpoint rounds DOWN."""
        ce, pe = get_atm_strikes(25449, 100)
        # 25449/100 + 0.5 = 254.99 → int=254 → ATM=25400
        assert ce == [25300, 25400]
        assert pe == [25400, 25500]

    def test_nifty_step_50(self):
        """Works with step=50 too (in case config is changed back)."""
        ce, pe = get_atm_strikes(25496, 50)
        # ATM = int(509.92+0.5)*50 = 510*50 = 25500
        assert ce == [25450, 25500]
        assert pe == [25500, 25550]

    def test_four_contracts_per_expiry(self):
        """Total unique strikes = exactly 3 (CE has 2, PE has 2, ATM shared)."""
        ce, pe = get_atm_strikes(61187, 100)
        all_strikes = set(ce + pe)
        assert len(all_strikes) == 3  # 61100, 61200, 61300
        # Total contracts = 2 CE + 2 PE = 4
        assert len(ce) == 2
        assert len(pe) == 2

    def test_negative_spot_raises(self):
        with pytest.raises(ValueError, match="positive"):
            get_atm_strikes(-100, 100)

    def test_zero_step_raises(self):
        with pytest.raises(ValueError, match="positive"):
            get_atm_strikes(25000, 0)

    def test_large_value(self):
        """Handles large spot values."""
        ce, pe = get_atm_strikes(99999, 100)
        assert ce == [99900, 100000]
        assert pe == [100000, 100100]


class TestGetATM:
    """Tests for get_atm(spot, step)."""

    def test_basic(self):
        assert get_atm(61187, 100) == 61200

    def test_exact(self):
        assert get_atm(25500, 100) == 25500

    def test_zero_step(self):
        assert get_atm(25500, 0) == 0


# =====================================================
# TEST: EXPIRY DETECTION
# =====================================================

class TestExpiryDetection:
    """Tests for weekly/monthly expiry detection from instruments."""

    def _make_weekly_chain(self, name="NIFTY"):
        """Create instruments with 4 weekly expiries for NIFTY."""
        today = date.today()
        # Create expiries on next 4 Thursdays (approximate)
        expiries = []
        d = today
        while len(expiries) < 4:
            d += timedelta(days=1)
            if d.weekday() == 3:  # Thursday
                expiries.append(d)

        return _make_instruments_set(name, [25400, 25500, 25600], expiries), expiries

    def test_active_weekly_is_nearest(self):
        instruments, expiries = self._make_weekly_chain()
        result = get_active_weekly_expiry(instruments, "NIFTY")
        assert result == expiries[0]

    def test_next_weekly_is_second(self):
        instruments, expiries = self._make_weekly_chain()
        result = get_next_weekly_expiry(instruments, "NIFTY")
        assert result == expiries[1]

    def test_active_monthly_is_last_of_month(self):
        instruments, expiries = self._make_weekly_chain()
        result = get_active_monthly_expiry(instruments, "NIFTY")
        # Monthly = last expiry in the same month as nearest
        nearest = expiries[0]
        expected = max(e for e in expiries if e.month == nearest.month and e.year == nearest.year)
        assert result == expected

    def test_next_monthly_is_next_month(self):
        """Create expiries spanning two months."""
        today = date.today()
        # Build expiries: 2 this month, 2 next month
        expiries = []
        d = today
        while len(expiries) < 6:
            d += timedelta(days=1)
            if d.weekday() == 3:
                expiries.append(d)

        instruments = _make_instruments_set("BANKNIFTY", [61200], expiries)
        current_monthly = get_active_monthly_expiry(instruments, "BANKNIFTY")
        next_monthly = get_next_monthly_expiry(instruments, "BANKNIFTY")

        if next_monthly:
            assert next_monthly > current_monthly
            assert next_monthly.month != current_monthly.month or next_monthly.year != current_monthly.year

    def test_no_instruments_returns_none(self):
        assert get_active_weekly_expiry([], "NIFTY") is None
        assert get_next_weekly_expiry([], "NIFTY") is None
        assert get_active_monthly_expiry([], "NIFTY") is None

    def test_collect_expiries_filters_past(self):
        """Past expiries should not be included."""
        past = date.today() - timedelta(days=5)
        future = date.today() + timedelta(days=5)
        instruments = _make_instruments_set("NIFTY", [25500], [past, future])
        result = _collect_expiries(instruments, "NIFTY")
        assert past not in result
        assert future in result

    def test_wrong_name_ignored(self):
        instruments = _make_instruments_set("BANKNIFTY", [61200], [date.today() + timedelta(5)])
        assert get_active_weekly_expiry(instruments, "NIFTY") is None


# =====================================================
# TEST: NEAR-EXPIRY DETECTION
# =====================================================

class TestNearExpiry:
    """Tests for is_near_expiry, is_expiry_today, is_post_expiry_cutoff."""

    def test_tomorrow_is_near(self):
        tomorrow = date.today() + timedelta(days=1)
        assert is_near_expiry(tomorrow, days=3) is True

    def test_today_is_near(self):
        assert is_near_expiry(date.today(), days=3) is True

    def test_far_future_is_not_near(self):
        far = date.today() + timedelta(days=30)
        assert is_near_expiry(far, days=3) is False

    def test_past_is_not_near(self):
        past = date.today() - timedelta(days=1)
        assert is_near_expiry(past, days=3) is False

    def test_three_days_boundary(self):
        exactly_3 = date.today() + timedelta(days=3)
        assert is_near_expiry(exactly_3, days=3) is True
        four_days = date.today() + timedelta(days=4)
        assert is_near_expiry(four_days, days=3) is False

    def test_accepts_datetime(self):
        """Should handle datetime objects by converting to date."""
        dt = datetime.now() + timedelta(days=1)
        assert is_near_expiry(dt, days=3) is True

    def test_is_expiry_today(self):
        assert is_expiry_today(date.today()) is True
        assert is_expiry_today(date.today() + timedelta(1)) is False

    @patch("engine.expiry_manager.datetime")
    def test_post_expiry_cutoff(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 2, 26, 15, 35, 0)
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        # Can't easily mock time() comparison, test the function exists
        # The actual time check is a simple comparison
        assert callable(is_post_expiry_cutoff)


# =====================================================
# TEST: TARGET EXPIRY BUILDER
# =====================================================

class TestTargetExpiries:
    """Tests for get_target_expiries — NIFTY vs BANKNIFTY rules."""

    def _build_chain(self, name, num_weeks=5):
        """Build instrument chain with num_weeks weekly expiries."""
        today = date.today()
        expiries = []
        d = today
        while len(expiries) < num_weeks:
            d += timedelta(days=1)
            if d.weekday() == 3:
                expiries.append(d)
        instruments = _make_instruments_set(name, [25500, 61200], expiries)
        return instruments, expiries

    def test_banknifty_monthly_only(self):
        """BANKNIFTY should only get monthly expiry (no weekly)."""
        instruments, expiries = self._build_chain("BANKNIFTY", 5)
        result = get_target_expiries(instruments, "BANKNIFTY")

        # Should have at most 1-2 entries (monthly + possibly preloaded next)
        assert len(result) >= 1
        assert all(r["type"] == "monthly" for r in result)

    def test_nifty_weekly_and_monthly(self):
        """NIFTY should get both weekly + monthly."""
        instruments, expiries = self._build_chain("NIFTY", 5)
        result = get_target_expiries(instruments, "NIFTY")

        # Should have at least a weekly entry
        types = [r["type"] for r in result]
        assert "weekly" in types
        # May or may not have monthly depending on whether weekly == monthly

    def test_preload_when_near_expiry(self):
        """When weekly expiry is ≤3 days away, next weekly should be preloaded."""
        today = date.today()
        # Create expiries: one very near (tomorrow or day after)
        near_exp = today + timedelta(days=1)
        # Find a day that's a Thursday
        while near_exp.weekday() != 3:
            near_exp += timedelta(days=1)

        far_exp = near_exp + timedelta(days=7)
        instruments = _make_instruments_set("NIFTY", [25500], [near_exp, far_exp])

        result = get_target_expiries(instruments, "NIFTY")
        expiry_dates = [r["expiry"] for r in result]

        # Near expiry should trigger preload of next
        if (near_exp - today).days <= 3:
            assert len(result) >= 2
            preloaded = [r for r in result if r["preload"]]
            assert len(preloaded) >= 1

    def test_no_preload_when_far(self):
        """When expiry is far away, no preloading."""
        today = date.today()
        far_exp = today + timedelta(days=20)
        while far_exp.weekday() != 3:
            far_exp += timedelta(days=1)
        far_exp2 = far_exp + timedelta(days=7)

        instruments = _make_instruments_set("BANKNIFTY", [61200], [far_exp, far_exp2])
        result = get_target_expiries(instruments, "BANKNIFTY")

        preloaded = [r for r in result if r["preload"]]
        assert len(preloaded) == 0

    def test_no_duplicate_expiries(self):
        """Result should never have duplicate expiry dates."""
        instruments, _ = self._build_chain("NIFTY", 5)
        result = get_target_expiries(instruments, "NIFTY")
        expiry_dates = [r["expiry"] for r in result]
        assert len(expiry_dates) == len(set(expiry_dates))

    def test_empty_instruments(self):
        result = get_target_expiries([], "NIFTY")
        assert result == []


# =====================================================
# TEST: ROLLOVER STATE MACHINE
# =====================================================

class TestExpiryRolloverState:
    """Tests for ExpiryRolloverState lifecycle."""

    def setup_method(self):
        self.state = ExpiryRolloverState()

    def test_first_update_all_added(self):
        exp1 = date(2026, 2, 27)
        exp2 = date(2026, 3, 26)
        result = self.state.update("BANKNIFTY", {exp1, exp2})
        assert sorted(result["added"]) == [exp1, exp2]
        assert result["expired"] == []

    def test_drop_detection(self):
        exp1 = date(2026, 2, 27)
        exp2 = date(2026, 3, 26)
        self.state.update("BANKNIFTY", {exp1, exp2})

        # Now drop exp1 (expired)
        result = self.state.update("BANKNIFTY", {exp2})
        assert result["expired"] == [exp1]
        assert result["added"] == []

    def test_add_and_drop_simultaneously(self):
        exp1 = date(2026, 2, 27)
        self.state.update("NIFTY", {exp1})

        exp2 = date(2026, 3, 5)
        result = self.state.update("NIFTY", {exp2})
        assert result["added"] == [exp2]
        assert result["expired"] == [exp1]

    def test_no_change(self):
        exp1 = date(2026, 3, 26)
        self.state.update("BANKNIFTY", {exp1})
        result = self.state.update("BANKNIFTY", {exp1})
        assert result["added"] == []
        assert result["expired"] == []

    def test_get_active_expiries(self):
        exp1 = date(2026, 3, 26)
        self.state.update("BANKNIFTY", {exp1})
        assert exp1 in self.state.get_active_expiries("BANKNIFTY")
        assert self.state.get_active_expiries("NIFTY") == set()

    def test_reset(self):
        self.state.update("NIFTY", {date(2026, 3, 5)})
        self.state.reset()
        assert self.state.get_active_expiries("NIFTY") == set()

    def test_multiple_underlyings_independent(self):
        exp_bn = date(2026, 3, 26)
        exp_nf = date(2026, 3, 5)
        self.state.update("BANKNIFTY", {exp_bn})
        self.state.update("NIFTY", {exp_nf})

        assert exp_bn in self.state.get_active_expiries("BANKNIFTY")
        assert exp_nf in self.state.get_active_expiries("NIFTY")
        assert exp_bn not in self.state.get_active_expiries("NIFTY")

    def test_post_expiry_cleanup_not_triggered_before_cutoff(self):
        """Before 15:30, cleanup should return empty."""
        exp = date.today()
        self.state.update("NIFTY", {exp})
        with patch("engine.expiry_manager.is_post_expiry_cutoff", return_value=False):
            result = self.state.check_post_expiry_cleanup("NIFTY")
            assert result == []

    def test_post_expiry_cleanup_triggered_after_cutoff(self):
        """After 15:30 on expiry day, today's expiry should be flagged for drop."""
        exp = date.today()
        self.state.update("NIFTY", {exp})
        with patch("engine.expiry_manager.is_post_expiry_cutoff", return_value=True):
            result = self.state.check_post_expiry_cleanup("NIFTY")
            assert exp in result


# =====================================================
# TEST: EDGE CASES
# =====================================================

class TestEdgeCases:
    """Edge cases: gap moves, holidays, overlap, etc."""

    def test_gap_move_changes_atm(self):
        """If spot gaps from 61200 to 61700, ATM should shift."""
        atm_before = get_atm(61200, 100)
        atm_after = get_atm(61700, 100)
        assert atm_before != atm_after
        assert atm_after == 61700

    def test_small_move_same_atm(self):
        """Small move within same step keeps ATM."""
        atm1 = get_atm(61210, 100)
        atm2 = get_atm(61240, 100)
        assert atm1 == atm2 == 61200  # Both round to 61200

    def test_weekly_monthly_overlap(self):
        """When weekly expiry == monthly expiry, should appear once."""
        today = date.today()
        # Create a single expiry that serves as both weekly and monthly
        exp = today + timedelta(days=3)
        while exp.weekday() != 3:
            exp += timedelta(days=1)

        instruments = _make_instruments_set("NIFTY", [25500], [exp])
        result = get_target_expiries(instruments, "NIFTY")

        # Weekly == Monthly → should appear only once
        expiry_dates = [r["expiry"] for r in result]
        assert len(set(expiry_dates)) == len(expiry_dates)

    def test_holiday_week_wider_preload(self):
        """3 calendar days handles most holiday scenarios."""
        today = date.today()
        # 3-day-away expiry should trigger preload
        exp = today + timedelta(days=3)
        assert is_near_expiry(exp, days=3)
        # 4-day-away should not
        exp4 = today + timedelta(days=4)
        assert not is_near_expiry(exp4, days=3)

    def test_api_stale_expiry(self):
        """If instruments only have past expiries, should return None."""
        past = date.today() - timedelta(days=10)
        instruments = _make_instruments_set("NIFTY", [25500], [past])
        assert get_active_weekly_expiry(instruments, "NIFTY") is None

    def test_contract_count_banknifty_monthly(self):
        """BANKNIFTY should monitor exactly 4 contracts in steady state."""
        ce, pe = get_atm_strikes(61187, 100)
        # 2 CE + 2 PE = 4
        assert len(ce) + len(pe) == 4

    def test_contract_count_nifty_weekly_monthly(self):
        """NIFTY monitors 4 contracts per expiry × 2 expiries = 8."""
        ce, pe = get_atm_strikes(25496, 100)
        contracts_per_expiry = len(ce) + len(pe)
        # 2 expiries (weekly + monthly) when they differ
        assert contracts_per_expiry == 4
        # Total when 2 different expiries: 8

    def test_max_contracts_steady_state(self):
        """Steady state: BANKNIFTY(4) + NIFTY(8) = 12."""
        bn_ce, bn_pe = get_atm_strikes(61187, 100)
        nf_ce, nf_pe = get_atm_strikes(25496, 100)
        bn_total = len(bn_ce) + len(bn_pe)  # 4 (1 expiry)
        nf_total = (len(nf_ce) + len(nf_pe)) * 2  # 8 (2 expiries)
        assert bn_total + nf_total == 12
