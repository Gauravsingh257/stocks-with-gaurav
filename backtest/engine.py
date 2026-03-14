"""
backtest/engine.py — Candle-by-Candle Backtester (F3.3)
=======================================================
Walks through historical candles sequentially, detects SMC setups using
the corrected smc_detectors module, manages trades, and records results.

Architecture:
  1. Load historical data from DataStore
  2. For each 5-minute candle (primary timeframe):
     a. Build rolling windows for all timeframes
     b. Run setup detection (A, B, C, D) using smc_detectors
     c. Check open trades for SL/TP hits (intra-bar)
     d. Score valid signals via confluence engine
     e. Record trade entries and exits
  3. Output trade log with full metrics
"""

import os
import sys
import logging
from datetime import datetime, time, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from copy import deepcopy

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import smc_detectors as smc
from backtest.cost_model import cost_as_points, CostConfig, INDEX_OPTIONS_COST, EQUITY_INTRADAY_COST

logger = logging.getLogger(__name__)


# =====================================================
# CONFIGURATION
# =====================================================

@dataclass
class BacktestConfig:
    """Configuration for the backtester."""
    # Timeframes
    primary_tf: str = "5minute"         # Signal detection timeframe
    htf_tf: str = "60minute"            # HTF bias timeframe

    # Setups to test
    enable_setup_a: bool = True   # HTF BOS Continuation
    enable_setup_b: bool = False  # SWEEP-B: disabled — grid search showed -0.31R expectancy
    enable_setup_c: bool = True   # HTF OB + LTF FVG
    enable_setup_d: bool = False  # CHoCH (disabled per F1.5 — negative expectancy)

    # Killzone hours (IST)
    killzone_start: time = field(default_factory=lambda: time(9, 20))
    killzone_end: time = field(default_factory=lambda: time(15, 0))

    # High-quality windows (from killzone_confidence)
    prime_start: time = field(default_factory=lambda: time(11, 0))
    prime_end: time = field(default_factory=lambda: time(13, 0))

    # Risk
    max_daily_signals: int = 5
    max_concurrent_trades: int = 3
    max_signals_per_symbol_per_day: int = 1

    # SL/TP defaults (GRID SEARCH Feb 2026: buf=0.1, rr=2.0 → Sharpe=3.51)
    default_rr_a: float = 2.0   # Setup A risk:reward (was 3.0, grid search → 2.0)
    default_rr_b: float = 2.0   # Setup B risk:reward
    default_rr_c: float = 2.0   # Setup C risk:reward
    default_rr_d: float = 2.0   # Setup D risk:reward
    atr_buffer_mult: float = 0.1  # SL buffer as fraction of ATR (grid search winner)

    # Confluence
    min_smc_score: int = 5  # Minimum confluence score to take a trade

    # Candle windows
    ltf_lookback: int = 200    # 5-min candles to keep
    htf_lookback: int = 200    # 1H candles to keep

    # Transaction costs
    apply_costs: bool = True

    # Partial profit taking
    partial_tp_enabled: bool = False
    partial_tp_at_rr: float = 1.5
    partial_tp_pct: float = 0.5   # close 50% at partial TP


# =====================================================
# TRADE RECORD
# =====================================================

@dataclass
class Trade:
    """Single trade record."""
    trade_id: int
    symbol: str
    setup: str
    direction: str           # "LONG" or "SHORT"
    entry_price: float
    sl: float
    target: float
    rr: float
    smc_score: int
    entry_time: str          # ISO datetime string
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None  # "TP", "SL", "EOD", "MANUAL"
    gross_pnl_pts: float = 0.0
    net_pnl_pts: float = 0.0
    r_multiple: float = 0.0
    cost_pts: float = 0.0
    smc_breakdown: Optional[Dict] = None

    @property
    def risk_pts(self) -> float:
        return abs(self.entry_price - self.sl)

    @property
    def is_winner(self) -> bool:
        return self.r_multiple > 0

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "setup": self.setup,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "sl": self.sl,
            "target": self.target,
            "rr": self.rr,
            "smc_score": self.smc_score,
            "entry_time": self.entry_time,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time,
            "exit_reason": self.exit_reason,
            "gross_pnl_pts": round(self.gross_pnl_pts, 2),
            "net_pnl_pts": round(self.net_pnl_pts, 2),
            "r_multiple": round(self.r_multiple, 4),
            "cost_pts": round(self.cost_pts, 2),
        }


# =====================================================
# CONFLUENCE SCORER (mirrors engine's smc_confluence_score)
# =====================================================

def confluence_score(signal: dict, ltf_data: list, htf_data: list) -> Tuple[int, dict]:
    """
    Score a signal using SMC confluence factors.
    Returns (score 0-10, breakdown dict).
    """
    score = 0
    breakdown = {}

    # 1. Liquidity sweep
    if smc.liquidity_sweep_detected(ltf_data):
        score += 2; breakdown["liquidity"] = 2
    elif smc.minor_liquidity(ltf_data):
        score += 1; breakdown["liquidity"] = 1
    else:
        breakdown["liquidity"] = 0

    # 2. Location (premium/discount)
    price = signal["entry"]
    direction = signal["direction"]
    if direction == "LONG":
        if smc.is_discount_zone(htf_data, price):
            score += 2; breakdown["location"] = 2
        elif smc.near_equilibrium(htf_data, price):
            score += 1; breakdown["location"] = 1
        else:
            breakdown["location"] = 0
    else:
        if smc.is_premium_zone(htf_data, price):
            score += 2; breakdown["location"] = 2
        elif smc.near_equilibrium(htf_data, price):
            score += 1; breakdown["location"] = 1
        else:
            breakdown["location"] = 0

    # 3. HTF narrative alignment
    htf_bias = smc.detect_htf_bias(htf_data)
    if htf_bias == direction:
        score += 2; breakdown["htf"] = 2
    elif htf_bias is None:
        score += 1; breakdown["htf"] = 1
    else:
        breakdown["htf"] = 0

    # 4. Structure shift
    if ltf_data:
        c = ltf_data[-1]
        body = abs(c["close"] - c["open"])
        atr = smc.calculate_atr(ltf_data)
        if atr > 0 and body > atr * 1.2:
            score += 2; breakdown["structure"] = 2
        elif atr > 0 and body > atr * 0.6:
            score += 1; breakdown["structure"] = 1
        else:
            breakdown["structure"] = 0
    else:
        breakdown["structure"] = 0

    # 5. Execution quality (OB + FVG)
    has_ob = bool(signal.get("ob"))
    has_fvg = bool(signal.get("fvg"))
    if has_ob and has_fvg:
        score += 2; breakdown["execution"] = 2
    elif has_ob or has_fvg:
        score += 1; breakdown["execution"] = 1
    else:
        breakdown["execution"] = 0

    return score, breakdown


# =====================================================
# HELPER: IS INDEX SYMBOL
# =====================================================

def _is_index(symbol: str) -> bool:
    return any(x in symbol.upper() for x in ("NIFTY", "BANKNIFTY", "NIFTY BANK"))


# =====================================================
# HELPER: KILLZONE CHECK
# =====================================================

def _in_killzone(dt_str: str, config: BacktestConfig) -> bool:
    """Check if a datetime string falls within tradeable hours."""
    try:
        if isinstance(dt_str, str):
            dt = datetime.fromisoformat(dt_str)
        else:
            dt = dt_str
        t = dt.time()
        return config.killzone_start <= t <= config.killzone_end
    except Exception:
        return True  # If we can't parse, allow


def _killzone_confidence(dt_str: str) -> float:
    """Return time-based confidence (mirror of engine's killzone_confidence)."""
    try:
        if isinstance(dt_str, str):
            dt = datetime.fromisoformat(dt_str)
        else:
            dt = dt_str
        t = dt.time()

        if time(9, 15) <= t < time(10, 0):
            return 0.8
        if time(10, 0) <= t < time(11, 0):
            return 0.0   # Dead zone
        if time(11, 0) <= t < time(13, 0):
            return 1.0   # Prime
        return 0.0       # Late session
    except Exception:
        return 0.5


# =====================================================
# HELPER: RESAMPLE CANDLES TO HIGHER TF
# =====================================================

def resample_to_htf(candles_5m: list, htf_minutes: int = 60) -> list:
    """
    Resample 5-minute candles into higher timeframe candles.

    Groups by rounded time buckets and builds OHLCV bars.
    Returns list of dicts matching the standard candle format.
    """
    if not candles_5m:
        return []

    htf_candles = []
    bucket = None
    current = None

    for c in candles_5m:
        dt_str = c.get("date", "")
        try:
            if isinstance(dt_str, str):
                dt = datetime.fromisoformat(dt_str)
            else:
                dt = dt_str
        except Exception:
            continue

        # Compute bucket start
        total_min = dt.hour * 60 + dt.minute
        bucket_start_min = (total_min // htf_minutes) * htf_minutes
        bucket_key = (dt.date(), bucket_start_min)

        if current is None or current["_bucket"] != bucket_key:
            if current is not None:
                # Close previous bucket
                del current["_bucket"]
                htf_candles.append(current)

            current = {
                "date": dt.isoformat(),
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c.get("volume", 0),
                "_bucket": bucket_key,
            }
        else:
            current["high"] = max(current["high"], c["high"])
            current["low"] = min(current["low"], c["low"])
            current["close"] = c["close"]
            current["volume"] = current.get("volume", 0) + c.get("volume", 0)

    if current is not None:
        if "_bucket" in current:
            del current["_bucket"]
        htf_candles.append(current)

    return htf_candles


# =====================================================
# SETUP DETECTORS (backtest versions using smc_detectors)
# =====================================================

def _detect_setup_a(ltf: list, htf: list, symbol: str, config: BacktestConfig) -> Optional[dict]:
    """
    Setup A — HTF BOS Continuation.
    HTF bias LONG/SHORT → LTF OB + FVG → entry at FVG with confirmation.
    """
    if len(ltf) < 30 or len(htf) < 25:
        return None

    bias = smc.detect_htf_bias(htf)
    if not bias:
        return None

    price = ltf[-1]["close"]
    atr = smc.calculate_atr(ltf)
    if atr <= 0:
        return None

    ob = smc.detect_order_block(ltf, bias)
    fvg = smc.detect_fvg(ltf, bias)

    if not ob or not fvg:
        return None

    # Price must be near the FVG zone
    fvg_low, fvg_high = fvg
    fvg_mid = (fvg_low + fvg_high) / 2

    # Check if price is within or near FVG (within 1 ATR)
    dist_to_fvg = abs(price - fvg_mid)
    if dist_to_fvg > atr * 2:
        return None

    # Confirmation: last candle should show rejection (body direction matches bias)
    last = ltf[-1]
    if bias == "LONG" and last["close"] <= last["open"]:
        return None  # Need bullish candle for long entry
    if bias == "SHORT" and last["close"] >= last["open"]:
        return None  # Need bearish candle for short entry

    # Entry at FVG midpoint or current close
    entry = price
    buffer = atr * config.atr_buffer_mult

    if bias == "LONG":
        sl = min(ob[0], fvg_low) - buffer
        target = entry + config.default_rr_a * (entry - sl)
    else:
        sl = max(ob[1], fvg_high) + buffer
        target = entry - config.default_rr_a * (sl - entry)

    if sl == entry:
        return None

    rr = abs(target - entry) / abs(entry - sl)

    return {
        "setup": "SETUP-A",
        "symbol": symbol,
        "direction": bias,
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "target": round(target, 2),
        "rr": round(rr, 2),
        "ob": ob,
        "fvg": fvg,
    }


def _detect_setup_b(ltf: list, htf: list, symbol: str, config: BacktestConfig) -> List[dict]:
    """
    Setup B — Range Mean-Reversion.
    OB at range extremes + volume expansion + confirmation candle.
    """
    signals = []
    if len(ltf) < 30:
        return signals

    price = ltf[-1]["close"]
    atr = smc.calculate_atr(ltf)
    if atr <= 0:
        return signals

    # Check volume expansion (last candle vol > 1.2x avg of last 20)
    volumes = [c.get("volume", 0) for c in ltf[-20:]]
    avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1) if len(volumes) > 1 else 0
    has_volume = volumes[-1] > avg_vol * 1.2 if avg_vol > 0 else True  # skip filter if no vol data

    for direction in ("LONG", "SHORT"):
        ob = smc.detect_order_block(ltf, direction)
        if not ob:
            continue

        # Price must be near OB
        if direction == "LONG" and price > ob[1]:
            continue
        if direction == "SHORT" and price < ob[0]:
            continue

        fvg = smc.detect_fvg(ltf, direction)

        # Confirmation candle
        last = ltf[-1]
        if direction == "LONG" and last["close"] <= last["open"]:
            continue
        if direction == "SHORT" and last["close"] >= last["open"]:
            continue

        if not has_volume and not _is_index(symbol):
            continue

        buffer = atr * config.atr_buffer_mult
        if direction == "LONG":
            sl = ob[0] - buffer
            target = price + config.default_rr_b * (price - sl)
        else:
            sl = ob[1] + buffer
            target = price - config.default_rr_b * (sl - price)

        if sl == price:
            continue

        rr = abs(target - price) / abs(price - sl)
        if rr < 2.0:
            continue

        signals.append({
            "setup": "SETUP-B",
            "symbol": symbol,
            "direction": direction,
            "entry": round(price, 2),
            "sl": round(sl, 2),
            "target": round(target, 2),
            "rr": round(rr, 2),
            "ob": ob,
            "fvg": fvg,
        })

    return signals


def _detect_setup_c(ltf: list, htf: list, symbol: str, config: BacktestConfig) -> List[dict]:
    """
    Setup C — HTF OB + LTF FVG (Universal Setup).
    HTF OB → LTF FVG → price taps zone → rejection candle → entry.
    """
    signals = []
    if len(ltf) < 30 or len(htf) < 25:
        return signals

    price = ltf[-1]["close"]
    atr_ltf = smc.calculate_atr(ltf)
    if atr_ltf <= 0:
        return signals

    for direction in ("LONG", "SHORT"):
        # HTF OB
        htf_ob = smc.detect_order_block(htf, direction)
        if not htf_ob:
            continue

        # LTF FVG in the same direction
        ltf_fvg = smc.detect_fvg(ltf, direction)
        if not ltf_fvg:
            continue

        # LTF FVG should overlap or be near HTF OB
        fvg_mid = (ltf_fvg[0] + ltf_fvg[1]) / 2
        ob_mid = (htf_ob[0] + htf_ob[1]) / 2
        if abs(fvg_mid - ob_mid) > atr_ltf * 3:
            continue

        # Price should be in/near the zone
        zone_low = min(htf_ob[0], ltf_fvg[0])
        zone_high = max(htf_ob[1], ltf_fvg[1])

        # Recent tap check (last 3 candles touched zone)
        tapped = False
        for c in ltf[-3:]:
            if direction == "LONG" and c["low"] <= zone_high:
                tapped = True
            elif direction == "SHORT" and c["high"] >= zone_low:
                tapped = True
        if not tapped:
            continue

        # Rejection confirmation
        last = ltf[-1]
        if direction == "LONG" and last["close"] <= last["open"]:
            continue
        if direction == "SHORT" and last["close"] >= last["open"]:
            continue

        buffer = atr_ltf * config.atr_buffer_mult
        if direction == "LONG":
            sl = zone_low - buffer
            target = price + config.default_rr_c * (price - sl)
        else:
            sl = zone_high + buffer
            target = price - config.default_rr_c * (sl - price)

        if sl == price:
            continue

        rr = abs(target - price) / abs(price - sl)
        if rr < 2.0:
            continue

        signals.append({
            "setup": "SETUP-C",
            "symbol": symbol,
            "direction": direction,
            "entry": round(price, 2),
            "sl": round(sl, 2),
            "target": round(target, 2),
            "rr": round(rr, 2),
            "ob": htf_ob,
            "fvg": ltf_fvg,
        })

    return signals


def _detect_setup_d(ltf: list, htf: list, symbol: str, config: BacktestConfig) -> Optional[dict]:
    """
    Setup D — CHoCH + OB + FVG reversal.
    Disabled by default (negative expectancy from live data).
    """
    if len(ltf) < 30:
        return None

    result = smc.detect_choch_setup_d(ltf)
    if not result:
        return None

    direction, break_idx = result
    atr = smc.calculate_atr(ltf)
    if atr <= 0:
        return None

    # HTF alignment check
    if htf and len(htf) >= 25:
        htf_bias = smc.detect_htf_bias(htf)
        if htf_bias and direction != htf_bias:
            return None

    # Need OB and FVG near the break
    ob = smc.detect_order_block(ltf[:break_idx + 1], direction)
    fvg = smc.detect_fvg(ltf, direction)
    if not ob or not fvg:
        return None

    price = ltf[-1]["close"]
    buffer = atr * config.atr_buffer_mult

    if direction == "LONG":
        entry = (fvg[0] + fvg[1]) / 2
        sl = ob[0] - buffer
        target = entry + config.default_rr_d * (entry - sl)
    else:
        entry = (fvg[0] + fvg[1]) / 2
        sl = ob[1] + buffer
        target = entry - config.default_rr_d * (sl - entry)

    if sl == entry:
        return None

    rr = abs(target - entry) / abs(entry - sl)
    if rr < 2.0:
        return None

    return {
        "setup": "SETUP-D",
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "target": round(target, 2),
        "rr": round(rr, 2),
        "ob": ob,
        "fvg": fvg,
    }


# =====================================================
# MAIN BACKTESTER ENGINE
# =====================================================

class BacktestEngine:
    """
    Candle-by-candle backtester.

    Feed it historical 5-minute candles + 1H candles per symbol.
    It walks through time, detects setups, manages trades, and records results.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.trades: List[Trade] = []
        self.open_trades: List[Trade] = []
        self._trade_counter = 0
        self._daily_signal_count = {}   # {date_str: count}
        self._symbol_daily_count = {}   # {(symbol, date_str): count}
        self._daily_pnl_r = {}          # {date_str: float}  cumulative R per day

    def reset(self):
        """Clear all state for a fresh run."""
        self.trades = []
        self.open_trades = []
        self._trade_counter = 0
        self._daily_signal_count = {}
        self._symbol_daily_count = {}
        self._daily_pnl_r = {}

    def _next_id(self) -> int:
        self._trade_counter += 1
        return self._trade_counter

    # ------------------------------------------------------------------
    # TRADE MANAGEMENT
    # ------------------------------------------------------------------
    def _check_open_trades(self, candle: dict, candle_time: str):
        """
        Check all open trades against the current candle for SL/TP hits.

        Priority: SL checked FIRST (conservative — assume worst case intra-bar).
        """
        still_open = []

        for trade in self.open_trades:
            hit = False
            h = candle["high"]
            l = candle["low"]

            if trade.direction == "LONG":
                # SL hit? (price went below SL)
                if l <= trade.sl:
                    trade.exit_price = trade.sl
                    trade.exit_reason = "SL"
                    hit = True
                # TP hit? (price went above target)
                elif h >= trade.target:
                    trade.exit_price = trade.target
                    trade.exit_reason = "TP"
                    hit = True
            else:  # SHORT
                # SL hit? (price went above SL)
                if h >= trade.sl:
                    trade.exit_price = trade.sl
                    trade.exit_reason = "SL"
                    hit = True
                # TP hit? (price went below target)
                elif l <= trade.target:
                    trade.exit_price = trade.target
                    trade.exit_reason = "TP"
                    hit = True

            if hit:
                trade.exit_time = candle_time
                self._close_trade(trade)
            else:
                still_open.append(trade)

        self.open_trades = still_open

    def _eod_close(self, candle: dict, candle_time: str):
        """Force-close all open trades at end of day (EOD) price."""
        for trade in self.open_trades:
            trade.exit_price = candle["close"]
            trade.exit_time = candle_time
            trade.exit_reason = "EOD"
            self._close_trade(trade)
        self.open_trades = []

    def _close_trade(self, trade: Trade):
        """Calculate P&L and finalize a trade."""
        if trade.direction == "LONG":
            trade.gross_pnl_pts = trade.exit_price - trade.entry_price
        else:
            trade.gross_pnl_pts = trade.entry_price - trade.exit_price

        risk = trade.risk_pts
        if risk > 0:
            trade.r_multiple = trade.gross_pnl_pts / risk
        else:
            trade.r_multiple = 0.0

        # Transaction costs
        if self.config.apply_costs:
            is_idx = _is_index(trade.symbol)
            cost_cfg = INDEX_OPTIONS_COST if is_idx else EQUITY_INTRADAY_COST
            trade.cost_pts = cost_as_points(trade.entry_price, trade.exit_price,
                                            is_idx, cost_cfg)
            trade.net_pnl_pts = trade.gross_pnl_pts - trade.cost_pts
        else:
            trade.cost_pts = 0.0
            trade.net_pnl_pts = trade.gross_pnl_pts

        # Track daily R
        try:
            day = trade.exit_time[:10] if trade.exit_time else trade.entry_time[:10]
        except Exception:
            day = "unknown"

        net_r = trade.net_pnl_pts / risk if risk > 0 else 0.0
        self._daily_pnl_r[day] = self._daily_pnl_r.get(day, 0.0) + net_r

        self.trades.append(trade)

    def _open_trade(self, signal: dict, candle_time: str, smc_score: int,
                    smc_breakdown: Optional[dict] = None):
        """Open a new trade from a signal."""
        trade = Trade(
            trade_id=self._next_id(),
            symbol=signal["symbol"],
            setup=signal["setup"],
            direction=signal["direction"],
            entry_price=signal["entry"],
            sl=signal["sl"],
            target=signal["target"],
            rr=signal["rr"],
            smc_score=smc_score,
            entry_time=candle_time,
            smc_breakdown=smc_breakdown,
        )
        self.open_trades.append(trade)

        # Track counts
        try:
            day = candle_time[:10]
        except Exception:
            day = "unknown"
        self._daily_signal_count[day] = self._daily_signal_count.get(day, 0) + 1
        sk = (signal["symbol"], day)
        self._symbol_daily_count[sk] = self._symbol_daily_count.get(sk, 0) + 1

    def _can_take_signal(self, signal: dict, candle_time: str) -> bool:
        """Check daily caps and concurrent trade limits."""
        try:
            day = candle_time[:10]
        except Exception:
            day = "unknown"

        # Daily signal cap
        if self._daily_signal_count.get(day, 0) >= self.config.max_daily_signals:
            return False

        # Per-symbol daily cap
        sk = (signal["symbol"], day)
        if self._symbol_daily_count.get(sk, 0) >= self.config.max_signals_per_symbol_per_day:
            return False

        # Concurrent trades cap
        if len(self.open_trades) >= self.config.max_concurrent_trades:
            return False

        # Circuit breaker: daily R < -3
        daily_r = self._daily_pnl_r.get(day, 0.0)
        if daily_r <= -3.0:
            return False

        return True

    # ------------------------------------------------------------------
    # MAIN BACKTEST LOOP
    # ------------------------------------------------------------------
    def run(self, symbol: str, candles_5m: list,
            candles_1h: Optional[list] = None) -> List[Trade]:
        """
        Run backtest for a single symbol.

        Args:
            symbol: e.g. "NSE:NIFTY 50"
            candles_5m: list of 5-min OHLCV dicts (must have "date" key)
            candles_1h: list of 1H OHLCV dicts (optional — resampled from 5m if not provided)

        Returns:
            List of completed Trade records
        """
        if not candles_5m:
            return []

        # Resample HTF if not provided
        if candles_1h is None:
            candles_1h = resample_to_htf(candles_5m, htf_minutes=60)

        start_trades = len(self.trades)
        prev_day = None

        # Walk through each 5-min candle
        for i in range(self.config.ltf_lookback, len(candles_5m)):
            candle = candles_5m[i]
            candle_time = candle.get("date", "")

            # Extract day for EOD logic
            try:
                dt = datetime.fromisoformat(candle_time) if isinstance(candle_time, str) else candle_time
                current_day = dt.date().isoformat()
                current_time = dt.time()
            except Exception:
                current_day = prev_day or "unknown"
                current_time = time(12, 0)

            # Day change → EOD close previous day's trades
            if prev_day and current_day != prev_day:
                # Use previous candle for EOD close
                if i > 0:
                    self._eod_close(candles_5m[i - 1], candles_5m[i - 1].get("date", ""))
            prev_day = current_day

            # Build rolling windows
            ltf_window = candles_5m[max(0, i - self.config.ltf_lookback + 1):i + 1]

            # Build HTF window: all 1H candles up to this point in time
            htf_window = [c for c in candles_1h
                          if c.get("date", "") <= candle_time][-self.config.htf_lookback:]

            # Check open trades against this candle
            self._check_open_trades(candle, candle_time)

            # Skip signal detection outside killzone
            if not _in_killzone(candle_time, self.config):
                continue

            # Skip dead zone (10:00-11:00) — zero confidence
            kz_conf = _killzone_confidence(candle_time)
            if kz_conf <= 0:
                continue

            # Detect signals
            signals = []

            if self.config.enable_setup_a:
                s = _detect_setup_a(ltf_window, htf_window, symbol, self.config)
                if s:
                    signals.append(s)

            if self.config.enable_setup_b:
                sb = _detect_setup_b(ltf_window, htf_window, symbol, self.config)
                signals.extend(sb)

            if self.config.enable_setup_c:
                sc = _detect_setup_c(ltf_window, htf_window, symbol, self.config)
                signals.extend(sc)

            if self.config.enable_setup_d:
                sd = _detect_setup_d(ltf_window, htf_window, symbol, self.config)
                if sd:
                    signals.append(sd)

            # Score and filter
            for sig in signals:
                if not self._can_take_signal(sig, candle_time):
                    continue

                score, breakdown = confluence_score(sig, ltf_window, htf_window)

                if score < self.config.min_smc_score:
                    continue

                # Open trade
                self._open_trade(sig, candle_time, score, breakdown)

        # EOD close any remaining open trades at last candle
        if candles_5m and self.open_trades:
            last = candles_5m[-1]
            self._eod_close(last, last.get("date", ""))

        return self.trades[start_trades:]

    def run_multi(self, data: Dict[str, dict]) -> List[Trade]:
        """
        Run backtest across multiple symbols.

        Args:
            data: {symbol: {"5m": [...], "1h": [...]} }

        Returns:
            All trades (also accessible via self.trades)
        """
        total = len(data)
        for idx, (symbol, tf_data) in enumerate(data.items(), 1):
            candles_5m = tf_data.get("5m", tf_data.get("5minute", []))
            candles_1h = tf_data.get("1h", tf_data.get("60minute", None))
            logger.info(f"[{idx}/{total}] Backtesting {symbol} "
                        f"({len(candles_5m)} candles)...")
            self.run(symbol, candles_5m, candles_1h)

        return self.trades
