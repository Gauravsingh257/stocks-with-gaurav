"""
Execution Core
==============
Shared logic for trade lifecycle execution, PnL calculation, and risk rules.
Used by BOTH Backtest Engine and Live Engine to ensure identical behavior.
"""

from typing import Dict, Tuple, Optional
import datetime

# ─── CORE CONSTANTS ────────────────────────────────
MAX_TRADES_PER_DAY = 2
MAX_CONCURRENT_TRADES = 1
ONE_TRADE_PER_SYMBOL = True  # If True, only 1 active trade per symbol allowed


# ─── STATE MANAGEMENT ─────────────────────────────
class ExecutionState:
    """Tracks global execution state."""
    def __init__(self):
        self.active_trades = [] # List of trade dicts
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.current_equity = 0.0 # Will be initialized by engine
        self.peak_equity = 0.0
        self.max_drawdown = 0.0
        self.last_reset_date = None

    def reset_daily_if_needed(self, current_date: datetime.date):
        if self.last_reset_date != current_date:
            self.daily_trades = 0
            self.daily_pnl = 0.0
            self.last_reset_date = current_date


# ─── CORE FUNCTIONS ───────────────────────────────

def calculate_rr(entry: float, sl: float, target: float) -> float:
    """Calculate Risk:Reward ratio."""
    risk = abs(entry - sl)
    reward = abs(target - entry)
    if risk == 0:
        return 0.0
    return round(reward / risk, 2)


def open_trade(signal: Dict, timestamp: datetime.datetime, state: ExecutionState) -> Tuple[Optional[Dict], str]:
    """
    Attempts to open a trade based on signal and risk limits.
    Returns: (trade_dict, reason)
        trade_dict: The new trade object if accepted, None otherwise
        reason: "ACCEPTED" or rejection reason
    """
    
    # 1. Check Limits
    if state.daily_trades >= MAX_TRADES_PER_DAY:
        return None, "DAILY_LIMIT_REACHED"
        
    if len(state.active_trades) >= MAX_CONCURRENT_TRADES:
        return None, "MAX_CONCURRENT_LIMIT"
        
    if ONE_TRADE_PER_SYMBOL:
        for t in state.active_trades:
            if t["symbol"] == signal["symbol"]:
                return None, f"ALREADY_ACTIVE_ON_{signal['symbol']}"

    # 2. Construct Trade
    # Ensure all required fields exist
    sl = signal.get("stop_loss", signal.get("sl"))
    target = signal.get("target", signal.get("tp"))
    
    if sl is None or target is None:
        return None, "MISSING_SL_OR_TARGET"
        
    rr = signal.get("rr", calculate_rr(signal["entry"], sl, target))
    
    trade = {
        "id": f"{signal['symbol']}_{timestamp.strftime('%Y%m%d%H%M')}",
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": sl,
        "target": target,
        "rr": rr,
        "entry_time": timestamp,
        "status": "OPEN",
        "pnl": 0.0,
        "exit_time": None,
        "exit_price": 0.0,
        "exit_reason": None,
        # Logging fields
        "setup": signal.get("setup", "UNKNOWN"),
        "confidence": signal.get("confidence_score", 0),
        "regime": signal.get("regime", "UNKNOWN"),
        "displacement": signal.get("displacement_multiple", 0),
        "ob_age": signal.get("ob_age", 0)
    }
    
    # Update State
    state.active_trades.append(trade)
    state.daily_trades += 1
    
    return trade, "ACCEPTED"


def check_exit(trade: Dict, candle: Dict) -> Tuple[bool, str, float]:
    """
    Checks if a trade should exit based on the provided candle (OHLC).
    Handles TP/SL conflicts.
    
    Returns: (should_exit, reason, exit_price)
    """
    high = candle["high"]
    low = candle["low"]
    
    # Levels
    tp = trade["target"]
    sl = trade["sl"]
    entry = trade["entry"]
    direction = trade["direction"]
    
    hit_tp = False
    hit_sl = False
    
    # Check simple range intersection
    if direction == "LONG":
        if low <= sl: hit_sl = True
        if high >= tp: hit_tp = True
    else: # SHORT
        if high >= sl: hit_sl = True
        if low <= tp: hit_tp = True
        
    # No interaction
    if not hit_tp and not hit_sl:
        return False, None, 0.0
        
    # Clear result execution
    if hit_tp and not hit_sl:
        return True, "TARGET", tp
    if hit_sl and not hit_tp:
        return True, "STOP_LOSS", sl
        
    # CONFLICT: Both hit in same candle
    return handle_tp_sl_conflict(trade, candle)


def handle_tp_sl_conflict(trade: Dict, candle: Dict) -> Tuple[bool, str, float]:
    """
    Resolves TP/SL conflict using distance logic (Open bias).
    Rules:
    If LONG:
        If abs(open - SL) < abs(open - TP) → SL first
        Else → TP first
    If SHORT:
        If abs(open - SL) < abs(open - TP) → SL first
        Else → TP first
    """
    open_price = candle["open"]
    tp = trade["target"]
    sl = trade["sl"]
    
    dist_sl = abs(open_price - sl)
    dist_tp = abs(open_price - tp)
    
    if dist_sl < dist_tp:
        return True, "STOP_LOSS", sl
    else:
        return True, "TARGET", tp


def close_trade(trade: Dict, timestamp: datetime.datetime, reason: str, exit_price: float, state: ExecutionState) -> Dict:
    """
    Closes a trade, calculates PnL, updates state.
    Returns: Updated trade dict
    """
    trade["status"] = "CLOSED"
    trade["exit_time"] = timestamp
    trade["exit_reason"] = reason
    trade["exit_price"] = exit_price
    
    # Calculate PnL (R-Multiples)
    # On WIN: pnl = trade["rr"]
    # On LOSS: pnl = -1.0
    
    if reason == "TARGET":
        pnl = trade["rr"]
    elif reason == "STOP_LOSS":
        pnl = -1.0
    else:
        # Manual/Time exit - calculate actual R
        risk = abs(trade["entry"] - trade["sl"]) if trade["entry"] != trade["sl"] else 1.0
        diff = 0
        if trade["direction"] == "LONG":
            diff = exit_price - trade["entry"]
        else:
            diff = trade["entry"] - exit_price
        pnl = diff / risk
        
    trade["pnl"] = round(pnl, 2)
    
    # Update State
    if trade in state.active_trades:
        state.active_trades.remove(trade)
    
    state.daily_pnl += pnl
    state.current_equity += pnl # Assuming equity tracks R's for now, or scaled value
    
    # Drawdown update
    if state.current_equity > state.peak_equity:
        state.peak_equity = state.current_equity
    
    dd = state.current_equity - state.peak_equity
    if dd < state.max_drawdown:
        state.max_drawdown = dd
        
    return trade
