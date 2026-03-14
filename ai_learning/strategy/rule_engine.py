"""
Rule Engine — Converts learned patterns into executable trading rules.
======================================================================
Takes a TradingStyleProfile's StrategyCluster and synthesizes
algorithmic conditions that can be evaluated on live candle data.
"""

import logging
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass

from ai_learning.data.schemas import StrategyCluster, StrategyRule, SMCFeatures
from ai_learning.config import MIN_CONFIDENCE_FOR_RULE, MAX_CONDITIONS_PER_RULE

log = logging.getLogger("ai_learning.rule_engine")


# ─── Condition Templates ─────────────────────────────────────────────────
# These map feature names to executable condition functions.
# Each returns (bool, description_string).

CONDITION_TEMPLATES = {
    # Trend
    "trend_aligned": {
        "feature": "trend_aligned",
        "operator": "==",
        "value": True,
        "weight": 2.0,
        "description": "HTF trend aligns with trade direction",
        "code": "trend_aligned == True",
    },
    # Order Block
    "ob_present": {
        "feature": "order_block_present",
        "operator": "==",
        "value": True,
        "weight": 2.0,
        "description": "Order Block present near entry",
        "code": "order_block_present == True",
    },
    "entry_in_ob": {
        "feature": "entry_inside_ob",
        "operator": "==",
        "value": True,
        "weight": 2.5,
        "description": "Entry price inside Order Block zone",
        "code": "entry_inside_ob == True",
    },
    "ob_close": {
        "feature": "ob_distance_atr",
        "operator": "<=",
        "value": 1.0,
        "weight": 1.5,
        "description": "Entry within 1 ATR of Order Block",
        "code": "ob_distance_atr <= {value}",
    },
    # FVG
    "fvg_present": {
        "feature": "fvg_present",
        "operator": "==",
        "value": True,
        "weight": 1.5,
        "description": "Fair Value Gap present",
        "code": "fvg_present == True",
    },
    "fvg_quality_high": {
        "feature": "fvg_quality",
        "operator": ">=",
        "value": 0.5,
        "weight": 1.0,
        "description": "FVG quality above threshold",
        "code": "fvg_quality >= {value}",
    },
    # Liquidity
    "liq_sweep": {
        "feature": "liquidity_sweep",
        "operator": "==",
        "value": True,
        "weight": 2.0,
        "description": "Liquidity sweep detected",
        "code": "liquidity_sweep == True",
    },
    "eq_highs": {
        "feature": "equal_highs_nearby",
        "operator": "==",
        "value": True,
        "weight": 1.0,
        "description": "Equal highs (buyside liquidity) nearby",
        "code": "equal_highs_nearby == True",
    },
    "eq_lows": {
        "feature": "equal_lows_nearby",
        "operator": "==",
        "value": True,
        "weight": 1.0,
        "description": "Equal lows (sellside liquidity) nearby",
        "code": "equal_lows_nearby == True",
    },
    # Structure
    "bos": {
        "feature": "bos_detected",
        "operator": "==",
        "value": True,
        "weight": 2.0,
        "description": "Break of Structure confirmed",
        "code": "bos_detected == True",
    },
    "choch": {
        "feature": "choch_detected",
        "operator": "==",
        "value": True,
        "weight": 2.0,
        "description": "Change of Character detected",
        "code": "choch_detected == True",
    },
    "displacement": {
        "feature": "displacement_detected",
        "operator": "==",
        "value": True,
        "weight": 1.5,
        "description": "Displacement (institutional momentum) detected",
        "code": "displacement_detected == True",
    },
    "displacement_strong": {
        "feature": "displacement_strength",
        "operator": ">=",
        "value": 1.5,
        "weight": 1.0,
        "description": "Strong displacement (>1.5 ATR)",
        "code": "displacement_strength >= {value}",
    },
    # Zone / Location
    "in_discount": {
        "feature": "in_discount",
        "operator": "==",
        "value": True,
        "weight": 1.5,
        "description": "Price in discount zone (for longs)",
        "code": "in_discount == True",
    },
    "in_premium": {
        "feature": "in_premium",
        "operator": "==",
        "value": True,
        "weight": 1.5,
        "description": "Price in premium zone (for shorts)",
        "code": "in_premium == True",
    },
    "in_ote": {
        "feature": "in_ote",
        "operator": "==",
        "value": True,
        "weight": 2.0,
        "description": "Price in OTE zone (62-79% retracement)",
        "code": "in_ote == True",
    },
    # Candle patterns
    "rejection": {
        "feature": "rejection_candle",
        "operator": "==",
        "value": True,
        "weight": 1.5,
        "description": "Rejection candle at zone",
        "code": "rejection_candle == True",
    },
    "engulfing": {
        "feature": "engulfing_candle",
        "operator": "==",
        "value": True,
        "weight": 1.5,
        "description": "Engulfing candle confirmation",
        "code": "engulfing_candle == True",
    },
    "pin_bar": {
        "feature": "pin_bar",
        "operator": "==",
        "value": True,
        "weight": 1.5,
        "description": "Pin bar at zone",
        "code": "pin_bar == True",
    },
    # Session
    "is_killzone": {
        "feature": "is_killzone",
        "operator": "==",
        "value": True,
        "weight": 1.0,
        "description": "Entry during killzone session",
        "code": "is_killzone == True",
    },
    # Volatility
    "vol_not_extreme": {
        "feature": "atr_percentile",
        "operator": "<=",
        "value": 0.85,
        "weight": 0.5,
        "description": "Volatility not in extreme regime",
        "code": "atr_percentile <= {value}",
    },
    "vol_adequate": {
        "feature": "atr_percentile",
        "operator": ">=",
        "value": 0.20,
        "weight": 0.5,
        "description": "Adequate volatility (not too low)",
        "code": "atr_percentile >= {value}",
    },
    # Confluence
    "min_confluence": {
        "feature": "confluence_score",
        "operator": ">=",
        "value": 5,
        "weight": 1.0,
        "description": "Minimum confluence score met",
        "code": "confluence_score >= {value}",
    },
}


class RuleEngine:
    """
    Converts StrategyCluster dominant features into executable StrategyRules.
    """

    def generate_rules(self, cluster: StrategyCluster) -> StrategyRule:
        """
        Generate an algorithmic StrategyRule from a cluster's dominant features.
        """
        conditions = []
        dominant = cluster.dominant_features
        entry_conds = cluster.entry_conditions

        # Map entry conditions to rule conditions
        condition_map = {
            "HTF trend alignment": "trend_aligned",
            "Order Block present": "ob_present",
            "Entry inside Order Block": "entry_in_ob",
            "FVG confluence": "fvg_present",
            "Liquidity sweep": "liq_sweep",
            "Break of Structure": "bos",
            "Change of Character": "choch",
            "Displacement move": "displacement",
            "Discount zone entry": "in_discount",
            "Premium zone entry": "in_premium",
            "OTE zone (62-79%)": "in_ote",
            "Rejection candle": "rejection",
            "Engulfing candle": "engulfing",
            "Pin bar": "pin_bar",
            "Killzone timing": "is_killzone",
            "High volatility context": "vol_adequate",
        }

        # Add conditions from entry conditions
        for ec in entry_conds:
            template_key = condition_map.get(ec)
            if template_key and template_key in CONDITION_TEMPLATES:
                template = CONDITION_TEMPLATES[template_key].copy()
                conditions.append(template)

        # Add conditions from dominant features with high values
        for feat_name, feat_val in dominant.items():
            if feat_name in CONDITION_TEMPLATES and abs(feat_val) > 0.5:
                template = CONDITION_TEMPLATES[feat_name].copy()
                # Check if not already added
                existing_features = {c["feature"] for c in conditions}
                if template["feature"] not in existing_features:
                    conditions.append(template)

        # Ensure we always have a confirmation candle condition
        candle_conds = {"rejection_candle", "engulfing_candle", "pin_bar"}
        if not any(c["feature"] in candle_conds for c in conditions):
            # Add rejection OR engulfing as default confirmation
            conditions.append({
                **CONDITION_TEMPLATES["rejection"],
                "description": "Confirmation candle (rejection/engulfing/pin bar)",
                "code": "(rejection_candle or engulfing_candle or pin_bar)",
            })

        # Limit conditions
        if len(conditions) > MAX_CONDITIONS_PER_RULE:
            conditions.sort(key=lambda c: c.get("weight", 0), reverse=True)
            conditions = conditions[:MAX_CONDITIONS_PER_RULE]

        # Compute minimum score from conditions
        total_weight = sum(c.get("weight", 1.0) for c in conditions)
        min_score = total_weight * 0.6  # require 60% of total weight

        # Direction
        direction = cluster.preferred_direction

        # Generate entry/SL/TP logic strings
        entry_logic = self._generate_entry_logic(conditions, direction)
        sl_logic = self._generate_sl_logic(direction)
        tp_logic = self._generate_tp_logic(cluster.avg_rr, direction)

        # Filters
        filters = {}
        if cluster.preferred_session:
            filters["session"] = cluster.preferred_session
        if "vol_adequate" in [c.get("feature") for c in conditions]:
            filters["min_atr_percentile"] = 20
        if "vol_not_extreme" in [c.get("feature") for c in conditions]:
            filters["max_atr_percentile"] = 85

        rule = StrategyRule(
            rule_id=f"RULE-{cluster.cluster_id:02d}-{cluster.name[:20].replace(' ', '_')}",
            strategy_name=cluster.name,
            direction=direction,
            conditions=conditions,
            min_score=round(min_score, 1),
            entry_logic=entry_logic,
            sl_logic=sl_logic,
            tp_logic=tp_logic,
            filters=filters,
            confidence=cluster.confidence,
            source_cluster=cluster.cluster_id,
        )

        log.info(f"Generated rule '{rule.rule_id}' with {len(conditions)} conditions "
                 f"(confidence={rule.confidence:.2f})")
        return rule

    def generate_all_rules(
        self, clusters: List[StrategyCluster]
    ) -> List[StrategyRule]:
        """Generate rules for all clusters."""
        rules = []
        for cluster in clusters:
            if cluster.confidence < MIN_CONFIDENCE_FOR_RULE:
                log.info(f"Skipping cluster '{cluster.name}' "
                         f"(confidence={cluster.confidence:.2f} < {MIN_CONFIDENCE_FOR_RULE})")
                continue
            rule = self.generate_rules(cluster)
            rules.append(rule)
        return rules

    # ─── Logic Generators ─────────────────────────────────────────────

    def _generate_entry_logic(self, conditions: List[dict], direction: str) -> str:
        """Generate Python-like entry logic string."""
        lines = [f'def check_entry(features: SMCFeatures, direction="{direction}") -> bool:']
        lines.append('    """Auto-generated entry check."""')

        # Scored approach: each condition adds weight, must meet threshold
        lines.append("    score = 0.0")
        lines.append("")

        for c in conditions:
            feat = c["feature"]
            op = c["operator"]
            val = c["value"]
            weight = c.get("weight", 1.0)

            if isinstance(val, bool):
                val_str = str(val)
            elif isinstance(val, str):
                val_str = f'"{val}"'
            else:
                val_str = str(val)

            lines.append(f"    # {c.get('description', feat)}")
            lines.append(f"    if features.{feat} {op} {val_str}:")
            lines.append(f"        score += {weight}")
            lines.append("")

        total_weight = sum(c.get("weight", 1.0) for c in conditions)
        threshold = total_weight * 0.6
        lines.append(f"    return score >= {threshold:.1f}  "
                      f"# {threshold:.0f}/{total_weight:.0f} = 60% of conditions")
        lines.append("")

        return "\n".join(lines)

    def _generate_sl_logic(self, direction: str) -> str:
        if direction == "LONG":
            return (
                "def calculate_sl(entry, atr, ob_zone=None):\n"
                "    if ob_zone:\n"
                "        return ob_zone[0] - 0.5 * atr  # Below OB zone\n"
                "    return entry - 1.5 * atr  # Default 1.5 ATR\n"
            )
        elif direction == "SHORT":
            return (
                "def calculate_sl(entry, atr, ob_zone=None):\n"
                "    if ob_zone:\n"
                "        return ob_zone[1] + 0.5 * atr  # Above OB zone\n"
                "    return entry + 1.5 * atr  # Default 1.5 ATR\n"
            )
        else:
            return (
                "def calculate_sl(entry, atr, direction, ob_zone=None):\n"
                "    buffer = 0.5 * atr\n"
                "    if direction == 'LONG':\n"
                "        return (ob_zone[0] - buffer) if ob_zone else (entry - 1.5 * atr)\n"
                "    else:\n"
                "        return (ob_zone[1] + buffer) if ob_zone else (entry + 1.5 * atr)\n"
            )

    def _generate_tp_logic(self, avg_rr: float, direction: str) -> str:
        rr1 = round(max(1.5, avg_rr * 0.6), 1)
        rr2 = round(max(2.5, avg_rr), 1)
        return (
            f"def calculate_tp(entry, sl, direction='{direction}'):\n"
            f"    risk = abs(entry - sl)\n"
            f"    if direction == 'LONG':\n"
            f"        tp1 = entry + risk * {rr1}  # {rr1}R\n"
            f"        tp2 = entry + risk * {rr2}  # {rr2}R\n"
            f"    else:\n"
            f"        tp1 = entry - risk * {rr1}\n"
            f"        tp2 = entry - risk * {rr2}\n"
            f"    return tp1, tp2\n"
        )


class RuleEvaluator:
    """
    Evaluates a StrategyRule against live SMCFeatures.
    Returns a score and whether the rule conditions are met.
    """

    def evaluate(self, rule: StrategyRule, features: SMCFeatures) -> Dict[str, Any]:
        """
        Evaluate a rule against features.

        Returns:
            {
                "triggered": bool,
                "score": float,
                "max_score": float,
                "matched_conditions": [...],
                "missing_conditions": [...],
            }
        """
        score = 0.0
        max_score = 0.0
        matched = []
        missing = []

        for cond in rule.conditions:
            weight = cond.get("weight", 1.0)
            max_score += weight

            feat_name = cond["feature"]
            operator = cond["operator"]
            expected = cond["value"]

            actual = getattr(features, feat_name, None)
            if actual is None:
                missing.append(cond.get("description", feat_name))
                continue

            passed = self._check_condition(actual, operator, expected)
            if passed:
                score += weight
                matched.append(cond.get("description", feat_name))
            else:
                missing.append(cond.get("description", feat_name))

        # Check filters
        filter_pass = True
        if rule.filters:
            if "session" in rule.filters and features.session != rule.filters["session"]:
                if features.session != "UNKNOWN":
                    filter_pass = False
            if "min_atr_percentile" in rule.filters:
                if features.atr_percentile < rule.filters["min_atr_percentile"] / 100:
                    filter_pass = False
            if "max_atr_percentile" in rule.filters:
                if features.atr_percentile > rule.filters["max_atr_percentile"] / 100:
                    filter_pass = False

        triggered = (score >= rule.min_score) and filter_pass

        return {
            "triggered": triggered,
            "score": round(score, 2),
            "max_score": round(max_score, 2),
            "score_pct": round(score / max_score, 3) if max_score > 0 else 0,
            "matched_conditions": matched,
            "missing_conditions": missing,
            "filter_pass": filter_pass,
        }

    @staticmethod
    def _check_condition(actual, operator: str, expected) -> bool:
        try:
            if operator == "==":
                return actual == expected
            elif operator == "!=":
                return actual != expected
            elif operator == ">=":
                return actual >= expected
            elif operator == "<=":
                return actual <= expected
            elif operator == ">":
                return actual > expected
            elif operator == "<":
                return actual < expected
            elif operator == "in":
                return actual in expected
            else:
                return False
        except (TypeError, ValueError):
            return False
