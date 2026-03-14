"""
Feature Extractor — Extracts SMC structural features from trade context.
=========================================================================
Uses the existing smc_detectors.py primitives to analyze the candle data
surrounding each manual trade and produce a rich SMCFeatures object.
"""

import sys
import os
import logging
from datetime import datetime, time as dtime
from typing import List, Optional, Dict, Tuple

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from ai_learning.config import (
    SESSIONS, SWING_LEFT, SWING_RIGHT, OB_LOOKBACK, FVG_LOOKBACK,
    OB_DISPLACEMENT_MULT, OB_BODY_ATR_RATIO, FVG_MIN_GAP_ATR, LIQUIDITY_LOOKBACK,
    CANDLE_LOOKBACK,
)
from ai_learning.data.schemas import ManualTrade, SMCFeatures

# Import existing SMC detectors
try:
    from smc_detectors import (
        calculate_atr, detect_swing_points, classify_swings, determine_trend,
        detect_fvg, detect_all_fvgs, detect_order_block, detect_htf_bias,
        detect_choch, get_swing_range, is_discount_zone, is_premium_zone,
        get_zone_detail, detect_equal_highs, detect_equal_lows,
        liquidity_sweep_detected, get_ltf_structure_bias,
    )
except ImportError:
    logging.warning("smc_detectors not on path; feature extraction will be limited.")

try:
    from engine.displacement_detector import detect_displacement
except ImportError:
    detect_displacement = None

log = logging.getLogger("ai_learning.feature_extractor")


class FeatureExtractor:
    """
    Extracts SMC features from candle data surrounding a manual trade.

    Usage:
        extractor = FeatureExtractor()
        features = extractor.extract(trade, candles_5m, candles_15m, candles_1h)
    """

    def extract(
        self,
        trade: ManualTrade,
        ltf_candles: List[dict],
        htf_candles: Optional[List[dict]] = None,
        htf2_candles: Optional[List[dict]] = None,
    ) -> SMCFeatures:
        """
        Extract all SMC features for one trade.

        Args:
            trade: ManualTrade record
            ltf_candles: Lower timeframe candles (5m) around entry time
            htf_candles: Higher timeframe candles (15m/30m) for trend context
            htf2_candles: Even higher timeframe (1h/4h) for macro bias
        """
        features = SMCFeatures(trade_id=trade.trade_id)

        # Validate inputs
        if not ltf_candles or len(ltf_candles) < 20:
            log.warning(f"Insufficient LTF candles for {trade.trade_id}")
            return features

        atr = calculate_atr(ltf_candles)
        features.atr = atr

        # ─── 1. Trend Context ────────────────────────────────────────
        features.ltf_trend = self._detect_trend(ltf_candles)
        if htf_candles and len(htf_candles) >= 20:
            features.htf_trend = self._detect_trend(htf_candles)
        elif htf2_candles and len(htf2_candles) >= 20:
            features.htf_trend = self._detect_trend(htf2_candles)

        # Trend alignment
        dir_map = {"LONG": "BULLISH", "SHORT": "BEARISH"}
        expected = dir_map.get(trade.direction, "")
        features.trend_aligned = (
            features.htf_trend == expected or features.ltf_trend == expected
        )

        # ─── 2. Order Block ──────────────────────────────────────────
        ob_dir = "bullish" if trade.direction == "LONG" else "bearish"
        ob = detect_order_block(
            ltf_candles, ob_dir, lookback=OB_LOOKBACK,
            min_displacement_mult=OB_DISPLACEMENT_MULT,
            min_body_atr_ratio=OB_BODY_ATR_RATIO,
        )
        if ob:
            features.order_block_present = True
            features.ob_zone = ob
            ob_mid = (ob[0] + ob[1]) / 2
            features.ob_distance_atr = abs(trade.entry - ob_mid) / atr if atr > 0 else 0
            features.entry_inside_ob = ob[0] <= trade.entry <= ob[1]

        # ─── 3. Fair Value Gap ────────────────────────────────────────
        fvg_dir = "bullish" if trade.direction == "LONG" else "bearish"
        fvgs = detect_all_fvgs(
            ltf_candles, fvg_dir, lookback=FVG_LOOKBACK,
            min_gap_atr_ratio=FVG_MIN_GAP_ATR,
        )
        if fvgs:
            features.fvg_present = True
            # Find closest FVG to entry
            best_fvg = min(fvgs, key=lambda f: abs(
                (f["low"] + f["high"]) / 2 - trade.entry
            ))
            features.fvg_zone = (best_fvg["low"], best_fvg["high"])
            fvg_mid = (best_fvg["low"] + best_fvg["high"]) / 2
            features.fvg_distance_atr = abs(trade.entry - fvg_mid) / atr if atr > 0 else 0
            features.fvg_quality = best_fvg.get("quality", 0.5)

        # ─── 4. Liquidity ────────────────────────────────────────────
        features.liquidity_sweep = liquidity_sweep_detected(ltf_candles, LIQUIDITY_LOOKBACK)

        eq_highs = detect_equal_highs(ltf_candles, lookback=LIQUIDITY_LOOKBACK)
        eq_lows = detect_equal_lows(ltf_candles, lookback=LIQUIDITY_LOOKBACK)
        features.equal_highs_nearby = len(eq_highs) > 0
        features.equal_lows_nearby = len(eq_lows) > 0

        if features.liquidity_sweep:
            if trade.direction == "LONG":
                features.sweep_type = "SELLSIDE"
            else:
                features.sweep_type = "BUYSIDE"

        # ─── 5. Structure ────────────────────────────────────────────
        htf_bias = detect_htf_bias(ltf_candles if not htf_candles else htf_candles)
        if htf_bias:
            if (htf_bias == "LONG" and trade.direction == "LONG") or \
               (htf_bias == "SHORT" and trade.direction == "SHORT"):
                features.bos_detected = True

        choch_dir = "bullish" if trade.direction == "LONG" else "bearish"
        features.choch_detected = detect_choch(ltf_candles, choch_dir)

        if detect_displacement:
            disp = detect_displacement(ltf_candles)
            if disp:
                features.displacement_detected = True
                features.displacement_strength = disp.get("atr_ratio", 0)

        # ─── 6. Zone / Location ──────────────────────────────────────
        features.in_discount = is_discount_zone(ltf_candles, trade.entry)
        features.in_premium = is_premium_zone(ltf_candles, trade.entry)

        zone = get_zone_detail(ltf_candles, trade.entry)
        features.zone_detail = zone.get("zone", "UNKNOWN")
        features.in_ote = zone.get("in_ote", False)

        # ─── 7. Session / Timing ─────────────────────────────────────
        features.session, features.is_killzone = self._classify_session(trade.timestamp)
        features.minutes_from_open = self._minutes_from_open(trade.timestamp)

        # ─── 8. Volatility Context ───────────────────────────────────
        features.atr_percentile = self._atr_percentile(ltf_candles, atr)
        features.range_expansion = self._is_range_expansion(ltf_candles, atr)
        features.volatility_regime = self._volatility_regime(features.atr_percentile)

        # ─── 9. Candle Patterns at Entry ─────────────────────────────
        if len(ltf_candles) >= 2:
            last = ltf_candles[-1]
            prev = ltf_candles[-2]
            features.rejection_candle = self._is_rejection(last, trade.direction)
            features.engulfing_candle = self._is_engulfing(last, prev, trade.direction)
            features.pin_bar = self._is_pin_bar(last, trade.direction)

        # ─── 10. Confluence Score ────────────────────────────────────
        features.confluence_score = self._compute_confluence(features, trade)

        return features

    # ─── Trend Detection ──────────────────────────────────────────────

    def _detect_trend(self, candles: List[dict]) -> str:
        sh, sl = detect_swing_points(candles, SWING_LEFT, SWING_RIGHT)
        if not sh or not sl:
            return "UNKNOWN"
        classified = classify_swings(sh, sl)
        return determine_trend(classified)

    # ─── Session Classification ───────────────────────────────────────

    def _classify_session(self, timestamp_str: Optional[str]) -> Tuple[str, bool]:
        if not timestamp_str:
            return "UNKNOWN", False
        try:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            t = dt.time()
        except (ValueError, AttributeError):
            return "UNKNOWN", False

        session = "OTHER"
        is_kz = False
        for name, hours in SESSIONS.items():
            start = dtime(*map(int, hours["start"].split(":")))
            end = dtime(*map(int, hours["end"].split(":")))
            if start <= t <= end:
                session = name
                if "KILLZONE" in name:
                    is_kz = True
        return session, is_kz

    def _minutes_from_open(self, timestamp_str: Optional[str]) -> int:
        if not timestamp_str:
            return 0
        try:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            market_open = dt.replace(hour=9, minute=15, second=0, microsecond=0)
            diff = (dt - market_open).total_seconds() / 60
            return max(0, int(diff))
        except (ValueError, AttributeError):
            return 0

    # ─── Volatility Analysis ─────────────────────────────────────────

    def _atr_percentile(self, candles: List[dict], current_atr: float) -> float:
        """Where current ATR sits relative to recent ATR history."""
        if len(candles) < 50:
            return 0.5
        # Calculate rolling ATRs
        atrs = []
        for i in range(30, len(candles)):
            chunk = candles[max(0, i - 14):i]
            if len(chunk) >= 5:
                a = calculate_atr(chunk)
                if a > 0:
                    atrs.append(a)
        if not atrs:
            return 0.5
        below = sum(1 for a in atrs if a <= current_atr)
        return below / len(atrs)

    def _is_range_expansion(self, candles: List[dict], atr: float) -> bool:
        """Check if recent range is expanding (last 5 bars vs prior 20)."""
        if len(candles) < 25 or atr <= 0:
            return False
        recent = candles[-5:]
        prior = candles[-25:-5]
        recent_range = sum(c["high"] - c["low"] for c in recent) / 5
        prior_range = sum(c["high"] - c["low"] for c in prior) / 20
        return recent_range > prior_range * 1.3

    def _volatility_regime(self, pctile: float) -> str:
        if pctile < 0.20:
            return "LOW"
        elif pctile < 0.60:
            return "NORMAL"
        elif pctile < 0.85:
            return "HIGH"
        else:
            return "EXTREME"

    # ─── Candle Pattern Detection ─────────────────────────────────────

    def _is_rejection(self, candle: dict, direction: str) -> bool:
        body = abs(candle["close"] - candle["open"])
        full_range = candle["high"] - candle["low"]
        if full_range == 0:
            return False
        body_ratio = body / full_range
        if direction == "LONG":
            lower_wick = min(candle["open"], candle["close"]) - candle["low"]
            return lower_wick / full_range > 0.5 and body_ratio < 0.4
        else:
            upper_wick = candle["high"] - max(candle["open"], candle["close"])
            return upper_wick / full_range > 0.5 and body_ratio < 0.4

    def _is_engulfing(self, curr: dict, prev: dict, direction: str) -> bool:
        if direction == "LONG":
            return (curr["close"] > curr["open"] and
                    prev["close"] < prev["open"] and
                    curr["close"] > prev["open"] and
                    curr["open"] < prev["close"])
        else:
            return (curr["close"] < curr["open"] and
                    prev["close"] > prev["open"] and
                    curr["close"] < prev["open"] and
                    curr["open"] > prev["close"])

    def _is_pin_bar(self, candle: dict, direction: str) -> bool:
        body = abs(candle["close"] - candle["open"])
        full_range = candle["high"] - candle["low"]
        if full_range == 0:
            return False
        body_ratio = body / full_range
        if body_ratio > 0.3:
            return False
        if direction == "LONG":
            lower_wick = min(candle["open"], candle["close"]) - candle["low"]
            return lower_wick / full_range > 0.6
        else:
            upper_wick = candle["high"] - max(candle["open"], candle["close"])
            return upper_wick / full_range > 0.6

    # ─── Confluence Scoring ───────────────────────────────────────────

    def _compute_confluence(self, f: SMCFeatures, trade: ManualTrade) -> int:
        """Compute simplified confluence score (0-10)."""
        score = 0
        if f.trend_aligned:
            score += 2
        if f.order_block_present:
            score += 1
        if f.entry_inside_ob:
            score += 1
        if f.fvg_present:
            score += 1
        if f.liquidity_sweep:
            score += 1
        if f.bos_detected:
            score += 1
        if f.choch_detected:
            score += 1
        if f.displacement_detected:
            score += 1
        if f.rejection_candle or f.engulfing_candle or f.pin_bar:
            score += 1
        # Location bonus
        if (trade.direction == "LONG" and f.in_discount) or \
           (trade.direction == "SHORT" and f.in_premium):
            score += 1
        if f.in_ote:
            score += 1
        if f.is_killzone:
            score += 1
        return min(10, score)


class OfflineFeatureExtractor(FeatureExtractor):
    """
    Feature extractor that works offline with pre-loaded candle data.
    Useful for batch processing historical trades where you provide
    candle data from CSV/pickle rather than fetching live.
    """

    def extract_from_candle_dict(
        self,
        trade: ManualTrade,
        candle_data: Dict[str, List[dict]],
    ) -> SMCFeatures:
        """
        Extract features from a dict of {timeframe: [candles]}.
        Typically: {"5minute": [...], "15minute": [...], "60minute": [...]}
        """
        ltf = candle_data.get("5minute", candle_data.get("3minute", []))
        htf = candle_data.get("15minute", candle_data.get("30minute", []))
        htf2 = candle_data.get("60minute", candle_data.get("day", []))
        return self.extract(trade, ltf, htf, htf2)


class LiveFeatureExtractor(FeatureExtractor):
    """
    Feature extractor that fetches candle data live from Kite.
    Uses the existing fetch_ohlc infrastructure from smc_mtf_engine_v4.
    """

    def __init__(self):
        super().__init__()
        self._fetch_fn = None
        try:
            from smc_mtf_engine_v4 import fetch_ohlc
            self._fetch_fn = fetch_ohlc
        except ImportError:
            log.warning("Cannot import fetch_ohlc; live extraction unavailable.")

    def extract_live(self, trade: ManualTrade) -> SMCFeatures:
        """Fetch candles from broker API and extract features."""
        if not self._fetch_fn:
            log.error("fetch_ohlc not available")
            return SMCFeatures(trade_id=trade.trade_id)

        symbol = trade.symbol
        ltf = self._fetch_fn(symbol, "5minute", lookback=CANDLE_LOOKBACK)
        htf = self._fetch_fn(symbol, "15minute", lookback=CANDLE_LOOKBACK)
        htf2 = self._fetch_fn(symbol, "60minute", lookback=CANDLE_LOOKBACK)
        return self.extract(trade, ltf, htf, htf2)
