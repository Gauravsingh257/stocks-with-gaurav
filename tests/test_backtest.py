"""
tests/test_backtest.py — Tests for the Backtest Framework (Phase 3)
===================================================================
Validates DataStore, CostModel, Engine, and Runner.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest.data_store import DataStore
from backtest.cost_model import (
    calculate_round_trip_cost, cost_as_points, calculate_slippage,
    CostConfig, INDEX_OPTIONS_COST, EQUITY_INTRADAY_COST
)
from backtest.engine import (
    BacktestEngine, BacktestConfig, Trade, confluence_score,
    resample_to_htf, _is_index, _in_killzone
)
from backtest.runner import (
    calculate_metrics, split_candles, walk_forward_split,
    generate_synthetic_candles
)


# =====================================================
# DATA STORE TESTS
# =====================================================

class TestDataStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = DataStore(self.tmp.name)

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_insert_and_retrieve(self):
        candles = [
            {"date": "2025-08-01T09:15:00", "open": 100, "high": 102,
             "low": 99, "close": 101, "volume": 1000},
            {"date": "2025-08-01T09:20:00", "open": 101, "high": 103,
             "low": 100, "close": 102, "volume": 1500},
        ]
        count = self.store.insert_candles("TEST:SYM", "5minute", candles)
        self.assertEqual(count, 2)

        result = self.store.get_candles("TEST:SYM", "5minute")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[1]["close"], 102)

    def test_get_symbols(self):
        self.store.insert_candles("SYM_A", "5minute",
                                  [{"date": "2025-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5}])
        self.store.insert_candles("SYM_B", "5minute",
                                  [{"date": "2025-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5}])
        symbols = self.store.get_symbols()
        self.assertIn("SYM_A", symbols)
        self.assertIn("SYM_B", symbols)

    def test_date_range_filter(self):
        candles = [
            {"date": f"2025-08-0{d}T09:15:00", "open": 100 + d, "high": 102 + d,
             "low": 99 + d, "close": 101 + d}
            for d in range(1, 6)
        ]
        self.store.insert_candles("SYM", "5minute", candles)
        result = self.store.get_candles("SYM", "5minute",
                                         start="2025-08-03", end="2025-08-05")
        self.assertEqual(len(result), 2)

    def test_upsert_replaces(self):
        self.store.insert_candles("SYM", "5minute",
                                  [{"date": "2025-01-01T09:15:00", "open": 100, "high": 102, "low": 99, "close": 101}])
        self.store.insert_candles("SYM", "5minute",
                                  [{"date": "2025-01-01T09:15:00", "open": 200, "high": 202, "low": 199, "close": 201}])
        result = self.store.get_candles("SYM", "5minute")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 200)


# =====================================================
# COST MODEL TESTS
# =====================================================

class TestCostModel(unittest.TestCase):
    def test_round_trip_cost_positive(self):
        costs = calculate_round_trip_cost(22000, 22300, 50, is_index=True)
        self.assertGreater(costs["total"], 0)
        self.assertIn("brokerage", costs)
        self.assertIn("stt", costs)
        self.assertIn("slippage", costs)

    def test_slippage_index(self):
        slip = calculate_slippage(22000, "LONG", is_index=True,
                                 config=INDEX_OPTIONS_COST)
        self.assertAlmostEqual(slip, 5.0, places=1)  # INDEX_OPTIONS_COST slippage

    def test_slippage_stock(self):
        slip = calculate_slippage(1500, "LONG", is_index=False)
        self.assertGreater(slip, 0)
        self.assertLess(slip, 5)  # Should be small for ₹1500 stock

    def test_cost_as_points(self):
        pts = cost_as_points(22000, 22300, is_index=True)
        self.assertGreater(pts, 0)

    def test_zero_quantity_handling(self):
        costs = calculate_round_trip_cost(100, 110, 0, is_index=False)
        # With 0 quantity, most costs should be tiny or zero
        self.assertGreaterEqual(costs["total"], 0)


# =====================================================
# ENGINE TESTS
# =====================================================

class TestEngine(unittest.TestCase):
    def test_is_index(self):
        self.assertTrue(_is_index("NSE:NIFTY 50"))
        self.assertTrue(_is_index("NSE:NIFTY BANK"))
        self.assertFalse(_is_index("NSE:RELIANCE"))

    def test_killzone(self):
        config = BacktestConfig()
        self.assertTrue(_in_killzone("2025-08-01T11:30:00", config))
        self.assertFalse(_in_killzone("2025-08-01T08:00:00", config))
        self.assertFalse(_in_killzone("2025-08-01T16:00:00", config))

    def test_resample_to_htf(self):
        # 12 five-minute candles → should produce 1 hourly candle
        candles = []
        base = datetime(2025, 8, 1, 9, 15)
        for i in range(12):
            dt = base + timedelta(minutes=5 * i)
            candles.append({
                "date": dt.isoformat(),
                "open": 100 + i, "high": 105 + i,
                "low": 95 + i, "close": 102 + i,
                "volume": 1000,
            })
        htf = resample_to_htf(candles, htf_minutes=60)
        self.assertGreaterEqual(len(htf), 1)
        # First HTF candle should have open from first 5m and close from last 5m in hour
        self.assertEqual(htf[0]["open"], 100)

    def test_trade_sl_hit(self):
        """Test that SL is properly detected."""
        config = BacktestConfig(
            enable_setup_a=False, enable_setup_b=False,
            enable_setup_c=False, enable_setup_d=False,
            apply_costs=False,
        )
        engine = BacktestEngine(config)

        # Manually open a LONG trade
        engine._open_trade({
            "symbol": "TEST", "setup": "SETUP-A", "direction": "LONG",
            "entry": 100.0, "sl": 95.0, "target": 115.0, "rr": 3.0,
        }, "2025-08-01T11:00:00", smc_score=6)

        self.assertEqual(len(engine.open_trades), 1)

        # Candle that hits SL
        engine._check_open_trades(
            {"open": 98, "high": 99, "low": 94, "close": 94.5},
            "2025-08-01T11:05:00"
        )

        self.assertEqual(len(engine.open_trades), 0)
        self.assertEqual(len(engine.trades), 1)
        self.assertEqual(engine.trades[0].exit_reason, "SL")
        self.assertAlmostEqual(engine.trades[0].r_multiple, -1.0, places=2)

    def test_trade_tp_hit(self):
        """Test that TP is properly detected."""
        config = BacktestConfig(apply_costs=False)
        engine = BacktestEngine(config)

        engine._open_trade({
            "symbol": "TEST", "setup": "SETUP-A", "direction": "LONG",
            "entry": 100.0, "sl": 95.0, "target": 115.0, "rr": 3.0,
        }, "2025-08-01T11:00:00", smc_score=6)

        # Candle that hits TP
        engine._check_open_trades(
            {"open": 112, "high": 116, "low": 111, "close": 115},
            "2025-08-01T12:00:00"
        )

        self.assertEqual(len(engine.open_trades), 0)
        self.assertEqual(engine.trades[0].exit_reason, "TP")
        self.assertAlmostEqual(engine.trades[0].r_multiple, 3.0, places=2)

    def test_sl_priority_over_tp(self):
        """When both SL and TP are hit in same candle, SL takes priority."""
        config = BacktestConfig(apply_costs=False)
        engine = BacktestEngine(config)

        engine._open_trade({
            "symbol": "TEST", "setup": "SETUP-A", "direction": "LONG",
            "entry": 100.0, "sl": 95.0, "target": 110.0, "rr": 2.0,
        }, "2025-08-01T11:00:00", smc_score=6)

        # Wide candle that hits both SL and TP
        engine._check_open_trades(
            {"open": 100, "high": 112, "low": 93, "close": 105},
            "2025-08-01T11:05:00"
        )

        self.assertEqual(engine.trades[0].exit_reason, "SL")

    def test_eod_close(self):
        """Open trades close at EOD."""
        config = BacktestConfig(apply_costs=False)
        engine = BacktestEngine(config)

        engine._open_trade({
            "symbol": "TEST", "setup": "SETUP-B", "direction": "SHORT",
            "entry": 100.0, "sl": 105.0, "target": 90.0, "rr": 2.0,
        }, "2025-08-01T11:00:00", smc_score=5)

        engine._eod_close(
            {"open": 98, "high": 99, "low": 97, "close": 97.5},
            "2025-08-01T15:25:00"
        )

        self.assertEqual(len(engine.open_trades), 0)
        self.assertEqual(engine.trades[0].exit_reason, "EOD")
        self.assertEqual(engine.trades[0].exit_price, 97.5)

    def test_daily_cap_respected(self):
        """Signals beyond daily cap are rejected."""
        config = BacktestConfig(max_daily_signals=2, apply_costs=False)
        engine = BacktestEngine(config)

        sig = {
            "symbol": "TEST", "setup": "SETUP-A", "direction": "LONG",
            "entry": 100.0, "sl": 95.0, "target": 115.0, "rr": 3.0,
        }

        engine._open_trade(sig, "2025-08-01T11:00:00", smc_score=6)
        engine._open_trade(sig, "2025-08-01T11:30:00", smc_score=6)

        self.assertFalse(engine._can_take_signal(sig, "2025-08-01T12:00:00"))


# =====================================================
# METRICS & RUNNER TESTS
# =====================================================

class TestMetrics(unittest.TestCase):
    def _make_trades(self, r_values):
        trades = []
        for i, r in enumerate(r_values):
            t = Trade(
                trade_id=i + 1, symbol="TEST", setup="SETUP-A",
                direction="LONG", entry_price=100, sl=95, target=115,
                rr=3.0, smc_score=6, entry_time=f"2025-08-{i+1:02d}T11:00:00",
                exit_time=f"2025-08-{i+1:02d}T14:00:00",
                exit_price=100 + r * 5, exit_reason="TP" if r > 0 else "SL",
            )
            t.r_multiple = r
            t.gross_pnl_pts = r * 5
            t.net_pnl_pts = r * 5
            trades.append(t)
        return trades

    def test_basic_metrics(self):
        trades = self._make_trades([3.0, -1.0, 3.0, -1.0, 3.0])
        m = calculate_metrics(trades)
        self.assertEqual(m["total_trades"], 5)
        self.assertEqual(m["winners"], 3)
        self.assertEqual(m["losers"], 2)
        self.assertAlmostEqual(m["win_rate"], 0.6, places=2)

    def test_profit_factor(self):
        trades = self._make_trades([3.0, -1.0, 3.0, -1.0])
        m = calculate_metrics(trades)
        self.assertAlmostEqual(m["profit_factor"], 3.0, places=2)

    def test_expectancy(self):
        trades = self._make_trades([2.0, -1.0, 2.0, -1.0])
        m = calculate_metrics(trades)
        self.assertAlmostEqual(m["expectancy_r"], 0.5, places=2)

    def test_max_drawdown(self):
        trades = self._make_trades([3.0, -1.0, -1.0, -1.0, 3.0])
        m = calculate_metrics(trades)
        self.assertAlmostEqual(m["max_drawdown_r"], 3.0, places=2)

    def test_max_consec_losses(self):
        trades = self._make_trades([3.0, -1.0, -1.0, -1.0, 3.0])
        m = calculate_metrics(trades)
        self.assertEqual(m["max_consecutive_losses"], 3)

    def test_empty_trades(self):
        m = calculate_metrics([])
        self.assertEqual(m["total_trades"], 0)

    def test_per_setup_breakdown(self):
        trades = self._make_trades([3.0, -1.0])
        trades[1].setup = "SETUP-B"
        m = calculate_metrics(trades)
        self.assertIn("SETUP-A", m["per_setup"])
        self.assertIn("SETUP-B", m["per_setup"])


class TestWalkForward(unittest.TestCase):
    def test_split_candles(self):
        candles = list(range(100))
        train, test = split_candles(candles, 0.7)
        self.assertEqual(len(train), 70)
        self.assertEqual(len(test), 30)

    def test_walk_forward_split(self):
        data = {
            "SYM": {"5m": list(range(100)), "1h": list(range(20))}
        }
        train, test = walk_forward_split(data, 0.7)
        self.assertEqual(len(train["SYM"]["5m"]), 70)
        self.assertEqual(len(test["SYM"]["5m"]), 30)
        self.assertEqual(len(train["SYM"]["1h"]), 14)
        self.assertEqual(len(test["SYM"]["1h"]), 6)


class TestSyntheticData(unittest.TestCase):
    def test_generates_correct_count(self):
        candles = generate_synthetic_candles(days=10)
        self.assertEqual(len(candles), 10 * 75)  # 75 candles per day

    def test_candle_format(self):
        candles = generate_synthetic_candles(days=5)
        c = candles[0]
        self.assertIn("date", c)
        self.assertIn("open", c)
        self.assertIn("high", c)
        self.assertIn("low", c)
        self.assertIn("close", c)
        self.assertIn("volume", c)
        # OHLC consistency
        self.assertGreaterEqual(c["high"], max(c["open"], c["close"]))
        self.assertLessEqual(c["low"], min(c["open"], c["close"]))


# =====================================================
# INTEGRATION TEST
# =====================================================

class TestIntegration(unittest.TestCase):
    def test_synthetic_end_to_end(self):
        """Full pipeline: generate data → run backtest → check metrics."""
        candles = generate_synthetic_candles(days=30, base_price=22000)
        config = BacktestConfig(apply_costs=True, min_smc_score=4)
        engine = BacktestEngine(config)
        trades = engine.run("NSE:NIFTY 50", candles)

        # Should produce at least some trades
        # (may be 0 if synthetic data doesn't trigger setups, but pipeline shouldn't crash)
        m = calculate_metrics(trades)
        self.assertIn("total_trades", m)
        self.assertIn("win_rate", m)

    def test_multiple_symbols(self):
        """Multi-symbol backtest doesn't crash."""
        candles1 = generate_synthetic_candles(days=20, base_price=22000)
        candles2 = generate_synthetic_candles(days=20, base_price=45000)
        data = {
            "NSE:NIFTY 50": {"5m": candles1},
            "NSE:NIFTY BANK": {"5m": candles2},
        }
        config = BacktestConfig(apply_costs=False, min_smc_score=4)
        engine = BacktestEngine(config)
        trades = engine.run_multi(data)
        m = calculate_metrics(trades)
        self.assertIn("total_trades", m)


if __name__ == "__main__":
    unittest.main(verbosity=2)
