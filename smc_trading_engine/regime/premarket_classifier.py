"""
Pre-Market Classifier Module
=============================
Combines global sentiment, OI positioning, volatility model, and
event flags into a single regime classification.

Four regimes (priority order):
    1. HIGH_VOL_EVENT  — event day + rising VIX
    2. TREND_DOWN      — bearish alignment + gap down
    3. TREND_UP        — bullish alignment + gap up
    4. ROTATIONAL      — fallback (low gap, balanced OI, low VIX)

Deterministic. No ML. Backtest-compatible.
"""

import logging
from enum import Enum
from typing import Dict, Optional

import pandas as pd

from smc_trading_engine.regime.global_data import compute_global_score
from smc_trading_engine.regime.oi_analyzer import compute_oi_bias_score
from smc_trading_engine.regime.volatility_model import (
    compute_atr,
    compute_volatility_regime,
)

logger = logging.getLogger(__name__)


# ─── Enums ─────────────────────────────────────────────
class RegimeType(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    ROTATIONAL = "ROTATIONAL"
    HIGH_VOL_EVENT = "HIGH_VOL_EVENT"


class DirectionalBias(str, Enum):
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"
    BOTH = "BOTH"


# ─── WEIGHTS ───────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "global_sentiment": 0.25,
    "oi_positioning": 0.25,
    "gap_vs_atr": 0.15,
    "volatility_model": 0.15,
    "event_flag": 0.20,
}


class PremarketClassifier:
    """
    Orchestrates all sub-modules and produces a final regime classification.

    Usage:
        classifier = PremarketClassifier()
        result = classifier.classify(
            global_data={...},
            oi_data={...},
            volatility_data={...},
            event_flag=False,
        )
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or DEFAULT_WEIGHTS.copy()

    # ──────────────────────────────────────────────────
    # PUBLIC: Classify from raw inputs
    # ──────────────────────────────────────────────────
    def classify_from_raw(
        self,
        # Global data
        sp500_change_pct: float = 0.0,
        nasdaq_change_pct: float = 0.0,
        dow_change_pct: float = 0.0,
        nikkei_change_pct: float = 0.0,
        hangseng_change_pct: float = 0.0,
        sgx_change_pct: float = 0.0,
        gift_nifty_price: float = 0.0,
        prev_nifty_close: float = 0.0,
        # OI data
        option_chain_df: Optional[pd.DataFrame] = None,
        spot_price: float = 0.0,
        # Volatility data
        ohlc_df: Optional[pd.DataFrame] = None,
        india_vix: float = 15.0,
        india_vix_prev: float = 15.0,
        gap_points: float = 0.0,
        prev_day_range: float = 0.0,
        # Event
        event_flag: bool = False,
    ) -> Dict:
        """
        Full classification pipeline from raw market data.

        Returns:
            Classification result dict.
        """
        # 1. Global score
        global_result = compute_global_score(
            sp500_change_pct=sp500_change_pct,
            nasdaq_change_pct=nasdaq_change_pct,
            dow_change_pct=dow_change_pct,
            nikkei_change_pct=nikkei_change_pct,
            hangseng_change_pct=hangseng_change_pct,
            sgx_change_pct=sgx_change_pct,
            gift_nifty_price=gift_nifty_price,
            prev_nifty_close=prev_nifty_close,
        )

        # 2. OI score
        if option_chain_df is not None and not option_chain_df.empty and spot_price > 0:
            oi_result = compute_oi_bias_score(option_chain_df, spot_price)
        else:
            oi_result = {
                "oi_bias": "NEUTRAL",
                "oi_score": 50,
                "pcr": 1.0,
                "call_wall": 0.0,
                "put_wall": 0.0,
                "max_pain": 0.0,
                "heavy_call_writing": False,
                "heavy_put_writing": False,
            }

        # 3. Volatility regime
        atr_14 = compute_atr(ohlc_df) if ohlc_df is not None else 0.0
        vol_result = compute_volatility_regime(
            atr_14=atr_14,
            india_vix=india_vix,
            india_vix_prev=india_vix_prev,
            gap_points=gap_points,
            prev_day_range=prev_day_range,
        )

        return self.classify(
            global_data=global_result,
            oi_data=oi_result,
            volatility_data=vol_result,
            gap_points=gap_points,
            atr_14=atr_14,
            event_flag=event_flag,
        )

    # ──────────────────────────────────────────────────
    # PUBLIC: Classify from pre-computed sub-module dicts
    # ──────────────────────────────────────────────────
    def classify(
        self,
        global_data: Dict,
        oi_data: Dict,
        volatility_data: Dict,
        gap_points: float = 0.0,
        atr_14: float = 0.0,
        event_flag: bool = False,
    ) -> Dict:
        """
        Combine sub-module outputs into final regime classification.

        Args:
            global_data:     Output of compute_global_score()
            oi_data:         Output of compute_oi_bias_score()
            volatility_data: Output of compute_volatility_regime()
            gap_points:      Raw gap in points (signed: + = gap up)
            atr_14:          ATR(14) in points
            event_flag:      True if known event day (RBI, expiry, etc.)

        Returns:
            {
                "regime": str,
                "directional_bias": str,
                "confidence": int (0-100),
                "composite_score": int,
                "components": {...},
            }
        """
        global_bias = global_data.get("global_bias", "NEUTRAL")
        global_score = global_data.get("global_score", 50)
        oi_bias = oi_data.get("oi_bias", "NEUTRAL")
        oi_score = oi_data.get("oi_score", 50)
        vol_regime = volatility_data.get("volatility_regime", "NORMAL")
        vol_score = volatility_data.get("volatility_score", 50)
        vix_rising = volatility_data.get("vix_rising", False)

        # Gap/ATR sub-score (0-100, 50=neutral)
        gap_atr_ratio = (gap_points / atr_14) if atr_14 > 0 else 0.0
        # Map signed ratio -2..+2 → 0..100
        gap_ratio_clamped = max(-2.0, min(2.0, gap_atr_ratio))
        gap_score = ((gap_ratio_clamped + 2.0) / 4.0) * 100.0
        gap_score = int(round(gap_score))

        # Event sub-score (0 or 100)
        event_score = 100 if event_flag else 0

        # ── Composite weighted score (0-100) ──
        composite = int(round(
            global_score * self.weights["global_sentiment"]
            + oi_score * self.weights["oi_positioning"]
            + gap_score * self.weights["gap_vs_atr"]
            + vol_score * self.weights["volatility_model"]
            + event_score * self.weights["event_flag"]
        ))
        composite = max(0, min(100, composite))

        # ── Regime classification (priority order) ──
        regime, bias = self._classify_regime(
            global_bias=global_bias,
            oi_bias=oi_bias,
            vol_regime=vol_regime,
            vix_rising=vix_rising,
            gap_points=gap_points,
            atr_14=atr_14,
            event_flag=event_flag,
        )

        # ── Confidence scoring ──
        confidence = self._compute_confidence(
            global_bias=global_bias,
            oi_bias=oi_bias,
            regime=regime,
            composite=composite,
        )

        result = {
            "regime": regime.value,
            "directional_bias": bias.value,
            "confidence": confidence,
            "composite_score": composite,
            "components": {
                "global": global_data,
                "oi": oi_data,
                "volatility": volatility_data,
                "gap_score": gap_score,
                "event_flag": event_flag,
            },
        }

        logger.info(
            f"[REGIME] {regime.value} | bias={bias.value} "
            f"confidence={confidence} composite={composite}"
        )

        return result

    # ──────────────────────────────────────────────────
    # PRIVATE: Rule-based regime determination
    # ──────────────────────────────────────────────────
    def _classify_regime(
        self,
        global_bias: str,
        oi_bias: str,
        vol_regime: str,
        vix_rising: bool,
        gap_points: float,
        atr_14: float,
        event_flag: bool,
    ) -> tuple:
        """
        Apply deterministic rules in priority order.

        Returns:
            (RegimeType, DirectionalBias)
        """
        gap_abs = abs(gap_points)
        gap_threshold = 0.5 * atr_14 if atr_14 > 0 else float("inf")

        # Priority 1: HIGH_VOL_EVENT
        if event_flag and vix_rising:
            return RegimeType.HIGH_VOL_EVENT, DirectionalBias.BOTH

        # Priority 2: TREND_DOWN
        if (
            global_bias == "BEARISH"
            and oi_bias == "BEARISH"
            and gap_points < 0
            and gap_abs > gap_threshold
        ):
            return RegimeType.TREND_DOWN, DirectionalBias.SHORT_ONLY

        # Priority 3: TREND_UP
        if (
            global_bias == "BULLISH"
            and oi_bias == "BULLISH"
            and gap_points > 0
            and gap_abs > gap_threshold
        ):
            return RegimeType.TREND_UP, DirectionalBias.LONG_ONLY

        # Priority 4: Partial trend (one strong confirmation)
        if global_bias == "BEARISH" and oi_bias == "BEARISH":
            return RegimeType.TREND_DOWN, DirectionalBias.SHORT_ONLY

        if global_bias == "BULLISH" and oi_bias == "BULLISH":
            return RegimeType.TREND_UP, DirectionalBias.LONG_ONLY

        # Priority 5: Event without rising VIX
        if event_flag:
            return RegimeType.HIGH_VOL_EVENT, DirectionalBias.BOTH

        # Default: ROTATIONAL
        return RegimeType.ROTATIONAL, DirectionalBias.BOTH

    def _compute_confidence(
        self,
        global_bias: str,
        oi_bias: str,
        regime: RegimeType,
        composite: int,
    ) -> int:
        """
        Compute classification confidence (0-100).

        Higher confidence when:
        - Multiple signals agree
        - Composite score is far from neutral (50)
        """
        confidence = 50  # Base

        # Agreement bonus
        if global_bias == oi_bias and global_bias != "NEUTRAL":
            confidence += 20

        # Composite deviation from neutral
        deviation = abs(composite - 50)
        confidence += int(deviation * 0.5)

        # Regime-specific adjustments
        if regime == RegimeType.HIGH_VOL_EVENT:
            confidence += 10  # Event days are distinctive

        return max(0, min(100, confidence))
