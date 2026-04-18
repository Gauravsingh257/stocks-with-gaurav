"""
services/feedback_analyzer.py

ADVANCED Feedback & Learning System — evolves selection quality over time
using closed portfolio trades.

Features:
  1. Continuous scoring — 3-bucket analysis (low/mid/high) + return-weighted correlation
  2. Time decay — recent trades weighted exponentially higher (exp(-days/decay))
  3. Setup performance tracking — per-setup win rate, avg return, R-multiple
  4. Return-based learning — uses actual return % and R-multiples, not just win/loss
  5. Historical success bonus — composite score adjustment for new candidates

Data flow:
  portfolio_journal → JOIN portfolio_positions → JOIN stock_recommendations
  → extract factors + setup + returns per trade
  → 3-bucket win-rate + return-weighted correlation per factor
  → setup-level performance stats
  → adaptive weight_adjustments + setup penalties
  → consumed by idea_selector scoring functions

Cached in-memory with configurable TTL (default 6h).
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

log = logging.getLogger("services.feedback_analyzer")

_IST = timezone(timedelta(hours=5, minutes=30))

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

MIN_TRADES_FOR_FEEDBACK = 10
CACHE_TTL_SECONDS = 6 * 3600
MAX_WEIGHT_DELTA = 0.08

# Time decay — half-life in days (trade from 60 days ago has ~50% weight)
DECAY_HALFLIFE_DAYS = 60

# 3-bucket thresholds for continuous scoring
BUCKET_LOW_MAX = 0.3
BUCKET_MID_MAX = 0.6


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class BucketStats:
    """Stats for one factor bucket (low/mid/high)."""
    weighted_wins: float = 0.0
    weighted_losses: float = 0.0
    weighted_return_sum: float = 0.0
    weighted_count: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.weighted_wins + self.weighted_losses
        return self.weighted_wins / total if total > 0.5 else 0.5

    @property
    def avg_return(self) -> float:
        return self.weighted_return_sum / self.weighted_count if self.weighted_count > 0.5 else 0.0


@dataclass
class FactorAnalysis:
    """Advanced factor analysis with 3 buckets + correlation."""
    factor: str
    low: BucketStats = field(default_factory=BucketStats)
    mid: BucketStats = field(default_factory=BucketStats)
    high: BucketStats = field(default_factory=BucketStats)
    correlation: float = 0.0          # Pearson correlation with returns

    @property
    def edge(self) -> float:
        """High bucket win-rate minus low bucket win-rate."""
        return self.high.win_rate - self.low.win_rate

    @property
    def return_edge(self) -> float:
        """High bucket avg return minus low bucket avg return."""
        return self.high.avg_return - self.low.avg_return


@dataclass
class SetupStats:
    """Performance stats for one setup type."""
    setup: str
    weighted_wins: float = 0.0
    weighted_losses: float = 0.0
    weighted_return_sum: float = 0.0
    weighted_count: float = 0.0
    weighted_r_multiple_sum: float = 0.0
    best_return: float = 0.0
    worst_return: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.weighted_wins + self.weighted_losses
        return self.weighted_wins / total if total > 0.5 else 0.5

    @property
    def avg_return(self) -> float:
        return self.weighted_return_sum / self.weighted_count if self.weighted_count > 0.5 else 0.0

    @property
    def avg_r_multiple(self) -> float:
        return self.weighted_r_multiple_sum / self.weighted_count if self.weighted_count > 0.5 else 0.0

    @property
    def is_failing(self) -> bool:
        """Setup is failing if win-rate < 35% with enough samples."""
        return self.weighted_count >= 5 and self.win_rate < 0.35


@dataclass
class FeedbackResult:
    """Complete advanced feedback analysis for one horizon."""
    horizon: str
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy: float = 0.0            # (win_rate * avg_win) + (loss_rate * avg_loss)
    factor_analysis: dict[str, FactorAnalysis] = field(default_factory=dict)
    setup_stats: dict[str, SetupStats] = field(default_factory=dict)
    weight_adjustments: dict[str, float] = field(default_factory=dict)
    setup_penalties: dict[str, float] = field(default_factory=dict)
    feature_importance: list[dict] = field(default_factory=list)
    setup_ranking: list[dict] = field(default_factory=list)
    computed_at: float = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────

_cache: dict[str, FeedbackResult] = {}


def _is_cache_valid(horizon: str) -> bool:
    result = _cache.get(horizon)
    if not result:
        return False
    return (time.time() - result.computed_at) < CACHE_TTL_SECONDS


def invalidate_cache(horizon: str | None = None) -> None:
    """Force recomputation on next call."""
    if horizon:
        _cache.pop(horizon, None)
    else:
        _cache.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_json(val) -> dict:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _time_decay_weight(closed_at_str: str | None) -> float:
    """
    Exponential time decay: recent trades get weight ~1.0, old trades decay toward 0.

    weight = exp(-days_since_trade * ln(2) / halflife)
    """
    if not closed_at_str:
        return 0.5
    try:
        closed_dt = datetime.fromisoformat(closed_at_str.replace("Z", "+00:00"))
        if closed_dt.tzinfo is None:
            closed_dt = closed_dt.replace(tzinfo=_IST)
        days_ago = (datetime.now(_IST) - closed_dt).total_seconds() / 86400
        if days_ago < 0:
            days_ago = 0
        return math.exp(-days_ago * math.log(2) / DECAY_HALFLIFE_DAYS)
    except (ValueError, TypeError):
        return 0.5


def _compute_r_multiple(entry: float, exit_price: float, stop_loss: float) -> float:
    """Compute R-multiple: how many R units of profit/loss."""
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return 0.0
    pnl = exit_price - entry
    return round(pnl / risk, 2)


def _pearson_correlation(xs: list[float], ys: list[float]) -> float:
    """Compute Pearson correlation between two lists."""
    n = len(xs)
    if n < 5:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom == 0:
        return 0.0
    return round(cov / denom, 4)


def _bucket_label(val: float) -> str:
    if val < BUCKET_LOW_MAX:
        return "low"
    if val < BUCKET_MID_MAX:
        return "mid"
    return "high"


# ──────────────────────────────────────────────────────────────────────────────
# Data fetch
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_closed_trades(horizon: str) -> list[dict]:
    """Fetch closed journal entries enriched with factor data from the original recommendation."""
    from dashboard.backend.db.schema import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                j.symbol,
                j.horizon,
                j.profit_loss_pct,
                j.days_held,
                j.confidence_score,
                j.exit_reason,
                j.entry_price,
                j.exit_price,
                j.stop_loss,
                j.target_1,
                j.target_2,
                j.high_since_entry,
                j.low_since_entry,
                j.closed_at,
                r.technical_factors,
                r.fundamental_factors,
                r.sentiment_factors,
                r.setup
            FROM portfolio_journal j
            LEFT JOIN portfolio_positions p ON j.position_id = p.id
            LEFT JOIN stock_recommendations r ON p.recommendation_id = r.id
            WHERE j.horizon = ?
            ORDER BY j.closed_at DESC
            """,
            (horizon,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Factor keys per horizon
# ──────────────────────────────────────────────────────────────────────────────

SWING_FACTORS = ["trend", "momentum", "breakout", "mtf_alignment", "liquidity", "volume_expansion"]
LONGTERM_FACTORS = ["trend", "momentum", "breakout", "volume_expansion",
                    "growth", "quality", "institutional_accumulation"]


# ──────────────────────────────────────────────────────────────────────────────
# Core analysis
# ──────────────────────────────────────────────────────────────────────────────

def analyze_horizon(horizon: str, force: bool = False) -> FeedbackResult:
    """
    Advanced analysis of all closed trades for a horizon.

    Computes:
      - 3-bucket factor stats (low/mid/high) with time-decay weighting
      - Return-weighted correlation per factor
      - Setup-level performance (win-rate, avg return, R-multiple)
      - Adaptive weight adjustments from combined signals
    """
    horizon = horizon.upper()
    if not force and _is_cache_valid(horizon):
        return _cache[horizon]

    trades = _fetch_closed_trades(horizon)
    result = FeedbackResult(horizon=horizon, total_trades=len(trades), computed_at=time.time())

    if len(trades) < MIN_TRADES_FOR_FEEDBACK:
        log.info("[Feedback] %s: only %d trades (need %d) — using default weights",
                 horizon, len(trades), MIN_TRADES_FOR_FEEDBACK)
        _cache[horizon] = result
        return result

    factors = SWING_FACTORS if horizon == "SWING" else LONGTERM_FACTORS
    fa: dict[str, FactorAnalysis] = {f: FactorAnalysis(factor=f) for f in factors}
    setups: dict[str, SetupStats] = {}

    # For Pearson correlation: collect (factor_value, return_pct) pairs per factor
    corr_data: dict[str, tuple[list[float], list[float]]] = {f: ([], []) for f in factors}

    win_pcts: list[float] = []
    loss_pcts: list[float] = []

    for trade in trades:
        pnl_pct = float(trade.get("profit_loss_pct") or 0)
        is_win = pnl_pct > 0
        entry = float(trade.get("entry_price") or 0)
        exit_p = float(trade.get("exit_price") or entry)
        sl = float(trade.get("stop_loss") or entry * 0.95)
        r_mult = _compute_r_multiple(entry, exit_p, sl) if entry > 0 else 0.0

        # Time decay weight
        tw = _time_decay_weight(trade.get("closed_at"))

        if is_win:
            result.total_wins += 1
            win_pcts.append(pnl_pct)
        else:
            result.total_losses += 1
            loss_pcts.append(pnl_pct)

        # ── Factor analysis (3-bucket + correlation) ──

        tf = _parse_json(trade.get("technical_factors"))
        ff = _parse_json(trade.get("fundamental_factors"))
        all_factors = {**tf, **ff}

        for fname in factors:
            fval = float(all_factors.get(fname, 0))
            bucket = _bucket_label(fval)
            analysis = fa[fname]
            bs = getattr(analysis, bucket)

            if is_win:
                bs.weighted_wins += tw
            else:
                bs.weighted_losses += tw
            bs.weighted_return_sum += pnl_pct * tw
            bs.weighted_count += tw

            corr_data[fname][0].append(fval)
            corr_data[fname][1].append(pnl_pct)

        # ── Setup performance tracking ──

        setup_name = (trade.get("setup") or "UNKNOWN").strip().upper()
        if setup_name not in setups:
            setups[setup_name] = SetupStats(setup=setup_name)
        ss = setups[setup_name]

        if is_win:
            ss.weighted_wins += tw
        else:
            ss.weighted_losses += tw
        ss.weighted_return_sum += pnl_pct * tw
        ss.weighted_count += tw
        ss.weighted_r_multiple_sum += r_mult * tw
        ss.best_return = max(ss.best_return, pnl_pct)
        ss.worst_return = min(ss.worst_return, pnl_pct)

    # ── Compute correlations ──

    for fname in factors:
        xs, ys = corr_data[fname]
        fa[fname].correlation = _pearson_correlation(xs, ys)

    # ── Aggregate stats ──

    result.avg_win_pct = sum(win_pcts) / len(win_pcts) if win_pcts else 0
    result.avg_loss_pct = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0

    win_rate = result.total_wins / result.total_trades if result.total_trades else 0
    result.expectancy = round(
        (win_rate * result.avg_win_pct) + ((1 - win_rate) * result.avg_loss_pct), 2
    )

    result.factor_analysis = fa
    result.setup_stats = setups

    # ── Compute weight adjustments (combined signal) ──

    adjustments: dict[str, float] = {}
    importance: list[dict] = []

    for fname, analysis in fa.items():
        # Combined signal: 50% edge (win-rate) + 30% return-edge + 20% correlation
        edge = analysis.edge
        return_edge_norm = max(-1, min(1, analysis.return_edge / 10))  # normalize ~±10% range
        combined = 0.5 * edge + 0.3 * return_edge_norm + 0.2 * analysis.correlation

        adj = max(-MAX_WEIGHT_DELTA, min(MAX_WEIGHT_DELTA, combined * 0.15))
        adjustments[fname] = round(adj, 4)

        importance.append({
            "factor": fname,
            "edge": round(edge, 4),
            "return_edge": round(analysis.return_edge, 2),
            "correlation": analysis.correlation,
            "combined_signal": round(combined, 4),
            "buckets": {
                "low":  {"win_rate": round(analysis.low.win_rate, 3),  "avg_return": round(analysis.low.avg_return, 2),  "trades": round(analysis.low.weighted_count, 1)},
                "mid":  {"win_rate": round(analysis.mid.win_rate, 3),  "avg_return": round(analysis.mid.avg_return, 2),  "trades": round(analysis.mid.weighted_count, 1)},
                "high": {"win_rate": round(analysis.high.win_rate, 3), "avg_return": round(analysis.high.avg_return, 2), "trades": round(analysis.high.weighted_count, 1)},
            },
            "weight_adjustment": round(adj, 4),
        })

    importance.sort(key=lambda x: abs(x["combined_signal"]), reverse=True)
    result.weight_adjustments = adjustments
    result.feature_importance = importance

    # ── Setup penalties (for failing setups) ──

    setup_penalties: dict[str, float] = {}
    setup_ranking: list[dict] = []

    for sname, ss in setups.items():
        penalty = 0.0
        if ss.is_failing:
            penalty = -5.0 * (0.35 - ss.win_rate)  # up to -1.75 penalty
        setup_penalties[sname] = round(penalty, 2)

        setup_ranking.append({
            "setup": sname,
            "win_rate": round(ss.win_rate, 3),
            "avg_return": round(ss.avg_return, 2),
            "avg_r_multiple": round(ss.avg_r_multiple, 2),
            "trades": round(ss.weighted_count, 1),
            "best_return": round(ss.best_return, 2),
            "worst_return": round(ss.worst_return, 2),
            "is_failing": ss.is_failing,
            "penalty": round(penalty, 2),
        })

    setup_ranking.sort(key=lambda x: x["avg_return"], reverse=True)
    result.setup_penalties = setup_penalties
    result.setup_ranking = setup_ranking

    _cache[horizon] = result

    log.info("[Feedback] %s: %d trades (%d W / %d L), expectancy=%.2f%%, avg_win=%.1f%%, avg_loss=%.1f%%",
             horizon, result.total_trades, result.total_wins, result.total_losses,
             result.expectancy, result.avg_win_pct, result.avg_loss_pct)
    for fi in importance[:3]:
        log.info("  Factor %s: combined=%.3f (edge=%.3f, corr=%.3f, adj=%+.4f)",
                 fi["factor"], fi["combined_signal"], fi["edge"], fi["correlation"], fi["weight_adjustment"])
    for sr in setup_ranking[:3]:
        log.info("  Setup %s: wr=%.0f%% avg_ret=%.1f%% avg_R=%.2f (%s)",
                 sr["setup"], sr["win_rate"] * 100, sr["avg_return"], sr["avg_r_multiple"],
                 "FAILING" if sr["is_failing"] else "ok")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Public API — consumed by idea_selector
# ──────────────────────────────────────────────────────────────────────────────

def get_weight_adjustments(horizon: str) -> dict[str, float]:
    """Get adaptive weight adjustments. Empty dict if insufficient data."""
    result = analyze_horizon(horizon)
    if result.total_trades < MIN_TRADES_FOR_FEEDBACK:
        return {}
    return result.weight_adjustments


def get_setup_penalty(setup_name: str, horizon: str) -> float:
    """
    Get score penalty for a specific setup.
    Returns 0.0 if setup is performing well, negative if failing.
    """
    result = analyze_horizon(horizon)
    if result.total_trades < MIN_TRADES_FOR_FEEDBACK:
        return 0.0
    return result.setup_penalties.get(setup_name.strip().upper(), 0.0)


def get_historical_success_bonus(rec: dict, horizon: str) -> float:
    """
    Compute a score bonus/penalty for a recommendation based on:
      1. Factor-return correlation alignment (do this rec's factors predict positive returns?)
      2. Setup performance (is this setup winning or failing?)

    Returns a value in [-8, +8] added to the base score.
    """
    result = analyze_horizon(horizon)
    if result.total_trades < MIN_TRADES_FOR_FEEDBACK:
        return 0.0

    tf = _parse_json(rec.get("technical_factors"))
    ff = _parse_json(rec.get("fundamental_factors"))
    all_factors = {**tf, **ff}
    factors = SWING_FACTORS if horizon == "SWING" else LONGTERM_FACTORS

    # ── Part 1: Factor alignment bonus (±5 pts) ──
    factor_bonus = 0.0
    for fname in factors:
        fval = float(all_factors.get(fname, 0))
        analysis = result.factor_analysis.get(fname)
        if not analysis:
            continue

        bucket = _bucket_label(fval)
        bs = getattr(analysis, bucket)
        if bs.weighted_count < 2:
            continue

        # Reward if this factor bucket historically has positive avg return
        if bs.avg_return > 0:
            factor_bonus += min(1.5, bs.avg_return / 5)  # scale: 5% return → +1.0
        else:
            factor_bonus += max(-1.5, bs.avg_return / 5)

    factor_bonus = max(-5.0, min(5.0, factor_bonus))

    # ── Part 2: Setup penalty (0 to -3 pts) ──
    setup_name = (rec.get("setup") or "").strip().upper()
    setup_pen = get_setup_penalty(setup_name, horizon) if setup_name else 0.0

    return round(factor_bonus + setup_pen, 2)


# ──────────────────────────────────────────────────────────────────────────────
# Summary for API / dashboard
# ──────────────────────────────────────────────────────────────────────────────

def get_feedback_summary() -> dict:
    """Full feedback analysis for both horizons — for API consumption."""
    swing = analyze_horizon("SWING")
    longterm = analyze_horizon("LONGTERM")

    def _serialize(r: FeedbackResult) -> dict:
        return {
            "horizon": r.horizon,
            "total_trades": r.total_trades,
            "total_wins": r.total_wins,
            "total_losses": r.total_losses,
            "win_rate": round(r.total_wins / r.total_trades, 4) if r.total_trades > 0 else 0,
            "avg_win_pct": round(r.avg_win_pct, 2),
            "avg_loss_pct": round(r.avg_loss_pct, 2),
            "expectancy": r.expectancy,
            "feature_importance": r.feature_importance,
            "weight_adjustments": r.weight_adjustments,
            "setup_ranking": r.setup_ranking,
            "sufficient_data": r.total_trades >= MIN_TRADES_FOR_FEEDBACK,
        }

    return {
        "swing": _serialize(swing),
        "longterm": _serialize(longterm),
        "min_trades_required": MIN_TRADES_FOR_FEEDBACK,
    }
