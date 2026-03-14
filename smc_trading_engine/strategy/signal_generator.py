"""
Signal Generator
================
Generates structured trading signals by orchestrating the entry model.
Outputs JSON-friendly signal dicts with confidence scoring.
"""

import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime
import logging
import json

from smc_trading_engine.strategy.entry_model import (
    evaluate_entry, TradeSetup, RejectedTrade
)
from smc_trading_engine.strategy.risk_management import RiskManager
from smc_trading_engine.regime.regime_controller import RegimeControlFlags

logger = logging.getLogger(__name__)


class SignalGenerator:
    """
    Orchestrates signal generation across multiple symbols.
    Maintains rejected trade log for analysis.
    """

    def __init__(self, risk_mgr: RiskManager = None, min_confidence: float = 5.0):
        self.risk_mgr = risk_mgr or RiskManager()
        self.min_confidence = min_confidence
        self.signals: List[Dict] = []
        self.rejected: List[Dict] = []

    def generate_signal(
        self,
        symbol: str,
        htf_df: pd.DataFrame,
        ltf_df: pd.DataFrame,
        current_time=None,
        regime_flags: Optional[RegimeControlFlags] = None
    ) -> Optional[Dict]:
        """
        Generate a signal for a single symbol.

        Returns:
            Signal dict or None if no valid setup.
        """
        setup, rejection = evaluate_entry(
            symbol, htf_df, ltf_df,
            risk_mgr=self.risk_mgr,
            current_time=current_time,
            regime_flags=regime_flags
        )

        if rejection:
            rej_dict = {
                "symbol": rejection.symbol,
                "direction": rejection.direction,
                "reason": rejection.reason,
                "timestamp": str(rejection.timestamp),
                "details": rejection.details
            }
            self.rejected.append(rej_dict)
            logger.debug(f"[REJECTED] {symbol}: {rejection.reason}")
            return None

        if setup is None:
            return None

        if setup.confidence_score < self.min_confidence:
            self.rejected.append({
                "symbol": symbol,
                "direction": setup.direction,
                "reason": f"LOW_CONFIDENCE_{setup.confidence_score}",
                "timestamp": str(datetime.now())
            })
            return None

        signal = self._format_signal(setup)
        self.signals.append(signal)
        logger.info(f"[SIGNAL] {symbol} {setup.direction} | RR:{setup.rr} | Conf:{setup.confidence_score}")
        return signal

    def scan_symbols(
        self,
        symbol_data: Dict[str, Dict[str, pd.DataFrame]],
        current_time=None,
        regime_flags: Optional[RegimeControlFlags] = None
    ) -> List[Dict]:
        """
        Scan multiple symbols for signals.

        Args:
            symbol_data: {symbol: {"htf": DataFrame, "ltf": DataFrame}}
            current_time: Override time (backtest)
            regime_flags: Optional regime control flags

        Returns:
            List of generated signals
        """
        batch_signals = []
        for symbol, data in symbol_data.items():
            htf = data.get("htf")
            ltf = data.get("ltf")
            if htf is None or ltf is None:
                continue
            signal = self.generate_signal(symbol, htf, ltf, current_time, regime_flags)
            if signal:
                batch_signals.append(signal)
        return batch_signals

    def _format_signal(self, setup: TradeSetup) -> Dict:
        """Format TradeSetup into structured signal dict."""
        qty = self.risk_mgr.calculate_position_size(setup.entry, setup.stop_loss)
        return {
            "symbol": setup.symbol,
            "direction": setup.direction,
            "entry": setup.entry,
            "stop_loss": setup.stop_loss,
            "target": setup.target,
            "RR": setup.rr,
            "confidence_score": setup.confidence_score,
            "position_size": qty,
            "htf_bias": setup.htf_bias,
            "ob_quality": setup.ob_quality,
            "fvg_quality": setup.fvg_quality,
            "sweep_quality": setup.sweep_quality,
            "volume_confirmed": setup.volume_confirmed,
            "reasons": setup.reasons,
            "timestamp": str(setup.timestamp),
        }

    def get_rejected_log(self) -> List[Dict]:
        return self.rejected

    def get_rejection_summary(self) -> Dict[str, int]:
        """Summarize rejection reasons."""
        summary = {}
        for r in self.rejected:
            reason = r.get("reason", "UNKNOWN")
            summary[reason] = summary.get(reason, 0) + 1
        return dict(sorted(summary.items(), key=lambda x: x[1], reverse=True))

    def export_signals_json(self, path: str):
        with open(path, "w") as f:
            json.dump(self.signals, f, indent=2, default=str)

    def export_rejected_json(self, path: str):
        with open(path, "w") as f:
            json.dump(self.rejected, f, indent=2, default=str)

    def reset(self):
        self.signals.clear()
        self.rejected.clear()
        self.risk_mgr.reset_daily()
