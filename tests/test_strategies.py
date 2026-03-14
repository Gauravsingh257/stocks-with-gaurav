"""
tests/test_strategies.py — Tests for the strategy framework.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.base_strategy import Signal, BaseStrategy


class TestSignal:
    def test_signal_creation(self):
        sig = Signal(
            symbol="NSE:NIFTY 50",
            direction="LONG",
            setup="SETUP-A",
            entry=22000.0,
            stop_loss=21950.0,
            target=22100.0,
            rr=2.0,
            confidence=7.5,
        )
        assert sig.symbol == "NSE:NIFTY 50"
        assert sig.direction == "LONG"
        assert sig.rr == 2.0

    def test_risk_points(self):
        sig = Signal(
            symbol="TEST", direction="LONG", setup="A",
            entry=100.0, stop_loss=95.0, target=110.0, rr=2.0,
        )
        assert sig.risk_points == 5.0
        assert sig.reward_points == 10.0

    def test_to_dict(self):
        sig = Signal(
            symbol="TEST", direction="SHORT", setup="C",
            entry=100.0, stop_loss=105.0, target=90.0, rr=2.0,
            confidence=8.0,
        )
        d = sig.to_dict()
        assert d["symbol"] == "TEST"
        assert d["direction"] == "SHORT"
        assert d["confidence"] == 8.0
        assert "timestamp" in d


class TestBaseStrategy:
    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseStrategy()

    def test_concrete_strategy(self):
        class DummyStrategy(BaseStrategy):
            name = "Dummy"
            def detect(self, symbol, ltf, htf, current_time=None):
                return None
            def validate(self, signal):
                return True

        strat = DummyStrategy()
        assert strat.name == "Dummy"
        assert strat.enabled is True
        assert strat.detect("TEST", [], []) is None
        assert "Dummy" in repr(strat)
