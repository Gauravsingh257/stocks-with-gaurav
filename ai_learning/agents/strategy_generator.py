"""
FORGE — Framework for Optimal Rule Generation Engine (Agent 2)
===============================================================
Forges the trader's learned style (from PRISM) into executable
algorithmic strategy modules ready for live deployment.

This agent:
    1. Takes a TradingStyleProfile
    2. For each strategy cluster, synthesizes algorithmic rules
    3. Generates Python strategy modules with detection functions
    4. Outputs ready-to-use strategy files compatible with the existing
       smc_mtf_engine_v4.py signal pipeline
"""

import logging
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from ai_learning.config import STRATEGY_OUTPUT_DIR, EXPORTS_DIR
from ai_learning.data.schemas import (
    TradingStyleProfile, StrategyCluster, StrategyRule, SMCFeatures,
)
from ai_learning.data.trade_store import TradeStore
from ai_learning.strategy.rule_engine import RuleEngine, RuleEvaluator

log = logging.getLogger("ai_learning.FORGE")


class FORGE:
    """
    FORGE (Agent 2): Forges learned style profiles into executable algorithmic strategies.

    Usage:
        agent2 = FORGE()
        rules = agent2.generate(profile)          # from PRISM
        agent2.export_strategy_module(rules)       # Python module
        agent2.export_engine_integration(rules)    # engine-compatible
    """

    def __init__(self, store: Optional[TradeStore] = None):
        self.store = store or TradeStore()
        self.rule_engine = RuleEngine()
        self.evaluator = RuleEvaluator()
        self.rules: List[StrategyRule] = []

    # ──────────────────────────────────────────────────────────────────
    #  Strategy Generation
    # ──────────────────────────────────────────────────────────────────

    def generate(self, profile: TradingStyleProfile) -> List[StrategyRule]:
        """
        Generate algorithmic rules from a TradingStyleProfile.

        Args:
            profile: Output from PRISM (PRISM.learn())

        Returns:
            List of StrategyRule objects
        """
        if not profile.strategies:
            log.warning("No strategy clusters in profile. Nothing to generate.")
            return []

        log.info(f"Generating rules for {len(profile.strategies)} strategy clusters...")

        self.rules = self.rule_engine.generate_all_rules(profile.strategies)

        # Save to store
        for rule in self.rules:
            self.store.save_strategy_rule(rule)

        log.info(f"Generated {len(self.rules)} strategy rules")
        return self.rules

    def get_rules(self) -> List[StrategyRule]:
        """Get generated rules (or load from store)."""
        if self.rules:
            return self.rules
        return self.store.get_all_strategy_rules()

    # ──────────────────────────────────────────────────────────────────
    #  Strategy Module Export
    # ──────────────────────────────────────────────────────────────────

    def export_strategy_module(
        self,
        rules: Optional[List[StrategyRule]] = None,
        output_dir: Optional[str] = None,
    ) -> str:
        """
        Generate a complete Python strategy module from the rules.

        Creates a file like:
            generated_strategies/ai_strategy_module.py

        Contains:
            - detect_order_block()
            - detect_fvg()
            - detect_liquidity_sweep()
            - detect_structure_break()
            - generate_trade_signal()
            - Strategy-specific check functions
        """
        rules = rules or self.get_rules()
        if not rules:
            raise ValueError("No rules available. Run generate() first.")

        out_dir = Path(output_dir) if output_dir else STRATEGY_OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        module_path = out_dir / "ai_strategy_module.py"

        code = self._generate_module_code(rules)

        with open(module_path, "w") as f:
            f.write(code)

        log.info(f"Exported strategy module: {module_path}")
        return str(module_path)

    def export_engine_integration(
        self,
        rules: Optional[List[StrategyRule]] = None,
    ) -> str:
        """
        Generate a detector function compatible with smc_mtf_engine_v4.py.

        Creates: generated_strategies/ai_setup_detector.py
        This can be imported into the main engine's scan loop.
        """
        rules = rules or self.get_rules()
        if not rules:
            raise ValueError("No rules available. Run generate() first.")

        module_path = STRATEGY_OUTPUT_DIR / "ai_setup_detector.py"
        code = self._generate_engine_detector(rules)

        with open(module_path, "w") as f:
            f.write(code)

        log.info(f"Exported engine detector: {module_path}")
        return str(module_path)

    # ──────────────────────────────────────────────────────────────────
    #  Code Generation
    # ──────────────────────────────────────────────────────────────────

    def _generate_module_code(self, rules: List[StrategyRule]) -> str:
        """Generate complete strategy module Python code."""
        timestamp = datetime.now().isoformat()
        strategy_names = [r.strategy_name for r in rules]

        code = f'''"""
AI-Generated Strategy Module
==============================
Auto-generated on {timestamp}
Strategies: {', '.join(strategy_names)}

This module contains detection functions derived from the trader's
manual trading style, learned via the AI Style Learning Agent.
"""

import sys
import os
import logging
from typing import Optional, Dict, List, Any, Tuple

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smc_detectors import (
    calculate_atr, detect_swing_points, classify_swings, determine_trend,
    detect_fvg, detect_all_fvgs, detect_order_block, detect_htf_bias,
    detect_choch, get_swing_range, is_discount_zone, is_premium_zone,
    get_zone_detail, detect_equal_highs, detect_equal_lows,
    liquidity_sweep_detected, get_ltf_structure_bias,
)

try:
    from engine.displacement_detector import detect_displacement
except ImportError:
    detect_displacement = None

log = logging.getLogger("ai_strategy")


# ═══════════════════════════════════════════════════════════════════════
#  SMC DETECTION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def detect_order_block_zone(
    candles: List[dict],
    direction: str,
    lookback: int = 30,
) -> Optional[Tuple[float, float]]:
    """
    Detect the nearest valid Order Block zone.

    Args:
        candles: OHLC candle list
        direction: "bullish" or "bearish"
        lookback: Number of bars to scan

    Returns:
        (zone_low, zone_high) or None
    """
    return detect_order_block(
        candles, direction, lookback=lookback,
        min_displacement_mult=2.0, min_body_atr_ratio=0.3,
    )


def detect_fvg_zone(
    candles: List[dict],
    direction: str,
    lookback: int = 30,
) -> Optional[Tuple[float, float]]:
    """
    Detect the nearest Fair Value Gap.

    Returns:
        (gap_low, gap_high) or None
    """
    return detect_fvg(candles, direction, lookback=lookback)


def detect_all_fvg_zones(
    candles: List[dict],
    direction: str,
) -> List[dict]:
    """Detect all active FVGs with quality scores."""
    return detect_all_fvgs(candles, direction)


def detect_liquidity_sweep(candles: List[dict], lookback: int = 50) -> bool:
    """Detect if the latest candle swept liquidity."""
    return liquidity_sweep_detected(candles, lookback)


def detect_structure_break(
    candles: List[dict],
    direction: str = None,
) -> Dict[str, Any]:
    """
    Detect structural breaks: BOS and CHoCH.

    Returns:
        {{"bos": bool, "choch": bool, "bias": str, "trend": str}}
    """
    sh, sl = detect_swing_points(candles)
    classified = classify_swings(sh, sl)
    trend = determine_trend(classified)
    htf_bias = detect_htf_bias(candles)

    choch = False
    if direction:
        choch_dir = "bullish" if direction.upper() in ("LONG", "BULLISH") else "bearish"
        choch = detect_choch(candles, choch_dir)

    return {{
        "bos": htf_bias is not None,
        "choch": choch,
        "bias": htf_bias or "NEUTRAL",
        "trend": trend,
    }}


def detect_displacement_move(candles: List[dict]) -> Optional[dict]:
    """Detect institutional displacement candle."""
    if detect_displacement:
        return detect_displacement(candles)
    return None


def analyze_zone_location(
    candles: List[dict],
    price: float,
) -> Dict[str, Any]:
    """
    Analyze where price sits in the market structure.

    Returns:
        {{"in_discount": bool, "in_premium": bool, "in_ote": bool,
          "zone": str, "swing_range": (low, high)}}
    """
    zone = get_zone_detail(candles, price)
    swing_range = get_swing_range(candles)
    return {{
        "in_discount": is_discount_zone(candles, price),
        "in_premium": is_premium_zone(candles, price),
        "in_ote": zone.get("in_ote", False),
        "zone": zone.get("zone", "UNKNOWN"),
        "swing_range": swing_range,
    }}


def detect_confirmation_candle(
    candle: dict,
    prev_candle: dict,
    direction: str,
) -> Dict[str, bool]:
    """
    Check for confirmation candle patterns.

    Returns:
        {{"rejection": bool, "engulfing": bool, "pin_bar": bool, "any": bool}}
    """
    body = abs(candle["close"] - candle["open"])
    full_range = candle["high"] - candle["low"]
    if full_range == 0:
        return {{"rejection": False, "engulfing": False, "pin_bar": False, "any": False}}

    body_ratio = body / full_range

    # Rejection
    rejection = False
    if direction.upper() in ("LONG", "BULLISH"):
        lower_wick = min(candle["open"], candle["close"]) - candle["low"]
        rejection = lower_wick / full_range > 0.5 and body_ratio < 0.4
    else:
        upper_wick = candle["high"] - max(candle["open"], candle["close"])
        rejection = upper_wick / full_range > 0.5 and body_ratio < 0.4

    # Engulfing
    engulfing = False
    if direction.upper() in ("LONG", "BULLISH"):
        engulfing = (candle["close"] > candle["open"] and
                     prev_candle["close"] < prev_candle["open"] and
                     candle["close"] > prev_candle["open"] and
                     candle["open"] < prev_candle["close"])
    else:
        engulfing = (candle["close"] < candle["open"] and
                     prev_candle["close"] > prev_candle["open"] and
                     candle["close"] < prev_candle["open"] and
                     candle["open"] > prev_candle["close"])

    # Pin bar
    pin_bar = body_ratio < 0.3
    if direction.upper() in ("LONG", "BULLISH"):
        lower_wick = min(candle["open"], candle["close"]) - candle["low"]
        pin_bar = pin_bar and (lower_wick / full_range > 0.6)
    else:
        upper_wick = candle["high"] - max(candle["open"], candle["close"])
        pin_bar = pin_bar and (upper_wick / full_range > 0.6)

    return {{
        "rejection": rejection,
        "engulfing": engulfing,
        "pin_bar": pin_bar,
        "any": rejection or engulfing or pin_bar,
    }}


# ═══════════════════════════════════════════════════════════════════════
#  STRATEGY-SPECIFIC SIGNAL GENERATORS
# ═══════════════════════════════════════════════════════════════════════

'''
        # Generate strategy functions
        for rule in rules:
            code += self._generate_strategy_function(rule)
            code += "\n\n"

        # Generate master signal function
        code += self._generate_master_signal_function(rules)

        return code

    def _generate_strategy_function(self, rule: StrategyRule) -> str:
        """Generate a single strategy detection function."""
        func_name = self._to_func_name(rule.strategy_name)
        direction = rule.direction

        cond_checks = []
        for i, cond in enumerate(rule.conditions):
            feat = cond["feature"]
            op = cond["operator"]
            val = cond["value"]
            weight = cond.get("weight", 1.0)
            desc = cond.get("description", feat)
            val_str = repr(val)
            cond_checks.append(
                f'    # {desc}\n'
                f'    if {feat} {op} {val_str}:\n'
                f'        score += {weight}\n'
                f'        reasons.append("{desc}")'
            )

        conds_code = "\n\n".join(cond_checks)
        threshold = rule.min_score

        code = f'''def detect_{func_name}(
    candles_5m: List[dict],
    candles_15m: Optional[List[dict]] = None,
    candles_1h: Optional[List[dict]] = None,
) -> Optional[Dict[str, Any]]:
    """
    AI-Learned Strategy: {rule.strategy_name}
    Direction: {direction}
    Conditions: {len(rule.conditions)}
    Min Score: {threshold}
    Confidence: {rule.confidence:.1%}
    Source Cluster: {rule.source_cluster}
    """
    if not candles_5m or len(candles_5m) < 30:
        return None

    atr = calculate_atr(candles_5m)
    if atr <= 0:
        return None

    score = 0.0
    reasons = []
    entry = candles_5m[-1]["close"]

    # --- Detect SMC elements ---
    ob_dir = "{'bullish' if direction in ('LONG', 'BOTH') else 'bearish'}"
    ob = detect_order_block(candles_5m, ob_dir)
    fvgs = detect_all_fvgs(candles_5m, ob_dir)
    structure = detect_structure_break(candles_5m, "{direction}")
    liq_sweep = detect_liquidity_sweep(candles_5m)
    zone_info = analyze_zone_location(candles_5m, entry)

    # Compute derived features
    trend_aligned = False
    if candles_15m and len(candles_15m) >= 20:
        htf_bias = detect_htf_bias(candles_15m)
        if htf_bias == "{direction if direction != 'BOTH' else 'LONG'}":
            trend_aligned = True
    elif structure["bias"] == "{direction if direction != 'BOTH' else 'LONG'}":
        trend_aligned = True

    order_block_present = ob is not None
    entry_inside_ob = ob is not None and ob[0] <= entry <= ob[1]
    ob_distance_atr = abs(entry - (ob[0]+ob[1])/2) / atr if ob and atr > 0 else 99
    fvg_present = len(fvgs) > 0
    fvg_quality = max((f.get("quality", 0) for f in fvgs), default=0)
    fvg_distance_atr = 0
    if fvgs:
        best_fvg = min(fvgs, key=lambda f: abs((f["low"]+f["high"])/2 - entry))
        fvg_distance_atr = abs((best_fvg["low"]+best_fvg["high"])/2 - entry) / atr

    liquidity_sweep = liq_sweep
    equal_highs_nearby = len(detect_equal_highs(candles_5m)) > 0
    equal_lows_nearby = len(detect_equal_lows(candles_5m)) > 0
    bos_detected = structure["bos"]
    choch_detected = structure["choch"]

    disp = detect_displacement_move(candles_5m)
    displacement_detected = disp is not None
    displacement_strength = disp.get("atr_ratio", 0) if disp else 0

    in_discount = zone_info["in_discount"]
    in_premium = zone_info["in_premium"]
    in_ote = zone_info["in_ote"]

    # Candle patterns
    if len(candles_5m) >= 2:
        conf = detect_confirmation_candle(candles_5m[-1], candles_5m[-2], "{direction}")
        rejection_candle = conf["rejection"]
        engulfing_candle = conf["engulfing"]
        pin_bar = conf["pin_bar"]
    else:
        rejection_candle = engulfing_candle = pin_bar = False

    confluence_score = sum([
        2 if trend_aligned else 0,
        1 if order_block_present else 0,
        1 if entry_inside_ob else 0,
        1 if fvg_present else 0,
        1 if liquidity_sweep else 0,
        1 if bos_detected else 0,
        1 if choch_detected else 0,
        1 if displacement_detected else 0,
        1 if rejection_candle or engulfing_candle or pin_bar else 0,
    ])

    atr_percentile = 0.5  # TODO: compute from history
    is_killzone = False    # TODO: compute from timestamp

    # --- Score conditions ---
{conds_code}

    # --- Check threshold ---
    if score < {threshold}:
        return None

    # --- Calculate entry/SL/TP ---
    if ob:
        sl = ob[0] - 0.5 * atr if "{direction}" in ("LONG", "BOTH") else ob[1] + 0.5 * atr
    else:
        sl = entry - 1.5 * atr if "{direction}" in ("LONG", "BOTH") else entry + 1.5 * atr

    risk = abs(entry - sl)
    tp1 = entry + risk * 1.5 if "{direction}" in ("LONG", "BOTH") else entry - risk * 1.5
    tp2 = entry + risk * 2.5 if "{direction}" in ("LONG", "BOTH") else entry - risk * 2.5

    return {{
        "setup": "AI-{func_name.upper()[:20]}",
        "strategy_name": "{rule.strategy_name}",
        "direction": "{direction}",
        "entry": round(entry, 1),
        "sl": round(sl, 1),
        "target": round(tp1, 1),
        "tp2": round(tp2, 1),
        "rr": round(risk / abs(entry - sl), 2) if abs(entry - sl) > 0 else 0,
        "score": round(score, 1),
        "confluence_score": confluence_score,
        "reasons": reasons,
        "ob": ob,
        "fvg": (fvgs[0]["low"], fvgs[0]["high"]) if fvgs else None,
        "confidence": {rule.confidence:.3f},
    }}
'''
        return code

    def _generate_master_signal_function(self, rules: List[StrategyRule]) -> str:
        """Generate the master signal detection function."""
        func_calls = []
        for rule in rules:
            fn = self._to_func_name(rule.strategy_name)
            func_calls.append(
                f'    result = detect_{fn}(candles_5m, candles_15m, candles_1h)\n'
                f'    if result:\n'
                f'        result["symbol"] = symbol\n'
                f'        signals.append(result)'
            )

        calls_code = "\n\n".join(func_calls)

        return f'''
# ═══════════════════════════════════════════════════════════════════════
#  MASTER SIGNAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════

def generate_trade_signal(
    symbol: str,
    candles_5m: List[dict],
    candles_15m: Optional[List[dict]] = None,
    candles_1h: Optional[List[dict]] = None,
) -> List[Dict[str, Any]]:
    """
    Scan for all AI-learned setups on a symbol.

    Args:
        symbol: Trading symbol (e.g., "NSE:NIFTY 50")
        candles_5m: 5-minute candle data
        candles_15m: 15-minute candle data (optional, for HTF context)
        candles_1h: 1-hour candle data (optional, for macro bias)

    Returns:
        List of signal dicts, sorted by score (best first)
    """
    signals = []

{calls_code}

    # Sort by score (highest first) and deduplicate
    signals.sort(key=lambda s: s.get("score", 0), reverse=True)

    # Return best signal only (avoid conflicting signals)
    return signals[:1] if signals else []


def detect_trading_setup(symbol: str, candles: List[dict]) -> Optional[Dict]:
    """
    Simple interface: detect the best setup for a symbol.
    Compatible with the existing engine's scan_symbol() pattern.

    Usage:
        signal = detect_trading_setup("NSE:NIFTY 50", candles_5m)
        if signal:
            send_trade_alert(signal)
    """
    results = generate_trade_signal(symbol, candles)
    return results[0] if results else None
'''

    def _generate_engine_detector(self, rules: List[StrategyRule]) -> str:
        """Generate a detector compatible with smc_mtf_engine_v4.py."""
        timestamp = datetime.now().isoformat()

        code = f'''"""
AI Setup Detector — Engine Integration Module
===============================================
Auto-generated on {timestamp}
Integrates AI-learned strategies with smc_mtf_engine_v4.py scan loop.

Usage in smc_mtf_engine_v4.py:
    from generated_strategies.ai_setup_detector import detect_ai_setup

    # Inside scan_symbol():
    ai_signal = detect_ai_setup(symbol, tf_data)
    if ai_signal:
        process_signal(ai_signal)
"""

import logging
from typing import Optional, Dict, List, Any

log = logging.getLogger("ai_setup_detector")

# Import the generated strategy module
try:
    from generated_strategies.ai_strategy_module import generate_trade_signal
except ImportError:
    log.error("ai_strategy_module not found. Run Agent 2 to generate it.")
    generate_trade_signal = None


def detect_ai_setup(
    symbol: str,
    tf_data: Dict[str, List[dict]],
) -> Optional[Dict[str, Any]]:
    """
    Detect AI-learned setups. Compatible with smc_mtf_engine_v4.py.

    Args:
        symbol: e.g., "NSE:NIFTY 50"
        tf_data: {{"5minute": [...], "15minute": [...], "60minute": [...]}}

    Returns:
        Signal dict or None
    """
    if not generate_trade_signal:
        return None

    candles_5m = tf_data.get("5minute", [])
    candles_15m = tf_data.get("15minute", [])
    candles_1h = tf_data.get("60minute", [])

    if not candles_5m or len(candles_5m) < 30:
        return None

    signals = generate_trade_signal(symbol, candles_5m, candles_15m, candles_1h)

    if not signals:
        return None

    # Take best signal
    signal = signals[0]

    # Format for engine compatibility
    return {{
        "setup": signal.get("setup", "AI-LEARNED"),
        "symbol": symbol,
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "target": signal["target"],
        "rr": round(abs(signal["target"] - signal["entry"]) /
                     abs(signal["entry"] - signal["sl"]), 2)
                if abs(signal["entry"] - signal["sl"]) > 0 else 0,
        "ob": signal.get("ob"),
        "fvg": signal.get("fvg"),
        "smc_score": signal.get("confluence_score", 0),
        "ai_score": signal.get("score", 0),
        "ai_confidence": signal.get("confidence", 0),
        "ai_strategy": signal.get("strategy_name", ""),
        "analysis": " | ".join(signal.get("reasons", [])),
    }}
'''
        return code

    # ─── Utilities ────────────────────────────────────────────────────

    @staticmethod
    def _to_func_name(name: str) -> str:
        """Convert strategy name to valid Python function name."""
        return (name.lower()
                .replace(" ", "_")
                .replace("+", "_")
                .replace("-", "_")
                .replace("(", "")
                .replace(")", "")
                .replace("/", "_")
                .replace(".", "")
                .replace(",", ""))

    def export_rules_json(self, rules: Optional[List[StrategyRule]] = None) -> str:
        """Export rules as JSON for inspection / external use."""
        rules = rules or self.get_rules()
        path = EXPORTS_DIR / "strategy_rules.json"
        with open(path, "w") as f:
            json.dump([r.to_dict() for r in rules], f, indent=2)
        log.info(f"Exported {len(rules)} rules to {path}")
        return str(path)
