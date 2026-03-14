"""
signals/pipeline.py — Signal processing pipeline.

Collects signals from multiple strategies, ranks them by confidence,
applies portfolio-level filters, and outputs actionable trade candidates.
"""

import logging
from datetime import datetime
from typing import Optional

from strategies.base_strategy import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class SignalPipeline:
    """
    Multi-strategy signal aggregation and filtering pipeline.

    Usage:
        pipeline = SignalPipeline()
        pipeline.register(SetupAStrategy())
        pipeline.register(SetupCStrategy())
        signals = pipeline.scan(symbol_data)
    """

    def __init__(self, max_signals_per_scan: int = 5, min_confidence: float = 5.0):
        self.strategies: list[BaseStrategy] = []
        self.max_signals_per_scan = max_signals_per_scan
        self.min_confidence = min_confidence
        self._results: list[Signal] = []
        self._rejected: list[dict] = []

    def register(self, strategy: BaseStrategy) -> None:
        self.strategies.append(strategy)
        logger.info("Registered strategy: %s", strategy)

    def scan(
        self,
        symbol_data: dict[str, dict[str, list[dict]]],
        current_time: Optional[datetime] = None,
    ) -> list[Signal]:
        """
        Run all registered strategies across all symbols.

        Args:
            symbol_data: {symbol: {"ltf": [...], "htf": [...]}}
            current_time: Override for backtesting

        Returns:
            List of validated signals, sorted by confidence descending.
        """
        candidates: list[Signal] = []

        for symbol, data in symbol_data.items():
            ltf = data.get("ltf", [])
            htf = data.get("htf", [])

            for strategy in self.strategies:
                if not strategy.enabled:
                    continue

                try:
                    signal = strategy.detect(symbol, ltf, htf, current_time)
                    if signal is None:
                        continue

                    if signal.confidence < self.min_confidence:
                        self._rejected.append({
                            "symbol": symbol,
                            "strategy": strategy.name,
                            "reason": f"LOW_CONFIDENCE_{signal.confidence}",
                        })
                        continue

                    if not strategy.validate(signal):
                        self._rejected.append({
                            "symbol": symbol,
                            "strategy": strategy.name,
                            "reason": "VALIDATION_FAILED",
                        })
                        continue

                    candidates.append(signal)

                except Exception:
                    logger.exception("Strategy %s failed on %s", strategy.name, symbol)

        candidates.sort(key=lambda s: s.confidence, reverse=True)
        selected = candidates[: self.max_signals_per_scan]
        self._results.extend(selected)

        logger.info(
            "Scan complete: %d candidates, %d selected, %d rejected",
            len(candidates), len(selected), len(self._rejected),
        )
        return selected

    @property
    def results(self) -> list[Signal]:
        return self._results

    @property
    def rejected(self) -> list[dict]:
        return self._rejected

    def reset(self) -> None:
        self._results.clear()
        self._rejected.clear()
