"""
Pipeline Orchestrator — End-to-end coordination of the three AI agents.
========================================================================
Manages the complete workflow from trade ingestion to live scanning.
"""

import logging
import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ai_learning.config import EXPORTS_DIR, MIN_TRADES_FOR_LEARNING
from ai_learning.data.trade_store import TradeStore
from ai_learning.data.schemas import (
    ManualTrade, TradingStyleProfile, StrategyRule, OptimizationResult,
)
from ai_learning.agents.style_learner import PRISM
from ai_learning.agents.strategy_generator import FORGE
from ai_learning.agents.strategy_optimizer import SHIELD
from ai_learning.live_scanner import LiveScanner

log = logging.getLogger("ai_learning.pipeline")


class TradingAIPipeline:
    """
    Master orchestrator for the three-agent AI trading pipeline.

    Agents:
        PRISM  (Agent 1) — Pattern Recognition & Intelligence for Style Mapping
        FORGE  (Agent 2) — Framework for Optimal Rule Generation Engine
        SHIELD (Agent 3) — Statistical Hardening & Intelligence Engine for Live Deployment

    Workflow:
        1. Ingest manual trades
        2. PRISM:  Extract features & learn style clusters
        3. FORGE:  Generate algorithmic strategies from clusters
        4. SHIELD: Backtest, optimize & validate with Monte Carlo / Walk-Forward
        5. Deploy: Live scanning with learned strategies

    Usage:
        pipeline = TradingAIPipeline()

        # Step 1: Ingest trades
        pipeline.ingest_trades_csv("my_trades.csv")

        # Step 2: Extract features (needs candle data)
        pipeline.extract_features(candle_data_map)

        # Step 3: Full pipeline
        pipeline.run_full_pipeline()

        # Step 4: Live scanning
        pipeline.start_live_scanner(symbols, fetch_func)
    """

    def __init__(self):
        self.store = TradeStore()
        self.agent1 = PRISM(self.store)   # Pattern Recognition & Intelligence for Style Mapping
        self.agent2 = FORGE(self.store)   # Framework for Optimal Rule Generation Engine
        self.agent3 = SHIELD(self.store)  # Statistical Hardening & Intelligence Engine for Live Deployment

        self.profile: Optional[TradingStyleProfile] = None
        self.rules: List[StrategyRule] = []
        self.optimization_results: List[OptimizationResult] = []
        self.scanner: Optional[LiveScanner] = None

    # ──────────────────────────────────────────────────────────────────
    #  Ingestion
    # ──────────────────────────────────────────────────────────────────

    def ingest_trades_csv(self, csv_path: str) -> int:
        """Ingest trades from CSV."""
        return self.agent1.ingest_from_csv(csv_path)

    def ingest_trades_json(self, json_path: str) -> int:
        """Ingest trades from JSON."""
        return self.agent1.ingest_from_json(json_path)

    def ingest_from_ledger(self) -> int:
        """Ingest from existing trade_ledger_2026.csv."""
        return self.agent1.ingest_from_ledger()

    def ingest_trade(self, trade: ManualTrade) -> str:
        """Add a single trade."""
        return self.agent1.ingest_trade(trade)

    # ──────────────────────────────────────────────────────────────────
    #  Feature Extraction
    # ──────────────────────────────────────────────────────────────────

    def extract_features(
        self,
        candle_data_map: Dict[str, Dict[str, List[dict]]] = None,
        live: bool = False,
    ) -> int:
        """
        Extract SMC features for all trades.

        Args:
            candle_data_map: {trade_id: {timeframe: [candles]}} for offline mode
            live: If True, fetch candles from Kite API
        """
        if live:
            return self.agent1.extract_features_live()
        elif candle_data_map:
            return self.agent1.extract_features_offline(candle_data_map)
        else:
            raise ValueError("Provide candle_data_map or set live=True")

    # ──────────────────────────────────────────────────────────────────
    #  Full Pipeline
    # ──────────────────────────────────────────────────────────────────

    def run_full_pipeline(
        self,
        backtest_candles: Optional[List[dict]] = None,
        signal_funcs: Optional[Dict[str, Callable]] = None,
    ) -> Dict[str, Any]:
        """
        Run the complete three-agent pipeline.

        Args:
            backtest_candles: Historical candle data for optimization
            signal_funcs: Strategy signal functions for backtesting

        Returns:
            Pipeline report dict
        """
        report = {
            "pipeline_run": datetime.now().isoformat(),
            "stages": {},
        }

        # ─── Stage 1: Learn Trading Style ────────────────────────
        log.info("=" * 60)
        log.info("STAGE 1: TRADING STYLE LEARNING")
        log.info("=" * 60)

        coverage = self.agent1.get_feature_coverage()
        report["stages"]["ingestion"] = coverage

        if coverage["features_extracted"] < MIN_TRADES_FOR_LEARNING:
            log.warning(
                f"Only {coverage['features_extracted']} trades with features. "
                f"Need at least {MIN_TRADES_FOR_LEARNING}."
            )

        try:
            self.profile = self.agent1.learn()
            log.info("\n" + self.profile.summary())
            report["stages"]["learning"] = {
                "total_trades": self.profile.total_trades,
                "strategies_discovered": len(self.profile.strategies),
                "overall_win_rate": self.profile.overall_win_rate,
                "overall_expectancy": self.profile.overall_expectancy,
            }
        except Exception as e:
            log.error(f"Learning failed: {e}")
            report["stages"]["learning"] = {"error": str(e)}
            return report

        # ─── Stage 2: Generate Strategies ─────────────────────────
        log.info("\n" + "=" * 60)
        log.info("STAGE 2: STRATEGY GENERATION")
        log.info("=" * 60)

        self.rules = self.agent2.generate(self.profile)
        self.agent2.export_strategy_module(self.rules)
        self.agent2.export_engine_integration(self.rules)
        self.agent2.export_rules_json(self.rules)

        report["stages"]["generation"] = {
            "rules_generated": len(self.rules),
            "strategies": [
                {"name": r.strategy_name, "direction": r.direction,
                 "conditions": len(r.conditions), "confidence": r.confidence}
                for r in self.rules
            ],
        }

        log.info(f"Generated {len(self.rules)} strategy rules")
        for r in self.rules:
            log.info(f"  • {r.strategy_name}: {len(r.conditions)} conditions, "
                     f"confidence={r.confidence:.1%}")

        # ─── Stage 3: Optimize & Validate ─────────────────────────
        if backtest_candles and signal_funcs:
            log.info("\n" + "=" * 60)
            log.info("STAGE 3: OPTIMIZATION & VALIDATION")
            log.info("=" * 60)

            self.optimization_results = self.agent3.optimize_all(
                self.rules, backtest_candles, signal_funcs
            )

            optimized_config = self.agent3.generate_optimized_config(
                self.optimization_results
            )

            report["stages"]["optimization"] = {
                "total_optimized": len(self.optimization_results),
                "robust_count": sum(1 for r in self.optimization_results if r.is_robust),
                "config": optimized_config,
            }

            for opt in self.optimization_results:
                status = "✅ ROBUST" if opt.is_robust else "❌ NOT ROBUST"
                log.info(f"  {opt.strategy_name}: {status} "
                         f"(robustness={opt.robustness_score:.0%})")
        else:
            log.info("\n⚠️ Skipping optimization (no backtest data provided)")
            report["stages"]["optimization"] = {"skipped": True}

        # ─── Save Report ──────────────────────────────────────────
        report_path = EXPORTS_DIR / "pipeline_report.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        log.info(f"\nPipeline report saved: {report_path}")

        return report

    # ──────────────────────────────────────────────────────────────────
    #  Live Scanning
    # ──────────────────────────────────────────────────────────────────

    def start_live_scanner(
        self,
        symbols: List[str],
        fetch_candles_func: Callable,
        alert_func: Optional[Callable] = None,
        mode: str = "hybrid",
    ):
        """
        Start the live scanning loop.

        Args:
            symbols: List of symbols to scan
            fetch_candles_func: Function(symbol, interval) -> List[dict]
            alert_func: Optional alerting function (e.g., Telegram)
            mode: "rules" | "similarity" | "hybrid"
        """
        profile = self.profile or self.agent1.get_profile()
        rules = self.rules or self.agent2.get_rules()

        if not profile or not rules:
            raise RuntimeError(
                "No profile or rules available. Run run_full_pipeline() first."
            )

        self.scanner = LiveScanner(profile, rules, self.store, mode)
        self.scanner.start_scanning(symbols, fetch_candles_func, alert_func)

    def scan_once(
        self,
        symbols: List[str],
        fetch_candles_func: Callable,
    ) -> List:
        """Single-pass scan of all symbols."""
        profile = self.profile or self.agent1.get_profile()
        rules = self.rules or self.agent2.get_rules()

        if not profile or not rules:
            raise RuntimeError("No profile or rules. Run pipeline first.")

        scanner = LiveScanner(profile, rules, self.store)
        return scanner.scan_batch(symbols, fetch_candles_func)

    # ──────────────────────────────────────────────────────────────────
    #  Status & Diagnostics
    # ──────────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Get pipeline status."""
        return {
            "trades_ingested": self.store.trade_count(),
            "feature_coverage": self.agent1.get_feature_coverage(),
            "profile_available": self.profile is not None or self.agent1.get_profile() is not None,
            "rules_count": len(self.rules) or len(self.store.get_all_strategy_rules()),
            "optimization_results": len(self.optimization_results),
            "scanner_active": self.scanner is not None,
        }

    def retrain(
        self,
        new_trades: Optional[List[ManualTrade]] = None,
    ) -> TradingStyleProfile:
        """
        Re-learn the trading style with updated trade data.
        Use this after adding new manual trades to improve the system.
        """
        if new_trades:
            for t in new_trades:
                self.agent1.ingest_trade(t)

        self.profile = self.agent1.learn()
        self.rules = self.agent2.generate(self.profile)
        self.agent2.export_strategy_module(self.rules)

        log.info("Retrained with updated trade data")
        return self.profile
