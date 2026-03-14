"""
AI Learning System — Global Configuration
==========================================
All constants, paths, and hyperparameters for the three-agent pipeline.
"""

import os
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent          # Trading Algo/
AI_DIR = Path(__file__).resolve().parent                   # Trading Algo/ai_learning/
DB_PATH = AI_DIR / "data" / "trade_learning.db"
MODELS_DIR = AI_DIR / "models"
CHARTS_DIR = AI_DIR / "charts"
EXPORTS_DIR = AI_DIR / "exports"
STRATEGY_OUTPUT_DIR = AI_DIR / "generated_strategies"

# Ensure directories exist
for d in [AI_DIR / "data", MODELS_DIR, CHARTS_DIR, EXPORTS_DIR, STRATEGY_OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Feature Extraction ──────────────────────────────────────────────────
CANDLE_LOOKBACK = 200          # bars before entry for context
HTF_INTERVALS = ["15minute", "30minute", "60minute", "day"]
LTF_INTERVALS = ["5minute", "3minute"]
DEFAULT_TIMEFRAME = "5minute"

# SMC detection parameters (aligned with existing smc_detectors.py)
SWING_LEFT = 3
SWING_RIGHT = 3
OB_LOOKBACK = 30
FVG_LOOKBACK = 30
OB_DISPLACEMENT_MULT = 2.0
OB_BODY_ATR_RATIO = 0.3
FVG_MIN_GAP_ATR = 0.1
LIQUIDITY_LOOKBACK = 50

# ─── Session Definitions (IST) ───────────────────────────────────────────
SESSIONS = {
    "ASIA":          {"start": "09:15", "end": "11:30"},
    "INDIA_MID":     {"start": "11:30", "end": "13:30"},
    "LONDON_OVERLAP": {"start": "13:30", "end": "15:30"},
    "LONDON_OPEN":   {"start": "14:00", "end": "16:00"},
    "NY_OVERLAP":    {"start": "18:30", "end": "21:00"},
    "KILLZONE_AM":   {"start": "09:15", "end": "10:30"},
    "KILLZONE_PM":   {"start": "14:00", "end": "15:30"},
}

# ─── Pattern Learning (Agent 1) ──────────────────────────────────────────
MIN_TRADES_FOR_LEARNING = 15       # minimum trades to start clustering
MAX_CLUSTERS = 8                    # upper bound for auto K selection
CLUSTER_METHOD = "kmeans"           # "kmeans" | "hierarchical" | "dbscan"
SIMILARITY_THRESHOLD = 0.70        # cosine similarity cutoff for pattern matching
MIN_CLUSTER_SIZE = 3               # minimum trades to form a valid strategy cluster
WIN_RATE_CONFIDENCE_MIN_TRADES = 10 # min trades for reliable win-rate estimate

# Feature weights for style profiling
FEATURE_WEIGHTS = {
    "trend_alignment":     0.15,
    "order_block":         0.20,
    "fair_value_gap":      0.15,
    "liquidity_sweep":     0.15,
    "structure_break":     0.10,
    "displacement":        0.10,
    "session_timing":      0.05,
    "volatility_context":  0.05,
    "rr_ratio":            0.05,
}

# ─── Strategy Generation (Agent 2) ────────────────────────────────────────
MIN_CONFIDENCE_FOR_RULE = 0.60     # minimum confidence to generate a rule
RULE_CONJUNCTION_MODE = "AND"      # "AND" (strict) | "WEIGHTED" (scored)
MAX_CONDITIONS_PER_RULE = 8

# ─── Strategy Optimization (Agent 3) ──────────────────────────────────────
BACKTEST_LOOKBACK_DAYS = 90
WALK_FORWARD_WINDOWS = 5
WALK_FORWARD_TRAIN_PCT = 0.70
MONTE_CARLO_SIMULATIONS = 1000
MONTE_CARLO_CONFIDENCE = 0.95

# Optimization parameter ranges
PARAM_RANGES = {
    "sl_atr_mult":     (0.5, 3.0, 0.25),    # (min, max, step)
    "tp_rr_ratio":     (1.5, 4.0, 0.5),
    "ob_depth_atr":    (0.1, 0.8, 0.1),
    "fvg_threshold":   (0.1, 0.6, 0.05),
    "entry_window_minutes": (5, 30, 5),
    "min_score":       (3, 8, 1),
}

# Performance thresholds
MIN_WIN_RATE = 0.45
MIN_PROFIT_FACTOR = 1.3
MAX_DRAWDOWN_PCT = 0.15
MIN_EXPECTANCY_R = 0.3

# ─── Live Signal Generation ──────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 30
SIGNAL_COOLDOWN_MINUTES = 60
MAX_DAILY_SIGNALS = 10
MIN_SIGNAL_SCORE = 5
