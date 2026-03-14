"""
Pattern Clusterer — Discovers repeating trade patterns via clustering.
=======================================================================
Uses K-means, hierarchical clustering, and DBSCAN to group similar trades
by their SMC feature vectors, then profiles each cluster.
"""

import logging
import numpy as np
from typing import List, Tuple, Dict, Optional
from collections import Counter

from ai_learning.config import (
    MAX_CLUSTERS, MIN_CLUSTER_SIZE, CLUSTER_METHOD,
    WIN_RATE_CONFIDENCE_MIN_TRADES,
)
from ai_learning.data.schemas import (
    ManualTrade, SMCFeatures, StrategyCluster, TradingStyleProfile,
)

log = logging.getLogger("ai_learning.pattern_clusterer")


class PatternClusterer:
    """
    Clusters trades by SMC feature similarity to discover strategy archetypes.

    Pipeline:
        1. Normalize feature vectors
        2. Determine optimal K (silhouette analysis)
        3. Cluster using K-means / hierarchical / DBSCAN
        4. Profile each cluster (win rate, dominant features, entry conditions)
        5. Name clusters by dominant pattern
        6. Build TradingStyleProfile
    """

    def __init__(self, method: str = CLUSTER_METHOD):
        self.method = method
        self.scaler_mean: Optional[np.ndarray] = None
        self.scaler_std: Optional[np.ndarray] = None

    def cluster_trades(
        self,
        trades: List[ManualTrade],
        features: List[SMCFeatures],
    ) -> TradingStyleProfile:
        """
        Main entry: cluster trades and build a complete style profile.

        Args:
            trades: List of ManualTrade objects
            features: Corresponding SMCFeatures for each trade (same order)

        Returns:
            TradingStyleProfile with discovered strategy clusters
        """
        if len(trades) != len(features):
            raise ValueError("trades and features lists must be same length")

        n = len(trades)
        log.info(f"Clustering {n} trades using {self.method}")

        # Build feature matrix
        feature_names = SMCFeatures.feature_names()
        X = np.array([f.to_feature_vector() for f in features], dtype=np.float64)

        # Normalize
        X_norm = self._normalize(X)

        # Determine optimal K
        k = self._find_optimal_k(X_norm, max_k=min(MAX_CLUSTERS, n // 2))
        log.info(f"Optimal K = {k}")

        # Cluster
        labels = self._cluster(X_norm, k)

        # Build cluster profiles
        clusters = self._profile_clusters(trades, features, labels, X_norm, feature_names)

        # Build style profile
        profile = self._build_profile(trades, features, clusters, feature_names, X)

        return profile

    # ─── Normalization ────────────────────────────────────────────────

    def _normalize(self, X: np.ndarray) -> np.ndarray:
        """Z-score normalization, storing params for later inference."""
        self.scaler_mean = X.mean(axis=0)
        self.scaler_std = X.std(axis=0)
        # Avoid division by zero
        self.scaler_std[self.scaler_std == 0] = 1.0
        return (X - self.scaler_mean) / self.scaler_std

    def normalize_single(self, feature_vector: List[float]) -> np.ndarray:
        """Normalize a single feature vector using stored params."""
        if self.scaler_mean is None:
            return np.array(feature_vector)
        x = np.array(feature_vector)
        return (x - self.scaler_mean) / self.scaler_std

    # ─── Optimal K Selection ─────────────────────────────────────────

    def _find_optimal_k(self, X: np.ndarray, max_k: int = 8) -> int:
        """Find optimal K using silhouette score."""
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score

        if X.shape[0] < 6:
            return min(2, X.shape[0])

        best_k = 2
        best_score = -1

        for k in range(2, min(max_k + 1, X.shape[0])):
            try:
                km = KMeans(n_clusters=k, random_state=42, n_init=10)
                labels = km.fit_predict(X)
                if len(set(labels)) < 2:
                    continue
                score = silhouette_score(X, labels)
                log.debug(f"  K={k}  silhouette={score:.3f}")
                if score > best_score:
                    best_score = score
                    best_k = k
            except Exception as e:
                log.debug(f"  K={k} failed: {e}")
                continue

        return best_k

    # ─── Clustering ───────────────────────────────────────────────────

    def _cluster(self, X: np.ndarray, k: int) -> np.ndarray:
        """Run clustering algorithm."""
        if self.method == "kmeans":
            return self._kmeans(X, k)
        elif self.method == "hierarchical":
            return self._hierarchical(X, k)
        elif self.method == "dbscan":
            return self._dbscan(X)
        else:
            return self._kmeans(X, k)

    def _kmeans(self, X: np.ndarray, k: int) -> np.ndarray:
        from sklearn.cluster import KMeans
        km = KMeans(n_clusters=k, random_state=42, n_init=10, max_iter=300)
        return km.fit_predict(X)

    def _hierarchical(self, X: np.ndarray, k: int) -> np.ndarray:
        from sklearn.cluster import AgglomerativeClustering
        hc = AgglomerativeClustering(n_clusters=k, linkage="ward")
        return hc.fit_predict(X)

    def _dbscan(self, X: np.ndarray) -> np.ndarray:
        from sklearn.cluster import DBSCAN
        db = DBSCAN(eps=0.8, min_samples=max(2, len(X) // 10))
        return db.fit_predict(X)

    # ─── Cluster Profiling ────────────────────────────────────────────

    def _profile_clusters(
        self,
        trades: List[ManualTrade],
        features: List[SMCFeatures],
        labels: np.ndarray,
        X_norm: np.ndarray,
        feature_names: List[str],
    ) -> List[StrategyCluster]:
        """Profile each cluster: win rate, dominant features, naming."""
        clusters = []
        unique_labels = sorted(set(labels))
        if -1 in unique_labels:
            unique_labels.remove(-1)  # DBSCAN noise

        for cid in unique_labels:
            mask = labels == cid
            cluster_trades = [t for t, m in zip(trades, mask) if m]
            cluster_features = [f for f, m in zip(features, mask) if m]
            cluster_X = X_norm[mask]

            if len(cluster_trades) < MIN_CLUSTER_SIZE:
                continue

            # Win rate
            results = [t.result for t in cluster_trades if t.result]
            wins = sum(1 for r in results if r == "WIN")
            wr = wins / len(results) if results else 0.0

            # RR and PnL
            rrs = [t.rr_ratio for t in cluster_trades]
            pnls = [t.pnl_r for t in cluster_trades if t.pnl_r is not None]
            avg_rr = np.mean(rrs) if rrs else 0.0
            avg_pnl = np.mean(pnls) if pnls else 0.0

            # Expectancy
            if results:
                avg_win = np.mean([t.pnl_r for t in cluster_trades
                                   if t.pnl_r and t.result == "WIN"] or [avg_rr])
                avg_loss = np.mean([abs(t.pnl_r) for t in cluster_trades
                                    if t.pnl_r and t.result == "LOSS"] or [1.0])
                expectancy = (wr * avg_win) - ((1 - wr) * avg_loss)
            else:
                expectancy = 0.0

            # Dominant features (centroid analysis)
            centroid = cluster_X.mean(axis=0)
            dominant = {}
            for i, (name, val) in enumerate(zip(feature_names, centroid)):
                if abs(val) > 0.3:  # significantly above/below mean
                    dominant[name] = round(float(val), 3)

            # Direction preference
            dirs = Counter(t.direction for t in cluster_trades)
            pref_dir = dirs.most_common(1)[0][0] if dirs else "BOTH"
            if dirs.get("LONG", 0) > 0 and dirs.get("SHORT", 0) > 0:
                ratio = max(dirs.values()) / sum(dirs.values())
                pref_dir = dirs.most_common(1)[0][0] if ratio > 0.7 else "BOTH"

            # Session preference
            sessions = Counter(f.session for f in cluster_features if f.session != "UNKNOWN")
            pref_session = sessions.most_common(1)[0][0] if sessions else ""

            # Entry conditions (human-readable)
            entry_conditions = self._derive_entry_conditions(dominant, cluster_features)

            # Name the cluster
            name = self._name_cluster(dominant, entry_conditions, pref_dir)

            # Confidence based on sample size and consistency
            confidence = min(1.0, len(cluster_trades) / WIN_RATE_CONFIDENCE_MIN_TRADES)
            if wr > 0:
                # Adjust by win-rate stability (lower if close to 50%)
                confidence *= (0.5 + abs(wr - 0.5))

            cluster = StrategyCluster(
                cluster_id=cid,
                name=name,
                description=f"Cluster of {len(cluster_trades)} trades: {name}",
                trade_ids=[t.trade_id for t in cluster_trades],
                trade_count=len(cluster_trades),
                win_rate=round(wr, 3),
                avg_rr=round(float(avg_rr), 2),
                avg_pnl_r=round(float(avg_pnl), 2),
                expectancy=round(float(expectancy), 3),
                dominant_features=dominant,
                entry_conditions=entry_conditions,
                preferred_session=pref_session,
                preferred_direction=pref_dir,
                confidence=round(confidence, 3),
                centroid=centroid.tolist(),
            )
            clusters.append(cluster)

        return clusters

    # ─── Entry Condition Derivation ───────────────────────────────────

    def _derive_entry_conditions(
        self,
        dominant: Dict[str, float],
        features: List[SMCFeatures],
    ) -> List[str]:
        """Convert dominant features into human-readable entry conditions."""
        conditions = []

        if dominant.get("trend_aligned", 0) > 0.5:
            conditions.append("HTF trend alignment")

        if dominant.get("ob_present", 0) > 0.5:
            conditions.append("Order Block present")
        if dominant.get("entry_in_ob", 0) > 0.5:
            conditions.append("Entry inside Order Block")

        if dominant.get("fvg_present", 0) > 0.5:
            conditions.append("FVG confluence")

        if dominant.get("liq_sweep", 0) > 0.5:
            conditions.append("Liquidity sweep")

        if dominant.get("bos", 0) > 0.5:
            conditions.append("Break of Structure")
        if dominant.get("choch", 0) > 0.5:
            conditions.append("Change of Character")
        if dominant.get("displacement", 0) > 0.5:
            conditions.append("Displacement move")

        if dominant.get("in_discount", 0) > 0.5:
            conditions.append("Discount zone entry")
        if dominant.get("in_premium", 0) > 0.5:
            conditions.append("Premium zone entry")
        if dominant.get("in_ote", 0) > 0.5:
            conditions.append("OTE zone (62-79%)")

        if dominant.get("rejection", 0) > 0.5:
            conditions.append("Rejection candle")
        if dominant.get("engulfing", 0) > 0.5:
            conditions.append("Engulfing candle")
        if dominant.get("pin_bar", 0) > 0.5:
            conditions.append("Pin bar")

        if dominant.get("is_killzone", 0) > 0.5:
            conditions.append("Killzone timing")

        # Check for high volatility preference
        if dominant.get("vol_regime", 0) > 0.5:
            conditions.append("High volatility context")

        return conditions

    # ─── Cluster Naming ───────────────────────────────────────────────

    def _name_cluster(
        self,
        dominant: Dict[str, float],
        conditions: List[str],
        direction: str,
    ) -> str:
        """Generate a descriptive name for the cluster."""
        parts = []

        # Primary pattern
        if "Break of Structure" in conditions and "HTF trend alignment" in conditions:
            parts.append("HTF BOS Continuation")
        elif "Change of Character" in conditions:
            parts.append("CHoCH Reversal")
        elif "Liquidity sweep" in conditions:
            parts.append("Liquidity Sweep")
        elif "FVG confluence" in conditions and "Order Block present" in conditions:
            parts.append("OB+FVG Confluence")
        elif "Order Block present" in conditions:
            parts.append("Order Block Retest")
        elif "FVG confluence" in conditions:
            parts.append("FVG Rejection")
        elif "Displacement move" in conditions:
            parts.append("Displacement Entry")
        else:
            parts.append("Mixed Pattern")

        # Entry refinement
        if "OTE zone (62-79%)" in conditions:
            parts.append("at OTE")
        elif "Discount zone entry" in conditions:
            parts.append("in Discount")
        elif "Premium zone entry" in conditions:
            parts.append("in Premium")

        return " ".join(parts)

    # ─── Full Profile Builder ─────────────────────────────────────────

    def _build_profile(
        self,
        trades: List[ManualTrade],
        features: List[SMCFeatures],
        clusters: List[StrategyCluster],
        feature_names: List[str],
        X_raw: np.ndarray,
    ) -> TradingStyleProfile:
        """Build complete TradingStyleProfile."""
        from datetime import datetime

        # Overall stats
        results = [t.result for t in trades if t.result]
        wins = sum(1 for r in results if r == "WIN")
        overall_wr = wins / len(results) if results else 0.0
        overall_rr = float(np.mean([t.rr_ratio for t in trades])) if trades else 0.0

        pnls = [t.pnl_r for t in trades if t.pnl_r is not None]
        overall_exp = float(np.mean(pnls)) if pnls else 0.0

        # Feature importance via variance-based ranking
        feature_importance = {}
        if X_raw.shape[0] > 1:
            variances = X_raw.var(axis=0)
            total_var = variances.sum()
            for i, name in enumerate(feature_names):
                if total_var > 0:
                    feature_importance[name] = round(float(variances[i] / total_var), 4)
                else:
                    feature_importance[name] = 0.0

            # Also weight by correlation with outcome
            outcomes = np.array([
                1.0 if t.result == "WIN" else (0.0 if t.result == "LOSS" else 0.5)
                for t in trades
            ])
            if outcomes.std() > 0:
                for i, name in enumerate(feature_names):
                    col = X_raw[:, i]
                    if col.std() > 0:
                        corr = np.corrcoef(col, outcomes)[0, 1]
                        if not np.isnan(corr):
                            # Blend variance importance with outcome correlation
                            feature_importance[name] = round(
                                0.5 * feature_importance.get(name, 0) +
                                0.5 * abs(corr), 4
                            )

        # Distributions
        dir_dist = Counter(t.direction for t in trades)
        total = sum(dir_dist.values())
        dir_pct = {k: round(v / total, 3) for k, v in dir_dist.items()} if total else {}

        sess_dist = Counter(f.session for f in features if f.session != "UNKNOWN")
        total_s = sum(sess_dist.values())
        sess_pct = {k: round(v / total_s, 3) for k, v in sess_dist.items()} if total_s else {}

        sym_dist = Counter(t.symbol for t in trades)
        total_sym = sum(sym_dist.values())
        sym_pct = {k: round(v / total_sym, 3) for k, v in sym_dist.most_common(10)} if total_sym else {}

        profile = TradingStyleProfile(
            trader_id="default",
            created_at=datetime.now().isoformat(),
            total_trades=len(trades),
            overall_win_rate=round(overall_wr, 3),
            overall_avg_rr=round(overall_rr, 2),
            overall_expectancy=round(overall_exp, 3),
            strategies=sorted(clusters, key=lambda c: c.expectancy, reverse=True),
            preferred_session=sess_dist.most_common(1)[0][0] if sess_dist else "",
            preferred_direction=dir_dist.most_common(1)[0][0] if dir_dist else "",
            preferred_timeframe="5minute",
            feature_importance=feature_importance,
            direction_distribution=dir_pct,
            session_distribution=sess_pct,
            symbol_distribution=sym_pct,
        )

        return profile


class PatternSimilarity:
    """
    Computes similarity between a live setup and learned patterns.
    Uses cosine similarity against cluster centroids.
    """

    def __init__(self, clusterer: PatternClusterer):
        self.clusterer = clusterer

    def match_to_cluster(
        self,
        feature_vector: List[float],
        profile: TradingStyleProfile,
        threshold: float = 0.70,
    ) -> Optional[StrategyCluster]:
        """
        Find the best matching strategy cluster for a new feature vector.

        Returns:
            Best matching StrategyCluster, or None if below threshold.
        """
        if not profile.strategies:
            return None

        x = self.clusterer.normalize_single(feature_vector)

        best_match = None
        best_sim = -1

        for cluster in profile.strategies:
            if not cluster.centroid:
                continue
            centroid = np.array(cluster.centroid)
            sim = self._cosine_similarity(x, centroid)

            if sim > best_sim and sim >= threshold:
                best_sim = sim
                best_match = cluster

        if best_match:
            log.info(f"Matched to cluster '{best_match.name}' "
                     f"(similarity={best_sim:.3f})")
        return best_match

    def rank_clusters(
        self,
        feature_vector: List[float],
        profile: TradingStyleProfile,
    ) -> List[Tuple[StrategyCluster, float]]:
        """Rank all clusters by similarity. Returns [(cluster, similarity)]."""
        if not profile.strategies:
            return []

        x = self.clusterer.normalize_single(feature_vector)
        ranked = []

        for cluster in profile.strategies:
            if not cluster.centroid:
                continue
            centroid = np.array(cluster.centroid)
            sim = self._cosine_similarity(x, centroid)
            ranked.append((cluster, float(sim)))

        ranked.sort(key=lambda t: t[1], reverse=True)
        return ranked

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm == 0:
            return 0.0
        return float(dot / norm)
