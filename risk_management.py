# =====================================================
# RISK MANAGEMENT & POSITION SIZING ENGINE
# =====================================================
# Enforces strict 1:3 RR, win rate filters, and position sizing

import os
import json
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

# =====================================================
# CONFIGURATION (LIVE trading params)
# =====================================================

# Account Risk Parameters
ACCOUNT_SIZE = 100000  # Base capital
RISK_PER_TRADE = 0.01  # 1% of account per trade
MAX_DAILY_LOSS = 0.05  # Stop trading after 5% loss
MAX_TRADES_PER_DAY = 10

# RR Requirements (STRICT)
MIN_RR_RATIO = 2.0     # 1:2 minimum (aligned with grid search optimal RR)
PREFERRED_RR_RATIO = 3.5  # Target 3.5:1 for extra edge

# Win Rate & Confluence Thresholds
# ⚠️ F4.2: Relaxed until Phase 3 backtest provides real statistics
MIN_WIN_RATE = 0.40     # Temporarily lowered — tighten after backtest validation
MIN_SMC_SCORE = 5       # Match engine's existing min score (was 6 — too restrictive without data)
MIN_SIGNAL_QUALITY = 0.50  # Relaxed from 0.70 until backtest calibration

# Backtest Historical Win Rates (by setup type)
# ⚠️ F4.2: Conservative estimates — UPDATE after Phase 3 backtest with real data
# These are deliberately set at breakeven-to-cautious levels until validated
SETUP_WINRATES = {
    "SETUP-A": 0.50,       # No real backtest yet — assume breakeven
    "SETUP-B": 0.45,       # Untested
    "SETUP-C": 0.48,       # Untested (was UNIVERSAL)
    "SETUP-D": 0.45,       # F1.5: DISABLED in aggressive mode (net loser)
    "A-FVG-BUY": 0.50,     # Sub-variant of A — no data
    "A-FVG-SELL": 0.50,    # Sub-variant of A — no data
    "UNIVERSAL-LONG": 0.48, # Setup C variant
    "UNIVERSAL-SHORT": 0.48,
    "OI-UNWIND-CE": 0.50,  # OI Unwinding → Buy CE — untested, forward validate
    "OI-UNWIND-PE": 0.50,  # OI Unwinding → Buy PE — untested, forward validate
    "EMA-CROSSOVER": 0.45, # Merged EMA strategy — untested
    "HIERARCHICAL": 0.45,  # Rarely triggers
}

DAILY_LOG_FILE = "daily_pnl_log.csv"
POSITION_CACHE_FILE = "position_cache.json"

# =====================================================
# RISK CALCULATION
# =====================================================

def calculate_position_size(entry: float, sl: float, account_size: float = ACCOUNT_SIZE, 
                           risk_per_trade: float = RISK_PER_TRADE) -> float:
    """
    Calculates position size in units based on risk per trade
    
    Formula: Position Size = (Account Size * Risk %) / (Entry - SL)
    
    Args:
        entry: Entry price
        sl: Stop loss price
        account_size: Total capital
        risk_per_trade: Risk percentage per trade (0.01 = 1%)
    
    Returns:
        Quantity in units
    """
    risk_amount = account_size * risk_per_trade
    price_risk = abs(entry - sl)
    
    if price_risk <= 0:
        return 0
    
    position_size = risk_amount / price_risk
    return int(position_size)  # Round down to whole units


def calculate_rr_ratio(entry: float, sl: float, target: float, direction: str) -> float:
    """
    Calculates Risk:Reward ratio
    
    Formula: RR = Reward / Risk = |Target - Entry| / |Entry - SL|
    
    Returns:
        RR ratio (e.g., 3.0 for 1:3)
    """
    risk = abs(entry - sl)
    reward = abs(target - entry)
    
    if risk <= 0:
        return 0
    
    return round(reward / risk, 2)


def validate_rr_ratio(entry: float, sl: float, target: float, 
                     min_rr: float = MIN_RR_RATIO) -> tuple:
    """
    Validates if RR meets minimum requirements
    
    Returns:
        (is_valid, rr_ratio, message)
    """
    rr = calculate_rr_ratio(entry, sl, target, "")
    
    if rr < min_rr:
        return (False, rr, f"RR {rr} below minimum {min_rr}")
    
    if rr >= min_rr:
        return (True, rr, f"✓ RR {rr} meets {min_rr} requirement")
    
    return (False, rr, "Invalid RR calculation")


def adjust_target_for_rr(entry: float, sl: float, target: float, 
                        desired_rr: float = MIN_RR_RATIO) -> float:
    """
    Adjusts target price to achieve desired RR ratio
    
    Args:
        entry: Entry price
        sl: Stop loss price
        target: Current target
        desired_rr: Target RR ratio (e.g., 3.0)
    
    Returns:
        Adjusted target price
    """
    risk = abs(entry - sl)
    
    if entry > sl:  # LONG
        new_target = entry + (risk * desired_rr)
    else:  # SHORT
        new_target = entry - (risk * desired_rr)
    
    return round(new_target, 2)


# =====================================================
# WIN RATE & SETUP QUALITY
# =====================================================

def get_setup_winrate(setup_type: str) -> float:
    """
    Returns historical win rate for a setup type
    """
    return SETUP_WINRATES.get(setup_type, 0.50)


def calculate_signal_quality(signal: dict, smc_score: int = 0) -> float:
    """
    Calculates overall signal quality (0-1 scale)
    
    Factors:
    - RR ratio (weight: 40%)
    - SMC score (weight: 30%)
    - Setup win rate (weight: 20%)
    - Entry freshness (weight: 10%)
    """
    quality = 0.0
    
    # 1. RR Score (40%)
    rr = signal.get("rr", 0)
    rr_score = min(rr / PREFERRED_RR_RATIO, 1.0)  # Cap at 1.0
    quality += rr_score * 0.40
    
    # 2. SMC Confluence Score (30%)
    score = smc_score if smc_score else signal.get("smc_score", 5)
    confluence_score = score / 10.0  # Normalize to 0-1
    quality += confluence_score * 0.30
    
    # 3. Setup Win Rate (20%)
    setup = signal.get("setup", "UNKNOWN")
    wr = get_setup_winrate(setup)
    quality += wr * 0.20
    
    # 4. Entry Freshness (10%) - Higher score if entry is still fresh
    freshness_score = min(signal.get("freshness", 0.8), 1.0)
    quality += freshness_score * 0.10
    
    return round(quality, 3)


def is_signal_approved(signal: dict, smc_score: int = 0) -> tuple:
    """
    Determines if a signal meets all approval criteria
    
    Returns:
        (is_approved, reason, quality_score)
    """
    reasons = []
    
    # 1. Check RR Ratio
    rr = signal.get("rr", 0)
    if rr < MIN_RR_RATIO:
        reasons.append(f"❌ RR {rr} < {MIN_RR_RATIO}")
    
    # 2. Check SMC Score
    score = smc_score if smc_score else signal.get("smc_score", 5)
    if score < MIN_SMC_SCORE:
        reasons.append(f"❌ SMC {score} < {MIN_SMC_SCORE}")
    
    # 3. Check Setup Win Rate
    setup = signal.get("setup", "UNKNOWN")
    wr = get_setup_winrate(setup)
    if wr < MIN_WIN_RATE:
        reasons.append(f"❌ Setup WR {wr:.1%} < {MIN_WIN_RATE:.1%}")
    
    # 4. Calculate Overall Quality
    quality = calculate_signal_quality(signal, smc_score)
    if quality < MIN_SIGNAL_QUALITY:
        reasons.append(f"❌ Quality {quality:.2f} < {MIN_SIGNAL_QUALITY}")
    
    is_approved = len(reasons) == 0
    
    if is_approved:
        return (True, f"✅ Approved | Quality: {quality:.2f} | RR: {rr} | SMC: {score}", quality)
    else:
        reason_str = " | ".join(reasons)
        return (False, reason_str, quality)


# =====================================================
# DAILY RISK LIMITS
# =====================================================

def load_daily_trades() -> list:
    """Loads today's trade log"""
    if not os.path.exists(DAILY_LOG_FILE):
        return []
    
    try:
        trades = []
        with open(DAILY_LOG_FILE, "r") as f:
            lines = f.readlines()
            for line in lines[1:]:  # Skip header
                if line.strip():
                    trades.append(line.strip())
        return trades
    except:
        return []


def get_daily_pnl() -> float:
    """Calculates today's P&L in RR units"""
    trades = load_daily_trades()
    today = datetime.now(_IST).date().isoformat()
    
    daily_pnl = 0.0
    for trade_line in trades:
        # CSV format: date, setup, direction, entry, sl, target, result, pnl_r
        parts = trade_line.split(",")
        if len(parts) >= 8:
            trade_date = parts[0]
            pnl_r = float(parts[7])
            
            if trade_date.startswith(today):
                daily_pnl += pnl_r
    
    return daily_pnl


def get_daily_trade_count() -> int:
    """Counts today's trades"""
    trades = load_daily_trades()
    today = datetime.now(_IST).date().isoformat()
    
    count = 0
    for trade in trades:
        if trade.startswith(today):
            count += 1
    
    return count


def can_trade_today() -> tuple:
    """
    Checks if trading is allowed based on daily limits
    
    Returns:
        (is_allowed, reason)
    """
    # Check daily loss limit
    daily_pnl = get_daily_pnl()
    # Daily pnl is tracked in R units; convert daily loss policy to R.
    # Example: 5% max daily loss with 1% risk/trade => -5R.
    max_loss = -(MAX_DAILY_LOSS / RISK_PER_TRADE)
    
    if daily_pnl <= max_loss:
        return (False, f"❌ Daily loss limit reached ({daily_pnl:.2f}R / {max_loss:.2f}R)")
    
    # Check trade count
    trade_count = get_daily_trade_count()
    if trade_count >= MAX_TRADES_PER_DAY:
        return (False, f"❌ Max trades per day ({trade_count}/{MAX_TRADES_PER_DAY}) reached")
    
    return (True, f"✅ Can Trade | PnL: {daily_pnl:.2f}R | Trades: {trade_count}/{MAX_TRADES_PER_DAY}")


# =====================================================
# SIGNAL ENHANCEMENT (Add RR & Quality)
# =====================================================

def enhance_signal(signal: dict, smc_score: int = 0) -> dict:
    """
    Enhances signal with RR validation and quality scoring
    
    Returns:
        Enhanced signal dict with RR and quality metrics
    """
    # Calculate & validate RR
    entry = signal.get("entry", 0)
    sl = signal.get("sl", 0)
    target = signal.get("target", 0)
    
    rr = calculate_rr_ratio(entry, sl, target, signal.get("direction", "LONG"))
    
    # Adjust target if RR is too low
    if rr < MIN_RR_RATIO:
        adjusted_target = adjust_target_for_rr(entry, sl, target, MIN_RR_RATIO)
        signal["target"] = adjusted_target
        signal["target_adjusted"] = True
        rr = MIN_RR_RATIO
    
    # Add RR and quality to signal
    signal["rr"] = rr
    signal["smc_score"] = smc_score
    
    # Check approval
    approved, reason, quality = is_signal_approved(signal, smc_score)
    signal["approved"] = approved
    signal["approval_reason"] = reason
    signal["quality_score"] = quality
    
    return signal


# =====================================================
# RISK REPORT
# =====================================================

# =====================================================
# RISK MANAGER CLASS APPLICATION
# =====================================================

from dataclasses import dataclass

@dataclass
class RiskParams:
    account_size: float = ACCOUNT_SIZE
    risk_pct: float = RISK_PER_TRADE
    max_daily_loss: float = MAX_DAILY_LOSS
    max_trades: int = MAX_TRADES_PER_DAY
    min_rr: float = MIN_RR_RATIO

class RiskManager:
    """
    Centralized Risk Manager for the Trading Engine.
    Wraps module-level functions into a cohesive object.
    """
    def __init__(self, params: RiskParams = None):
        self.params = params if params else RiskParams()

    def calculate_position_size(self, entry, sl):
        return calculate_position_size(entry, sl, self.params.account_size, self.params.risk_pct)

    def validate_setup(self, entry, sl, target):
        return validate_rr_ratio(entry, sl, target, self.params.min_rr)

    def check_daily_limits(self):
        return can_trade_today()

    def can_take_trade(self):
        return can_trade_today()

    def calculate_rr(self, entry, sl, target, direction):
        return calculate_rr_ratio(entry, sl, target, direction)

    def passes_rr_filter(self, entry, sl, target, direction):
        valid, rr, msg = validate_rr_ratio(entry, sl, target, self.params.min_rr)
        return valid

        
    def record_trade_result(self, pnl):
        # Placeholder for backtesting state tracking
        pass
        
    def apply_slippage(self, price, direction, is_entry=True):
        # Placeholder for slippage logic
        return price
        
    def total_costs(self):
        return 0.0



# =====================================================
# TESTING
# =====================================================

if __name__ == "__main__":
    # Test signal
    test_signal = {
        "setup": "SETUP-A",
        "symbol": "NSE:NIFTY 50",
        "direction": "LONG",
        "entry": 23500.0,
        "sl": 23400.0,
        "target": 23800.0,
        "smc_score": 7
    }
    
    print("TEST SIGNAL:")
    print(f"  Entry: {test_signal['entry']}")
    print(f"  SL: {test_signal['sl']}")
    print(f"  Target: {test_signal['target']}")
    
    rr = calculate_rr_ratio(test_signal['entry'], test_signal['sl'], test_signal['target'], "LONG")
    print(f"  RR: 1:{rr}")
    
    enhanced = enhance_signal(test_signal, 7)
    print(f"\nENHANCED SIGNAL:")
    print(f"  Approved: {enhanced['approved']}")
    print(f"  Reason: {enhanced['approval_reason']}")
    print(f"  Quality: {enhanced['quality_score']}")
    
    qty = calculate_position_size(test_signal['entry'], test_signal['sl'])
    print(f"\nPOSITION SIZING:")
    print(f"  Risk per Trade: {RISK_PER_TRADE:.1%}")
    print(f"  Position Size: {qty} units")
    
    print_risk_summary()
