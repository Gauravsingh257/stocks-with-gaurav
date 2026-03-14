"""
PRISM — Pattern Recognition & Intelligence for Style Mapping (Agent 1)
========================================================================
Analyzes historical manual trades and refracts them into distinct strategy
clusters, revealing the trader's underlying SMC setup patterns.

Pipeline:
    1. Ingest manual trades (CSV, JSON, or direct input)
    2. For each trade, extract SMC features from candle data
    3. Cluster trades by feature similarity
    4. Profile each cluster (win rate, dominant setup, entry logic)
    5. Produce a TradingStyleProfile

The agent can work offline (with preloaded candle data) or live (fetching
candles from Kite Connect).
"""

import csv
import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from ai_learning.config import (
    MIN_TRADES_FOR_LEARNING, MODELS_DIR, EXPORTS_DIR, CHARTS_DIR,
)
from ai_learning.data.schemas import ManualTrade, SMCFeatures, TradingStyleProfile
from ai_learning.data.trade_store import TradeStore
from ai_learning.data.feature_extractor import (
    FeatureExtractor, OfflineFeatureExtractor, LiveFeatureExtractor,
)
from ai_learning.learning.pattern_clusterer import PatternClusterer

log = logging.getLogger("ai_learning.PRISM")


class PRISM:
    """
    PRISM (Agent 1): Refracts raw trade history into distinct strategy clusters.

    Usage:
        agent = PRISM()

        # Ingest trades
        agent.ingest_from_csv("my_trades.csv")
        # Or: agent.ingest_trade(ManualTrade(...))

        # Extract features (offline with candle data, or live from broker)
        agent.extract_features_offline(candle_data_map)
        # Or: agent.extract_features_live()

        # Learn the style
        profile = agent.learn()
        print(profile.summary())
    """

    def __init__(self, store: Optional[TradeStore] = None):
        self.store = store or TradeStore()
        self.extractor = FeatureExtractor()
        self.offline_extractor = OfflineFeatureExtractor()
        self.clusterer = PatternClusterer()
        self.profile: Optional[TradingStyleProfile] = None

    # ──────────────────────────────────────────────────────────────────
    #  PHASE 1: Trade Ingestion
    # ──────────────────────────────────────────────────────────────────

    def ingest_trade(self, trade: ManualTrade) -> str:
        """Add a single manual trade."""
        trade_id = self.store.add_trade(trade)
        log.info(f"Ingested trade {trade_id}: {trade.symbol} {trade.direction}")
        return trade_id

    def ingest_from_csv(self, csv_path: str) -> int:
        """
        Ingest trades from a CSV file.

        Expected columns:
            trade_id, symbol, timeframe, direction, entry, stop_loss, target,
            result, pnl_r, chart_image, notes, timestamp, exit_price, exit_time

        Minimum required: symbol, direction, entry, stop_loss, target
        """
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV not found: {csv_path}")

        trades = []
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                try:
                    trade = ManualTrade(
                        trade_id=row.get("trade_id", f"CSV-{i:04d}"),
                        symbol=row["symbol"].strip(),
                        timeframe=row.get("timeframe", "5minute").strip(),
                        direction=row["direction"].strip().upper(),
                        entry=float(row["entry"]),
                        stop_loss=float(row["stop_loss"]),
                        target=float(row["target"]),
                        result=row.get("result", "").strip().upper() or None,
                        pnl_r=float(row["pnl_r"]) if row.get("pnl_r") else None,
                        chart_image=row.get("chart_image", "").strip() or None,
                        notes=row.get("notes", "").strip(),
                        timestamp=row.get("timestamp", "").strip() or None,
                        exit_price=float(row["exit_price"]) if row.get("exit_price") else None,
                        exit_time=row.get("exit_time", "").strip() or None,
                    )
                    trades.append(trade)
                except (KeyError, ValueError) as e:
                    log.warning(f"Skipping CSV row {i}: {e}")
                    continue

        count = self.store.add_trades_bulk(trades)
        log.info(f"Ingested {count} trades from {csv_path}")
        return count

    def ingest_from_json(self, json_path: str) -> int:
        """Ingest trades from a JSON file (list of trade objects)."""
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"JSON not found: {json_path}")

        with open(path, "r") as f:
            data = json.load(f)

        trades = []
        for i, d in enumerate(data):
            try:
                trade = ManualTrade(
                    trade_id=d.get("trade_id", f"JSON-{i:04d}"),
                    symbol=d["symbol"],
                    timeframe=d.get("timeframe", "5minute"),
                    direction=d["direction"].upper(),
                    entry=float(d["entry"]),
                    stop_loss=float(d["stop_loss"]),
                    target=float(d["target"]),
                    result=d.get("result"),
                    pnl_r=float(d["pnl_r"]) if d.get("pnl_r") else None,
                    chart_image=d.get("chart_image"),
                    notes=d.get("notes", ""),
                    timestamp=d.get("timestamp"),
                    exit_price=float(d["exit_price"]) if d.get("exit_price") else None,
                    exit_time=d.get("exit_time"),
                )
                trades.append(trade)
            except (KeyError, ValueError) as e:
                log.warning(f"Skipping JSON entry {i}: {e}")
                continue

        count = self.store.add_trades_bulk(trades)
        log.info(f"Ingested {count} trades from {json_path}")
        return count

    def ingest_from_ledger(self) -> int:
        """
        Ingest trades from the existing trade_ledger_2026.csv.
        Maps the existing schema to ManualTrade format.
        """
        ledger_path = Path(__file__).resolve().parent.parent.parent / "trade_ledger_2026.csv"
        if not ledger_path.exists():
            log.warning("trade_ledger_2026.csv not found")
            return 0

        trades = []
        with open(ledger_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                try:
                    direction = row.get("direction", "").strip().upper()
                    if direction not in ("LONG", "SHORT"):
                        continue
                    entry = float(row.get("entry", 0))
                    exit_price = float(row.get("exit_price", 0)) if row.get("exit_price") else None
                    result = row.get("result", "").strip().upper()
                    pnl_r = float(row.get("pnl_r", 0)) if row.get("pnl_r") else None

                    # Estimate SL and target from setup info if not available
                    sl = entry * (0.99 if direction == "LONG" else 1.01)  # 1% default
                    target = entry * (1.02 if direction == "LONG" else 0.98)  # 2% default

                    trade = ManualTrade(
                        trade_id=f"LEDGER-{i:04d}",
                        symbol=row.get("symbol", "UNKNOWN").strip(),
                        timeframe="5minute",
                        direction=direction,
                        entry=entry,
                        stop_loss=sl,
                        target=target,
                        result=result if result in ("WIN", "LOSS", "BE") else None,
                        pnl_r=pnl_r,
                        notes=row.get("setup", ""),
                        timestamp=row.get("date", ""),
                        exit_price=exit_price,
                    )
                    trades.append(trade)
                except (KeyError, ValueError) as e:
                    log.warning(f"Skipping ledger row {i}: {e}")
                    continue

        if trades:
            count = self.store.add_trades_bulk(trades)
            log.info(f"Ingested {count} trades from trade ledger")
            return count
        return 0

    # ──────────────────────────────────────────────────────────────────
    #  PHASE 2: Feature Extraction
    # ──────────────────────────────────────────────────────────────────

    def extract_features_offline(
        self,
        candle_data_map: Dict[str, Dict[str, List[dict]]],
    ) -> int:
        """
        Extract SMC features using preloaded candle data.

        Args:
            candle_data_map: {trade_id: {timeframe: [candles]}}
                e.g., {"MT-001": {"5minute": [...], "15minute": [...]}}

        Returns:
            Number of features extracted.
        """
        trades = self.store.get_all_trades()
        count = 0

        for trade in trades:
            if trade.trade_id not in candle_data_map:
                log.debug(f"No candle data for {trade.trade_id}, skipping")
                continue
            try:
                candle_data = candle_data_map[trade.trade_id]
                features = self.offline_extractor.extract_from_candle_dict(
                    trade, candle_data
                )
                self.store.save_features(features)
                count += 1
                log.debug(f"Extracted features for {trade.trade_id}: "
                          f"score={features.confluence_score}")
            except Exception as e:
                log.error(f"Feature extraction failed for {trade.trade_id}: {e}")
                continue

        log.info(f"Extracted features for {count}/{len(trades)} trades")
        return count

    def extract_features_live(self) -> int:
        """
        Extract SMC features by fetching candle data live from Kite.
        Requires active Kite session.
        """
        live_ext = LiveFeatureExtractor()
        trades = self.store.get_all_trades()
        count = 0

        for trade in trades:
            # Check if already extracted
            existing = self.store.get_features(trade.trade_id)
            if existing:
                count += 1
                continue
            try:
                features = live_ext.extract_live(trade)
                self.store.save_features(features)
                count += 1
                log.debug(f"Live-extracted features for {trade.trade_id}")
            except Exception as e:
                log.error(f"Live extraction failed for {trade.trade_id}: {e}")
                continue

        log.info(f"Extracted features for {count}/{len(trades)} trades (live)")
        return count

    def extract_features_from_candles(
        self,
        trade: ManualTrade,
        ltf_candles: List[dict],
        htf_candles: Optional[List[dict]] = None,
    ) -> SMCFeatures:
        """Extract features for a single trade given candle data directly."""
        features = self.extractor.extract(trade, ltf_candles, htf_candles)
        self.store.save_features(features)
        return features

    # ──────────────────────────────────────────────────────────────────
    #  PHASE 3: Pattern Learning
    # ──────────────────────────────────────────────────────────────────

    def learn(self) -> TradingStyleProfile:
        """
        Run the full learning pipeline: cluster trades and build style profile.

        Returns:
            TradingStyleProfile with discovered strategy archetypes.
        """
        trades = self.store.get_all_trades()
        features = self.store.get_all_features()

        if len(trades) < MIN_TRADES_FOR_LEARNING:
            log.warning(
                f"Only {len(trades)} trades available "
                f"(need {MIN_TRADES_FOR_LEARNING}). "
                f"Profile may be unreliable."
            )

        # Align trades and features
        feature_map = {f.trade_id: f for f in features}
        aligned_trades = []
        aligned_features = []
        for t in trades:
            if t.trade_id in feature_map:
                aligned_trades.append(t)
                aligned_features.append(feature_map[t.trade_id])

        if len(aligned_trades) < 5:
            raise ValueError(
                f"Only {len(aligned_trades)} trades have features. "
                f"Extract features first using extract_features_offline() "
                f"or extract_features_live()."
            )

        # Cluster and profile
        log.info(f"Learning from {len(aligned_trades)} trades...")
        self.profile = self.clusterer.cluster_trades(aligned_trades, aligned_features)

        # Save profile
        profile_id = self.store.save_profile(self.profile)
        log.info(f"Saved profile {profile_id}")

        # Export
        self._export_profile(self.profile)

        return self.profile

    # ──────────────────────────────────────────────────────────────────
    #  Convenience Methods
    # ──────────────────────────────────────────────────────────────────

    def get_profile(self) -> Optional[TradingStyleProfile]:
        """Get the current or most recently saved profile."""
        if self.profile:
            return self.profile
        return self.store.get_latest_profile()

    def get_trade_count(self) -> int:
        return self.store.trade_count()

    def get_feature_coverage(self) -> Dict[str, int]:
        """How many trades have features extracted."""
        trades = self.store.get_all_trades()
        features = self.store.get_all_features()
        return {
            "total_trades": len(trades),
            "features_extracted": len(features),
            "missing": len(trades) - len(features),
        }

    def analyze_single_trade(
        self,
        trade: ManualTrade,
        ltf_candles: List[dict],
        htf_candles: Optional[List[dict]] = None,
    ) -> Dict:
        """
        Analyze a single trade and compare it to the learned profile.
        Useful for understanding why a trade matches (or doesn't) a cluster.
        """
        features = self.extractor.extract(trade, ltf_candles, htf_candles)

        result = {
            "trade": trade.to_dict(),
            "features": features.to_dict(),
            "confluence_score": features.confluence_score,
        }

        profile = self.get_profile()
        if profile:
            from ai_learning.learning.pattern_clusterer import PatternSimilarity
            matcher = PatternSimilarity(self.clusterer)
            ranking = matcher.rank_clusters(features.to_feature_vector(), profile)
            result["cluster_matches"] = [
                {"cluster": c.name, "similarity": round(s, 3),
                 "win_rate": c.win_rate, "expectancy": c.expectancy}
                for c, s in ranking
            ]

        return result

    # ──────────────────────────────────────────────────────────────────
    #  Export
    # ──────────────────────────────────────────────────────────────────

    def _export_profile(self, profile: TradingStyleProfile):
        """Export profile to JSON and human-readable text."""
        # JSON
        json_path = EXPORTS_DIR / "style_profile.json"
        with open(json_path, "w") as f:
            f.write(profile.to_json())
        log.info(f"Exported profile JSON: {json_path}")

        # Human-readable summary
        txt_path = EXPORTS_DIR / "style_profile_summary.txt"
        with open(txt_path, "w") as f:
            f.write(profile.summary())
        log.info(f"Exported profile summary: {txt_path}")

        # Feature importance chart data
        fi_path = EXPORTS_DIR / "feature_importance.json"
        with open(fi_path, "w") as f:
            json.dump(profile.feature_importance, f, indent=2)

    def export_training_data(self, output_path: Optional[str] = None) -> str:
        """Export all trades + features as a single JSON for external ML."""
        trades = self.store.get_all_trades()
        features = self.store.get_all_features()
        feature_map = {f.trade_id: f for f in features}

        data = []
        for t in trades:
            entry = t.to_dict()
            if t.trade_id in feature_map:
                entry["smc_features"] = feature_map[t.trade_id].to_dict()
                entry["feature_vector"] = feature_map[t.trade_id].to_feature_vector()
            data.append(entry)

        path = output_path or str(EXPORTS_DIR / "training_data.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        log.info(f"Exported training data ({len(data)} trades): {path}")
        return path
