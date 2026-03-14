"""
Live Scanner — Real-time signal generation using AI-learned strategies.
========================================================================
Scans live market data and generates signals matching the trader's
learned style. Integrates with the existing Kite data pipeline
and Telegram alerting.
"""

import logging
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple

from ai_learning.config import (
    SCAN_INTERVAL_SECONDS, SIGNAL_COOLDOWN_MINUTES,
    MAX_DAILY_SIGNALS, MIN_SIGNAL_SCORE, SIMILARITY_THRESHOLD,
)
from ai_learning.data.schemas import (
    AISignal, TradingStyleProfile, StrategyRule, SMCFeatures,
)
from ai_learning.data.trade_store import TradeStore
from ai_learning.data.feature_extractor import FeatureExtractor
from ai_learning.strategy.rule_engine import RuleEvaluator
from ai_learning.learning.pattern_clusterer import PatternClusterer, PatternSimilarity

log = logging.getLogger("ai_learning.live_scanner")


class LiveScanner:
    """
    Scans live market data for setups matching the learned trading style.

    Modes:
        1. Rule-based: Evaluates generated StrategyRules on live features
        2. Similarity-based: Compares live features to learned cluster centroids
        3. Hybrid: Uses both (rule must fire AND similarity must exceed threshold)

    Usage:
        scanner = LiveScanner(profile, rules)

        # Single scan
        signal = scanner.scan_symbol("NSE:NIFTY 50", candles_5m, candles_15m)

        # Continuous scanning loop
        scanner.start_scanning(symbols, fetch_candles_func)
    """

    def __init__(
        self,
        profile: TradingStyleProfile,
        rules: List[StrategyRule],
        store: Optional[TradeStore] = None,
        mode: str = "hybrid",          # "rules" | "similarity" | "hybrid"
    ):
        self.profile = profile
        self.rules = rules
        self.store = store or TradeStore()
        self.mode = mode

        self.extractor = FeatureExtractor()
        self.evaluator = RuleEvaluator()
        self.clusterer = PatternClusterer()
        self.matcher = PatternSimilarity(self.clusterer)

        # State
        self._signal_history: Dict[str, datetime] = {}  # symbol -> last signal time
        self._daily_signal_count = 0
        self._daily_reset_date = datetime.now().date()

    def scan_symbol(
        self,
        symbol: str,
        candles_5m: List[dict],
        candles_15m: Optional[List[dict]] = None,
        candles_1h: Optional[List[dict]] = None,
    ) -> Optional[AISignal]:
        """
        Scan a single symbol for AI-learned setups.

        Returns:
            AISignal if a valid setup is detected, else None
        """
        # Daily reset
        if datetime.now().date() != self._daily_reset_date:
            self._daily_signal_count = 0
            self._daily_reset_date = datetime.now().date()

        # Limits check
        if self._daily_signal_count >= MAX_DAILY_SIGNALS:
            return None

        # Cooldown check
        last_signal = self._signal_history.get(symbol)
        if last_signal:
            diff = (datetime.now() - last_signal).total_seconds() / 60
            if diff < SIGNAL_COOLDOWN_MINUTES:
                return None

        if not candles_5m or len(candles_5m) < 30:
            return None

        # ─── Extract features from current market state ──────────
        from ai_learning.data.schemas import ManualTrade

        # Create a synthetic "trade" at current price for feature extraction
        current_price = candles_5m[-1]["close"]
        probe_trade = ManualTrade(
            trade_id=f"PROBE-{uuid.uuid4().hex[:6]}",
            symbol=symbol,
            timeframe="5minute",
            direction="LONG",  # We'll check both directions
            entry=current_price,
            stop_loss=current_price * 0.99,
            target=current_price * 1.02,
        )

        features_long = self.extractor.extract(probe_trade, candles_5m, candles_15m)

        probe_trade.direction = "SHORT"
        features_short = self.extractor.extract(probe_trade, candles_5m, candles_15m)

        # ─── Evaluate strategies ─────────────────────────────────
        best_signal = None
        best_score = 0

        for rule in self.rules:
            features = features_long if rule.direction in ("LONG", "BOTH") else features_short
            signal = self._evaluate_rule(rule, features, symbol, candles_5m)
            if signal and signal.score > best_score:
                best_score = signal.score
                best_signal = signal

        if best_signal and best_signal.score >= MIN_SIGNAL_SCORE:
            self._signal_history[symbol] = datetime.now()
            self._daily_signal_count += 1
            self.store.log_signal(best_signal.to_dict())
            log.info(f"🤖 AI Signal: {best_signal.direction} {symbol} "
                     f"@ {best_signal.entry:.1f} | "
                     f"Strategy: {best_signal.strategy_name} | "
                     f"Score: {best_signal.score:.1f}")
            return best_signal

        return None

    def _evaluate_rule(
        self,
        rule: StrategyRule,
        features: SMCFeatures,
        symbol: str,
        candles: List[dict],
    ) -> Optional[AISignal]:
        """Evaluate a single rule and produce a signal if triggered."""
        # Rule evaluation
        eval_result = self.evaluator.evaluate(rule, features)

        if self.mode == "rules" or self.mode == "hybrid":
            if not eval_result["triggered"]:
                return None

        # Similarity check (for hybrid/similarity modes)
        similarity = 0.0
        matched_cluster = None
        if self.mode in ("similarity", "hybrid"):
            vec = features.to_feature_vector()
            matched_cluster = self.matcher.match_to_cluster(
                vec, self.profile, SIMILARITY_THRESHOLD
            )
            if self.mode == "similarity" and matched_cluster is None:
                return None
            if self.mode == "hybrid" and matched_cluster is None:
                # Hybrid: rule fired but no similarity match — lower confidence
                pass
            if matched_cluster:
                ranking = self.matcher.rank_clusters(vec, self.profile)
                if ranking:
                    similarity = ranking[0][1]

        # Compute entry/SL/TP
        from smc_detectors import calculate_atr, detect_order_block
        atr = calculate_atr(candles)
        entry = candles[-1]["close"]
        direction = rule.direction if rule.direction != "BOTH" else (
            "LONG" if features.trend_aligned and features.htf_trend == "BULLISH"
            else "SHORT"
        )

        ob_dir = "bullish" if direction == "LONG" else "bearish"
        ob = detect_order_block(candles, ob_dir)

        if direction == "LONG":
            sl = (ob[0] - 0.5 * atr) if ob else (entry - 1.5 * atr)
            risk = entry - sl
            tp1 = entry + risk * 1.5
            tp2 = entry + risk * 2.5
        else:
            sl = (ob[1] + 0.5 * atr) if ob else (entry + 1.5 * atr)
            risk = sl - entry
            tp1 = entry - risk * 1.5
            tp2 = entry - risk * 2.5

        # Score: blend rule score and similarity
        rule_score = eval_result["score"]
        combined_score = rule_score
        if similarity > 0:
            combined_score = rule_score * 0.6 + similarity * 10 * 0.4

        # Confidence
        confidence = rule.confidence
        if matched_cluster:
            confidence = (rule.confidence + matched_cluster.confidence) / 2

        # Reasoning
        reasoning = eval_result["matched_conditions"].copy()
        if matched_cluster:
            reasoning.append(f"Matched pattern: {matched_cluster.name} "
                             f"(similarity={similarity:.2f})")
        if eval_result["missing_conditions"]:
            reasoning.append(f"Missing: {', '.join(eval_result['missing_conditions'][:3])}")

        signal = AISignal(
            signal_id=f"AI-{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now().isoformat(),
            symbol=symbol,
            direction=direction,
            entry=round(entry, 1),
            stop_loss=round(sl, 1),
            target1=round(tp1, 1),
            target2=round(tp2, 1),
            strategy_name=rule.strategy_name,
            score=round(combined_score, 2),
            confidence=round(confidence, 3),
            matched_pattern=matched_cluster.name if matched_cluster else "Rule-based",
            features={
                "confluence_score": features.confluence_score,
                "trend_aligned": features.trend_aligned,
                "htf_trend": features.htf_trend,
                "ob_present": features.order_block_present,
                "fvg_present": features.fvg_present,
                "liq_sweep": features.liquidity_sweep,
                "session": features.session,
            },
            reasoning=reasoning,
        )

        return signal

    def scan_batch(
        self,
        symbols: List[str],
        fetch_candles_func,
    ) -> List[AISignal]:
        """
        Scan multiple symbols in a batch.

        Args:
            symbols: List of trading symbols
            fetch_candles_func: Function(symbol, interval) -> List[dict]

        Returns:
            List of AISignal objects
        """
        signals = []
        for symbol in symbols:
            try:
                candles_5m = fetch_candles_func(symbol, "5minute")
                candles_15m = fetch_candles_func(symbol, "15minute")
                signal = self.scan_symbol(symbol, candles_5m, candles_15m)
                if signal:
                    signals.append(signal)
            except Exception as e:
                log.debug(f"Scan failed for {symbol}: {e}")
                continue

        return signals

    def start_scanning(
        self,
        symbols: List[str],
        fetch_candles_func,
        alert_func=None,
        interval: int = SCAN_INTERVAL_SECONDS,
    ):
        """
        Start continuous scanning loop.

        Args:
            symbols: Symbols to scan
            fetch_candles_func: Data fetcher
            alert_func: Optional alert function (e.g., Telegram sender)
            interval: Seconds between scans
        """
        log.info(f"Starting AI scanner: {len(symbols)} symbols, "
                 f"interval={interval}s, mode={self.mode}")
        log.info(f"Loaded {len(self.rules)} strategy rules, "
                 f"{len(self.profile.strategies)} learned patterns")

        while True:
            try:
                signals = self.scan_batch(symbols, fetch_candles_func)
                for signal in signals:
                    log.info(f"\n{signal.alert_text()}\n")
                    if alert_func:
                        try:
                            alert_func(signal)
                        except Exception as e:
                            log.error(f"Alert send failed: {e}")

                time.sleep(interval)

            except KeyboardInterrupt:
                log.info("Scanner stopped by user")
                break
            except Exception as e:
                log.error(f"Scanner error: {e}")
                time.sleep(interval * 2)
