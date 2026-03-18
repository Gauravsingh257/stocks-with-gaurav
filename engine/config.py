"""
engine/config.py — All constants, strategy flags, and mutable global state.
Extracted from smc_mtf_engine_v4.py Part 1 + Global State (Phase 5).

Import this module to access shared state across all engine modules.
"""

import os
from datetime import datetime

# =====================================================
# ENGINE MODE & STRATEGY FLAGS
# =====================================================

ENGINE_MODE = "AGGRESSIVE"

if ENGINE_MODE == "CONSERVATIVE":
    ACTIVE_STRATEGIES = {"SETUP_A": False, "SETUP_B": False, "SETUP_C": False, "SETUP_D": False, "HIERARCHICAL": True}
elif ENGINE_MODE == "BALANCED":
    ACTIVE_STRATEGIES = {"SETUP_A": False, "SETUP_B": False, "SETUP_C": False, "SETUP_D": False, "HIERARCHICAL": True}
elif ENGINE_MODE == "AGGRESSIVE":
    ACTIVE_STRATEGIES = {"SETUP_A": True, "SETUP_B": False, "SETUP_C": True, "SETUP_D": False, "HIERARCHICAL": True}
    # F1.5: SETUP_D disabled — confirmed net negative from backtest data
    # SWEEP-B: SETUP_B disabled — grid search showed -0.31R expectancy

# =====================================================
# STOCK SIGNAL CUTOFF (morning-only trading)
# =====================================================
# Backtest shows: Morning (9-12) = +22R, 46% WR | Afternoon (12-15:30) = -15.75R, 26.7% WR
# New stock entries blocked after this time. Existing trades still monitored for SL/TP.
STOCK_SIGNAL_CUTOFF_HOUR = 12   # No new stock signals after 12:00 PM
STOCK_SIGNAL_CUTOFF_MIN = 0

# =====================================================
# SLIPPAGE MODEL
# =====================================================
SLIPPAGE_INDEX_PTS = 3
SLIPPAGE_STOCK_PCT = 0.05
BROKERAGE_PER_ORDER = 20

# =====================================================
# EMA CROSSOVER PARAMETERS
# =====================================================
EMA_FAST = 10
EMA_SLOW = 20
EMA_TREND = 200
ADX_PERIOD = 14
ADX_THRESHOLD = 20
VOLUME_MULTIPLIER = 1.2

# =====================================================
# TELEGRAM / ENVIRONMENT
# =====================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SMC_PRO_CHAT_ID = os.getenv("SMC_PRO_CHAT_ID", "")

DEBUG_MODE = True
BACKTEST_MODE = os.environ.get("BACKTEST_MODE", "") == "1"

# =====================================================
# FILE PATHS
# =====================================================
ACTIVE_SETUPS_FILE = "active_setups.json"
POSITION_CACHE_FILE = "position_cache.json"
MORNING_WATCHLIST_FLAG = "morning_watchlist.flag"

# =====================================================
# INDEX SYMBOLS
# =====================================================
INDEX_SYMBOLS = [
    "NSE:NIFTY 50",
    "NSE:NIFTY BANK"
]

# =====================================================
# MUTABLE GLOBAL STATE
# =====================================================
# These are modified at runtime by various engine components.
# Access via `from engine.config import VAR` and mutate in place
# (for dicts/lists) or reassign via `engine.config.VAR = val`.

HTF_CACHE = {}
STRUCTURE_STATE = {}
HTF_CACHE_TIME = {}
TOKEN_CACHE = {}
LAST_HOURLY_SUMMARY = None
TRADING_MODE = "AGGRESSIVE"

ZONE_STATE = {}  # { "symbol": { "LONG": zone, "SHORT": zone } }
ACTIVE_TRADES = []
DAILY_LOG = []
EOD_SENT = False
SWING_SCAN_SENT = False
ACTIVE_SWING_TRADES = []

# Circuit breaker
DAILY_PNL_R = 0.0
CONSECUTIVE_LOSSES = 0
MAX_DAILY_LOSS_R = -3.0
COOLDOWN_AFTER_STREAK = 3
CIRCUIT_BREAKER_ACTIVE = False
COOLDOWN_UNTIL = None

SETUP_D_STATE = {}
MANUAL_ORDER_CACHE = set()
TRADED_TODAY = set()
MAX_DAILY_SIGNALS = 5

# Multi-day drawdown breaker
MULTI_DAY_DD_LIMIT = -10.0
MULTI_DAY_DD_WINDOW = 5
MULTI_DAY_HALT_HOURS = 48
MULTI_DAY_HALT_UNTIL = None

# Setup-C daily cap
SETUP_C_DAILY_COUNT = {}

# Market regime
MARKET_REGIME = "UNKNOWN"
REGIME_LAST_UPDATE = None

# Volatility regime
VOLATILITY_REGIME = "NORMAL"
VOL_REGIME_CACHE = {}

# Option chain
OPTION_CHAIN_DATA = {}

# =====================================================
# OPTIONS SIGNAL ENGINE CONFIG
# =====================================================
OPT_UNDERLYINGS = [
    {"symbol": "NSE:NIFTY BANK", "name": "BANKNIFTY", "step": 100, "range": 0},
    {"symbol": "NSE:NIFTY 50",   "name": "NIFTY",     "step": 100, "range": 0},
]
OPT_OI_DELTA_THRESHOLD = 0.05
OPT_OI_LARGE_THRESHOLD = 0.05       # OI change >= 5% gets bonus point
OPT_SESSION_LOW_BOUNCE_PCT = 0.005
OPT_MOMENTUM_BODY_RATIO = 0.6
OPT_ALERT_COOLDOWN_SECS = 300
OPT_SCAN_INTERVAL = 60
OPT_ATM_REFRESH_INTERVAL = 600
OPT_OI_SNAPSHOT_INTERVAL = 300
OPT_SCORE_SESSION_LOW = 3
OPT_SCORE_SESSION_BREAK = 2         # Intraday session low break (early cycle)
OPT_SCORE_CALL_UNWIND = 2
OPT_SCORE_PUT_SURGE = 2
OPT_SCORE_OI_LARGE_BONUS = 1       # Extra point for OI change >= 5%
OPT_SCORE_MOMENTUM = 2
OPT_SCORE_EARLY_SESSION = 1
OPT_ALERT_THRESHOLD_HIGH = 7
OPT_ALERT_THRESHOLD_MED = 5
OPT_SESSION_SWING_MIN_PCT = 0.03   # 3% min swing from session high for session break
OPT_MIN_HISTORY_DAYS = 2           # Need 2+ days before monthly low is meaningful
OPT_BOUNCE_CONFIRM_PCT = 0.02     # 2% bounce from low = reversal confirmed
OPT_BOUNCE_MAX_WAIT_MIN = 30      # Max 30 min to wait for bounce after break
OPT_BREAK_INFO_ALERT = True       # Send informational alert on break (no trade plan)
OPT_OI_HISTORY_SIZE = 120
OPT_OI_DELTA_WINDOW_SECS = 60
OPT_CACHE_PKL = "instruments_nfo.pkl"
OPT_BN_STATE_FILE = "bn_signal_state.json"

# =====================================================
# OI SENTIMENT CONFIG
# =====================================================
OI_SENTIMENT_REFRESH_SECS = 120   # Refresh every 2 minutes (was 600 — too slow for reversals)
OI_PCR_BULLISH_THRESHOLD = 1.2    # PCR above this → bullish support
OI_PCR_BEARISH_THRESHOLD = 0.7    # PCR below this → bearish pressure
OI_PCR_STRONG_BULL = 1.3           # PCR above this → strong bullish (higher weight)
OI_PCR_STRONG_BEAR = 0.6           # PCR below this → strong bearish (higher weight)
OI_PCR_HISTORY_SIZE = 30           # Store last 30 readings (~1 hr at 2-min refresh)
OI_CHANGE_THRESHOLD_PCT = 2.0     # Min OI change % to count as significant (was 3%)
OI_BUILDUP_MIN_SIGNALS = 2        # Need 2+ sub-signals to call a bias

# Scoring weights (Phase 2 redesign)
OI_WEIGHT_PCR_LEVEL = 2            # PCR above/below threshold
OI_WEIGHT_PCR_STRONG = 3           # PCR at strong levels (>1.3 or <0.6)
OI_WEIGHT_PCR_TREND = 2            # PCR directional trend
OI_WEIGHT_LONG_BUILDUP = 3         # Price UP + OI UP
OI_WEIGHT_SHORT_COVERING = 3       # Price UP + OI DOWN (key reversal signal)
OI_WEIGHT_SHORT_BUILDUP = 3        # Price DOWN + OI UP
OI_WEIGHT_LONG_UNWINDING = 2       # Price DOWN + OI DOWN
OI_WEIGHT_OI_CHANGE_BIAS = 1       # Net directional OI change
OI_WEIGHT_HEATMAP_WALL = 2         # Strike heatmap PE/CE wall near spot

# =====================================================
# OI SHORT-COVERING DETECTOR CONFIG
# =====================================================
OI_SC_STRIKES_RANGE = 1             # ±1 strikes around ATM (ATM±1 only, legacy — see expiry_manager)
OI_SC_REFRESH_SECS = 60             # Scan every 60 seconds
OI_SC_HISTORY_SIZE = 60             # Keep last 60 readings per strike (~1hr at 60s)
OI_SC_MIN_READINGS = 3              # Need >= 3 readings before detecting
OI_SC_ROLLING_WINDOW = 5            # Compare current vs 5 readings ago
OI_SC_MIN_OI_DROP_PCT = 0.05        # 5% OI drop in rolling window
OI_SC_MIN_PRICE_RISE_PCT = 0.03     # 3% price rise in same window
OI_SC_PEAK_DROP_PCT = 0.08          # 8% OI drop from intraday peak
OI_SC_VELOCITY_PCT = 0.02           # 2% avg drop per reading = aggressive
OI_SC_VOLUME_MULT = 1.3             # Volume > 1.3× rolling avg
OI_SC_MIN_SCORE = 5                 # Min score to fire (out of 10)
OI_SC_MAX_PER_UL_DAY = 1            # Max 1 short-covering trade per underlying per day
OI_SC_SL_ATR_MULT = 1.2             # SL = 1.2× ATR
OI_SC_TARGET_RR = 2.0               # 2R minimum target
OI_SC_ALERT_COOLDOWN_SECS = 300     # 5 min cooldown between alerts per strike

# =====================================================
# EXPIRY MANAGEMENT CONFIG
# =====================================================
EXPIRY_PRELOAD_DAYS = 3              # Calendar days before expiry to start preloading next
EXPIRY_ATM_DRIFT_CHECK_SECS = 120   # Check ATM drift every 2 minutes

# =====================================================
# SWING SCANNER CONFIG
# =====================================================
MAX_SWING_PICKS = 4
MIN_SWING_QUALITY = 7
