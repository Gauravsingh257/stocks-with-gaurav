"""
Regime Classification Tests
============================
Comprehensive unit tests for the smc_trading_engine.regime package.

Covers all six modules:
    1. global_data   — score computation, bias boundaries
    2. oi_analyzer   — PCR, call/put walls, max pain
    3. volatility_model — HIGH/NORMAL/LOW classification
    4. premarket_classifier — all four regimes
    5. regime_controller — control flag generation
    6. morning_confirmation — 9:30-9:45 regime validation
"""

import pytest
import pandas as pd
import numpy as np

from smc_trading_engine.regime.global_data import (
    get_us_market_change,
    get_asia_market_change,
    get_gift_nifty_gap,
    compute_global_score,
)
from smc_trading_engine.regime.oi_analyzer import (
    fetch_option_chain,
    calculate_pcr,
    detect_call_writing,
    detect_put_writing,
    detect_max_pain,
    compute_oi_bias_score,
)
from smc_trading_engine.regime.volatility_model import (
    compute_atr,
    compute_previous_day_range,
    compute_volatility_regime,
)
from smc_trading_engine.regime.premarket_classifier import (
    PremarketClassifier,
    RegimeType,
    DirectionalBias,
)
from smc_trading_engine.regime.regime_controller import (
    RegimeController,
    RegimeControlFlags,
)
from smc_trading_engine.regime.morning_confirmation import (
    OpeningRange,
    ConfirmationResult,
    compute_opening_range,
    detect_volume_expansion,
    detect_structure_break,
    is_strong_bullish_candle,
    is_strong_bearish_candle,
    confirm_regime,
)


# ═══════════════════════════════════════════════════════
#  FIXTURES
# ═══════════════════════════════════════════════════════

@pytest.fixture
def sample_option_chain():
    """Balanced option chain around spot 22000."""
    return pd.DataFrame({
        "strike": [21700, 21800, 21900, 22000, 22100, 22200, 22300],
        "call_oi": [50000, 80000, 120000, 200000, 300000, 250000, 100000],
        "put_oi": [100000, 250000, 300000, 200000, 120000, 80000, 50000],
        "call_change_oi": [5000, 8000, 12000, 20000, 30000, 25000, 10000],
        "put_change_oi": [10000, 25000, 30000, 20000, 12000, 8000, 5000],
    })


@pytest.fixture
def bearish_option_chain():
    """Bearish OI: low PCR (heavy call writing, light put writing)."""
    return pd.DataFrame({
        "strike": [21700, 21800, 21900, 22000, 22100, 22200, 22300],
        "call_oi": [200000, 300000, 400000, 500000, 600000, 500000, 300000],
        "put_oi": [50000, 60000, 70000, 80000, 50000, 30000, 20000],
        "call_change_oi": [0] * 7,
        "put_change_oi": [0] * 7,
    })


@pytest.fixture
def bullish_option_chain():
    """Bullish OI: high PCR (heavy put writing, light call writing)."""
    return pd.DataFrame({
        "strike": [21700, 21800, 21900, 22000, 22100, 22200, 22300],
        "call_oi": [20000, 30000, 50000, 80000, 70000, 60000, 50000],
        "put_oi": [300000, 500000, 600000, 400000, 200000, 100000, 50000],
        "call_change_oi": [0] * 7,
        "put_change_oi": [0] * 7,
    })


@pytest.fixture
def sample_ohlc():
    """20 bars of synthetic OHLC data."""
    np.random.seed(42)
    n = 20
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    close = 22000 + np.cumsum(np.random.randn(n) * 50)
    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 20,
        "high": close + abs(np.random.randn(n)) * 60,
        "low": close - abs(np.random.randn(n)) * 60,
        "close": close,
        "volume": np.random.randint(100000, 500000, n),
    }, index=dates)
    return df


# ═══════════════════════════════════════════════════════
#  1. GLOBAL DATA TESTS
# ═══════════════════════════════════════════════════════

class TestGlobalData:

    def test_us_bullish(self):
        result = get_us_market_change(1.0, 1.5, 0.8)
        assert result["us_bias"] == "BULLISH"
        assert result["us_avg_change"] > 0

    def test_us_bearish(self):
        result = get_us_market_change(-1.0, -0.5, -0.8)
        assert result["us_bias"] == "BEARISH"

    def test_us_neutral(self):
        result = get_us_market_change(0.1, -0.1, 0.1)
        assert result["us_bias"] == "NEUTRAL"

    def test_asia_bullish(self):
        result = get_asia_market_change(0.5, 0.4, 0.3)
        assert result["asia_bias"] == "BULLISH"

    def test_asia_bearish(self):
        result = get_asia_market_change(-0.5, -0.3, -0.4)
        assert result["asia_bias"] == "BEARISH"

    def test_gift_gap_up(self):
        result = get_gift_nifty_gap(22100, 22000)
        assert result["gift_bias"] == "BULLISH"
        assert result["gift_gap_pct"] > 0

    def test_gift_gap_down(self):
        result = get_gift_nifty_gap(21900, 22000)
        assert result["gift_bias"] == "BEARISH"
        assert result["gift_gap_pct"] < 0

    def test_gift_zero_close(self):
        result = get_gift_nifty_gap(22000, 0)
        assert result["gift_bias"] == "NEUTRAL"

    def test_global_score_bullish(self):
        result = compute_global_score(
            sp500_change_pct=1.0, nasdaq_change_pct=1.2, dow_change_pct=0.8,
            nikkei_change_pct=0.5, hangseng_change_pct=0.6, sgx_change_pct=0.4,
            gift_nifty_price=22200, prev_nifty_close=22000,
        )
        assert result["global_bias"] == "BULLISH"
        assert result["global_score"] > 60

    def test_global_score_bearish(self):
        result = compute_global_score(
            sp500_change_pct=-1.0, nasdaq_change_pct=-1.2, dow_change_pct=-0.8,
            nikkei_change_pct=-0.5, hangseng_change_pct=-0.6, sgx_change_pct=-0.4,
            gift_nifty_price=21800, prev_nifty_close=22000,
        )
        assert result["global_bias"] == "BEARISH"
        assert result["global_score"] < 40

    def test_global_score_range(self):
        result = compute_global_score()
        assert 0 <= result["global_score"] <= 100


# ═══════════════════════════════════════════════════════
#  2. OI ANALYZER TESTS
# ═══════════════════════════════════════════════════════

class TestOIAnalyzer:

    def test_fetch_option_chain_validation(self, sample_option_chain):
        result = fetch_option_chain(sample_option_chain)
        assert "strike" in result.columns
        assert "call_oi" in result.columns
        assert "put_oi" in result.columns
        assert len(result) == 7

    def test_fetch_option_chain_missing_column(self):
        bad_df = pd.DataFrame({"strike": [100], "call_oi": [10]})
        with pytest.raises(ValueError, match="put_oi"):
            fetch_option_chain(bad_df)

    def test_pcr_balanced(self, sample_option_chain):
        pcr = calculate_pcr(sample_option_chain)
        assert 0.5 < pcr < 2.0  # Should be roughly balanced

    def test_pcr_bearish(self, bearish_option_chain):
        pcr = calculate_pcr(bearish_option_chain)
        assert pcr < 0.7

    def test_pcr_bullish(self, bullish_option_chain):
        pcr = calculate_pcr(bullish_option_chain)
        assert pcr > 1.3

    def test_call_wall_above_spot(self, sample_option_chain):
        result = detect_call_writing(sample_option_chain, 22000)
        assert result["call_wall"] > 22000
        assert result["call_wall_oi"] > 0

    def test_put_wall_below_spot(self, sample_option_chain):
        result = detect_put_writing(sample_option_chain, 22000)
        assert result["put_wall"] < 22000
        assert result["put_wall_oi"] > 0

    def test_max_pain(self, sample_option_chain):
        mp = detect_max_pain(sample_option_chain)
        assert 21700 <= mp <= 22300  # Within strike range

    def test_max_pain_empty(self):
        mp = detect_max_pain(pd.DataFrame())
        assert mp == 0.0

    def test_oi_bias_bearish(self, bearish_option_chain):
        result = compute_oi_bias_score(bearish_option_chain, 22000)
        assert result["oi_bias"] == "BEARISH"
        assert result["pcr"] < 0.7
        assert 0 <= result["oi_score"] <= 100

    def test_oi_bias_bullish(self, bullish_option_chain):
        result = compute_oi_bias_score(bullish_option_chain, 22000)
        assert result["oi_bias"] == "BULLISH"
        assert result["pcr"] > 1.3

    def test_oi_score_range(self, sample_option_chain):
        result = compute_oi_bias_score(sample_option_chain, 22000)
        assert 0 <= result["oi_score"] <= 100


# ═══════════════════════════════════════════════════════
#  3. VOLATILITY MODEL TESTS
# ═══════════════════════════════════════════════════════

class TestVolatilityModel:

    def test_atr_computation(self, sample_ohlc):
        atr = compute_atr(sample_ohlc, period=14)
        assert atr > 0

    def test_atr_insufficient_data(self):
        small = pd.DataFrame({
            "high": [100, 101], "low": [99, 100], "close": [100, 101]
        })
        atr = compute_atr(small, period=14)
        assert atr == 0.0

    def test_prev_day_range(self, sample_ohlc):
        rng = compute_previous_day_range(sample_ohlc)
        assert rng > 0

    def test_prev_day_range_empty(self):
        assert compute_previous_day_range(pd.DataFrame()) == 0.0

    def test_high_volatility_gap(self):
        result = compute_volatility_regime(
            atr_14=100, india_vix=18, india_vix_prev=16,
            gap_points=90, prev_day_range=120,
        )
        assert result["volatility_regime"] == "HIGH"

    def test_high_volatility_vix(self):
        result = compute_volatility_regime(
            atr_14=100, india_vix=22, india_vix_prev=20,
            gap_points=30, prev_day_range=120,
        )
        assert result["volatility_regime"] == "HIGH"

    def test_high_volatility_rising_vix(self):
        result = compute_volatility_regime(
            atr_14=100, india_vix=16, india_vix_prev=14,
            gap_points=30, prev_day_range=120,
        )
        assert result["volatility_regime"] == "HIGH"
        assert result["vix_rising"] is True

    def test_low_volatility(self):
        result = compute_volatility_regime(
            atr_14=100, india_vix=12, india_vix_prev=12,
            gap_points=10, prev_day_range=80,
        )
        assert result["volatility_regime"] == "LOW"

    def test_normal_volatility(self):
        result = compute_volatility_regime(
            atr_14=100, india_vix=16, india_vix_prev=16,
            gap_points=50, prev_day_range=100,
        )
        assert result["volatility_regime"] == "NORMAL"

    def test_score_range(self):
        result = compute_volatility_regime(
            atr_14=100, india_vix=15, gap_points=50,
        )
        assert 0 <= result["volatility_score"] <= 100


# ═══════════════════════════════════════════════════════
#  4. PREMARKET CLASSIFIER TESTS
# ═══════════════════════════════════════════════════════

class TestPremarketClassifier:

    def setup_method(self):
        self.classifier = PremarketClassifier()

    def _make_global(self, bias, score):
        return {"global_bias": bias, "global_score": score}

    def _make_oi(self, bias, score):
        return {
            "oi_bias": bias, "oi_score": score, "pcr": 1.0,
            "call_wall": 22200, "put_wall": 21800, "max_pain": 22000,
            "heavy_call_writing": False, "heavy_put_writing": False,
        }

    def _make_vol(self, regime, score, vix_rising=False):
        return {
            "volatility_regime": regime, "volatility_score": score,
            "atr_14": 100, "india_vix": 15, "vix_change_pct": 0,
            "gap_atr_ratio": 0.5, "vix_rising": vix_rising,
        }

    def test_trend_up(self):
        result = self.classifier.classify(
            global_data=self._make_global("BULLISH", 75),
            oi_data=self._make_oi("BULLISH", 75),
            volatility_data=self._make_vol("HIGH", 65),
            gap_points=60, atr_14=100,
        )
        assert result["regime"] == "TREND_UP"
        assert result["directional_bias"] == "LONG_ONLY"

    def test_trend_down(self):
        result = self.classifier.classify(
            global_data=self._make_global("BEARISH", 25),
            oi_data=self._make_oi("BEARISH", 25),
            volatility_data=self._make_vol("HIGH", 65),
            gap_points=-60, atr_14=100,
        )
        assert result["regime"] == "TREND_DOWN"
        assert result["directional_bias"] == "SHORT_ONLY"

    def test_rotational(self):
        result = self.classifier.classify(
            global_data=self._make_global("NEUTRAL", 50),
            oi_data=self._make_oi("NEUTRAL", 50),
            volatility_data=self._make_vol("LOW", 30),
            gap_points=10, atr_14=100,
        )
        assert result["regime"] == "ROTATIONAL"
        assert result["directional_bias"] == "BOTH"

    def test_high_vol_event(self):
        result = self.classifier.classify(
            global_data=self._make_global("NEUTRAL", 50),
            oi_data=self._make_oi("NEUTRAL", 50),
            volatility_data=self._make_vol("HIGH", 70, vix_rising=True),
            gap_points=30, atr_14=100,
            event_flag=True,
        )
        assert result["regime"] == "HIGH_VOL_EVENT"
        assert result["directional_bias"] == "BOTH"

    def test_high_vol_event_priority(self):
        """HIGH_VOL_EVENT should take priority over TREND_DOWN."""
        result = self.classifier.classify(
            global_data=self._make_global("BEARISH", 20),
            oi_data=self._make_oi("BEARISH", 20),
            volatility_data=self._make_vol("HIGH", 80, vix_rising=True),
            gap_points=-80, atr_14=100,
            event_flag=True,
        )
        assert result["regime"] == "HIGH_VOL_EVENT"

    def test_confidence_range(self):
        result = self.classifier.classify(
            global_data=self._make_global("BULLISH", 75),
            oi_data=self._make_oi("BULLISH", 75),
            volatility_data=self._make_vol("HIGH", 65),
            gap_points=60, atr_14=100,
        )
        assert 0 <= result["confidence"] <= 100

    def test_composite_score_range(self):
        result = self.classifier.classify(
            global_data=self._make_global("NEUTRAL", 50),
            oi_data=self._make_oi("NEUTRAL", 50),
            volatility_data=self._make_vol("NORMAL", 50),
            gap_points=0, atr_14=100,
        )
        assert 0 <= result["composite_score"] <= 100

    def test_partial_trend_bearish(self):
        """Bearish global + bearish OI but small gap → still TREND_DOWN."""
        result = self.classifier.classify(
            global_data=self._make_global("BEARISH", 25),
            oi_data=self._make_oi("BEARISH", 25),
            volatility_data=self._make_vol("NORMAL", 50),
            gap_points=-10, atr_14=100,
        )
        assert result["regime"] == "TREND_DOWN"

    def test_classify_from_raw(self, bullish_option_chain, sample_ohlc):
        """Smoke test for the raw-input pipeline."""
        result = self.classifier.classify_from_raw(
            sp500_change_pct=1.0, nasdaq_change_pct=1.2, dow_change_pct=0.8,
            nikkei_change_pct=0.5, hangseng_change_pct=0.6, sgx_change_pct=0.4,
            gift_nifty_price=22200, prev_nifty_close=22000,
            option_chain_df=bullish_option_chain, spot_price=22000,
            ohlc_df=sample_ohlc, india_vix=15, india_vix_prev=14,
            gap_points=50, prev_day_range=120,
        )
        assert result["regime"] in [r.value for r in RegimeType]
        assert 0 <= result["confidence"] <= 100


# ═══════════════════════════════════════════════════════
#  5. REGIME CONTROLLER TESTS
# ═══════════════════════════════════════════════════════

class TestRegimeController:

    def setup_method(self):
        self.controller = RegimeController()

    def _classification(self, regime, bias, confidence=70):
        return {
            "regime": regime,
            "directional_bias": bias,
            "confidence": confidence,
            "composite_score": 60,
            "components": {
                "oi": {"call_wall": 22200, "put_wall": 21800, "max_pain": 22000}
            },
        }

    def test_trend_up_flags(self):
        flags = self.controller.get_control_flags(
            self._classification("TREND_UP", "LONG_ONLY")
        )
        assert flags.allow_long is True
        assert flags.allow_short is False
        assert flags.position_size_multiplier == 1.0

    def test_trend_down_flags(self):
        flags = self.controller.get_control_flags(
            self._classification("TREND_DOWN", "SHORT_ONLY")
        )
        assert flags.allow_long is False
        assert flags.allow_short is True
        assert flags.position_size_multiplier == 1.0

    def test_rotational_flags(self):
        flags = self.controller.get_control_flags(
            self._classification("ROTATIONAL", "BOTH")
        )
        assert flags.allow_long is True
        assert flags.allow_short is True
        assert flags.position_size_multiplier == 1.0

    def test_high_vol_event_flags(self):
        flags = self.controller.get_control_flags(
            self._classification("HIGH_VOL_EVENT", "BOTH")
        )
        assert flags.allow_long is True
        assert flags.allow_short is True
        assert flags.position_size_multiplier == 0.5

    def test_should_allow_long(self):
        flags = RegimeControlFlags(allow_long=True, allow_short=False)
        assert self.controller.should_allow_entry(flags, "LONG") is True
        assert self.controller.should_allow_entry(flags, "SHORT") is False

    def test_should_allow_short(self):
        flags = RegimeControlFlags(allow_long=False, allow_short=True)
        assert self.controller.should_allow_entry(flags, "SHORT") is True
        assert self.controller.should_allow_entry(flags, "LONG") is False

    def test_adjust_position_size_full(self):
        flags = RegimeControlFlags(position_size_multiplier=1.0)
        assert self.controller.adjust_position_size(flags, 10) == 10

    def test_adjust_position_size_half(self):
        flags = RegimeControlFlags(position_size_multiplier=0.5)
        assert self.controller.adjust_position_size(flags, 10) == 5

    def test_adjust_position_size_minimum(self):
        flags = RegimeControlFlags(position_size_multiplier=0.1)
        assert self.controller.adjust_position_size(flags, 3) >= 1

    def test_adjust_position_size_zero_base(self):
        flags = RegimeControlFlags(position_size_multiplier=0.5)
        assert self.controller.adjust_position_size(flags, 0) == 0

    def test_unknown_regime_fallback(self):
        flags = self.controller.get_control_flags(
            {"regime": "INVALID_REGIME", "directional_bias": "BOTH", "confidence": 50}
        )
        assert flags.regime == "ROTATIONAL"  # Safe fallback
        assert flags.allow_long is True
        assert flags.allow_short is True

    def test_oi_walls_passed_through(self):
        flags = self.controller.get_control_flags(
            self._classification("ROTATIONAL", "BOTH")
        )
        assert flags.call_wall == 22200
        assert flags.put_wall == 21800
        assert flags.max_pain == 22000


# ═══════════════════════════════════════════════════════
#  6. MORNING CONFIRMATION TESTS
# ═══════════════════════════════════════════════════════

class TestMorningConfirmation:

    def _make_candle(self, open, high, low, close, volume=200000):
        return pd.DataFrame([{
            "open": open, "high": high, "low": low,
            "close": close, "volume": volume,
        }])

    def _make_opening_range(self, high=22050, low=21950, open_p=22000,
                            close_p=22020, vol=200000, avg_vol=150000):
        return OpeningRange(
            high=high, low=low, open_price=open_p,
            close_price=close_p, volume=vol, avg_volume=avg_vol,
        )

    # ── Opening range ──

    def test_compute_opening_range(self):
        candle = self._make_candle(22000, 22050, 21950, 22020, 250000)
        lookback = pd.DataFrame({"volume": [100000, 120000, 130000, 150000]})
        result = compute_opening_range(candle, lookback)
        assert result.high == 22050
        assert result.low == 21950
        assert result.open_price == 22000
        assert result.close_price == 22020
        assert result.volume == 250000
        assert result.avg_volume == 125000  # avg of lookback

    def test_compute_opening_range_empty(self):
        result = compute_opening_range(pd.DataFrame())
        assert result.high == 0.0

    # ── Volume expansion ──

    def test_volume_expansion_detected(self):
        rng = self._make_opening_range(vol=300000, avg_vol=150000)
        assert detect_volume_expansion(rng) is True

    def test_volume_expansion_not_detected(self):
        rng = self._make_opening_range(vol=150000, avg_vol=150000)
        assert detect_volume_expansion(rng) is False

    def test_volume_expansion_zero_avg(self):
        rng = self._make_opening_range(vol=200000, avg_vol=0)
        assert detect_volume_expansion(rng) is False

    # ── Structure break ──

    def test_bullish_structure_break(self):
        rng = self._make_opening_range(high=22050, low=21950)
        confirm = self._make_candle(22040, 22100, 22020, 22080)  # close > high
        assert detect_structure_break(rng, confirm) == "BULLISH"

    def test_bearish_structure_break(self):
        rng = self._make_opening_range(high=22050, low=21950)
        confirm = self._make_candle(21960, 21970, 21900, 21920)  # close < low
        assert detect_structure_break(rng, confirm) == "BEARISH"

    def test_no_structure_break(self):
        rng = self._make_opening_range(high=22050, low=21950)
        confirm = self._make_candle(22000, 22030, 21960, 22010)  # inside range
        assert detect_structure_break(rng, confirm) is None

    def test_structure_break_no_data(self):
        rng = self._make_opening_range()
        assert detect_structure_break(rng, pd.DataFrame()) is None

    # ── Candle quality ──

    def test_strong_bullish_candle(self):
        candle = pd.Series({"open": 22000, "high": 22100, "low": 21950, "close": 22090})
        assert is_strong_bullish_candle(candle) is True

    def test_not_strong_bullish_candle(self):
        candle = pd.Series({"open": 22050, "high": 22100, "low": 21950, "close": 21970})
        assert is_strong_bullish_candle(candle) is False

    def test_strong_bearish_candle(self):
        candle = pd.Series({"open": 22050, "high": 22100, "low": 21950, "close": 21960})
        assert is_strong_bearish_candle(candle) is True

    def test_not_strong_bearish_candle(self):
        candle = pd.Series({"open": 22000, "high": 22100, "low": 21950, "close": 22080})
        assert is_strong_bearish_candle(candle) is False

    # ── Regime confirmation ──

    def test_trend_down_downgraded_on_bullish_breakout(self):
        """TREND_DOWN + bullish breakout + strong close → ROTATIONAL."""
        rng = self._make_opening_range(high=22050, low=21950)
        confirm = self._make_candle(22040, 22120, 22030, 22110)  # strong bull above OR high
        result = confirm_regime("TREND_DOWN", "SHORT_ONLY", rng, confirm)
        assert result.regime_changed is True
        assert result.confirmed_regime == "ROTATIONAL"
        assert result.directional_bias == "BOTH"

    def test_trend_down_not_downgraded_weak_candle(self):
        """TREND_DOWN + breakout but weak candle → stays TREND_DOWN."""
        rng = self._make_opening_range(high=22050, low=21950)
        # Close barely above high, close near low of candle (not bullish body)
        confirm = self._make_candle(22045, 22080, 21990, 22055)
        result = confirm_regime("TREND_DOWN", "SHORT_ONLY", rng, confirm)
        # close=22055 > high=22050 but position = (22055-21990)/(22080-21990)=0.72 → bullish
        # Actually this IS bullish. Let me make a truly weak candle:
        confirm2 = self._make_candle(22060, 22080, 22040, 22051)  # barely above, close near low
        result2 = confirm_regime("TREND_DOWN", "SHORT_ONLY", rng, confirm2)
        # position = (22051-22040)/(22080-22040) = 0.275 → NOT bullish → no downgrade
        assert result2.regime_changed is False
        assert result2.confirmed_regime == "TREND_DOWN"

    def test_trend_up_downgraded_on_bearish_breakdown(self):
        """TREND_UP + bearish breakdown → ROTATIONAL."""
        rng = self._make_opening_range(high=22050, low=21950)
        confirm = self._make_candle(21960, 21970, 21900, 21920)  # close below OR low
        result = confirm_regime("TREND_UP", "LONG_ONLY", rng, confirm)
        assert result.regime_changed is True
        assert result.confirmed_regime == "ROTATIONAL"

    def test_trend_up_holds_when_inside_range(self):
        """TREND_UP + price stays inside range → confirmed."""
        rng = self._make_opening_range(high=22050, low=21950)
        confirm = self._make_candle(22000, 22040, 21960, 22030)
        result = confirm_regime("TREND_UP", "LONG_ONLY", rng, confirm)
        assert result.regime_changed is False
        assert result.confirmed_regime == "TREND_UP"

    def test_rotational_stays_rotational(self):
        """ROTATIONAL is never downgraded."""
        rng = self._make_opening_range(high=22050, low=21950)
        confirm = self._make_candle(22040, 22100, 22020, 22080)
        result = confirm_regime("ROTATIONAL", "BOTH", rng, confirm)
        assert result.regime_changed is False
        assert result.confirmed_regime == "ROTATIONAL"

    def test_high_vol_event_never_downgraded(self):
        """HIGH_VOL_EVENT skips confirmation entirely."""
        rng = self._make_opening_range(high=22050, low=21950)
        confirm = self._make_candle(22060, 22200, 22050, 22180)
        result = confirm_regime("HIGH_VOL_EVENT", "BOTH", rng, confirm)
        assert result.regime_changed is False
        assert result.reason == "CONFIRMED_EVENT_DAY"

    def test_no_confirmation_data(self):
        """Missing candle data → no change."""
        rng = self._make_opening_range()
        result = confirm_regime("TREND_DOWN", "SHORT_ONLY", rng, pd.DataFrame())
        assert result.regime_changed is False
        assert result.reason == "CONFIRMED_NO_DATA"


# ═══════════════════════════════════════════════════════
#  7. CONTROLLER + CONFIRMATION INTEGRATION
# ═══════════════════════════════════════════════════════

class TestRegimeControllerConfirmation:

    def setup_method(self):
        self.controller = RegimeController()

    def _make_candle(self, open, high, low, close, volume=200000):
        return pd.DataFrame([{
            "open": open, "high": high, "low": low,
            "close": close, "volume": volume,
        }])

    def test_controller_downgrades_trend_down(self):
        """Full pipeline: TREND_DOWN flags → morning bullish breakout → ROTATIONAL."""
        flags = RegimeControlFlags(
            allow_long=False, allow_short=True,
            regime="TREND_DOWN", directional_bias="SHORT_ONLY",
        )
        first_candle = self._make_candle(22000, 22050, 21950, 22020)
        confirm_candle = self._make_candle(22040, 22120, 22030, 22110)

        updated = self.controller.apply_morning_confirmation(
            flags, first_candle, confirm_candle,
        )
        assert updated.regime == "ROTATIONAL"
        assert updated.allow_long is True
        assert updated.allow_short is True

    def test_controller_confirms_trend_up(self):
        """Full pipeline: TREND_UP flags → normal candle → stays TREND_UP."""
        flags = RegimeControlFlags(
            allow_long=True, allow_short=False,
            regime="TREND_UP", directional_bias="LONG_ONLY",
        )
        first_candle = self._make_candle(22000, 22050, 21950, 22020)
        confirm_candle = self._make_candle(22020, 22040, 21960, 22030)

        updated = self.controller.apply_morning_confirmation(
            flags, first_candle, confirm_candle,
        )
        assert updated.regime == "TREND_UP"
        assert updated.allow_long is True
        assert updated.allow_short is False

    def test_controller_skips_with_no_data(self):
        """No first candle data → flags unchanged."""
        flags = RegimeControlFlags(
            allow_long=False, allow_short=True,
            regime="TREND_DOWN", directional_bias="SHORT_ONLY",
        )
        updated = self.controller.apply_morning_confirmation(flags, pd.DataFrame())
        assert updated.regime == "TREND_DOWN"
        assert updated.allow_short is True
