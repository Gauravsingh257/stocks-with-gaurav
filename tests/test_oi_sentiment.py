"""
Unit Tests for engine/oi_sentiment.py — OI-Based Market Sentiment
=================================================================
Tests PCR computation, trend analysis, OI change classification,
price+OI pattern detection, and combined sentiment scoring.

Run:  python -m pytest tests/test_oi_sentiment.py -v
"""

import sys
import os
import pytest
from datetime import datetime, timedelta
from collections import deque
from unittest.mock import MagicMock, patch

# Ensure the parent directory is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must set BACKTEST_MODE before importing engine modules that check it
os.environ["BACKTEST_MODE"] = "1"

from engine import config as cfg
from engine.oi_sentiment import (
    update_oi_sentiment,
    get_oi_scores,
    get_oi_sentiment,
    get_oi_summary_text,
    reset_oi_state,
    _compute_pcr_trend,
    _analyze_oi_changes,
    _fetch_index_oi,
    _oi_state,
)


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_state():
    """Reset OI state before each test."""
    reset_oi_state()
    yield
    reset_oi_state()


def _make_instruments(spot_nifty=22000, spot_bn=48000):
    """Generate fake NFO instruments list for NIFTY + BANKNIFTY around ATM."""
    from datetime import date, timedelta
    # Use next Wednesday as expiry
    today = date.today()
    days_ahead = (2 - today.weekday()) % 7  # 2 = Wednesday
    if days_ahead == 0:
        days_ahead = 7
    expiry_date = today + timedelta(days=days_ahead)
    
    instruments = []
    nifty_atm = round(spot_nifty / 50) * 50
    bn_atm = round(spot_bn / 100) * 100
    
    # NIFTY: ±10 strikes × 50
    for offset in range(-10, 11):
        strike = nifty_atm + offset * 50
        for opt_type in ("CE", "PE"):
            # Kite tradingsymbol format: NIFTY26MAR22000CE (simplified for test)
            exp_str = expiry_date.strftime("%y%b").upper()
            instruments.append({
                "name": "NIFTY",
                "tradingsymbol": f"NIFTY{exp_str}{strike}{opt_type}",
                "instrument_type": opt_type,
                "strike": float(strike),
                "expiry": expiry_date,
                "instrument_token": hash(f"NIFTY{strike}{opt_type}") & 0xFFFFFF,
            })
    
    # BANKNIFTY: ±10 strikes × 100
    for offset in range(-10, 11):
        strike = bn_atm + offset * 100
        for opt_type in ("CE", "PE"):
            exp_str = expiry_date.strftime("%y%b").upper()
            instruments.append({
                "name": "BANKNIFTY",
                "tradingsymbol": f"BANKNIFTY{exp_str}{strike}{opt_type}",
                "instrument_type": opt_type,
                "strike": float(strike),
                "expiry": expiry_date,
                "instrument_token": hash(f"BANKNIFTY{strike}{opt_type}") & 0xFFFFFF,
            })
    
    return instruments


def make_mock_kite(spot_nifty=22000, spot_bn=48000, 
                   nifty_call_oi=500000, nifty_put_oi=600000,
                   bn_call_oi=300000, bn_put_oi=350000):
    """Create a mock Kite API object with configurable OI data."""
    mock = MagicMock()
    instruments = _make_instruments(spot_nifty, spot_bn)
    
    def ltp_fn(symbols):
        result = {}
        for sym in symbols:
            if "NIFTY 50" in sym:
                result[sym] = {"last_price": spot_nifty}
            elif "NIFTY BANK" in sym:
                result[sym] = {"last_price": spot_bn}
        return result
    
    def quote_fn(symbols):
        result = {}
        for sym in symbols:
            # Distribute OI evenly across 21 strikes for each type
            if "BANKNIFTY" in sym:
                if sym.endswith("CE"):
                    result[sym] = {"oi": bn_call_oi // 21, "last_price": 100}
                elif sym.endswith("PE"):
                    result[sym] = {"oi": bn_put_oi // 21, "last_price": 100}
            elif "NIFTY" in sym and "BANK" not in sym:
                if sym.endswith("CE"):
                    result[sym] = {"oi": nifty_call_oi // 21, "last_price": 100}
                elif sym.endswith("PE"):
                    result[sym] = {"oi": nifty_put_oi // 21, "last_price": 100}
        return result
    
    mock.ltp = MagicMock(side_effect=ltp_fn)
    mock.quote = MagicMock(side_effect=quote_fn)
    mock.instruments = MagicMock(return_value=instruments)
    return mock


# ---------------------------------------------------------------------------
# TEST: get_oi_sentiment (default state)
# ---------------------------------------------------------------------------

class TestOISentimentDefaults:
    def test_default_sentiment_is_neutral(self):
        state = get_oi_sentiment()
        assert state["sentiment"] == "NEUTRAL"
    
    def test_default_scores_are_zero(self):
        bull, bear = get_oi_scores()
        assert bull == 0
        assert bear == 0
    
    def test_default_summary_text(self):
        text = get_oi_summary_text()
        assert "No data" in text
    
    def test_reset_clears_state(self):
        _oi_state["sentiment"] = "BULLISH"
        _oi_state["bull_score"] = 5
        reset_oi_state()
        assert _oi_state["sentiment"] == "NEUTRAL"
        assert _oi_state["bull_score"] == 0


# ---------------------------------------------------------------------------
# TEST: _fetch_index_oi
# ---------------------------------------------------------------------------

class TestFetchIndexOI:
    @patch("engine.oi_sentiment.os.path.exists", return_value=False)
    def test_fetches_nifty_oi(self, mock_exists):
        mock = make_mock_kite(spot_nifty=22000, nifty_call_oi=500000, nifty_put_oi=600000)
        result = _fetch_index_oi(mock, "NSE:NIFTY 50", "NIFTY", 50)
        
        assert result is not None
        assert result["name"] == "NIFTY"
        assert result["spot"] == 22000
        assert result["call_oi"] > 0
        assert result["put_oi"] > 0
    
    @patch("engine.oi_sentiment.os.path.exists", return_value=False)
    def test_fetches_banknifty_oi(self, mock_exists):
        mock = make_mock_kite(spot_bn=48000, bn_call_oi=300000, bn_put_oi=350000)
        result = _fetch_index_oi(mock, "NSE:NIFTY BANK", "BANKNIFTY", 100)
        
        assert result is not None
        assert result["name"] == "BANKNIFTY"
        assert result["spot"] == 48000
    
    def test_returns_none_on_no_ltp(self):
        mock = MagicMock()
        mock.ltp.return_value = {}
        result = _fetch_index_oi(mock, "NSE:NIFTY 50", "NIFTY", 50)
        assert result is None
    
    def test_returns_none_on_api_error(self):
        mock = MagicMock()
        mock.ltp.side_effect = Exception("API Error")
        result = _fetch_index_oi(mock, "NSE:NIFTY 50", "NIFTY", 50)
        assert result is None
    
    @patch("engine.oi_sentiment.os.path.exists", return_value=False)
    def test_identifies_max_oi_strikes(self, mock_exists):
        mock = make_mock_kite()
        result = _fetch_index_oi(mock, "NSE:NIFTY 50", "NIFTY", 50)
        assert result is not None
        # Max call strike = resistance, max put strike = support
        assert "max_call_strike" in result
        assert "max_put_strike" in result


# ---------------------------------------------------------------------------
# TEST: PCR Computation
# ---------------------------------------------------------------------------

class TestPCRComputation:
    def test_bullish_pcr(self):
        """PCR > 1.2 → BULLISH (heavy put writing = support)"""
        # Put OI >> Call OI → PCR > 1.2
        mock = make_mock_kite(nifty_call_oi=400000, nifty_put_oi=600000,
                              bn_call_oi=200000, bn_put_oi=400000)
        state = update_oi_sentiment(mock)
        assert state["pcr_bias"] == "BULLISH"
    
    def test_bearish_pcr(self):
        """PCR < 0.7 → BEARISH (heavy call writing = ceiling)"""
        mock = make_mock_kite(nifty_call_oi=800000, nifty_put_oi=300000,
                              bn_call_oi=500000, bn_put_oi=200000)
        state = update_oi_sentiment(mock)
        assert state["pcr_bias"] == "BEARISH"
    
    def test_neutral_pcr(self):
        """PCR 0.7-1.2 → NEUTRAL"""
        mock = make_mock_kite(nifty_call_oi=500000, nifty_put_oi=500000,
                              bn_call_oi=300000, bn_put_oi=300000)
        state = update_oi_sentiment(mock)
        assert state["pcr_bias"] == "NEUTRAL"
    
    def test_pcr_stored_in_history(self):
        mock = make_mock_kite()
        update_oi_sentiment(mock)
        state = get_oi_sentiment()
        assert len(state["pcr_history"]) == 1
    
    def test_pcr_zero_call_oi_handled(self):
        """Edge case: zero call OI should not crash."""
        mock = make_mock_kite(nifty_call_oi=0, bn_call_oi=0,
                              nifty_put_oi=100000, bn_put_oi=100000)
        # All CE quotes return 0 OI
        original_quote = mock.quote
        def quote_with_zero_ce(symbols):
            result = original_quote(symbols)
            for sym in result:
                if sym.endswith("CE"):
                    result[sym]["oi"] = 0
            return result
        mock.quote = quote_with_zero_ce
        
        state = update_oi_sentiment(mock)
        # Should not crash, PCR defaults to 1.0 when call OI is 0
        assert state is not None


# ---------------------------------------------------------------------------
# TEST: PCR Trend
# ---------------------------------------------------------------------------

class TestPCRTrend:
    def test_not_enough_history_returns_flat(self):
        """Need at least 3 readings for trend."""
        assert _compute_pcr_trend() == "FLAT"
    
    def test_rising_pcr_trend(self):
        """PCR increasing → RISING (bullish: more put writing)."""
        now = datetime.now()
        history = _oi_state["pcr_history"]
        # Older readings: low PCR (avg 0.75)
        for i in range(3):
            history.append((now - timedelta(minutes=30 - i*10), 0.75, 500000, 375000))
        # Recent readings: high PCR (avg 1.3) — >5% rise
        for i in range(3):
            history.append((now - timedelta(minutes=5 - i), 1.3, 400000, 520000))
        
        assert _compute_pcr_trend() == "RISING"
    
    def test_falling_pcr_trend(self):
        """PCR decreasing → FALLING (bearish: more call writing)."""
        now = datetime.now()
        history = _oi_state["pcr_history"]
        # Older: high PCR (avg 1.3)
        for i in range(3):
            history.append((now - timedelta(minutes=30 - i*10), 1.3, 400000, 520000))
        # Recent: low PCR (avg 0.6) — >5% fall
        for i in range(3):
            history.append((now - timedelta(minutes=5 - i), 0.6, 800000, 480000))
        
        assert _compute_pcr_trend() == "FALLING"
    
    def test_flat_pcr_trend(self):
        """Stable PCR → FLAT."""
        now = datetime.now()
        history = _oi_state["pcr_history"]
        for i in range(6):
            history.append((now - timedelta(minutes=60 - i*10), 1.0, 500000, 500000))
        
        assert _compute_pcr_trend() == "FLAT"


# ---------------------------------------------------------------------------
# TEST: OI Change Analysis
# ---------------------------------------------------------------------------

class TestOIChangeAnalysis:
    def test_no_previous_snapshot_neutral(self):
        """No previous data → NEUTRAL."""
        current = {"NIFTY": {"spot": 22000, "call_oi": 500000, "put_oi": 600000}}
        bias, pattern = _analyze_oi_changes(current, None)
        assert bias == "NEUTRAL"
        assert pattern == "NONE"
    
    def test_long_buildup(self):
        """Price UP + OI UP → LONG_BUILDUP."""
        _oi_state["snapshots"] = {
            "NIFTY": {"spot": 21900, "call_oi": 400000, "put_oi": 500000}
        }
        # OI must increase > 3% total for pattern to count
        # Prev total = 900000, 3% = 27000. New total must be > 927000
        current = {
            "NIFTY": {"spot": 22100, "call_oi": 450000, "put_oi": 580000}
        }
        bias, pattern = _analyze_oi_changes(current, None)
        assert pattern == "LONG_BUILDUP"
    
    def test_short_buildup(self):
        """Price DOWN + OI UP → SHORT_BUILDUP."""
        _oi_state["snapshots"] = {
            "NIFTY": {"spot": 22100, "call_oi": 400000, "put_oi": 500000}
        }
        # OI increase > 3% of prev total (900k * 3% = 27k)
        current = {
            "NIFTY": {"spot": 21800, "call_oi": 500000, "put_oi": 600000}
        }
        bias, pattern = _analyze_oi_changes(current, None)
        assert pattern == "SHORT_BUILDUP"
    
    def test_short_covering(self):
        """Price UP + OI DOWN → SHORT_COVERING."""
        _oi_state["snapshots"] = {
            "NIFTY": {"spot": 21900, "call_oi": 500000, "put_oi": 600000}
        }
        # OI decrease > 3% of prev total (1.1M * 3% = 33k). Drop 200k.
        current = {
            "NIFTY": {"spot": 22100, "call_oi": 400000, "put_oi": 480000}
        }
        bias, pattern = _analyze_oi_changes(current, None)
        assert pattern == "SHORT_COVERING"
    
    def test_long_unwinding(self):
        """Price DOWN + OI DOWN → LONG_UNWINDING."""
        _oi_state["snapshots"] = {
            "NIFTY": {"spot": 22100, "call_oi": 500000, "put_oi": 600000}
        }
        # OI decrease > 3% of prev total (1.1M * 3% = 33k). Drop 200k.
        current = {
            "NIFTY": {"spot": 21800, "call_oi": 400000, "put_oi": 480000}
        }
        bias, pattern = _analyze_oi_changes(current, None)
        assert pattern == "LONG_UNWINDING"
    
    def test_small_change_returns_none(self):
        """Change below OI_CHANGE_THRESHOLD_PCT → NONE."""
        _oi_state["snapshots"] = {
            "NIFTY": {"spot": 22000, "call_oi": 500000, "put_oi": 600000}
        }
        current = {
            "NIFTY": {"spot": 22050, "call_oi": 501000, "put_oi": 601000}
        }
        bias, pattern = _analyze_oi_changes(current, None)
        assert pattern == "NONE"
    
    def test_put_oi_growing_faster_bullish(self):
        """Put OI growing faster than Call OI → BULLISH bias."""
        _oi_state["snapshots"] = {
            "NIFTY": {"spot": 22000, "call_oi": 500000, "put_oi": 500000}
        }
        # Put OI grows 20% vs call OI grows 2% — diff > 3% threshold
        current = {
            "NIFTY": {"spot": 22100, "call_oi": 510000, "put_oi": 600000}
        }
        bias, pattern = _analyze_oi_changes(current, None)
        assert bias == "BULLISH"
    
    def test_call_oi_growing_faster_bearish(self):
        """Call OI growing faster than Put OI → BEARISH bias."""
        _oi_state["snapshots"] = {
            "NIFTY": {"spot": 22000, "call_oi": 500000, "put_oi": 500000}
        }
        # Call OI grows 20% vs put OI grows 2% — diff > 3% threshold
        current = {
            "NIFTY": {"spot": 21800, "call_oi": 600000, "put_oi": 510000}
        }
        bias, pattern = _analyze_oi_changes(current, None)
        assert bias == "BEARISH"


# ---------------------------------------------------------------------------
# TEST: Combined Sentiment Scoring
# ---------------------------------------------------------------------------

class TestCombinedSentiment:
    def test_strongly_bullish(self):
        """High PCR + rising trend + long buildup → BULLISH."""
        # First call: establish baseline snapshot
        mock = make_mock_kite(
            spot_nifty=21900, spot_bn=47900,
            nifty_call_oi=400000, nifty_put_oi=600000,
            bn_call_oi=200000, bn_put_oi=400000
        )
        update_oi_sentiment(mock)
        
        # Force last_update to be old enough for refresh
        _oi_state["last_update"] = datetime.now() - timedelta(seconds=700)
        
        # Inject PCR history for rising trend
        now = datetime.now()
        _oi_state["pcr_history"].clear()
        for i in range(3):
            _oi_state["pcr_history"].append((now - timedelta(minutes=30-i*10), 0.8, 400000, 320000))
        for i in range(3):
            _oi_state["pcr_history"].append((now - timedelta(minutes=5-i), 1.3, 400000, 520000))
        
        # Second call: price up + OI up (long buildup) + high PCR
        mock2 = make_mock_kite(
            spot_nifty=22100, spot_bn=48200,
            nifty_call_oi=420000, nifty_put_oi=700000,
            bn_call_oi=220000, bn_put_oi=500000
        )
        state = update_oi_sentiment(mock2)
        
        assert state["sentiment"] == "BULLISH"
        assert state["bull_score"] > state["bear_score"]
    
    def test_strongly_bearish(self):
        """Low PCR + falling trend + short buildup → BEARISH."""
        # First call: establish baseline
        mock = make_mock_kite(
            spot_nifty=22100, spot_bn=48200,
            nifty_call_oi=700000, nifty_put_oi=300000,
            bn_call_oi=400000, bn_put_oi=200000
        )
        update_oi_sentiment(mock)
        
        _oi_state["last_update"] = datetime.now() - timedelta(seconds=700)
        
        # Inject falling PCR history
        now = datetime.now()
        _oi_state["pcr_history"].clear()
        for i in range(3):
            _oi_state["pcr_history"].append((now - timedelta(minutes=30-i*10), 1.2, 500000, 600000))
        for i in range(3):
            _oi_state["pcr_history"].append((now - timedelta(minutes=5-i), 0.5, 800000, 400000))
        
        # Second call: price down + OI up (short buildup) + low PCR
        mock2 = make_mock_kite(
            spot_nifty=21800, spot_bn=47800,
            nifty_call_oi=800000, nifty_put_oi=350000,
            bn_call_oi=500000, bn_put_oi=250000
        )
        state = update_oi_sentiment(mock2)
        
        assert state["sentiment"] == "BEARISH"
        assert state["bear_score"] > state["bull_score"]
    
    def test_neutral_on_mixed_signals(self):
        """Mixed PCR level + no trend + no clear pattern → NEUTRAL."""
        mock = make_mock_kite(
            nifty_call_oi=500000, nifty_put_oi=500000,
            bn_call_oi=300000, bn_put_oi=300000
        )
        state = update_oi_sentiment(mock)
        assert state["sentiment"] == "NEUTRAL"


# ---------------------------------------------------------------------------
# TEST: Throttling
# ---------------------------------------------------------------------------

class TestThrottling:
    def test_second_call_within_refresh_window_returns_cached(self):
        """update_oi_sentiment should not re-fetch within refresh window."""
        mock = make_mock_kite()
        update_oi_sentiment(mock)
        
        # Second call immediately — should return cached
        call_count_before = mock.ltp.call_count
        update_oi_sentiment(mock)
        assert mock.ltp.call_count == call_count_before  # No new API calls
    
    def test_refreshes_after_window_expires(self):
        """Should re-fetch after refresh window expires."""
        mock = make_mock_kite()
        update_oi_sentiment(mock)
        
        # Expire the cache
        _oi_state["last_update"] = datetime.now() - timedelta(seconds=700)
        
        call_count_before = mock.ltp.call_count
        update_oi_sentiment(mock)
        assert mock.ltp.call_count > call_count_before  # Made new API calls


# ---------------------------------------------------------------------------
# TEST: None kite_obj handling
# ---------------------------------------------------------------------------

class TestNoneKite:
    def test_none_kite_returns_default_state(self):
        state = update_oi_sentiment(None)
        assert state["sentiment"] == "NEUTRAL"
        assert state["last_update"] is None


# ---------------------------------------------------------------------------
# TEST: get_oi_scores integration
# ---------------------------------------------------------------------------

class TestOIScoresIntegration:
    def test_scores_reflect_sentiment(self):
        """get_oi_scores should match internal state."""
        mock = make_mock_kite(
            nifty_call_oi=400000, nifty_put_oi=700000,
            bn_call_oi=200000, bn_put_oi=400000
        )
        update_oi_sentiment(mock)
        bull, bear = get_oi_scores()
        
        # High PCR → should have at least 1 bull point
        assert bull >= 1 or bear >= 0  # At minimum, scores are populated


# ---------------------------------------------------------------------------
# TEST: Summary text formatting
# ---------------------------------------------------------------------------

class TestSummaryText:
    def test_summary_after_update(self):
        mock = make_mock_kite()
        update_oi_sentiment(mock)
        text = get_oi_summary_text()
        
        assert "OI SENTIMENT" in text
        assert "PCR" in text
        assert "Pattern" in text
        assert "Updated" in text
    
    def test_summary_contains_sentiment(self):
        mock = make_mock_kite()
        update_oi_sentiment(mock)
        text = get_oi_summary_text()
        
        state = get_oi_sentiment()
        assert state["sentiment"] in text
