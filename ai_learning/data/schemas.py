"""
Data Schemas — Dataclasses for the AI learning pipeline.
=========================================================
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
import json


# ─── Manual Trade Record ─────────────────────────────────────────────────

@dataclass
class ManualTrade:
    """A single manual trade logged by the trader."""
    trade_id: str
    symbol: str
    timeframe: str                 # "5minute", "15minute", etc.
    direction: str                 # "LONG" | "SHORT"
    entry: float
    stop_loss: float
    target: float
    result: Optional[str] = None   # "WIN" | "LOSS" | "BE" | None (open)
    pnl_r: Optional[float] = None  # P&L in R-multiples
    chart_image: Optional[str] = None  # path to chart screenshot
    notes: str = ""
    timestamp: Optional[str] = None    # ISO format entry time
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    setup_type: str = ""           # e.g. "OB_BOS", "CHoCH_FVG", "LIQ_SWEEP"
    session: str = ""              # e.g. "KILLZONE_AM", "INDIA_MID"
    extra: Dict[str, Any] = field(default_factory=dict)  # raw SMC flags from screenshot

    @property
    def rr_ratio(self) -> float:
        risk = abs(self.entry - self.stop_loss)
        reward = abs(self.target - self.entry)
        return round(reward / risk, 2) if risk > 0 else 0.0

    @property
    def risk_points(self) -> float:
        return abs(self.entry - self.stop_loss)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rr_ratio"] = self.rr_ratio
        return d


# ─── Extracted SMC Features ──────────────────────────────────────────────

@dataclass
class SMCFeatures:
    """SMC structural features extracted from a trade's chart context."""
    trade_id: str

    # Trend context
    htf_trend: str = "UNKNOWN"           # "BULLISH" | "BEARISH" | "RANGING"
    ltf_trend: str = "UNKNOWN"
    trend_aligned: bool = False          # HTF and LTF agree with direction

    # Order Block
    order_block_present: bool = False
    ob_zone: Optional[tuple] = None      # (low, high)
    ob_distance_atr: float = 0.0         # distance from entry to OB in ATR units
    entry_inside_ob: bool = False

    # Fair Value Gap
    fvg_present: bool = False
    fvg_zone: Optional[tuple] = None
    fvg_distance_atr: float = 0.0
    fvg_quality: float = 0.0            # 0-1

    # Liquidity
    liquidity_sweep: bool = False
    equal_highs_nearby: bool = False
    equal_lows_nearby: bool = False
    sweep_type: str = "NONE"             # "BUYSIDE" | "SELLSIDE" | "NONE"

    # Structure
    bos_detected: bool = False
    choch_detected: bool = False
    displacement_detected: bool = False
    displacement_strength: float = 0.0

    # Zone / Location
    in_discount: bool = False
    in_premium: bool = False
    in_ote: bool = False                 # 62-79% retracement
    zone_detail: str = "UNKNOWN"

    # Session / Time
    session: str = "UNKNOWN"             # ASIA, LONDON, NY, etc.
    minutes_from_open: int = 0
    is_killzone: bool = False

    # Volatility
    atr: float = 0.0
    atr_percentile: float = 0.0          # where current ATR sits vs recent history
    range_expansion: bool = False
    volatility_regime: str = "NORMAL"    # "LOW" | "NORMAL" | "HIGH" | "EXTREME"

    # Candle patterns at entry
    rejection_candle: bool = False
    engulfing_candle: bool = False
    pin_bar: bool = False

    # Confluence score (computed)
    confluence_score: int = 0

    def to_feature_vector(self) -> List[float]:
        """Convert to numerical vector for clustering / ML."""
        return [
            1.0 if self.trend_aligned else 0.0,
            1.0 if self.order_block_present else 0.0,
            1.0 if self.entry_inside_ob else 0.0,
            self.ob_distance_atr,
            1.0 if self.fvg_present else 0.0,
            self.fvg_distance_atr,
            self.fvg_quality,
            1.0 if self.liquidity_sweep else 0.0,
            1.0 if self.equal_highs_nearby else 0.0,
            1.0 if self.equal_lows_nearby else 0.0,
            {"BUYSIDE": 1.0, "SELLSIDE": -1.0, "NONE": 0.0}.get(self.sweep_type, 0.0),
            1.0 if self.bos_detected else 0.0,
            1.0 if self.choch_detected else 0.0,
            1.0 if self.displacement_detected else 0.0,
            self.displacement_strength,
            1.0 if self.in_discount else 0.0,
            1.0 if self.in_premium else 0.0,
            1.0 if self.in_ote else 0.0,
            # Session one-hot
            1.0 if self.session == "ASIA" else 0.0,
            1.0 if self.session == "KILLZONE_AM" else 0.0,
            1.0 if self.session == "LONDON_OVERLAP" else 0.0,
            1.0 if self.session == "KILLZONE_PM" else 0.0,
            1.0 if self.is_killzone else 0.0,
            # Volatility
            self.atr_percentile,
            1.0 if self.range_expansion else 0.0,
            {"LOW": 0.0, "NORMAL": 0.33, "HIGH": 0.66, "EXTREME": 1.0}.get(
                self.volatility_regime, 0.33),
            # Candle patterns
            1.0 if self.rejection_candle else 0.0,
            1.0 if self.engulfing_candle else 0.0,
            1.0 if self.pin_bar else 0.0,
            # Score
            self.confluence_score / 10.0,
        ]

    @staticmethod
    def feature_names() -> List[str]:
        return [
            "trend_aligned", "ob_present", "entry_in_ob", "ob_dist_atr",
            "fvg_present", "fvg_dist_atr", "fvg_quality",
            "liq_sweep", "eq_highs", "eq_lows", "sweep_direction",
            "bos", "choch", "displacement", "disp_strength",
            "in_discount", "in_premium", "in_ote",
            "sess_asia", "sess_killzone_am", "sess_london", "sess_killzone_pm",
            "is_killzone",
            "atr_percentile", "range_expansion", "vol_regime",
            "rejection", "engulfing", "pin_bar",
            "confluence_score",
        ]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ─── Trading Style Profile ───────────────────────────────────────────────

@dataclass
class StrategyCluster:
    """A cluster of similar trades forming one strategy archetype."""
    cluster_id: int
    name: str                              # e.g., "HTF BOS Continuation"
    description: str
    trade_ids: List[str] = field(default_factory=list)
    trade_count: int = 0
    win_rate: float = 0.0
    avg_rr: float = 0.0
    avg_pnl_r: float = 0.0
    expectancy: float = 0.0

    # Dominant features
    dominant_features: Dict[str, float] = field(default_factory=dict)
    entry_conditions: List[str] = field(default_factory=list)
    exit_conditions: List[str] = field(default_factory=list)
    preferred_session: str = ""
    preferred_direction: str = ""          # "LONG" | "SHORT" | "BOTH"

    # Confidence
    confidence: float = 0.0               # 0-1 based on sample size & consistency
    centroid: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradingStyleProfile:
    """Complete trading style profile extracted from manual trades."""
    trader_id: str = "default"
    created_at: str = ""
    total_trades: int = 0
    overall_win_rate: float = 0.0
    overall_avg_rr: float = 0.0
    overall_expectancy: float = 0.0

    # Discovered strategies
    strategies: List[StrategyCluster] = field(default_factory=list)

    # Global preferences
    preferred_session: str = ""
    preferred_direction: str = ""
    preferred_timeframe: str = ""
    avg_hold_time_minutes: float = 0.0

    # Feature importance ranking
    feature_importance: Dict[str, float] = field(default_factory=dict)

    # Raw stats
    direction_distribution: Dict[str, float] = field(default_factory=dict)
    session_distribution: Dict[str, float] = field(default_factory=dict)
    symbol_distribution: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "TRADING STYLE PROFILE",
            "=" * 60,
            f"Total Trades Analyzed: {self.total_trades}",
            f"Overall Win Rate:      {self.overall_win_rate:.1%}",
            f"Overall Avg RR:        {self.overall_avg_rr:.2f}",
            f"Overall Expectancy:    {self.overall_expectancy:.2f}R",
            f"Preferred Session:     {self.preferred_session}",
            f"Preferred Direction:   {self.preferred_direction}",
            "",
            f"Discovered {len(self.strategies)} Strategy Archetypes:",
            "-" * 40,
        ]
        for s in self.strategies:
            lines.extend([
                f"\n  Strategy {s.cluster_id}: {s.name}",
                f"    Trades: {s.trade_count}  |  WR: {s.win_rate:.1%}  |  "
                f"Avg RR: {s.avg_rr:.2f}  |  Expectancy: {s.expectancy:.2f}R",
                f"    Conditions: {', '.join(s.entry_conditions[:5])}",
                f"    Session: {s.preferred_session}  |  "
                f"Direction: {s.preferred_direction}",
                f"    Confidence: {s.confidence:.1%}",
            ])
        lines.append("")
        lines.append("Feature Importance (top 10):")
        sorted_fi = sorted(self.feature_importance.items(),
                           key=lambda x: x[1], reverse=True)[:10]
        for fname, imp in sorted_fi:
            lines.append(f"  {fname:25s} {imp:.3f}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ─── Generated Strategy Rule ─────────────────────────────────────────────

@dataclass
class StrategyRule:
    """A single algorithmic trading rule synthesized from patterns."""
    rule_id: str
    strategy_name: str
    direction: str                         # "LONG" | "SHORT" | "BOTH"
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    # Each condition: {"feature": str, "operator": str, "value": Any, "weight": float}
    min_score: float = 0.0
    entry_logic: str = ""                  # Python code string
    sl_logic: str = ""
    tp_logic: str = ""
    filters: Dict[str, Any] = field(default_factory=dict)
    # e.g., {"session": "KILLZONE_AM", "min_atr_percentile": 30}
    confidence: float = 0.0
    source_cluster: int = -1
    backtested: bool = False
    backtest_metrics: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Optimization Result ─────────────────────────────────────────────────

@dataclass
class OptimizationResult:
    """Result from strategy optimization."""
    strategy_name: str
    optimized_params: Dict[str, float] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    # metrics: win_rate, profit_factor, max_drawdown, expectancy, sharpe
    monte_carlo: Dict[str, float] = field(default_factory=dict)
    # monte_carlo: median_return, p5_return, p95_return, ruin_probability
    walk_forward: Dict[str, float] = field(default_factory=dict)
    # walk_forward: avg_oos_win_rate, avg_oos_pf, consistency_ratio
    is_robust: bool = False
    robustness_score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Live Signal ──────────────────────────────────────────────────────────

@dataclass
class AISignal:
    """A signal generated by the AI learning system."""
    signal_id: str
    timestamp: str
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    target1: float
    target2: float
    strategy_name: str
    score: float
    confidence: float
    matched_pattern: str
    features: Dict[str, Any] = field(default_factory=dict)
    reasoning: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def alert_text(self) -> str:
        return (
            f"🤖 AI Signal — {'🟢 LONG' if self.direction == 'LONG' else '🔴 SHORT'}\n"
            f"Symbol: {self.symbol}\n"
            f"Strategy: {self.strategy_name}\n"
            f"Entry: {self.entry:.1f}  |  SL: {self.stop_loss:.1f}\n"
            f"TP1: {self.target1:.1f}  |  TP2: {self.target2:.1f}\n"
            f"Score: {self.score:.1f}  |  Confidence: {self.confidence:.1%}\n"
            f"Pattern: {self.matched_pattern}\n"
            f"Reasoning:\n" +
            "\n".join(f"  • {r}" for r in self.reasoning)
        )
