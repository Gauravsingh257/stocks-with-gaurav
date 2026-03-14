"""
tests/test_signal_pipeline.py — Tests for the signal pipeline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signals.pipeline import SignalPipeline
from strategies.base_strategy import BaseStrategy, Signal


class AlwaysSignalStrategy(BaseStrategy):
    name = "AlwaysSignal"

    def detect(self, symbol, ltf, htf, current_time=None):
        return Signal(
            symbol=symbol, direction="LONG", setup="TEST",
            entry=100.0, stop_loss=95.0, target=110.0, rr=2.0,
            confidence=8.0,
        )

    def validate(self, signal):
        return True


class NeverSignalStrategy(BaseStrategy):
    name = "NeverSignal"

    def detect(self, symbol, ltf, htf, current_time=None):
        return None

    def validate(self, signal):
        return True


class LowConfidenceStrategy(BaseStrategy):
    name = "LowConf"

    def detect(self, symbol, ltf, htf, current_time=None):
        return Signal(
            symbol=symbol, direction="LONG", setup="TEST",
            entry=100.0, stop_loss=95.0, target=110.0, rr=2.0,
            confidence=2.0,
        )

    def validate(self, signal):
        return True


class TestSignalPipeline:
    def test_empty_pipeline(self):
        pipeline = SignalPipeline()
        signals = pipeline.scan({})
        assert signals == []

    def test_register_strategy(self):
        pipeline = SignalPipeline()
        pipeline.register(AlwaysSignalStrategy())
        assert len(pipeline.strategies) == 1

    def test_scan_with_signal(self):
        pipeline = SignalPipeline()
        pipeline.register(AlwaysSignalStrategy())
        data = {"NSE:NIFTY 50": {"ltf": [{}], "htf": [{}]}}
        signals = pipeline.scan(data)
        assert len(signals) == 1
        assert signals[0].symbol == "NSE:NIFTY 50"

    def test_scan_no_signal(self):
        pipeline = SignalPipeline()
        pipeline.register(NeverSignalStrategy())
        data = {"TEST": {"ltf": [{}], "htf": [{}]}}
        signals = pipeline.scan(data)
        assert len(signals) == 0

    def test_low_confidence_rejected(self):
        pipeline = SignalPipeline(min_confidence=5.0)
        pipeline.register(LowConfidenceStrategy())
        data = {"TEST": {"ltf": [{}], "htf": [{}]}}
        signals = pipeline.scan(data)
        assert len(signals) == 0
        assert len(pipeline.rejected) == 1

    def test_max_signals_cap(self):
        pipeline = SignalPipeline(max_signals_per_scan=2)
        pipeline.register(AlwaysSignalStrategy())
        data = {f"SYM{i}": {"ltf": [{}], "htf": [{}]} for i in range(5)}
        signals = pipeline.scan(data)
        assert len(signals) == 2

    def test_reset(self):
        pipeline = SignalPipeline()
        pipeline.register(AlwaysSignalStrategy())
        pipeline.scan({"TEST": {"ltf": [{}], "htf": [{}]}})
        assert len(pipeline.results) > 0
        pipeline.reset()
        assert len(pipeline.results) == 0
        assert len(pipeline.rejected) == 0
