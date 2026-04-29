from datetime import datetime, time, timedelta
import json
import os
import time as t
import uuid
import requests
import threading
import pickle
from collections import deque
import csv
from typing import Any, Dict
from pathlib import Path

# ── Load .env before anything else (local dev + Railway uses env vars directly) ──
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_file = Path(__file__).parent / ".env"
    if _env_file.exists():
        _load_dotenv(dotenv_path=str(_env_file), override=False)
        print(f"[ENV] Loaded .env from {_env_file}")
    else:
        _load_dotenv(override=False)  # search up the directory tree
except ImportError:
    pass  # python-dotenv not installed; rely on env vars being set externally

import pandas as pd

import matplotlib
matplotlib.use('Agg') # MUST be before mpf to prevent tkinter threading crashes
import mplfinance as mpf

try:
    from kiteconnect import KiteConnect
except ImportError:
    print("KiteConnect library missing.")
    KiteConnect = None

try:
    from kiteconnect import KiteTicker
except ImportError:
    KiteTicker = None

# from kotak_neo_adapter import KotakNeoAdapter # REMOVED (Legacy)
from config.kite_auth import get_api_key, get_access_token, is_kite_available
from smc_trading_engine.strategy.entry_model import evaluate_entry
# smc_confluence_engine removed (Phase 4 cleanup — functionality merged into risk_management)
import risk_management as risk_mgr
from manual_trade_handler_v2 import ManualTradeHandlerV2
from option_monitor_module import OptionMonitor
from services.trade_graph_hooks import build_trade_graph, update_trade_graph_trail, close_trade_graph

# Feature 1 & 2: TradingView MCP bridge for visual validation + Pine cross-check
try:
    from services.tv_mcp_bridge import (
        capture_signal_chart,
        capture_trade_close_chart,
        get_pine_cross_validation,
    )
    _TV_BRIDGE_AVAILABLE = True
except ImportError:
    _TV_BRIDGE_AVAILABLE = False
# Phase 5: Modular imports
import engine.config as eng_cfg
from engine.indicators import (
    killzone_confidence, is_killzone, is_expiry_day, expiry_day_risk_adjustment,
    is_discount_zone, is_premium_zone, calculate_atr, index_atr_filter,
    calc_ema, calc_adx, volume_expansion, is_liquid_stock, compute_dynamic_buffer,
)
from engine.swing import (
    run_swing_scan as _swing_run_scan,
    monitor_swing_trades as _swing_monitor,
)
from engine.options import LiveTickStore, BankNiftySignalEngine
from engine.paper_mode import (
    PAPER_MODE, log_paper_trade, log_paper_outcome, paper_prefix,
    paper_daily_summary, paper_mode_banner,
)
from engine.oi_sentiment import (
    update_oi_sentiment, get_oi_scores, get_oi_sentiment,
    get_oi_summary_text, reset_oi_state,
)
from engine.oi_short_covering import (
    scan_short_covering,
    reset_state as reset_oi_sc_state,
)
from engine.smc_zone_tap import (
    scan_zone_taps, format_zone_tap_alert,
    reset_state as reset_zone_tap_state,
)
try:
    from trade_executor_bot import send_signal_with_buttons as _send_trade_buttons
    _TRADE_BUTTONS_AVAILABLE = True
except ImportError:
    _TRADE_BUTTONS_AVAILABLE = False
# ── Second Red Break Strategy (Full-Auto) ──────────────────────
try:
    from strategies.second_red_break.live_scanner import scan_second_red_break, get_scanner as _get_srb_scanner
    from strategies.second_red_break.live_executor import execute_srb_trade as _execute_srb_trade
    from strategies.second_red_break.live_executor import modify_srb_gtt as _modify_srb_gtt
    _SRB_AVAILABLE = True
except ImportError as _srb_e:
    _SRB_AVAILABLE = False
    logging.warning("SRB strategy not available: %s", _srb_e)
from engine.market_state_engine import (
    update_market_state, get_market_state, get_market_state_label,
    reset_market_state,
)
try:
    from engine.displacement_detector import (
        detect_displacement, detect_displacement_sequence,
        record_displacement_event, get_recent_displacement_events,
        DISPLACEMENT_EVENTS,
    )
    _DISPLACEMENT_MODULE_OK = True
except ImportError:
    _DISPLACEMENT_MODULE_OK = False
    DISPLACEMENT_EVENTS = []
    def detect_displacement(candles, **kwargs): return None
    def detect_displacement_sequence(candles, **kwargs): return []
    def record_displacement_event(*a, **kw): pass
    def get_recent_displacement_events(*a, **kw): return []


import logging

# Startup Telegram dedupe: run_live_mode() is re-entered from run_engine_main's outer
# retry loop (same process) after redis_lock_lost, OI watchdog recovery, etc. Without
# this, every re-entry fires RECOVERED + ONLINE again. Redis NX also limits duplicate
# alerts when two engine replicas start within the cooldown window.
_STARTUP_TELEGRAM_SENT_THIS_PROCESS: bool = False
_STARTUP_TELEGRAM_COOLDOWN_KEY = "engine:startup_telegram_cooldown"
_STARTUP_TELEGRAM_COOLDOWN_SEC = 600  # 10 minutes

# =====================================================
# EMA CROSSOVER SCANNER (MERGED)
# =====================================================
EMA_LAST_PROCESSED = {}

def scan_ema_crossover(symbol):
    """
    Scans for EMA 10/20 Crossover on 5-minute timeframe.
    Merged from live_ema_crossover.py
    """
    try:
        # Check Time Window (09:15 - 15:15 IST)
        now = now_ist().time()
        if not (time(9,15) <= now <= time(15,16)):
            return

        # Fetch Data (Reuse existing safe fetch)
        data = fetch_ohlc(symbol, "5minute", lookback=300)
        if not data or len(data) < 205:
            return

        # Check if already processed this candle
        last_candle = data[-1]
        ts = last_candle["date"].replace(tzinfo=None)
        key = f"{symbol}_{ts}"
        
        if key in EMA_LAST_PROCESSED:
            return

        # Calculate Indicators
        closes = [c["close"] for c in data]
        volumes = [c["volume"] for c in data]
        
        ema10 = calc_ema(closes, EMA_FAST)
        ema20 = calc_ema(closes, EMA_SLOW)
        ema200 = calc_ema(closes, EMA_TREND)
        
        # Verify Cross (Index -1 is the just-closed candle)
        cur_close = closes[-1]
        cur_e10 = ema10[-1]
        cur_e20 = ema20[-1]
        prev_e10 = ema10[-2]
        prev_e20 = ema20[-2]
        cur_e200 = ema200[-1]
        
        cross_up = cur_e10 > cur_e20 and prev_e10 <= prev_e20
        cross_down = cur_e10 < cur_e20 and prev_e10 >= prev_e20
        
        if not (cross_up or cross_down):
            EMA_LAST_PROCESSED[key] = True
            return

        # Filters
        atr = calculate_atr(data, 14)
        adx = calc_adx(data, 14)
        avg_vol = sum(volumes[-21:-1]) / 20
        cur_vol = volumes[-1]
        
        is_sideways = adx < ADX_THRESHOLD
        vol_ok = cur_vol > (avg_vol * VOLUME_MULTIPLIER)
        trend_long = cur_close > cur_e200
        trend_short = cur_close < cur_e200
        
        action = None
        if cross_up and trend_long and (not is_sideways or vol_ok):
            action = "LONG"
        elif cross_down and trend_short and (not is_sideways or vol_ok):
            action = "SHORT"
            
        if action:
            # Calculate Levels
            sl_dist = atr * 2.0 # Fixed 2.0 ATR for EMA Setup
            entry_p = cur_close + (SLIPPAGE_INDEX_PTS if "NIFTY" in symbol else 0)
            if action == "SHORT": entry_p = cur_close - SLIPPAGE_INDEX_PTS
            
            sl_p = entry_p - sl_dist if action == "LONG" else entry_p + sl_dist
            tp_p = entry_p + (sl_dist * 2.0) if action == "LONG" else entry_p - (sl_dist * 2.0)
            
            msg = (
                f"🚀 <b>EMA CROSSOVER SIGNAL</b>\n\n"
                f"<b>{symbol}</b>\n"
                f"Direction: <b>{action}</b>\n"
                f"Time: {ts.strftime('%H:%M')}\n\n"
                f"Entry: {entry_p:.2f}\n"
                f"SL: {sl_p:.2f} (2x ATR)\n"
                f"TP: {tp_p:.2f} (1:2)\n\n"
                f"ADX: {adx:.1f} | Vol: {cur_vol}"
            )
            # Signal ID: strategy_timeframe_symbol_timestamp (stronger dedupe across strategies/timeframes)
            _sym = (symbol or "").replace(" ", "_").replace(":", "_").strip("_") or "unknown"
            signal_id = f"ema_5m_{_sym}_{ts.timestamp():.0f}"
            telegram_send_signal(
                msg,
                signal_id=signal_id,
                signal_meta={
                    "signal_kind": "EMA_CROSS",
                    "symbol": symbol,
                    "direction": action,
                    "strategy_name": "EMA_CROSSOVER",
                    "entry": entry_p,
                    "stop_loss": sl_p,
                    "target1": tp_p,
                },
            )
            print(f"🔥 EMA SIGNAL SENT: {symbol} {action}")
            logging.info(f"EMA SIGNAL: {symbol} {action} @ {entry_p}")
            
        EMA_LAST_PROCESSED[key] = True

    except Exception as e:
        print(f"EMA Scan Error {symbol}: {e}")
        logging.error(f"EMA Scan Error {symbol}: {e}")

# =====================================================
# RANKING & FILTERING
# =====================================================
# =====================================================
# LOGGING SETUP — F4.9: Rotating file handler (7-day retention)
# =====================================================
from logging.handlers import TimedRotatingFileHandler

_log_handler = TimedRotatingFileHandler(
    "live_trading_debug.log",
    when="midnight",
    interval=1,
    backupCount=7,        # Keep 7 days of logs
    encoding="utf-8"
)
_log_handler.setLevel(logging.INFO)
_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
logging.getLogger('').addHandler(_log_handler)
logging.getLogger('').setLevel(logging.INFO)
# Add console handler to see logs in CLI too
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

try:
    from zoneinfo import ZoneInfo as _BootZI
except ImportError:
    from backports.zoneinfo import ZoneInfo as _BootZI  # type: ignore
print("ENGINE BOOTED (V4 MODULAR - ZERODHA MODE)", datetime.now(_BootZI("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S"), "IST")

# =====================================================
# SHARED ENGINE STATE FOR LOCAL API
# =====================================================
_ENGINE_STATE_LOCK = threading.Lock()
ACTIVE_TRADES_LOCK = threading.Lock()  # P0-1: Protect ACTIVE_TRADES from concurrent access
ENGINE_STATE: Dict[str, Any] = {
    "engine": "OFF",
    "market": "CLOSED",
    "nifty": None,
    "banknifty": None,
    "signals": [],
    "trades": [],
    "timestamp": None,
}


def update_engine_state(**kwargs: Any) -> None:
    """Thread-safe update of ENGINE_STATE; always refresh timestamp in IST.
    When nifty/banknifty are passed, also writes LTP to Redis for dashboard (standalone mode).
    """
    with _ENGINE_STATE_LOCK:
        ENGINE_STATE.update(kwargs)
        ENGINE_STATE["timestamp"] = now_ist().isoformat()
    if "nifty" in kwargs or "banknifty" in kwargs:
        try:
            import engine_runtime
            n = kwargs.get("nifty")
            b = kwargs.get("banknifty")
            if n is not None or b is not None:
                engine_runtime.set_index_ltp(
                    float(n) if isinstance(n, (int, float)) else None,
                    float(b) if isinstance(b, (int, float)) else None,
                )
        except Exception:
            pass


def get_engine_state_snapshot() -> Dict[str, Any]:
    """Return a shallow copy of ENGINE_STATE for API consumption."""
    with _ENGINE_STATE_LOCK:
        return dict(ENGINE_STATE)


def _publish_redis_snapshot() -> None:
    """Write the current engine state to Redis for dashboard (standalone mode).
    Safe to call at any point in the loop — never raises."""
    try:
        import engine_runtime
        with ACTIVE_TRADES_LOCK:
            _snap = {
                "active_trades": [_serialize_trade(t) for t in ACTIVE_TRADES],
                "signals_today": DAILY_SIGNAL_COUNT,
                "daily_pnl_r": DAILY_PNL_R,
                "traded_today": list(TRADED_TODAY),
                "consecutive_losses": CONSECUTIVE_LOSSES,
                "circuit_breaker_active": CIRCUIT_BREAKER_ACTIVE,
                "market_regime": str(MARKET_REGIME),
                "engine_mode": ENGINE_MODE,
                "index_ltp": {},
                "timestamp": now_ist().isoformat(),
            }
        _n = ENGINE_STATE.get("nifty")
        _b = ENGINE_STATE.get("banknifty")
        if _n is not None:
            _snap["index_ltp"]["NIFTY 50"] = float(_n)
        if _b is not None:
            _snap["index_ltp"]["NIFTY BANK"] = float(_b)
        engine_runtime.write_engine_snapshot(_snap)
    except Exception as _e:
        logging.debug("_publish_redis_snapshot: %s", _e)


# =====================================================
# PART 1 — CONFIG & BOOT (MODULAR V4)
# =====================================================

ENGINE_VERSION = "v4.2.1"
ENGINE_MODE = "AGGRESSIVE"

if ENGINE_MODE == "CONSERVATIVE":
    ACTIVE_STRATEGIES = {"SETUP_A": False, "SETUP_B": False, "SETUP_C": False, "SETUP_D": False, "HIERARCHICAL": True}
elif ENGINE_MODE == "BALANCED":
    ACTIVE_STRATEGIES = {"SETUP_A": False, "SETUP_B": False, "SETUP_C": False, "SETUP_D": False, "HIERARCHICAL": True}
elif ENGINE_MODE == "AGGRESSIVE":
    ACTIVE_STRATEGIES = {"SETUP_A": True, "SETUP_B": False, "SETUP_C": False, "SETUP_D": True, "SETUP_E": True, "HIERARCHICAL": True}
    # F1.5: SETUP_D re-enabled for INDEX instruments only (Phase 8 upgrade — BOS+FVG pipeline)
    # SETUP_E: Enhanced OB detection — two-tier CHoCH, wick zones, reaction entry, OB-only (FVG optional)
    # SETUP_D is gated inside scan_symbol to indices: NIFTY/BANKNIFTY. Negative expectancy on stocks still holds.
    # SWEEP-B: SETUP_B disabled — grid search showed -0.31R expectancy, worst setup by far

# W12: SLIPPAGE MODEL — applied in backtesting + signal validation
SLIPPAGE_INDEX_PTS = 3       # Points slippage for NIFTY/BANKNIFTY
SLIPPAGE_STOCK_PCT = 0.05    # 0.05% for liquid stocks
BROKERAGE_PER_ORDER = 20     # Zerodha flat fee per order

# EMA CROSSOVER PARAMETERS
EMA_FAST = 10
EMA_SLOW = 20
EMA_TREND = 200
ADX_PERIOD = 14
ADX_THRESHOLD = 20
VOLUME_MULTIPLIER = 1.2

# ================================
# SAFE GLOBAL DEFAULTS (MUST)
# ================================
STOCK_UNIVERSE = []
# Ensure you set these in your environment variables for security!
# For now, we keep defaults but allow env overrides
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SMC_PRO_CHAT_ID = os.getenv("SMC_PRO_CHAT_ID", "")

DEBUG_MODE = True

# =====================================================
# KITE CONNECTION
# =====================================================

BACKTEST_MODE = os.environ.get("BACKTEST_MODE", "") == "1"

# =====================================================
# TELEGRAM CORE (Early Definition for Init)
# =====================================================
def telegram_send(message: str, chat_id=None, signal_id=None, _max_retries: int = 2) -> bool:
    """
    Send message to Telegram with deduplication, retry on failure, and proper logging.

    Dedup: if signal_id is set, check Redis before sending. If Redis is unavailable,
    the check is skipped (fail-open) so signals are NOT silently dropped.

    Returns True if the message was delivered to Telegram; False if skipped, failed, or misconfigured.
    """
    if not BOT_TOKEN:
        logging.error("[Telegram] BOT_TOKEN not set — cannot send message")
        return False
    if not (chat_id or CHAT_ID):
        logging.error("[Telegram] CHAT_ID not set — cannot send message")
        return False

    # Deduplication via Redis (fail-open: skip check if Redis is down)
    if signal_id:
        try:
            import engine_runtime
            if not engine_runtime.should_send_signal(signal_id):
                logging.debug("[Telegram] Signal dedupe skip: %s", signal_id)
                return False
        except Exception as e:
            logging.warning("[Telegram] Signal dedupe check failed (sending anyway): %s", e)

    target = chat_id or CHAT_ID
    message = paper_prefix(message)

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target, "text": message, "parse_mode": "HTML"}

    last_exc = None
    for attempt in range(_max_retries):
        try:
            resp = requests.post(url, data=payload, timeout=8)
            if resp.ok:
                logging.info("[Telegram] Message sent (chat=%s signal=%s)", target, signal_id)
                if signal_id:
                    try:
                        import engine_runtime
                        engine_runtime.mark_signal_sent(signal_id)
                    except Exception:
                        pass
                return True
            else:
                logging.warning(
                    "[Telegram] API returned %s: %s (attempt %d/%d)",
                    resp.status_code, resp.text[:200], attempt + 1, _max_retries,
                )
                last_exc = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
        except requests.exceptions.Timeout:
            logging.warning("[Telegram] Timeout on attempt %d/%d", attempt + 1, _max_retries)
            last_exc = Exception("Timeout")
        except Exception as exc:
            logging.warning("[Telegram] Error on attempt %d/%d: %s", attempt + 1, _max_retries, exc)
            last_exc = exc
        if attempt < _max_retries - 1:
            t.sleep(2 ** attempt)  # Exponential backoff between retries

    logging.error("[Telegram] Failed to send after %d attempts. Last error: %s", _max_retries, last_exc)
    return False


def telegram_send_signal(
    message: str,
    signal_id: str = None,
    chat_id=None,
    signal_meta: dict = None,
):
    """
    Send a TRADING SIGNAL to Telegram with 3 retries + critical log on all-fail.
    Use this for all actual trade signals — never for status/diagnostic messages.
    This ensures signals are NEVER silently lost.

    On successful delivery, appends to ai_learning signal_log (for dashboard/journal/analytics).
    """
    if not BOT_TOKEN or not (chat_id or CHAT_ID):
        logging.critical(
            "[SIGNAL LOST] Telegram credentials missing — signal cannot be delivered. "
            "Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID. Signal: %s",
            message[:200],
        )
        return

    try:
        sent = telegram_send(message, chat_id=chat_id, signal_id=signal_id, _max_retries=3)
        if sent:
            try:
                from utils.telegram_signal_log import persist_telegram_signal

                persist_telegram_signal(message, signal_id, signal_meta)
            except Exception as _log_exc:
                logging.warning("[Telegram] signal_log persist failed: %s", _log_exc)
    except Exception as exc:
        logging.critical(
            "[SIGNAL LOST] telegram_send raised after 3 retries (%s). "
            "Signal text (first 300 chars): %s",
            exc, message[:300],
        )

# Current token in use (for refresh comparison). Set after successful set_access_token.
_current_kite_token = None

if BACKTEST_MODE:
    print("[BACKTEST] Skipping Kite/Telegram init...")
    kite = None
    manual_handler = None
    option_monitor = None
    bn_signal_engine = None
else:
    try:
        api_key = get_api_key()
        access_token = get_access_token()
        if not api_key or not access_token:
            raise Exception("Kite credentials missing. Set KITE_API_KEY + KITE_ACCESS_TOKEN env, or use access_token.txt + kite_credentials.")
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        # Validate session immediately — fail fast on wrong/expired token
        try:
            profile = kite.profile()
            _current_kite_token = access_token
            mask_key = (api_key[-4:] if len(api_key) >= 4 else "****")
            mask_tok = (access_token[:6] + "..." + access_token[-4:] if len(access_token) >= 10 else "****")
            print(f"Zerodha Kite Connected (api_key=...{mask_key} token={mask_tok})")
            logging.info("Kite session validated: user=%s", profile.get("user_name", "?"))
        except Exception as sess_e:
            err = str(sess_e).lower()
            _token_err = any(k in err for k in ("api_key", "access_token", "invalid", "token", "forbidden", "unauthori"))
            if _token_err:
                print("Incorrect api_key or access_token — token may be expired or wrong.")
                print("Run RUN_ENGINE_ON_RAILWAY.bat (or zerodha_login.py), then restart the engine.")
                logging.critical("Kite session invalid: %s", sess_e)
                # Send Telegram alert (best-effort; BOT_TOKEN/CHAT_ID may be set even if kite isn't)
                try:
                    import requests as _req
                    _bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
                    _cid = os.getenv("TELEGRAM_CHAT_ID", "")
                    if _bot and _cid:
                        _req.post(
                            f"https://api.telegram.org/bot{_bot}/sendMessage",
                            data={
                                "chat_id": _cid,
                                "text": (
                                    "❌ <b>ENGINE STOPPED — TOKEN INVALID</b>\n\n"
                                    "Kite access_token is expired or incorrect.\n"
                                    "👉 Run <code>RUN_ENGINE_ON_RAILWAY.bat</code> to refresh the token, "
                                    "then redeploy the engine.\n\n"
                                    f"Error: {str(sess_e)[:200]}"
                                ),
                                "parse_mode": "HTML",
                            },
                            timeout=8,
                        )
                except Exception:
                    pass
            raise sess_e

        # Initialize Manual Trade Handler
        manual_handler = ManualTradeHandlerV2(kite)

        # Initialize Option Monitor (Merged)
        option_monitor = OptionMonitor(kite)
        try:
            option_monitor.initialize()
        except Exception as om_e:
            print(f"Option Monitor Init Failed: {om_e}")

        # Initialize Bank Nifty Signal Engine
        bn_signal_engine = BankNiftySignalEngine(kite, telegram_fn=telegram_send)
        try:
            bn_signal_engine.initialize()
        except Exception as bn_e:
            print(f"BN Signal Engine Init Failed: {bn_e}")

    except Exception as e:
        print(f"Connection Failed: {e}")
        kite = None
        _current_kite_token = None
        manual_handler = None
        option_monitor = None
        bn_signal_engine = None
        logging.warning("Module-level Kite init failed — _reinit_kite() will retry with latest token")


# =====================================================
# GLOBAL STATE (SAFE)
# =====================================================
HTF_CACHE = {}
STRUCTURE_STATE = {}
HTF_CACHE_TIME = {}
TOKEN_CACHE = {}
LAST_HOURLY_SUMMARY = None
TRADING_MODE = "AGGRESSIVE" # Options: "CONSERVATIVE", "AGGRESSIVE"

ACTIVE_SETUPS_FILE = "active_setups.json"
POSITION_CACHE_FILE = "position_cache.json"
MORNING_WATCHLIST_FLAG = "morning_watchlist.flag"

# =====================================================
# SYMBOL GROUPS
# =====================================================

# =====================================================
# TIME SYNC HELPER
# =====================================================
def wait_for_next_minute():
    """Sleeps until the start of the next minute (plus 1s buffer).
    Uses safe_sleep to keep the watchdog alive."""
    now = now_ist()
    sleep_seconds = 60 - now.second + 1
    if sleep_seconds < 0:
        sleep_seconds = 1
    print(f"[INFO] Waiting {sleep_seconds}s for next candle close...")
    try:
        import engine_runtime
        engine_runtime.safe_sleep(sleep_seconds)
    except Exception:
        t.sleep(sleep_seconds)

INDEX_SYMBOLS = [
    "NSE:NIFTY 50",
    "NSE:NIFTY BANK"
]

# STATE MANAGEMENT
STRUCTURE_STATE = {}
ZONE_STATE = {} # Universal Setup State: { "symbol": { "LONG": zone, "SHORT": zone, "state": "TAPPED" } }

# TRADE MANAGEMENT
ACTIVE_TRADES = [] # List of running trades: {symbol, direction, entry, sl, target, id}
DAILY_LOG = []     # List of completed trades for EOD report
EOD_SENT = False   # Flag to prevent multiple EOD reports
# SWING_SCAN_SENT — moved to engine.config (Phase 5)
# ACTIVE_SWING_TRADES — moved to engine.config (Phase 5)

# W2: CIRCUIT BREAKER — halt trading after max daily loss
DAILY_PNL_R = 0.0           # Running daily P&L in R-multiples
CONSECUTIVE_LOSSES = 0      # Track consecutive losses for cooldown
MAX_DAILY_LOSS_R = -3.0     # Halt all new entries if daily PnL <= this
COOLDOWN_AFTER_STREAK = 3   # After N consecutive losses, pause entries
CIRCUIT_BREAKER_ACTIVE = False
COOLDOWN_UNTIL = None  # F1.4: datetime when cooldown expires (None = no cooldown)

SETUP_D_STATE = {} # { "symbol": { "bias": "LONG", "ob": [], "fvg": [], "stage": "FORMED", "time": ... } }
SETUP_D_STRUCTURE_TRACE = {}  # Phase 8: Decision audit log { symbol: [trace_entries, ...] } — last 50 per symbol

SETUP_E_STATE = {} # { "symbol": { "bias": "LONG"/"SHORT", "ob": (lo,hi), "fvg": (lo,hi)|None, "stage": "BOS_WAIT"/"WAIT"/"TAPPED", "time": ... } }

# Phase 6: Early Smart Money Activity state
# { symbol: { type, direction, confidence, displacement, timestamp } }
EARLY_WARNING_STATE: dict = {}
MANUAL_ORDER_CACHE = set() # Store processed order_ids to prevent re-adding
TRADED_TODAY = set() # P0 FIX: Track symbols traded today to prevent re-entries like AUROPHARMA
MAX_DAILY_SIGNALS = 5  # F1.6: Hard cap on total signals per day
DAILY_SIGNAL_COUNT = 0  # Global accepted signal counter (all setups)
ENGINE_LAST_LOOP_AT = None  # Heartbeat for dashboard liveness

# Tier 3: Adaptive intelligence state (self-tuning + explainability)
ADAPTIVE_PERF_CACHE = {"updated": None, "stats": {}, "window_days": 20}
LIVE_SETUP_STATS = {}  # {setup: {"trades":int,"wins":int,"sum_r":float}}
ADAPTIVE_BLOCK_LOG = deque(maxlen=200)  # [{ts,symbol,setup,direction,reason}]
ADAPTIVE_SCORE_LOG = deque(maxlen=200)  # [{ts,symbol,setup,direction,ai_score}]

# F4.3: Multi-day drawdown breaker — halt 48hrs after -10R in rolling 5 days
MULTI_DAY_DD_LIMIT = -10.0   # R-multiple threshold over rolling window
MULTI_DAY_DD_WINDOW = 5      # Rolling day count
MULTI_DAY_HALT_HOURS = 48    # How long to halt after triggering
MULTI_DAY_HALT_UNTIL = None  # datetime when multi-day halt expires (None = active)
# =====================================================
# TELEGRAM CORE
# =====================================================

def telegram_send_image(
    image_path: str,
    caption: str,
    signal_id: str = None,
    signal_meta: dict = None,
):
    """Send signal chart image with 3 retries; fall back to text-only on persistent failure."""
    if not BOT_TOKEN or not CHAT_ID:
        logging.critical("[SIGNAL LOST] Telegram credentials missing — image signal not delivered. Caption: %s", caption[:200])
        return
    meta_base = dict(signal_meta or {})
    last_exc = None
    for attempt in range(3):
        try:
            with open(image_path, "rb") as img:
                resp = requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                    files={"photo": img},
                    timeout=15,
                )
            if resp.ok:
                try:
                    from utils.telegram_signal_log import persist_telegram_signal

                    m = {**meta_base, "delivery_format": "photo"}
                    persist_telegram_signal(caption, signal_id, m)
                except Exception as _log_exc:
                    logging.warning("[Telegram] signal_log persist (photo) failed: %s", _log_exc)
                return
            last_exc = Exception(f"HTTP {resp.status_code}: {resp.text[:100]}")
            logging.warning("[Telegram] sendPhoto attempt %d/%d failed: %s", attempt + 1, 3, last_exc)
        except Exception as exc:
            last_exc = exc
            logging.warning("[Telegram] sendPhoto attempt %d/%d error: %s", attempt + 1, 3, exc)
        if attempt < 2:
            t.sleep(2 ** attempt)
    # All image retries failed — send text-only fallback so signal is NOT lost
    logging.critical("[SIGNAL DEGRADED] Image send failed after 3 attempts (%s). Sending text-only fallback.", last_exc)
    fb_meta = {**meta_base, "delivery_format": "text_fallback_after_photo_fail"}
    telegram_send_signal(caption, signal_id=signal_id, signal_meta=fb_meta)


# =====================================================
# JSON UTILITIES
# =====================================================

from utils.state_db import db

# =====================================================
# F2: SMC DETECTOR MODULE (Phase 2 rebuild)
# =====================================================
import smc_detectors as smc

# =====================================================
# F1.2: ACTIVE_TRADES PERSISTENCE (SQLite)
# =====================================================
def _serialize_value(v):
    """Recursively convert any value to JSON-safe form."""
    import numpy as np
    if isinstance(v, pd.DataFrame):
        return None                                      # skip dataframes
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.isoformat()
    if hasattr(v, 'date') and hasattr(v, 'year') and not isinstance(v, datetime):
        # datetime.date (not datetime subclass)
        return v.isoformat()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, dict):
        return {kk: _serialize_value(vv) for kk, vv in v.items()}
    if isinstance(v, (list, tuple)):
        return [_serialize_value(i) for i in v]
    return v


def _serialize_trade(trade: dict) -> dict:
    """Convert trade dict to JSON-safe dict (handles nested datetime/numpy)."""
    return {k: _serialize_value(v) for k, v in trade.items() if not isinstance(v, pd.DataFrame)}


def _deserialize_trade(data: dict) -> dict:
    """Restore trade dict from JSON (ISO string → datetime for known keys)"""
    dt_keys = {"start_time", "time", "entry_time"}
    for k in dt_keys:
        if k in data and isinstance(data[k], str):
            try:
                data[k] = datetime.fromisoformat(data[k])
            except (ValueError, TypeError):
                pass
    return data

def persist_active_trades(snapshot=None):
    """Save ACTIVE_TRADES list to SQLite for crash recovery.
    If snapshot is provided (already serialized under lock), skip re-locking."""
    try:
        if snapshot is None:
            with ACTIVE_TRADES_LOCK:
                serialized = [_serialize_trade(t) for t in ACTIVE_TRADES]
        else:
            serialized = snapshot
        db.set_value("engine_state", "active_trades", serialized)
    except Exception as e:
        logging.error(f"Failed to persist ACTIVE_TRADES: {e}")

def load_active_trades():
    """Restore ACTIVE_TRADES from SQLite on startup"""
    global ACTIVE_TRADES
    try:
        data = db.get_value("engine_state", "active_trades", default=[])
        if data:
            with ACTIVE_TRADES_LOCK:
                ACTIVE_TRADES = [_deserialize_trade(t) for t in data]
            logging.info(f"💾 Restored {len(ACTIVE_TRADES)} active trades from SQLite")
            # Telegram RECOVERED is sent once per process via _send_startup_telegram_bundle()
            # so run_live_mode() re-entries (Railway retry loop) do not spam the channel.
    except Exception as e:
        logging.error(f"Failed to load ACTIVE_TRADES: {e}")
        with ACTIVE_TRADES_LOCK:
            ACTIVE_TRADES = []


def _send_startup_telegram_bundle() -> None:
    """
    Send RECOVERED (if trades) + ONLINE once per process, with optional Redis cooldown
    so two replicas or rapid restarts do not duplicate alerts within 10 minutes.
    """
    global _STARTUP_TELEGRAM_SENT_THIS_PROCESS
    if _STARTUP_TELEGRAM_SENT_THIS_PROCESS:
        return

    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            import redis as _redis_mod
            _r = _redis_mod.from_url(redis_url, decode_responses=True)
            acquired = _r.set(
                _STARTUP_TELEGRAM_COOLDOWN_KEY,
                f"{os.getpid()}:{t.time():.0f}",
                nx=True,
                ex=_STARTUP_TELEGRAM_COOLDOWN_SEC,
            )
            if not acquired:
                logging.info(
                    "Startup Telegram suppressed (cooldown %ss) — another instance or recent startup notify",
                    _STARTUP_TELEGRAM_COOLDOWN_SEC,
                )
                _STARTUP_TELEGRAM_SENT_THIS_PROCESS = True
                return
        except Exception as _e:
            logging.debug("Startup Telegram Redis cooldown skipped: %s", _e)

    _STARTUP_TELEGRAM_SENT_THIS_PROCESS = True

    with ACTIVE_TRADES_LOCK:
        _trades_copy = list(ACTIVE_TRADES)
    if _trades_copy:
        symbols = [x.get("symbol", "?") for x in _trades_copy]
        telegram_send(
            f"💾 <b>RECOVERED {len(_trades_copy)} ACTIVE TRADES</b>\n"
            f"{', '.join(symbols)}\n"
            f"<i>Resuming monitoring after restart.</i>"
        )

    active_labels = [k for k, v in ACTIVE_STRATEGIES.items() if v]
    active_text = ", ".join(active_labels) if active_labels else "None"
    telegram_send(
        f"🚀 <b>V4 INSTITUTIONAL ENGINE :: ONLINE</b>\n"
        f"💎 Mode: {ENGINE_MODE}\n"
        f"🧩 Active Setups: {active_text}\n"
        f"⚡ EMA 10/20 Crossover: <b>ACTIVE</b>\n"
        f"📊 Bank Nifty Options Signal: <b>ACTIVE</b>"
    )

def load_json(path: str) -> dict:
    return db.get_value("legacy_json", path, default={})

def save_json(path: str, data: dict):
    db.set_value("legacy_json", path, data)

# =====================================================
# DEDUPLICATION ENGINE
# =====================================================

def already_alerted_today(key: str) -> bool:
    today = now_ist().date().isoformat()
    data = load_json(ACTIVE_SETUPS_FILE)

    if data.get(key) == today:
        return True

    data[key] = today
    save_json(ACTIVE_SETUPS_FILE, data)
    return False

def get_daily_trade_count(symbol: str) -> int:
    """
    Counts how many times a symbol has been alerted/traded today.
    Usage: Limit Stocks to 1 trade per day.
    """
    today = now_ist().date().isoformat()
    data = load_json(ACTIVE_SETUPS_FILE)
    
    count = 0
    clean_sym = clean_symbol(symbol)
    
    for key, date in data.items():
        if date == today and clean_sym in key:
            count += 1
            
    return count


# =====================================================
# TOKEN CACHE (500 STOCK SAFE)
# =====================================================

INSTRUMENT_TOKEN_MAP: dict[str, int] = {}
_INSTRUMENTS_LOADED_AT: datetime | None = None

def get_token(symbol: str):
    if symbol in TOKEN_CACHE:
        return TOKEN_CACHE[symbol]

    try:
        # Prefer instrument cache (fast, avoids ltp() dependency during recovery)
        tok = INSTRUMENT_TOKEN_MAP.get(symbol)
        if tok:
            TOKEN_CACHE[symbol] = tok
            return tok

        result = _kite_call(kite.ltp, symbol)
        token = list(result.values())[0]["instrument_token"]
        TOKEN_CACHE[symbol] = token
        return token
    except concurrent.futures.TimeoutError:
        logging.warning("[get_token] Kite ltp() timed out for %s", symbol)
        return None
    except Exception:
        return None


# =====================================================
# MARKET HOURS
# =====================================================

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

_IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Current time in IST — use this everywhere instead of now_ist()
    so Railway (UTC) and local (IST) both work correctly.
    Returns naive datetime (no tzinfo) to avoid tz-aware vs tz-naive comparison errors."""
    return datetime.now(_IST).replace(tzinfo=None)


def is_market_open() -> bool:
    """Return True only during Mon–Fri 09:00–16:30 IST (for general market state checks)."""
    n = now_ist()
    if n.weekday() >= 5:
        return False
    t_now = n.time()
    return time(9, 0) <= t_now <= time(16, 30)


def is_signal_window() -> bool:
    """
    Return True only when the signal-firing system should be active.
    Signals are enabled Mon–Fri 09:00–16:10 IST.
    After 16:10 the engine loop keeps running (website stays live) but no new
    trade signals are generated or sent to Telegram.
    """
    n = now_ist()
    if n.weekday() >= 5:
        return False
    t_now = n.time()
    return time(9, 0) <= t_now <= time(16, 10)

# =====================================================
# SYMBOL UTILS
# =====================================================

def clean_symbol(symbol: str) -> str:
    return symbol.replace("NSE:", "").replace(" ", "")

def option_strike(price, direction, step=100):
    """
    Returns ATM option strike suggestion
    """
    atm = round(price / step) * step

    if direction == "LONG":
        return f"{atm} CE"
    else:
        return f"{atm} PE"

# =====================================================
# STOCK UNIVERSE LOADER
# =====================================================
INDEX_ONLY = True  # Only NIFTY 50 and BANKNIFTY — stock signals disabled

# Setups that are historically disabled — excluded from stock scanning
# Phase 1: SETUP-D removed from disabled set — re-enabled for index instruments only (gated in scan_symbol)
_DISABLED_SETUPS = {"B", "SETUP-D-V2"}

def load_stock_universe(kite_client=None) -> list:
    """
    Cloud-safe stock universe loader.

    Order:
    1) Optional curated JSON in repo root (stock_universe_fno.json / stock_universe_500.json)
    2) Fallback: derive NSE equity universe from Kite instruments (no local files)

    NOTE: This does not change strategy logic — it only ensures the universe exists on Railway.
    """
    if INDEX_ONLY:
        logging.info("INDEX_ONLY mode — stock universe disabled")
        return []
    try:
        # Prefer curated files if present (supports legacy workflows)
        base = Path(__file__).resolve().parent
        fno_path = base / "stock_universe_fno.json"
        legacy_path = base / "stock_universe_500.json"
        path = fno_path if fno_path.exists() else legacy_path
        if path.exists():
            symbols = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(symbols, list):
                raise ValueError("Stock universe JSON is not a list")
            symbols = [s for s in symbols if not any(x in s for x in ("-SG", "-GB", "-GS", "-YI", "-YL", "-N", "-BE", "-BZ", "-ST", "-SM"))]
            logging.info("Stock universe loaded from %s (%d symbols)", path.name, len(symbols))
            return symbols

        # Fallback: build from Kite instruments (NSE EQ)
        if kite_client is None:
            logging.warning("Stock universe JSON missing and kite client not available — universe empty")
            return []
        instruments = _kite_call(kite_client.instruments, "NSE", timeout=_KITE_TIMEOUT_SEC)
        out: list[str] = []
        for ins in instruments or []:
            try:
                if ins.get("exchange") != "NSE":
                    continue
                if ins.get("segment") not in ("NSE", "NSE_EQ", None):
                    # Kite sometimes varies; we accept NSE-ish segments
                    continue
                if (ins.get("instrument_type") or "").upper() != "EQ":
                    continue
                tsym = ins.get("tradingsymbol") or ""
                if not tsym:
                    continue
                out.append(f"NSE:{tsym}")
            except Exception:
                continue
        # Keep a stable order to reduce churn
        out = sorted(set(out))
        logging.info("Stock universe derived from Kite instruments (%d symbols)", len(out))
        return out
    except Exception as e:
        logging.warning("Stock universe load failed: %s", e)
        return []


STOCK_UNIVERSE: list[str] = []


def get_stock_universe() -> list:
    global STOCK_UNIVERSE
    if STOCK_UNIVERSE:
        return STOCK_UNIVERSE
    # Lazy-load when Kite is available (Railway-safe)
    try:
        if not INDEX_ONLY and kite is not None:
            STOCK_UNIVERSE = load_stock_universe(kite)
    except Exception:
        pass
    return STOCK_UNIVERSE

if DEBUG_MODE:
    print(f"Loaded STOCK_UNIVERSE: {len(STOCK_UNIVERSE)} symbols")

# =====================================================
# BOOT MESSAGE
# =====================================================

telegram_send("🚀 <b>SMC MULTI-TF ENGINE STARTED</b>")
# =====================================================
# PART 2 — MARKET DATA & INDICATORS
# =====================================================

# -----------------------------
# FETCH OHLC DATA
# -----------------------------
# =====================================================
# FETCH OHLC DATA (RATE-LIMIT SAFE)
# =====================================================

LAST_API_CALL = 0
import threading
import concurrent.futures
OHLC_CACHE = {} 
OHLC_CACHE_LOCK = threading.Lock()
PREFETCHER_RUNNING = False
API_THROTTLE_LOCK = threading.Lock()


# ─── KITE TIMEOUT WRAPPER ────────────────────────────────────────────────────
# Prevents silent engine freeze when historical_data() or ltp() hangs.
# Railway only restarts on exit, NOT on a hung thread, so we force a timeout.
_KITE_TIMEOUT_SEC = 10   # 10s max per Kite call; adjust if needed

def _kite_call(fn, *args, timeout=_KITE_TIMEOUT_SEC, **kwargs):
    """
    Execute a Kite API call in a thread with a hard timeout.
    Raises concurrent.futures.TimeoutError if the call hangs beyond `timeout` seconds.
    This ensures the main loop can never be frozen by a hung network call.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout)
# ─────────────────────────────────────────────────────────────────────────────


def _respect_api_throttle(min_interval_sec: float = 0.35):
    """
    Global API pacing guard shared across threads.
    Ensures all Kite historical calls respect minimum spacing.
    Sleep happens OUTSIDE the lock so other threads are not blocked waiting.
    """
    global LAST_API_CALL
    with API_THROTTLE_LOCK:
        now = t.time()
        wait_for = min_interval_sec - (now - LAST_API_CALL)
        LAST_API_CALL = t.time() + max(wait_for, 0)  # Reserve the slot immediately
    if wait_for > 0:
        t.sleep(wait_for)

def update_cache(symbol, interval, lookback):
    token = get_token(symbol)
    if not token: return False
        
    for attempt in range(3):
        try:
            _respect_api_throttle()
            data = _kite_call(
                kite.historical_data,
                token,
                now_ist() - timedelta(days=15),
                now_ist(),
                interval,
                timeout=_KITE_TIMEOUT_SEC,
            )
            if data:
                with OHLC_CACHE_LOCK:
                    OHLC_CACHE[(symbol, interval)] = {
                        "data": data[-lookback:],
                        "updated_at": t.time(),
                        "trading_date": now_ist().strftime("%Y-%m-%d"),
                    }
                return True
        except concurrent.futures.TimeoutError:
            logging.warning("[update_cache] historical_data timed out: %s %s", symbol, interval)
            if attempt < 2:
                t.sleep(2 ** attempt)
        except Exception:
            if attempt < 2:
                t.sleep(2 ** attempt)
    return False

def data_prefetch_worker():
    global PREFETCHER_RUNNING
    PREFETCHER_RUNNING = True
    intervals = ["5minute", "15minute", "60minute", "day"]
    
    while True:
        try:
            # 1. Update Indices first (highest priority)
            for sym in INDEX_SYMBOLS:
                for inv in intervals:
                    update_cache(sym, inv, 200)
                    
            # 2. Update Universe (skipped in INDEX_ONLY mode)
            if not INDEX_ONLY:
                universe = get_stock_universe()
                for sym in universe:
                    for inv in intervals:
                        update_cache(sym, inv, 200)
            # Prevent tight-loop API hammering between full cycles.
            t.sleep(2)
                    
        except Exception as e:
            logging.error(f"Prefetcher error: {e}")
            t.sleep(5)

def start_data_prefetcher():
    global PREFETCHER_RUNNING
    if not PREFETCHER_RUNNING:
        thread = threading.Thread(target=data_prefetch_worker, daemon=True)
        thread.start()
        logging.info("Started Background Data Prefetcher")

def fetch_ltp(symbol: str):
    """
    Lightweight LTP fetch (Issue 5)
    """
    try:
        quote = _kite_call(kite.ltp, symbol)
        if symbol in quote:
            price = quote[symbol]["last_price"]
            try:
                if symbol.upper() == "NSE:NIFTY 50":
                    update_engine_state(nifty=price)
                    print("API update:", get_engine_state_snapshot())
                elif symbol.upper() == "NSE:NIFTY BANK":
                    update_engine_state(banknifty=price)
                    print("API update:", get_engine_state_snapshot())
            except Exception:
                pass
            return price
    except concurrent.futures.TimeoutError:
        logging.warning("[fetch_ltp] Kite ltp() timed out for %s", symbol)
        return None
    except Exception:
        return None

def fetch_ohlc(symbol: str, interval: str, lookback: int = 200):
    # Fast path: Serve from cache if available and fresh (< 15 mins)
    with OHLC_CACHE_LOCK:
        cached = OHLC_CACHE.get((symbol, interval))
        
    if cached and (t.time() - cached["updated_at"]) < 900:
        # Reject cache from a different trading day
        if cached.get("trading_date") != now_ist().strftime("%Y-%m-%d"):
            pass  # stale day — fall through to fresh fetch
        else:
            return cached["data"][-lookback:]

    token = get_token(symbol)
    if not token:
        return []

    for attempt in range(3):
        try:
            _respect_api_throttle()
            data = _kite_call(
                kite.historical_data,
                token,
                now_ist() - timedelta(days=15),
                now_ist(),
                interval,
                timeout=_KITE_TIMEOUT_SEC,
            )
            with OHLC_CACHE_LOCK:
                OHLC_CACHE[(symbol, interval)] = {
                    "data": data[-lookback:],
                    "updated_at": t.time(),
                    "trading_date": now_ist().strftime("%Y-%m-%d"),
                }
            return data[-lookback:]

        except concurrent.futures.TimeoutError:
            logging.warning("[fetch_ohlc] historical_data timed out: %s %s (attempt %d)", symbol, interval, attempt + 1)
            if attempt < 2:
                t.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s
            else:
                return []
        except Exception as e:
            if attempt < 2:
                t.sleep(2 ** attempt)
            else:
                if DEBUG_MODE:
                    print(f"OHLC error {symbol} {interval} (Max Retries):", e)
                return []



# -----------------------------
# CORE HELPERS — delegated to engine.indicators (Phase 5)
# killzone_confidence, is_killzone, is_expiry_day, expiry_day_risk_adjustment,
# is_discount_zone, is_premium_zone, calculate_atr, index_atr_filter,
# calc_ema, calc_adx, volume_expansion, is_liquid_stock, compute_dynamic_buffer
# -----------------------------

# -----------------------------
# HTF BIAS (BOS)
# -----------------------------

def detect_htf_bias(candles):
    """F2.3: Swing-based BOS detection (delegates to smc_detectors)"""
    return smc.detect_htf_bias(candles)


# ==============================
# LTF STRUCTURE (CHOCH / BOS)
# ==============================


# -----------------------------
# RANGE DETECTION (SETUP-B)
# -----------------------------

def is_range(candles):
    if len(candles) < 25:
        return False

    atr = calculate_atr(candles)
    highs = [c["high"] for c in candles[-20:]]
    lows = [c["low"] for c in candles[-20:]]

    return (max(highs) - min(lows)) < (2 * atr)


# -----------------------------
# ORDER BLOCK DETECTION
# -----------------------------

def detect_order_block(candles, direction):
    """F2.2: 30-bar OB scan with body-zone and mitigation check (delegates to smc_detectors)"""
    return smc.detect_order_block(candles, direction)


# -----------------------------
# FAIR VALUE GAP
# -----------------------------

def detect_fvg(candles, direction: str):
    """F2.1: Proper 3-candle FVG with displacement filter (delegates to smc_detectors)"""
    return smc.detect_fvg(candles, direction)
def is_price_inside_fvg(price, fvg):
    if not fvg:
        return False
    low, high = fvg
    return low <= price <= high
    
def confirmation_candle(candles, direction):
    """
    Confirmation = rejection candle near OB
    LONG  : long lower wick + close > open
    SHORT : long upper wick + close < open
    """
    if len(candles) < 2:
        return False

    c = candles[-1]
    body = abs(c["close"] - c["open"])
    wick_low = min(c["open"], c["close"]) - c["low"]
    wick_high = c["high"] - max(c["open"], c["close"])

    if direction == "LONG":
        return c["close"] > c["open"] and wick_low > body * 1.2

    if direction == "SHORT":
        return c["close"] < c["open"] and wick_high > body * 1.2

    return False
    
def fvg_rejection(candles, direction):
    """
    Strong rejection from FVG
    Uses confirmation candle logic
    """
    return confirmation_candle(candles, direction)
    
def invalidate_structure(price, ob, direction):
    """
    Invalidates structure if price closes continuously beyond the order block.
    """
    if not ob or len(ob) < 2: 
        return False
        
    if direction == "LONG" and price < ob[0]:
        return True
    if direction == "SHORT" and price > ob[1]:
        return True
        
    return False


# =====================================================
# MULTI-TIMEFRAME ENGINE
# =====================================================
# =====================================================
# MULTI-TIMEFRAME ENGINE (CACHED)
# =====================================================

def fetch_multitf(symbol: str):
    now = t.time()

    # -------- HTF CACHE (refresh every 10 minutes) --------
    if (
        symbol in HTF_CACHE and
        now - HTF_CACHE_TIME.get(symbol, 0) < 600
    ):
        htf_30m, htf_1h, htf_4h, htf_day = HTF_CACHE[symbol]
    else:
        htf_30m = fetch_ohlc(symbol, "30minute")
        htf_1h = fetch_ohlc(symbol, "60minute")
        htf_4h = fetch_ohlc(symbol, "4hour")
        htf_day = fetch_ohlc(symbol, "day")
        HTF_CACHE[symbol] = (htf_30m, htf_1h, htf_4h, htf_day)
        HTF_CACHE_TIME[symbol] = now

    # -------- LTF (always fresh) --------
    ltf_5m = fetch_ohlc(symbol, "5minute")
    ltf_15m = fetch_ohlc(symbol, "15minute")

    # Strip the FORMING (incomplete) candle from HTF data to prevent
    # MTF repainting. Kite historical_data returns the current candle
    # which has unreliable OHLC until the bar closes. This is the
    # equivalent of barstate.isconfirmed in TradingView.
    # LTF 5m/15m keep the forming candle for real-time price tracking.
    return {
        "5m": ltf_5m,
        "15m": ltf_15m,
        "30m": htf_30m[:-1] if htf_30m else htf_30m,
        "1h": htf_1h[:-1] if htf_1h else htf_1h,
        "4h": htf_4h[:-1] if htf_4h else htf_4h,
        "day": htf_day[:-1] if htf_day else htf_day,
    }


def multitf_bias(tf_data: dict):
    """
    HTF confirmation engine
    """
    bias_4h = detect_htf_bias(tf_data["4h"])
    bias_1h = detect_htf_bias(tf_data["1h"])

    if bias_4h and bias_4h == bias_1h:
        return bias_4h

    return None


def multitf_range(tf_data: dict):
    """
    Range confirmation across TFs
    """
    return is_range(tf_data["1h"]) and is_range(tf_data["4h"])


# =====================================================
# SL / TARGET ENGINE
# =====================================================
# -----------------------------
# ENTRY FRESHNESS FILTER
# -----------------------------

def is_fresh_entry(entry, target, current_price, max_progress=0.3):
    """
    Reject entries where price already moved too far
    max_progress = 0.3 means 30% of move allowed
    """
    total_move = abs(target - entry)
    if total_move == 0:
        return False

    progressed = abs(current_price - entry)
    return progressed <= total_move * max_progress
# -----------------------------
# ORDER BLOCK PROXIMITY FILTER
# -----------------------------

def is_price_near_ob(price, ob, direction, tolerance=0.2):
    """
    Price must be within OB zone (not mid-move)
    tolerance = 20% of OB height
    """
    ob_low, ob_high = ob
    ob_range = ob_high - ob_low

    if ob_range <= 0:
        return False

    if direction == "LONG":
        return price <= ob_low + ob_range * tolerance
    else:
        return price >= ob_high - ob_range * tolerance

def is_price_inside_ob(price, ob):
    return ob[0] <= price <= ob[1]

def compute_sl_target(direction, entry, ob, atr, setup_type):
    # GRID SEARCH (Feb 2026): rr=2.0 optimal for all setups
    rr = 2
        
    # 🛑 CORE FIX 3: ATR Buffer on Stops (Dynamic)
    # buffer = atr * 0.3 # OLD
    
    # We don't have symbol here easily, so we rely on passed args or generic
    # Wait, compute_sl_target is used by Setup A/B.
    # We should update the signature or just use a safe default if symbol unknown?
    # Actually, Setup B calls this. We need to pass symbol to it or handle it inside.
    # Refactor: We will use a simplified dynamic approach here assuming context is passed or default.
    # Since we can't easily change signature everywhere without breaking, we'll use a conservative default 
    # OR we assume this is mostly for stocks/banknifty where 0.3 was fine.
    # BUT wait, the extensive fix requested was specifically for checking symbol.
    # Let's fallback to 0.3 (BankNifty default) if we can't check symbol, 
    # but the user instruction was "Inside compute_sl_target... if 'BANK' in symbol..."
    # So we MUST pass symbol.
    
    # NOTE: To avoid signature breaking in 1 step, we will use a global lookup or just 
    # assume the caller handles it? No, user said "Inside compute_sl_target".
    # I will modify the function to accept 'symbol' as optional, defaulting to None.
    
    buffer = atr * 0.3
    
    if direction == "LONG":
        sl = ob[0] - buffer
        target = entry + rr * (entry - sl)
    else:
        sl = ob[1] + buffer
        target = entry - rr * (sl - entry)

    return round(sl, 2), round(target, 2)

def compute_sl_target_dynamic(symbol, direction, entry, ob, atr, setup_type):
    # GRID SEARCH (Feb 2026): rr=2.0 optimal for all setups
    rr = 2
        
    buffer = compute_dynamic_buffer(symbol, atr)

    if direction == "LONG":
        sl = ob[0] - buffer
        target = entry + rr * (entry - sl)
    else:
        sl = ob[1] + buffer
        target = entry - rr * (sl - entry)

    return round(sl, 2), round(target, 2)
# =====================================================
# PART 3 — SETUP ENGINES (A & B)
# =====================================================

# -----------------------------------------------------
# SETUP-A : HTF BOS CONTINUATION
# -----------------------------------------------------


# -----------------------------------------------------
# SETUP-HIERARCHICAL : V3 INSTITUTIONAL (STRICT)
# -----------------------------------------------------
def detect_hierarchical(symbol):
    tf = fetch_multitf(symbol)
    if not tf["5m"] or not tf["15m"]:
        return None
    
    # Convert dicts to DataFrame for V3 Logic
    df_15m = pd.DataFrame(tf["15m"])
    df_5m = pd.DataFrame(tf["5m"])
    
    # Ensure columns are lower case for the model
    if not df_15m.empty: df_15m.columns = [c.lower() for c in df_15m.columns]
    if not df_5m.empty: df_5m.columns = [c.lower() for c in df_5m.columns]

    try:
        setup, reject = evaluate_entry(
            symbol,
            df_15m,
            df_5m,
            current_time=now_ist().time()
        )
        
        if setup:
            return {
                "setup": "HIERARCHICAL",
                "symbol": symbol,
                "direction": setup.direction,
                "entry": setup.entry_price,
                "sl": setup.stop_loss,
                "target": setup.target,
                "rr": setup.rr,
                "confidence": None, # Let ranking engine decide (Issue 3)
                "ltf": tf["5m"][-80:],
                "analysis": f"V3 Institutional: {setup.setup_type}"
            }
    except Exception as e:
        print(f"Hierarchical Error {symbol}: {e}")
        
    return None

# -----------------------------------------------------
# SETUP-A : HTF BOS CONTINUATION (FIXED)
# -----------------------------------------------------

def detect_setup_a(symbol: str, tf_data: dict):
    """
    Setup-A:
    - HTF BOS (1H + 4H aligned)
    - Order Block on 5m
    - Entry only near OB
    - Reject late entries
    """
    
    # Required candles
    if not tf_data.get("5m") or not tf_data.get("1h"):
        logging.debug(f"[A] {symbol}: no 5m/1h data")
        return None

    # RELAXED BIAS: Use 1H Trend Only (Ignore 4H for now to increase frequency)
    bias = detect_htf_bias(tf_data["1h"])

    if not bias:
        logging.debug(f"[A] {symbol}: no HTF bias on 1h")
        return None

    ltf = tf_data["5m"]

    # ❌ Liquidity filter
    if not is_index(symbol):
        # 1. Trade Limit Check
        if get_daily_trade_count(symbol) >= 1: return None
        
        # 2. Liquidity Check
        if not is_liquid_stock(ltf):
            return None
        
    # ATR FILTER REMOVED (User Request: "Why no trades?")
    # if is_index(symbol):
    #    if not index_atr_filter(ltf):
    #        return None

    price = ltf[-1]["close"]
    atr = calculate_atr(ltf)
    
    # -----------------------------------------------------
    # 1. DETECT STRUCTURE FIRST (Always Fresh)
    # -----------------------------------------------------
    ob = detect_order_block(ltf, bias)
    fvg = detect_fvg(ltf, bias)
    
    # 🔑 Unique structure key
    key = f"{symbol}_{bias}"

    # 📦 Get existing structure state (if any)
    state = STRUCTURE_STATE.get(key)
    
    # -----------------------------------------------------
    # 2. STATE MACHINE (Strict Order)
    # -----------------------------------------------------
    
    # CASE: EXISTING STATE
    if state:
        # Check Expiry
        if (now_ist() - state["time"]).total_seconds() > 1800:
            STRUCTURE_STATE.pop(key, None)
            return None
            
        # ❌ Check for invalidation first
        # ❌ Check for invalidation first
        if invalidate_structure(price, state["ob"], bias):
            STRUCTURE_STATE.pop(key, None)
            return None # Exit immediately if invalidated

        # 🔵 STAGE-1: Price taps FVG
        if state["stage"] == "FORMED":
            current_fvg = detect_fvg(ltf, bias)
            if current_fvg and is_price_inside_fvg(price, current_fvg):
                STRUCTURE_STATE[key]["fvg"] = current_fvg
                STRUCTURE_STATE[key]["stage"] = "TAPPED"

                return {
                    "setup": "A-FVG-TAP",
                    "symbol": symbol,
                    "direction": bias,
                    "note": "FVG tapped – waiting for rejection"
                }
            return None # Wait for tap

        # 🔴 STAGE-2: Rejection from FVG → BUY
        if state["stage"] == "TAPPED":
            if not fvg_rejection(ltf, bias):
                return None # Wait for rejection

            # SL & Target from FVG (Refined Logic)
            entry = (state["fvg"][0] + state["fvg"][1]) / 2

            buffer = compute_dynamic_buffer(symbol, atr)

            if bias == "LONG":
                recent_low = min(c["low"] for c in ltf[-10:])
                sl = recent_low - buffer
                target = entry + 2 * (entry - sl)  # GRID SEARCH: rr=2.0
            else:
                recent_high = max(c["high"] for c in ltf[-10:])
                sl = recent_high + buffer
                target = entry - 2 * (sl - entry)  # GRID SEARCH: rr=2.0

            # Signal fired, clear state
            STRUCTURE_STATE.pop(key, None)

            return {
                "setup": f"A-FVG-{'BUY' if bias == 'LONG' else 'SELL'}",
                "symbol": symbol,
                "direction": bias,
                "entry": entry,
                "sl": round(sl, 2),
                "target": round(target, 2),
                "rr": round(abs(target - entry) / abs(entry - sl), 2),
                "option": option_strike(price, bias),
                "entry_type": "LIMIT",
                "note": "FVG rejection entry",
                "ltf": ltf[-80:]
            }
            
        return None # Should not reach here if state logic is exhaustive

    # (Configuration Removed - Managed Globally)

    # -----------------------------------------------------
    # 3. NEW STRUCTURE CREATION
    # -----------------------------------------------------
    
    # CASE: NO STATE (Fresh Search)
    if not state:
        # We need both OB and FVG to start tracking
        if not ob or not fvg:
            return None

        # Create new state
        STRUCTURE_STATE[key] = {
            "ob": ob,
            "fvg": fvg,
            "stage": "FORMED",
            "time": now_ist()
        }

        return {
            "setup": "A-STRUCTURE",
            "symbol": symbol,
            "direction": bias,
            "note": "OB + FVG formed – waiting for FVG tap"
        }

    return None


# -----------------------------------------------------
# SETUP-B : RANGE MEAN-REVERSION
# -----------------------------------------------------
def detect_setup_b(symbol: str, tf_data: dict):
    """
    Setup-B:
    - Market in HTF range
    - OB at extremes
    - Rejection + volume
    - RR >= 1:2
    """
    if not tf_data.get("5m") or not tf_data.get("1h"):
        return []

    # RELAXED: Removed Strict Range Check (It was killing all signals)
    # Instead, we rely on OB Rejection at Extremes
    ltf = tf_data["5m"]

    # 3. Volume spike on reversal candle
    if is_index(symbol):
        pass # Skip volume check for indices
    elif not volume_expansion(ltf):
        return []

    # ❌ Skip illiquid stocks
    # ❌ Skip illiquid stocks / Max Limit
    if not is_index(symbol):
        if get_daily_trade_count(symbol) >= 1: return []
        if not is_liquid_stock(ltf): return []
        
    # ATR FILTER REMOVED (Relaxed)
    # if is_index(symbol):
    #     if not index_atr_filter(ltf):
    #         return []


    price = ltf[-1]["close"]
    atr = calculate_atr(ltf)

    signals = []

    # ---------------- LONG REVERSAL ----------------
    ob_long = detect_order_block(ltf, "LONG")
    if ob_long and price <= ob_long[1]:
        if volume_expansion(ltf):
            # ✅ Confirmation candle (LONG)
            if not confirmation_candle(ltf, "LONG"):
                pass
            else:
                sl, target = compute_sl_target_dynamic(symbol, "LONG", price, ob_long, atr, "B")

                last_swing_price = ltf[-3]["close"]
                if is_fresh_entry(entry=price, target=target, current_price=last_swing_price):
                      # FVG must be near OB (e.g. above it)
                    # FVG must be near OB (e.g. above it)
                    fvg_long = detect_fvg(ltf, "LONG")
                    has_fvg = False
                    zone_low, zone_high = ob_long  # Initialize defaults from OB

                    if fvg_long:
                        f_low, f_high = fvg_long
                        if f_low >= zone_low: # FVG is above or inside OB
                            has_fvg = True
                            # UNION ZONE: Extend Trigger Zone to include FVG
                            zone_high = max(zone_high, f_high)
                    
                    # Check if recently tapped Combined Zone?
                    tapped = False
                    # Assuming htf_data is ltf here based on context
                    for i in range(-3, 0): # Check last 3 candles
                        if ltf[i]["low"] <= zone_high:
                            tapped = True
                    
                    if tapped:
                        # REACTION TRIGGER: 
                        # Current candle must be GREEN (Close > Open) indicating bounce
                        is_green = ltf[-1]["close"] > ltf[-1]["open"]
                        
                        if is_green and has_fvg: # Enforce FVG presence for Universal Setup
                            sl = zone_low - (atr * 0.3)
                            if sl != price:
                                rr = abs(target - price) / abs(price - sl)
                                if rr >= 2:
                                    signals.append({
                                        "setup": "B",
                                        "symbol": symbol,
                                        "direction": "LONG",
                                        "entry": price,
                                        "sl": round(sl, 2),
                                        "target": round(target, 2),
                                        "rr": round(rr, 2),
                                        "option": option_strike(price, "LONG"),
                                        "ob": ob_long,
                                        "fvg": bool(detect_fvg(ltf, "LONG")),
                                        "volume": True,
                                        "ltf": ltf[-80:]
                                    })

    # ---------------- SHORT REVERSAL ----------------
    ob_short = detect_order_block(ltf, "SHORT")
    if ob_short and price >= ob_short[0]:
        if volume_expansion(ltf):
            # ✅ Confirmation candle (SHORT)
            if not confirmation_candle(ltf, "SHORT"):
                pass
            else:
                sl, target = compute_sl_target_dynamic(symbol, "SHORT", price, ob_short, atr, "B")  # W6: Use dynamic buffer

                last_swing_price = ltf[-3]["close"]
                if is_fresh_entry(entry=price, target=target, current_price=last_swing_price):
                    if sl != price:
                        rr = abs(target - price) / abs(price - sl)
                        if rr >= 2:
                            signals.append({
                                "setup": "B",
                                "symbol": symbol,
                                "direction": "SHORT",
                                "entry": price,
                                "sl": sl,
                                "target": target,
                                "rr": round(rr, 2),
                                "option": option_strike(price, "SHORT"),
                                "ob": ob_short,
                                "fvg": bool(detect_fvg(ltf, "SHORT")),
                                "volume": True,
                                "ltf": ltf[-80:]
                            })
    
    return signals


# -----------------------------------------------------
# SETUP-C : HTF OB + LTF FVG (REJECTION)
# -----------------------------------------------------
# =====================================================
# POSITION SIZING (DIFFERENTIAL RISK)
# =====================================================

def position_risk_multiplier(setup_name: str) -> float:
    """
    Returns risk multiplier based on setup type
    """
    if "UNIVERSAL" in setup_name:   # Setup-C
        if "NIFTY 50" in setup_name: return 0.4 # Specific Reduction
        return 0.5
    if setup_name == "SETUP-D":
        return 1.0
    if setup_name.startswith("A-"):
        return 1.0
    return 1.0

# Global Throttle State
SETUP_C_DAILY_COUNT = {}

# -----------------------------------------------------
# STRUCTURE BIAS UTILS
# -----------------------------------------------------
def detect_choch(candles, direction, lookback=20):
    """F2.4: CHoCH with trend context and 3-bar fractals (delegates to smc_detectors)"""
    return smc.detect_choch(candles, direction, lookback=lookback)

def get_ltf_structure_bias(ltf_data):
    """F2.4: Swing-sequence based structure bias (delegates to smc_detectors)"""
    return smc.get_ltf_structure_bias(ltf_data)

def detect_setup_c(symbol: str, tf_data: dict):
    """
    UNIVERSAL SETUP (STATEFUL):
    1. Scan All TFs for Zones (OB+FVG). Store best active zone in ZONE_STATE.
    2. Monitor LTF (5m) for Tap -> Wait -> Reaction.
    3. Trigger Entry only if Candle Closes OUTSIDE Zone.

    Guards added:
    - GAP filter: suppress when open gaps >0.75% vs prior day close (gap-up for LONG,
      gap-down for SHORT) — chasing a gap has poor expectancy.
    - SAME-CANDLE guard: tap and reaction must not happen on the same 5m candle.
      Reaction candle's open must NOT equal the tap candle's timestamp.
    - WIDE-OPEN guard: if the opening 5m candle range > 1.5× ATR the signal is
      downgraded to grade B (still fires but analyst can choose to skip).
    """

    # ─── gap-filter constants (tunable) ───────────────────────────────────────
    GAP_UP_PCT   = 0.75   # % gap-up threshold to suppress LONG (chasing)
    GAP_DOWN_PCT = 0.75   # % gap-down threshold to suppress SHORT (chasing)
    WIDE_OPEN_ATR_MULT = 1.5   # opening candle range > X×ATR → downgrade
    # ──────────────────────────────────────────────────────────────────────────

    ltf_data = tf_data.get("5m")
    if not ltf_data: return []

    # -------------------------------------------------
    # INDEX-ONLY GATE
    # Stocks removed from scan — NIFTY/BANKNIFTY only
    # -------------------------------------------------
    if not is_index(symbol):
        return []

    # -------------------------------------------------
    # THROTTLE REMOVED AS PER USER REQUEST
    # We maintain 'key' definition because it might be used elsewhere (dedup/logging)
    # even if the limit check is disabled.
    current_date = ltf_data[-1]["date"].date().isoformat()
    key = f"{symbol}_{current_date}"

    # THROTTLE (Restored for FIN SERVICE only - Max 1 Trade)
    if "FIN SERVICE" in symbol:
        if SETUP_C_DAILY_COUNT.get(key, 0) >= 1: return []

    signals = []
    atr = calculate_atr(ltf_data)
    current_price = ltf_data[-1]["close"]
    ltf_candle = ltf_data[-1]

    # -------------------------------------------------
    # GAP DETECTION — find prior session close and today's first bar
    # -------------------------------------------------
    today_date = ltf_candle["date"].date()
    # First candle of today in the 5m data
    _first_today = next(
        (c for c in ltf_data if c["date"].date() == today_date), None
    )
    # Last candle of the previous session
    _last_prev = next(
        (c for c in reversed(ltf_data) if c["date"].date() < today_date), None
    )
    prior_close = _last_prev["close"] if _last_prev else None
    today_open  = _first_today["open"] if _first_today else None
    today_open_range = (
        (_first_today["high"] - _first_today["low"]) if _first_today else 0
    )

    # % gap relative to prior close
    if prior_close and today_open:
        _gap_pct = (today_open - prior_close) / prior_close * 100
    else:
        _gap_pct = 0.0

    _is_gap_up   = _gap_pct >  GAP_UP_PCT     # strong gap-up  → LONG  risky
    _is_gap_down = _gap_pct < -GAP_DOWN_PCT    # strong gap-down → SHORT risky
    _is_wide_open = atr > 0 and today_open_range > WIDE_OPEN_ATR_MULT * atr

    if _is_gap_up or _is_gap_down:
        logging.debug(
            "[SETUP_C] %s gap %.2f%% (up=%s down=%s) — suppressing signal on gap day",
            symbol, _gap_pct, _is_gap_up, _is_gap_down,
        )

    if _is_wide_open:
        logging.debug(
            "[SETUP_C] %s wide opening candle %.0f pts vs ATR %.0f pts",
            symbol, today_open_range, atr,
        )
    
    # Initialize State for Symbol if missing
    if symbol not in ZONE_STATE:
        ZONE_STATE[symbol] = {"LONG": None, "SHORT": None}

    # -------------------------------------------------
    # HARD STRUCTURE INVALIDATION (FIXED)
    # Only invalidate ACTIVE zones, preserve TAPPED zones
    # so they can complete their reaction check
    # -------------------------------------------------
    ltf_bias = get_ltf_structure_bias(ltf_data)

    if ltf_bias == "BULLISH":
        # Remove SHORT zones only if still ACTIVE (not yet tapped)
        short_z = ZONE_STATE[symbol]["SHORT"]
        if short_z and short_z.get("state") == "ACTIVE":
            ZONE_STATE[symbol]["SHORT"] = None

    elif ltf_bias == "BEARISH":
        # Remove LONG zones only if still ACTIVE (not yet tapped)
        long_z = ZONE_STATE[symbol]["LONG"]
        if long_z and long_z.get("state") == "ACTIVE":
            ZONE_STATE[symbol]["LONG"] = None

    # -------------------------------------------------
    # HTF TREND CONTEXT (For Phase 2 Filtering)
    # -------------------------------------------------
    htf_1h_data = tf_data.get("1h", [])
    htf_bias = detect_htf_bias(htf_1h_data) if htf_1h_data else None

    # -------------------------------------------------
    # PHASE 1: ZONE DISCOVERY (Run on every tick?)
    # ideally run less often, but for now run always to update zones
    # -------------------------------------------------
    
    # Phase 1: Zone Discovery
    intervals = ["5minute", "15minute", "30minute", "60minute", "4hour"]
    
    for interval, mapped_key in [("5minute", "5m"), ("15minute", "15m"), ("30minute", "30m"), ("60minute", "1h"), ("4hour", "4h")]:
        htf_data = tf_data.get(mapped_key, [])
        if not htf_data or len(htf_data) < 50: continue
            
        # Update LONG Zone
        ob_long = detect_order_block(htf_data, "LONG")
        if ob_long:
            z_low, z_high = ob_long
            fvg = detect_fvg(htf_data, "LONG")
            if fvg:
                 f_low, f_high = fvg
                 if f_low >= z_low:
                     z_high = max(z_high, f_high)  # expand zone to include FVG
            # Allow OB-only LONG zones (demand zone retests are valid without FVG)
            current_zone = ZONE_STATE[symbol]["LONG"]
            if not current_zone or z_high > current_zone["zone"][1]:
                ZONE_STATE[symbol]["LONG"] = {
                    "zone": (z_low, z_high),
                    "state": "ACTIVE",
                    "tf": interval,
                    "created": htf_data[-1]["date"],
                    "has_fvg": fvg is not None,
                }

        # Update SHORT Zone
        ob_short = detect_order_block(htf_data, "SHORT")
        if ob_short:
            z_low, z_high = ob_short
            fvg = detect_fvg(htf_data, "SHORT")
            if fvg:
                 f_low, f_high = fvg  # detect_fvg always returns (low, high)
                 if f_high <= z_high:
                     z_low = min(z_low, f_low)
            # Allow OB-only SHORT zones (swing-high supply zones are valid even
            # without a coincident FVG — requiring FVG confluence was causing the
            # algo to miss clean bearish OB retests like the Mar 21 23,310-23,344 zone)
            current_zone = ZONE_STATE[symbol]["SHORT"]
            if not current_zone or z_low < current_zone["zone"][0]:
                ZONE_STATE[symbol]["SHORT"] = {
                    "zone": (z_low, z_high),
                    "state": "ACTIVE",
                    "tf": interval,
                    "created": htf_data[-1]["date"],
                    "has_fvg": fvg is not None,
                }

    # -------------------------------------------------
    # PHASE 2: STATE MACHINE (Reaction on 5m)
    # -------------------------------------------------
    
    # --- LONG MACHINE ---
    long_state = ZONE_STATE[symbol]["LONG"]
    if long_state:
        z_low, z_high = long_state["zone"]
        state = long_state["state"]

        # Check Tap (Low touched zone)
        if ltf_candle["low"] <= z_high:
            if state == "ACTIVE":
                long_state["state"] = "TAPPED"
                long_state["tap_time"] = ltf_candle["date"]  # Record Time
                long_state["tap_candle_ts"] = ltf_candle["date"]  # for same-candle guard

        # Check Reaction (Only if Tapped)
        if state == "TAPPED":
            # 1. Close must be GREEN
            is_green = ltf_candle["close"] > ltf_candle["open"]
            # 2. Close must be ABOVE Zone High (Exit Zone)
            is_outside = ltf_candle["close"] > z_high

            if is_green and is_outside:
                # ── SAME-CANDLE GUARD ──────────────────────────────────────
                # Tap and reaction on the same 5m candle = unreliable (gap-open
                # with wick + recovery in a single bar). Require the reaction
                # candle to be strictly AFTER the tap candle.
                tap_ts  = long_state.get("tap_candle_ts")
                same_candle = (tap_ts is not None and ltf_candle["date"] == tap_ts)
                if same_candle:
                    logging.debug(
                        "[SETUP_C] %s LONG same-candle tap+react suppressed (%s)",
                        symbol, ltf_candle["date"],
                    )
                    # Do NOT return — let the machine stay TAPPED so the NEXT
                    # candle can attempt a clean reaction.
                    long_state["state"] = "TAPPED"
                else:
                # ── GAP-UP FILTER ──────────────────────────────────────────
                # Gap-up > GAP_UP_PCT from prior close → we are chasing a
                # momentum open, not a clean zone reaction. Suppress.
                    if _is_gap_up:
                        logging.info(
                            "[SETUP_C] %s LONG suppressed — gap-up %.2f%% (>%.2f%%) on %s",
                            symbol, _gap_pct, GAP_UP_PCT, ltf_candle["date"],
                        )
                    else:
                        # DEDUP — direction-aware: LONG gets its own key so a
                        # prior SHORT on the same symbol doesn't block a LONG retest
                        clean_sym = clean_symbol(symbol)
                        if f"{clean_sym}_LONG" in TRADED_TODAY:
                            if DEBUG_MODE: logging.debug(
                                "[SETUP_C] BLOCKED %s UNIVERSAL-LONG (Already traded LONG today)", symbol
                            )
                            return signals
                        # HTF TREND FILTER (LONG)
                        # REGIME OVERRIDE: allow LONG even if HTF swing bias is
                        # stale "SHORT" when market regime is BULLISH.
                        if htf_bias == "SHORT" and MARKET_REGIME != "BULLISH":
                            should_fire = False
                        elif htf_bias is None:
                            should_fire = True
                            sig_require_high_confluence = True
                        else:
                            should_fire = True
                            sig_require_high_confluence = False

                        if should_fire:
                            buffer = compute_dynamic_buffer(symbol, atr)
                            sl     = z_low - buffer
                            target = current_price + (current_price - sl) * 2

                            tap_time_str = long_state.get("tap_time", "Unknown")

                            # Wide-open candle note for the signal text
                            quality_note = " [WIDE OPEN — verify manually]" if _is_wide_open else ""

                            sig_data = {
                                "setup": "UNIVERSAL-LONG",
                                "symbol": symbol,
                                "direction": "LONG",
                                "entry": current_price,
                                "sl": round(sl, 2),
                                "target": round(target, 2),
                                "rr": 1.6 if "FIN SERVICE" in symbol else 2.0,
                                "option": option_strike(current_price, "LONG"),
                                "ob": long_state["zone"],
                                "ltf": ltf_data[-80:],
                                "analysis": (
                                    f"Reaction from {long_state['tf']} Zone "
                                    f"(Tapped at {tap_time_str}){quality_note}"
                                ),
                                "_require_high_confluence": (
                                    sig_require_high_confluence
                                    if "sig_require_high_confluence" in dir()
                                    else False
                                ),
                                "_gap_pct": round(_gap_pct, 2),
                                "_wide_open": _is_wide_open,
                            }
                            signals.append(sig_data)
                            TRADED_TODAY.add(f"{clean_symbol(symbol)}_LONG")
                            long_state["state"] = "FIRED"
                            if "FIN SERVICE" in symbol:
                                SETUP_C_DAILY_COUNT[key] = SETUP_C_DAILY_COUNT.get(key, 0) + 1

    # --- SHORT MACHINE ---
    short_state = ZONE_STATE[symbol]["SHORT"]
    if short_state:
        z_low, z_high = short_state["zone"]
        state = short_state["state"]

        if ltf_candle["high"] >= z_low:
            if state == "ACTIVE":
                short_state["state"] = "TAPPED"
                short_state["tap_time"] = ltf_candle["date"]
                short_state["tap_candle_ts"] = ltf_candle["date"]  # same-candle guard

        if state == "TAPPED":
            is_red     = ltf_candle["close"] < ltf_candle["open"]
            is_outside = ltf_candle["close"] < z_low

            if is_red and is_outside:
                # ── SAME-CANDLE GUARD ──────────────────────────────────────
                tap_ts      = short_state.get("tap_candle_ts")
                same_candle = (tap_ts is not None and ltf_candle["date"] == tap_ts)
                if same_candle:
                    logging.debug(
                        "[SETUP_C] %s SHORT same-candle tap+react suppressed (%s)",
                        symbol, ltf_candle["date"],
                    )
                    short_state["state"] = "TAPPED"  # stay TAPPED for next bar
                else:
                # ── GAP-DOWN FILTER ────────────────────────────────────────
                # Gap-down > GAP_DOWN_PCT → chasing a bearish momentum open.
                    if _is_gap_down:
                        logging.info(
                            "[SETUP_C] %s SHORT suppressed — gap-down %.2f%% (<-%.2f%%) on %s",
                            symbol, _gap_pct, GAP_DOWN_PCT, ltf_candle["date"],
                        )
                    else:
                        # DEDUP — direction-aware: SHORT gets its own key
                        clean_sym = clean_symbol(symbol)
                        if f"{clean_sym}_SHORT" in TRADED_TODAY:
                            if DEBUG_MODE: logging.debug(
                                "[SETUP_C] BLOCKED %s UNIVERSAL-SHORT (Already traded SHORT today)", symbol
                            )
                            return signals
                        # HTF TREND FILTER (SHORT)
                        # Zone-proximity override: if price has tapped into the
                        # bearish OB (which is true here — we're in TAPPED state),
                        # the zone itself is the edge. A supply zone retest is a
                        # valid SHORT even when the 1h bias is still reading LONG
                        # from the prior session's rally. Only hard-block when both
                        # HTF bias AND market regime confirm bullish momentum AND the
                        # zone has no FVG backing (pure OB-only, lower confidence).
                        _zone_has_fvg = short_state.get("has_fvg", False)
                        _htf_conflict = (htf_bias == "LONG" and MARKET_REGIME != "BEARISH")
                        if _htf_conflict and not _zone_has_fvg:
                            # OB-only zone against bullish bias → require extra caution
                            # but don't hard-block: fire with high-confluence flag
                            should_fire = True
                            sig_require_high_confluence = True
                            logging.info(
                                "[SETUP_C] %s UNIVERSAL-SHORT at supply zone — HTF bias LONG "
                                "but zone has no FVG; firing with HIGH_CONFLUENCE flag", symbol
                            )
                        elif _htf_conflict and _zone_has_fvg:
                            # OB+FVG zone at supply despite bullish bias → high-probability
                            # zone retest, allow with normal confluence
                            should_fire = True
                            sig_require_high_confluence = False
                        elif htf_bias is None:
                            should_fire = True
                            sig_require_high_confluence = True
                        else:
                            should_fire = True
                            sig_require_high_confluence = False

                        if should_fire:
                            buffer = compute_dynamic_buffer(symbol, atr)
                            sl     = z_high + buffer
                            target = current_price - (sl - current_price) * 2

                            tap_time_str = short_state.get("tap_time", "Unknown")

                            quality_note = " [WIDE OPEN — verify manually]" if _is_wide_open else ""

                            sig_data = {
                                "setup": "UNIVERSAL-SHORT",
                                "symbol": symbol,
                                "direction": "SHORT",
                                "entry": current_price,
                                "sl": round(sl, 2),
                                "target": round(target, 2),
                                "rr": 1.6 if "FIN SERVICE" in symbol else 2.0,
                                "option": option_strike(current_price, "SHORT"),
                                "ob": short_state["zone"],
                                "ltf": ltf_data[-80:],
                                "analysis": (
                                    f"Reaction from {short_state['tf']} Zone "
                                    f"(Tapped at {tap_time_str}){quality_note}"
                                ),
                                "_require_high_confluence": (
                                    sig_require_high_confluence
                                    if "sig_require_high_confluence" in dir()
                                    else False
                                ),
                                "_gap_pct": round(_gap_pct, 2),
                                "_wide_open": _is_wide_open,
                            }
                            signals.append(sig_data)
                            TRADED_TODAY.add(f"{clean_symbol(symbol)}_SHORT")
                            short_state["state"] = "FIRED"
                            if "FIN SERVICE" in symbol:
                                SETUP_C_DAILY_COUNT[key] = SETUP_C_DAILY_COUNT.get(key, 0) + 1

    return signals


# -----------------------------------------------------
# SETUP-D : CHOCH + OB + FVG (CONTINUATION/REVERSAL)
# -----------------------------------------------------
def is_strong_impulsive_trend(candles, check_direction=None):
    """Checks if last 3 candles are strong impulsive in COMPARED direction"""
    if len(candles) < 3: return False
    recent = candles[-3:]
    
    # Check if all Green or all Red
    greens = sum(1 for c in recent if c['close'] > c['open'])
    reds = sum(1 for c in recent if c['close'] < c['open'])
    
    if greens == 3: # Strong Up
        if check_direction == "SHORT": return False # It is strong UP, not Short
        if check_direction == "LONG": 
             ranges = [c['high']-c['low'] for c in recent]
             if ranges[0] < ranges[1] < ranges[2]: return True
             return False # Just 3 greens isn't enough, need expanding range
             
        # If no direction spec, just return True for any trend
        return True 
    
    if reds == 3: # Strong Down
        if check_direction == "LONG": return False
        if check_direction == "SHORT":
             ranges = [c['high']-c['low'] for c in recent]
             if ranges[0] < ranges[1] < ranges[2]: return True
             return False

        return True
             
    return False

def detect_choch_setup_d(candles, lookback=20):
    """F2.4: CHoCH for Setup-D with trend context (delegates to smc_detectors)"""
    return smc.detect_choch_setup_d(candles, lookback=lookback)

def detect_choch_opening_gap(candles_today):
    """Opening-gap CHoCH: uses same-day candles only (delegates to smc_detectors)"""
    return smc.detect_choch_opening_gap(candles_today)

def detect_setup_d(symbol: str, tf_data: dict):
    ltf_data = tf_data.get("5m")
    if not ltf_data: return None

    # Phase 1: INDEX ONLY gate — Setup-D re-enabled but restricted to index instruments
    if not is_index(symbol):
        return None

    # 1. HTF CONTEXT (1H)
    htf = tf_data.get("1h", [])

    atr = calculate_atr(ltf_data)
    key = f"{symbol}_SETUP_D"
    state = SETUP_D_STATE.get(key)

    # Phase 3: dynamic expiry — 4h for indices, 2h for stocks
    _expiry_secs = 14400 if is_index(symbol) else 7200

    # -------- PHASE 1: DETECT CHOCH --------
    if not state:
        # ── Phase 2 EARLY DISPLACEMENT: scan BEFORE CHoCH ─────────────────
        # Detect institutional displacement candle 30-40 bars earlier than CHoCH.
        # Run even if CHoCH has not fired — records to DISPLACEMENT_EVENTS and
        # sets EARLY_WARNING_STATE so the dashboard can show "pending" activity.
        recent_sweep_pre = liquidity_sweep_detected(ltf_data, lookback=20)
        disp_event = detect_displacement(
            ltf_data,
            near_sweep=bool(recent_sweep_pre),
            lookback=6,
        )
        if disp_event is not None:
            # Always record to the event buffer for the dashboard
            liq_ctx = "sweep_present" if recent_sweep_pre else "no_sweep"
            record_displacement_event(symbol, disp_event, liquidity_context=liq_ctx)

            # Phase 6: emit EARLY_SMART_MONEY_ACTIVITY state
            if disp_event["confidence"] in ("medium", "high"):
                EARLY_WARNING_STATE[symbol] = {
                    "type"         : "EARLY_SMART_MONEY_ACTIVITY",
                    "direction"    : "bullish" if disp_event["direction"] == "bullish" else "bearish",
                    "confidence"   : disp_event["confidence"],
                    "displacement" : disp_event,
                    "timestamp"    : disp_event["timestamp"],
                    "liquidity"    : liq_ctx,
                }
                logging.info(
                    f"[EARLY_WARNING] {symbol} | EARLY_SMART_MONEY_ACTIVITY | "
                    f"dir={disp_event['direction']} | conf={disp_event['confidence']} | "
                    f"sweep={liq_ctx}"
                )
        # ──────────────────────────────────────────────────────────────────

        # On significant gap days, yesterday's swings pollute the 30-bar lookback
        # window — the gap-down origin is 300-400 pts away, making it impossible
        # to ever "break" yesterday's swing high and fire a bullish CHoCH.
        # Solution: when the day gapped >0.3%, use same-day candles only.
        _today_date = now_ist().date()
        _today_ltf  = [c for c in ltf_data if c["date"].date() == _today_date]
        _prev_ltf   = [c for c in ltf_data if c["date"].date() < _today_date]
        _is_gap_day = False
        _today_start_in_ltf = len(ltf_data) - len(_today_ltf)

        if _today_ltf and _prev_ltf:
            _prev_close = _prev_ltf[-1]["close"]
            _today_open = _today_ltf[0]["open"]
            _gap_pct    = abs(_today_open - _prev_close) / _prev_close * 100
            _is_gap_day = _gap_pct > 0.3

        if _is_gap_day and len(_today_ltf) >= 10:
            _choch_raw = detect_choch_opening_gap(_today_ltf)
            if _choch_raw:
                choch = (_choch_raw[0], _today_start_in_ltf + _choch_raw[1])
            else:
                choch = None
            logging.debug(
                f"[SETUP-D] {symbol} | Gap day ({_gap_pct:.2f}%) | "
                f"opening-gap CHoCH scan ({len(_today_ltf)} today bars) | "
                f"result={'FOUND' if choch else 'None'}"
            )
        elif _is_gap_day:
            # Not enough same-day bars yet — skip to avoid cross-day CHoCH noise
            return None
        else:
            choch = detect_choch_setup_d(ltf_data)
        if not choch:
            return None

        direction, idx = choch

        # Phase 4: Liquidity sweep bypass — if sweep detected near CHoCH, skip HTF filter
        recent_sweep = liquidity_sweep_detected(ltf_data[:idx+1], lookback=15)
        # On gap days the gap itself IS a liquidity sweep (macro stop-hunt of prior lows/highs)
        if _is_gap_day and not recent_sweep:
            recent_sweep = True
        htf_bias = detect_htf_bias(htf)
        if htf_bias and direction != htf_bias and not recent_sweep:
            return None  # Block counter-trend ONLY when no liquidity sweep present

        disp = ltf_data[idx]

        # On gap days, the opening gap IS the displacement — the CHoCH candle is a
        # quiet recovery bar. Relax the idx guard and displacement size check.
        # _is_gap_day is already computed above (same-day CHoCH scan section).
        if not _is_gap_day:
            # Normal mid-session guard: CHoCH too early in buffer
            if idx < 10:
                return None

        avg_range = sum(c["high"] - c["low"] for c in ltf_data[max(0, idx-10):idx]) / max(1, min(10, idx))

        if not _is_gap_day:
            # TIGHTENED DISPLACEMENT (was 1.1, now 1.2) — mid-session only
            if avg_range > 0 and (disp["high"] - disp["low"]) < avg_range * 1.2:
                return None
        else:
            # Gap day: skip displacement check — the opening gap IS the displacement
            logging.info(
                f"[SETUP-D] {symbol} | Gap day ({_gap_pct:.2f}%) | "
                f"displacement check skipped | CHoCH at ltf idx {idx} | dir={direction}"
            )

        # Re-run displacement detection at CHoCH index for Phase 4 scoring
        choch_disp = detect_displacement(
            ltf_data[:idx + 2],
            near_sweep=bool(recent_sweep),
            lookback=8,
        )

        # Phase 2: Set BOS_WAIT stage — don't scan OB/FVG yet, wait for BOS confirmation
        choch_break_level = ltf_data[idx]["close"]
        SETUP_D_STATE[key] = {
            "bias"               : direction,
            "stage"              : "BOS_WAIT",
            "choch_level"        : choch_break_level,
            "choch_idx"          : idx,
            "is_gap_day"         : _is_gap_day,
            "sweep_detected"     : bool(recent_sweep),
            "choch_time"         : now_ist(),
            "time"               : now_ist(),
            "ob"                 : None,
            "fvg"                : None,
            # Phase 2 NEW — displacement context carried into scoring
            "displacement_event" : choch_disp,
            "displacement_detected": choch_disp is not None,
        }

        # Phase 8 trace — record CHoCH entry into audit log
        _trace_append(symbol, {
            "stage"               : "CHOCH_DETECTED",
            "direction"           : direction,
            "sweep_detected"      : bool(recent_sweep),
            "is_gap_day"          : _is_gap_day,
            "choch_detector"      : "opening_gap" if _is_gap_day else "standard",
            "displacement_detected": choch_disp is not None,
            "displacement_strength": choch_disp["strength"] if choch_disp else None,
            "displacement_fvg"    : choch_disp["created_fvg"] if choch_disp else None,
            "liquidity_event"     : "sweep_present" if recent_sweep else "no_sweep",
            "structure_state"     : "CHoCH",
        })
        return None

    # -------- DAY BOUNDARY CHECK --------
    # States from previous day are invalid — zones don't carry overnight
    state_date = state.get("choch_time", state["time"]).date()
    if state_date < now_ist().date():
        SETUP_D_STATE.pop(key, None)
        return None

    # -------- COMMON EXPIRY CHECK --------
    if (now_ist() - state["time"]).total_seconds() > _expiry_secs:
        SETUP_D_STATE.pop(key, None)
        return None

    bias = state["bias"]
    candle = ltf_data[-1]

    # -------- PHASE 2: BOS CONFIRMATION --------
    if state["stage"] == "BOS_WAIT":
        # BOS = price forms a new structural break confirming the CHoCH direction
        # Proxy: current close exceeds the 5-bar range high (LONG) or falls below 5-bar range low (SHORT)
        lookback_bars = ltf_data[-7:-1]  # 6 completed bars excluding current
        if len(lookback_bars) < 5:
            return None
        recent_high = max(c["high"] for c in lookback_bars)
        recent_low = min(c["low"] for c in lookback_bars)

        bos_confirmed = False
        # Gap-day BOS: use CHoCH close level — structural break already happened at open
        if state.get("is_gap_day") and state.get("choch_level", 0) > 0:
            cl = state["choch_level"]
            if bias == "LONG" and candle["close"] > cl * 1.001:
                bos_confirmed = True
            elif bias == "SHORT" and candle["close"] < cl * 0.999:
                bos_confirmed = True
        if not bos_confirmed:
            if bias == "LONG" and candle["close"] > recent_high:
                bos_confirmed = True
            elif bias == "SHORT" and candle["close"] < recent_low:
                bos_confirmed = True

        if bos_confirmed:
            # Now scan fresh OB + FVG from current candles
            ob = detect_order_block(ltf_data, bias)
            fvg = detect_fvg(ltf_data, bias)
            # FIX-1: OB-only setups valid — FVG adds confluency, not a gate
            if ob:
                state["ob"] = ob
                state["fvg"] = fvg  # may be None
                state["has_fvg"] = fvg is not None
                state["stage"] = "WAIT"
                state["bos_confirmed"] = True
        return None

    # -------- PHASE 3: WAIT FOR FVG TAP --------
    # Guard: OB must exist (FVG is optional after FIX-1)
    if not state.get("ob"):
        SETUP_D_STATE.pop(key, None)
        return None

    z_low, z_high = state["ob"]
    # FIX-1: Use FVG if available, else use OB zone as tap zone
    tap_zone = state["fvg"] if state.get("fvg") else state["ob"]
    f_low, f_high = tap_zone

    if state["stage"] == "WAIT":
        # FIX-2: Re-scan for fresher, closer FVG on each candle
        fresh_fvg = detect_fvg(ltf_data, bias)
        if fresh_fvg:
            ff_low, ff_high = fresh_fvg
            curr_price = candle["close"]
            old_dist = abs(curr_price - (f_low + f_high) / 2)
            new_dist = abs(curr_price - (ff_low + ff_high) / 2)
            if new_dist < old_dist:
                state["fvg"] = fresh_fvg
                state["has_fvg"] = True
                f_low, f_high = fresh_fvg
                tap_zone = fresh_fvg
                logging.info(
                    f"[SETUP-D] {symbol} | FVG RESCAN | upgraded to closer FVG "
                    f"({ff_low:.1f}, {ff_high:.1f}) dist={new_dist:.1f}"
                )

        if (bias == "LONG" and candle["low"] <= f_high) or \
           (bias == "SHORT" and candle["high"] >= f_low):
            state["stage"] = "TAPPED"
            state["tap_count"] = 0
            state["patience_bars"] = 0
        return None

    # -------- PHASE 4: REACTION / ENTRY (3-mode, like Setup-E) --------
    # Three entry modes (priority order):
    #   A) SWEEP: candle wicks beyond OB, closes back inside (highest win-rate)
    #   B) DEEP:  candle reaches deeper 40% of zone + wick rejection
    #   C) DOUBLE TAP: 2nd+ test of zone reaches midpoint + confirmation
    if state["stage"] == "TAPPED":
        zone_mid = (z_low + z_high) / 2
        zone_deep = z_low + (z_high - z_low) * 0.4   # lower 40% line for LONG
        buffer = compute_dynamic_buffer(symbol, atr)
        state["patience_bars"] = state.get("patience_bars", 0) + 1

        if state["patience_bars"] > 20:
            SETUP_D_STATE.pop(key, None)
            return None

        entry = sl = target = None
        entry_mode = None

        if bias == "LONG":
            body = abs(candle["close"] - candle["open"])
            lower_wick = min(candle["open"], candle["close"]) - candle["low"]
            bullish = candle["close"] > candle["open"]
            wick_rej = body > 0 and lower_wick > body * 0.8

            sweep = candle["low"] < z_low and candle["close"] > z_low and bullish
            if sweep:
                entry = candle["close"]
                sl = candle["low"] - atr * 0.1
                entry_mode = "SWEEP"
                logging.info(f"[SETUP-D] {symbol} SWEEP entry | low={candle['low']:.1f} < OB {z_low:.1f}")
            elif candle["low"] <= zone_deep and bullish and wick_rej:
                entry = candle["close"]
                sl = z_low - buffer
                entry_mode = "DEEP"
                logging.info(f"[SETUP-D] {symbol} DEEP entry | low={candle['low']:.1f} <= deep_line {zone_deep:.1f}")
            elif state.get("tap_count", 0) >= 1 and candle["low"] <= zone_mid and bullish:
                entry = candle["close"]
                sl = z_low - buffer
                entry_mode = "DOUBLE_TAP"
                logging.info(f"[SETUP-D] {symbol} DOUBLE_TAP entry | tap#{state['tap_count']+1}")
            else:
                if candle["low"] <= z_high:
                    state["tap_count"] = state.get("tap_count", 0) + 1
                if candle["close"] < z_low - atr * 0.3:
                    SETUP_D_STATE.pop(key, None)
                return None

            if entry and sl:
                target = entry + 2.0 * (entry - sl)

        elif bias == "SHORT":
            body = abs(candle["close"] - candle["open"])
            upper_wick = candle["high"] - max(candle["open"], candle["close"])
            bearish = candle["close"] < candle["open"]
            wick_rej = body > 0 and upper_wick > body * 0.8
            zone_deep_short = z_high - (z_high - z_low) * 0.4

            sweep = candle["high"] > z_high and candle["close"] < z_high and bearish
            if sweep:
                entry = candle["close"]
                sl = candle["high"] + atr * 0.1
                entry_mode = "SWEEP"
                logging.info(f"[SETUP-D] {symbol} SWEEP entry SHORT | high={candle['high']:.1f} > OB {z_high:.1f}")
            elif candle["high"] >= zone_deep_short and bearish and wick_rej:
                entry = candle["close"]
                sl = z_high + buffer
                entry_mode = "DEEP"
                logging.info(f"[SETUP-D] {symbol} DEEP entry SHORT | high={candle['high']:.1f} >= deep_line {zone_deep_short:.1f}")
            elif state.get("tap_count", 0) >= 1 and candle["high"] >= zone_mid and bearish:
                entry = candle["close"]
                sl = z_high + buffer
                entry_mode = "DOUBLE_TAP"
                logging.info(f"[SETUP-D] {symbol} DOUBLE_TAP entry SHORT | tap#{state['tap_count']+1}")
            else:
                if candle["high"] >= z_low:
                    state["tap_count"] = state.get("tap_count", 0) + 1
                if candle["close"] > z_high + atr * 0.3:
                    SETUP_D_STATE.pop(key, None)
                return None

            if entry and sl:
                target = entry - 2.0 * (sl - entry)
        else:
            return None

        if not entry or not sl or not target:
            return None

        rr = abs(target - entry) / abs(entry - sl) if sl != entry else 0
        if rr < 2:
            SETUP_D_STATE.pop(key, None)
            return None

        # Dedup
        dedup_key = f"{clean_symbol(symbol)}_SETUPD_{bias}"
        if already_alerted_today(dedup_key):
            SETUP_D_STATE.pop(key, None)
            return None

        SETUP_D_STATE.pop(key, None)

        # Phase 8 trace — record signal emission
        _trace_append(symbol, {
            "stage"                : "SIGNAL_FIRED",
            "direction"            : bias,
            "entry"                : round(entry, 2),
            "sl"                   : round(sl, 2),
            "target"               : round(target, 2),
            "rr"                   : round(rr, 2),
            "sweep_detected"       : state.get("sweep_detected", False),
            "bos_confirmed"        : state.get("bos_confirmed", False),
            "displacement_detected": state.get("displacement_detected", False),
            "displacement_strength": state.get("displacement_event", {}).get("strength") if state.get("displacement_event") else None,
            "displacement_fvg"     : state.get("displacement_event", {}).get("created_fvg") if state.get("displacement_event") else None,
            "liquidity_event"      : "sweep_present" if state.get("sweep_detected") else "no_sweep",
            "structure_state"      : "TAPPED_ENTRY",
            "zones_detected"       : {"ob": list(state["ob"]) if state.get("ob") else None, "fvg": list(state["fvg"]) if state.get("fvg") else None},
            "signal_fired"         : True,
        })

        return {
            "setup"                : "SETUP-D",
            "symbol"               : symbol,
            "direction"            : bias,
            "entry"                : round(entry, 2),
            "sl"                   : round(sl, 2),
            "target"               : round(target, 2),
            "rr"                   : round(rr, 2),
            "option"               : option_strike(entry, bias),
            "ob"                   : (z_low, z_high),
            "fvg"                  : (f_low, f_high),
            "ltf"                  : ltf_data[-80:],
            "analysis"             : f"CHOCH+BOS+OB+{entry_mode} (Setup-D)",
            "entry_mode"           : entry_mode,
            "bos_confirmed"        : state.get("bos_confirmed", False),
            "sweep_detected"       : state.get("sweep_detected", False),
            "has_fvg"              : state.get("has_fvg", False),
            "choch_time"           : state.get("choch_time"),
            # Phase 2: Displacement fields
            "displacement_detected": state.get("displacement_detected", False),
            "displacement_event"   : state.get("displacement_event"),
        }

    return None


# =====================================================
# SETUP-E: ENHANCED OB REACTION (TWO-TIER CHoCH + WICK ZONES)
# =====================================================

def detect_setup_e(symbol: str, tf_data: dict):
    """
    Setup-E — Enhanced OB detection fixing the 6 gaps from Setup-D:
    1. Two-tier CHoCH: macro HTF trend + micro LTF break (prevents wrong-direction signals)
    2. OB with wick zones and 50-bar lookback (captures more valid OBs)
    3. Reaction entry INSIDE the OB (not breakout above/below)
    4. FVG is optional — OB-only setups valid (FVG adds score, not a gate)
    5. BOS uses swing points (not 6-bar rolling high/low)
    6. Direction flip after SL — can re-enter opposite direction on same symbol
    """
    ltf_data = tf_data.get("5m")
    if not ltf_data:
        return None

    # INDEX ONLY gate (same as Setup-D)
    if not is_index(symbol):
        return None

    htf = tf_data.get("1h", [])
    atr = calculate_atr(ltf_data)
    key = f"{symbol}_SETUP_E"
    state = SETUP_E_STATE.get(key)

    _expiry_secs = 14400 if is_index(symbol) else 7200  # 4h index, 2h stock

    # -------- PHASE 1: DETECT CHoCH (Two-Tier) --------
    if not state:
        # Use the enhanced two-tier CHoCH: macro HTF context + micro LTF break
        choch = smc.detect_choch_setup_e(ltf_data, htf)
        if not choch:
            return None

        direction, idx = choch

        if idx < 10:
            return None

        # Displacement check (same as Setup-D but slightly relaxed)
        disp = ltf_data[idx]
        avg_range = sum(c["high"] - c["low"] for c in ltf_data[max(0, idx - 10):idx]) / max(1, min(10, idx))
        if avg_range > 0 and (disp["high"] - disp["low"]) < avg_range * 1.1:
            return None

        # Liquidity sweep check for scoring
        recent_sweep = liquidity_sweep_detected(ltf_data[:idx + 1], lookback=15)

        choch_break_level = ltf_data[idx]["close"]
        SETUP_E_STATE[key] = {
            "bias"           : direction,
            "stage"          : "BOS_WAIT",
            "choch_level"    : choch_break_level,
            "choch_idx"      : idx,
            "sweep_detected" : bool(recent_sweep),
            "choch_time"     : now_ist(),
            "time"           : now_ist(),
            "ob"             : None,
            "fvg"            : None,
            "has_fvg"        : False,
        }
        logging.info(
            f"[SETUP-E] {symbol} | CHoCH detected | dir={direction} | "
            f"idx={idx} | sweep={recent_sweep}"
        )
        return None

    # -------- DAY BOUNDARY CHECK --------
    state_date = state.get("choch_time", state["time"]).date()
    if state_date < now_ist().date():
        SETUP_E_STATE.pop(key, None)
        return None

    # -------- EXPIRY CHECK --------
    if (now_ist() - state["time"]).total_seconds() > _expiry_secs:
        SETUP_E_STATE.pop(key, None)
        return None

    bias = state["bias"]
    candle = ltf_data[-1]

    # -------- PHASE 2: BOS CONFIRMATION (Swing-based) --------
    if state["stage"] == "BOS_WAIT":
        # BOS = close beyond a recent swing point (not just 6-bar rolling high)
        swing_highs, swing_lows = smc.detect_swing_points(ltf_data[-20:], left=2, right=2)

        bos_confirmed = False
        if bias == "LONG" and swing_highs:
            # Close above the last confirmed swing high
            _, sh_price = swing_highs[-1]
            if candle["close"] > sh_price:
                bos_confirmed = True
        elif bias == "SHORT" and swing_lows:
            _, sl_price = swing_lows[-1]
            if candle["close"] < sl_price:
                bos_confirmed = True

        # Fallback: CHoCH level break (gap days, strong displacement)
        if not bos_confirmed:
            cl = state.get("choch_level", 0)
            if cl > 0:
                if bias == "LONG" and candle["close"] > cl * 1.001:
                    bos_confirmed = True
                elif bias == "SHORT" and candle["close"] < cl * 0.999:
                    bos_confirmed = True

        if bos_confirmed:
            # Enhanced OB: wick zones, 50-bar lookback
            ob = smc.detect_order_block_v2(ltf_data, bias)
            # FVG is OPTIONAL — OB alone is valid
            fvg = detect_fvg(ltf_data, bias)

            if ob:
                state["ob"] = ob
                state["fvg"] = fvg  # may be None
                state["has_fvg"] = fvg is not None
                state["stage"] = "WAIT"
                state["bos_confirmed"] = True
                logging.info(
                    f"[SETUP-E] {symbol} | BOS confirmed | OB={ob} | "
                    f"FVG={'yes' if fvg else 'no'} | dir={bias}"
                )
        return None

    # -------- GUARD: OB must exist --------
    if not state.get("ob"):
        SETUP_E_STATE.pop(key, None)
        return None

    z_low, z_high = state["ob"]

    # FVG zone (optional)
    f_low, f_high = None, None
    if state.get("fvg"):
        f_low, f_high = state["fvg"]

    # -------- PHASE 3: WAIT FOR PRICE TO REACH OB ZONE --------
    if state["stage"] == "WAIT":
        if bias == "LONG" and candle["low"] <= z_high:
            state["stage"] = "TAPPED"
            state["tap_count"] = 0
            state["patience_bars"] = 0
            state["sweep_low"] = None
        elif bias == "SHORT" and candle["high"] >= z_low:
            state["stage"] = "TAPPED"
            state["tap_count"] = 0
            state["patience_bars"] = 0
            state["sweep_high"] = None
        return None

    # -------- PHASE 4: PRODUCTION ENTRY LOGIC --------
    # Three entry modes (priority order):
    #   A) SWEEP: candle wicks below OB, closes back inside (highest win-rate)
    #   B) DEEP:  candle reaches lower 40% of zone + bullish wick rejection
    #   C) DOUBLE TAP: 2nd+ test of zone reaches zone midpoint + confirmation
    # NEVER enter on the first touch at zone top.
    if state["stage"] == "TAPPED":
        zone_mid = (z_low + z_high) / 2
        zone_deep = z_low + (z_high - z_low) * 0.4   # lower 40% line for LONG
        buffer = compute_dynamic_buffer(symbol, atr)
        state["patience_bars"] = state.get("patience_bars", 0) + 1

        # Max patience: 20 bars (100 min on 5m) after first tap
        if state["patience_bars"] > 20:
            SETUP_E_STATE.pop(key, None)
            return None

        entry = sl = target = None
        entry_mode = None

        if bias == "LONG":
            body = abs(candle["close"] - candle["open"])
            lower_wick = min(candle["open"], candle["close"]) - candle["low"]
            bullish = candle["close"] > candle["open"]
            wick_rej = body > 0 and lower_wick > body * 0.8

            # ── A) SWEEP entry: wick below OB low, close back inside ──
            sweep = candle["low"] < z_low and candle["close"] > z_low and bullish
            if sweep:
                entry = candle["close"]
                sl = candle["low"] - atr * 0.1   # tight SL under sweep wick
                state["sweep_low"] = candle["low"]
                entry_mode = "SWEEP"
                logging.info(f"[SETUP-E] {symbol} SWEEP entry | low={candle['low']:.1f} < OB {z_low:.1f}")

            # ── B) DEEP entry: price in lower 40% of zone + wick rejection ──
            elif candle["low"] <= zone_deep and bullish and wick_rej:
                entry = candle["close"]
                sl = z_low - buffer
                entry_mode = "DEEP"
                logging.info(f"[SETUP-E] {symbol} DEEP entry | low={candle['low']:.1f} <= deep_line {zone_deep:.1f}")

            # ── C) DOUBLE TAP: 2nd+ test reaches zone mid + bullish ──
            elif state.get("tap_count", 0) >= 1 and candle["low"] <= zone_mid and bullish:
                entry = candle["close"]
                sl = z_low - buffer
                entry_mode = "DOUBLE_TAP"
                logging.info(f"[SETUP-E] {symbol} DOUBLE_TAP entry | tap#{state['tap_count']+1}")

            else:
                # Track taps but don't enter
                if candle["low"] <= z_high:
                    state["tap_count"] = state.get("tap_count", 0) + 1

                # Invalidation: strong close below OB with margin
                if candle["close"] < z_low - atr * 0.3:
                    SETUP_E_STATE.pop(key, None)
                    _try_direction_flip(symbol, "SHORT", ltf_data, tf_data)
                return None

            if entry and sl:
                target = entry + 2.0 * (entry - sl)

        elif bias == "SHORT":
            body = abs(candle["close"] - candle["open"])
            upper_wick = candle["high"] - max(candle["open"], candle["close"])
            bearish = candle["close"] < candle["open"]
            wick_rej = body > 0 and upper_wick > body * 0.8
            zone_deep_short = z_high - (z_high - z_low) * 0.4  # upper 40% line

            # ── A) SWEEP entry: wick above OB high, close back inside ──
            sweep = candle["high"] > z_high and candle["close"] < z_high and bearish
            if sweep:
                entry = candle["close"]
                sl = candle["high"] + atr * 0.1
                state["sweep_high"] = candle["high"]
                entry_mode = "SWEEP"
                logging.info(f"[SETUP-E] {symbol} SWEEP entry SHORT | high={candle['high']:.1f} > OB {z_high:.1f}")

            # ── B) DEEP entry: price in upper 40% of zone + wick rejection ──
            elif candle["high"] >= zone_deep_short and bearish and wick_rej:
                entry = candle["close"]
                sl = z_high + buffer
                entry_mode = "DEEP"
                logging.info(f"[SETUP-E] {symbol} DEEP entry SHORT | high={candle['high']:.1f} >= deep_line {zone_deep_short:.1f}")

            # ── C) DOUBLE TAP: 2nd+ test reaches zone mid + bearish ──
            elif state.get("tap_count", 0) >= 1 and candle["high"] >= zone_mid and bearish:
                entry = candle["close"]
                sl = z_high + buffer
                entry_mode = "DOUBLE_TAP"
                logging.info(f"[SETUP-E] {symbol} DOUBLE_TAP entry SHORT | tap#{state['tap_count']+1}")

            else:
                if candle["high"] >= z_low:
                    state["tap_count"] = state.get("tap_count", 0) + 1

                if candle["close"] > z_high + atr * 0.3:
                    SETUP_E_STATE.pop(key, None)
                    _try_direction_flip(symbol, "LONG", ltf_data, tf_data)
                return None

            if entry and sl:
                target = entry - 2.0 * (sl - entry)
        else:
            return None

        # ── Common: RR gate, dedup, emit signal ──
        if not entry or not sl or not target:
            return None

        rr = abs(target - entry) / abs(entry - sl) if sl != entry else 0
        if rr < 1.8:
            SETUP_E_STATE.pop(key, None)
            return None

        dedup_key = f"{clean_symbol(symbol)}_SETUPE_{bias}"
        if already_alerted_today(dedup_key):
            SETUP_E_STATE.pop(key, None)
            return None

        SETUP_E_STATE.pop(key, None)

        return {
            "setup"          : "SETUP-E",
            "symbol"         : symbol,
            "direction"      : bias,
            "entry"          : round(entry, 2),
            "sl"             : round(sl, 2),
            "target"         : round(target, 2),
            "rr"             : round(rr, 2),
            "option"         : option_strike(entry, bias),
            "ob"             : (z_low, z_high),
            "fvg"            : (f_low, f_high) if f_low is not None else None,
            "ltf"            : ltf_data[-80:],
            "analysis"       : f"CHoCH(2-tier)+BOS(swing)+OBv2+{entry_mode} (Setup-E)",
            "entry_mode"     : entry_mode,
            "bos_confirmed"  : state.get("bos_confirmed", False),
            "sweep_detected" : state.get("sweep_detected", False),
            "has_fvg"        : state.get("has_fvg", False),
            "choch_time"     : state.get("choch_time"),
        }

    return None


def _try_direction_flip(symbol: str, new_direction: str, ltf_data: list, tf_data: dict):
    """
    After SL/invalidation, check if structure supports the opposite direction.
    If so, seed a fresh SETUP-E state for re-entry (direction flip).
    """
    key = f"{symbol}_SETUP_E"
    if key in SETUP_E_STATE:
        return  # Already has a state, don't overwrite

    htf = tf_data.get("1h", [])
    # Quick validation: does HTF support the new direction?
    htf_bias = detect_htf_bias(htf) if htf else None
    # Block flip if HTF strongly opposes
    if htf_bias == "LONG" and new_direction == "SHORT":
        return
    if htf_bias == "SHORT" and new_direction == "LONG":
        return

    # Check if there's a valid OB in the new direction
    ob = smc.detect_order_block_v2(ltf_data, new_direction)
    if not ob:
        return

    fvg = detect_fvg(ltf_data, new_direction)

    SETUP_E_STATE[key] = {
        "bias"           : new_direction,
        "stage"          : "WAIT",  # Skip BOS since structure just broke
        "choch_level"    : ltf_data[-1]["close"],
        "choch_idx"      : len(ltf_data) - 1,
        "sweep_detected" : False,
        "choch_time"     : now_ist(),
        "time"           : now_ist(),
        "ob"             : ob,
        "fvg"            : fvg,
        "has_fvg"        : fvg is not None,
        "bos_confirmed"  : True,  # Structure break is the OB invalidation itself
        "flipped"        : True,
    }
    logging.info(
        f"[SETUP-E] {symbol} | Direction FLIP → {new_direction} | "
        f"OB={ob} | FVG={'yes' if fvg else 'no'}"
    )


# =====================================================
# MASTER SIGNAL SCANNER
# =====================================================

# =====================================================
# PART 3.5 — CONFLUENCE ENGINE (INTEGRATED)
# =====================================================

# Duplicate calculate_atr removed (uses global definition)

def liquidity_sweep_detected(ltf_data, lookback=50):
    """F2.6: Equal H/L based liquidity sweep detection (delegates to smc_detectors)"""
    return smc.liquidity_sweep_detected(ltf_data, lookback=lookback)


# =====================================================
# TRACE HELPER (Phase 8 — Decision Audit Log)
# =====================================================

def _trace_append(symbol: str, entry: dict) -> None:
    """
    Append a trace entry to SETUP_D_STRUCTURE_TRACE for the given symbol.
    Automatically stamps timestamp and trims to last 50 entries per symbol.
    """
    global SETUP_D_STRUCTURE_TRACE
    entry.setdefault("timestamp", now_ist())
    entry.setdefault("symbol", symbol)
    bucket = SETUP_D_STRUCTURE_TRACE.setdefault(symbol, [])
    bucket.append(entry)
    if len(bucket) > 50:
        SETUP_D_STRUCTURE_TRACE[symbol] = bucket[-50:]

def minor_liquidity(ltf_data):
    """F2.6: Short-range liquidity check"""
    return smc.minor_liquidity(ltf_data)

def near_equilibrium(htf_data, price, tol=0.1):
    """F2.5: Swing-range equilibrium (delegates to smc_detectors)"""
    return smc.near_equilibrium(htf_data, price, tol=tol)

def detect_htf_state(htf_data):
    """F2.3: Reuses swing-based BOS from smc_detectors"""
    result = smc.detect_htf_bias(htf_data)
    return result if result else "RANGE"

def strong_structure_shift(ltf_data, mul=1.2):
    if not ltf_data: return False
    c = ltf_data[-1]
    body = abs(c["close"] - c["open"])
    atr = calculate_atr(ltf_data)
    return body > (atr * mul)

# =====================================================
# STEP 1: SIGNAL FAILURE REASON LOGGER
# =====================================================
# Appends structured rejection records to a daily JSON file.
# Records WHY a signal was NOT taken — critical for debugging missed trades.

_REJECTION_LOG_PATH = Path("signal_rejections_today.json")
_REJECTION_LOG: list = []

def _log_signal_rejection(sig: dict, reason: str, detail: str = "",
                          breakdown: dict = None):
    """Log a structured rejection record for a signal that was NOT taken.
    
    Args:
        sig: The signal dict (from detect_setup_*)
        reason: Short code — LOW_SCORE, RISK_MANAGER, AFTERNOON_CUTOFF, DEDUP, etc.
        detail: Human-readable explanation
        breakdown: The smc_breakdown dict showing which components passed/failed
    """
    try:
        record = {
            "timestamp": now_ist().isoformat(),
            "symbol": sig.get("symbol", "?"),
            "setup": sig.get("setup", "?"),
            "direction": sig.get("direction", "?"),
            "reason": reason,
            "detail": detail,
            "score": sig.get("smc_score"),
            "breakdown": breakdown or sig.get("smc_breakdown"),
            "entry": sig.get("entry"),
            "sl": sig.get("sl"),
            "target": sig.get("target"),
            "rr": sig.get("rr"),
            "ob": str(sig.get("ob")) if sig.get("ob") else None,
            "fvg": str(sig.get("fvg")) if sig.get("fvg") else None,
            "choch": bool(sig.get("choch_time") or sig.get("choch_detected")),
            "bos": bool(sig.get("bos_confirmed")),
            "sweep": bool(sig.get("sweep_detected")),
            "volume_ok": bool(sig.get("volume_expansion")),
            "htf_bias": sig.get("htf_bias"),
            "regime": MARKET_REGIME,
        }
        _REJECTION_LOG.append(record)
        
        # Pretty-print for immediate debugging
        _bd = breakdown or sig.get("smc_breakdown", {})
        _components = " | ".join(
            f"{k}: {'✅' if v and v > 0 else '❌'}{v if v else 0}"
            for k, v in _bd.items()
        ) if _bd else "no breakdown"
        logging.info(
            f"📋 REJECTION [{reason}] {sig.get('symbol','?')} {sig.get('setup','?')} "
            f"{sig.get('direction','?')} — {detail} | {_components}"
        )
        
        # Persist to daily file (append-safe)
        try:
            import json as _json
            _REJECTION_LOG_PATH.write_text(
                _json.dumps(_REJECTION_LOG[-200:], indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass
    except Exception as e:
        logging.debug(f"Rejection logging failed: {e}")


def smc_confluence_score(signal, ltf_data, htf_data):
    score = 0
    breakdown = {}
    
    # 1. Liquidity
    if liquidity_sweep_detected(ltf_data): score += 2; breakdown["liquidity"] = 2
    elif minor_liquidity(ltf_data): score += 1; breakdown["liquidity"] = 1
    else: breakdown["liquidity"] = 0
    
    # 2. Location
    p = signal["entry"]
    d = signal["direction"]
    if d == "LONG":
        if is_discount_zone(htf_data, p): score += 2; breakdown["location"] = 2
        elif near_equilibrium(htf_data, p): score += 1; breakdown["location"] = 1
        else: breakdown["location"] = 0
    elif d == "SHORT":
        if is_premium_zone(htf_data, p): score += 2; breakdown["location"] = 2
        elif near_equilibrium(htf_data, p): score += 1; breakdown["location"] = 1
        else: breakdown["location"] = 0
        
    # 3. HTF Narrative
    htf = detect_htf_state(htf_data)
    if d == htf: score += 2; breakdown["htf"] = 2
    elif htf == "RANGE": score += 1; breakdown["htf"] = 1
    else: breakdown["htf"] = 0
    
    # 4. Structure — P0 FIX: was always giving 1 due to dead code
    if strong_structure_shift(ltf_data): score += 2; breakdown["structure"] = 2
    elif strong_structure_shift(ltf_data, mul=0.6): score += 1; breakdown["structure"] = 1  # Weak but present
    else: breakdown["structure"] = 0  # No structure shift at all
    
    # 5. Execution
    has_ob = bool(signal.get("ob"))
    has_fvg = bool(signal.get("fvg"))
    if has_ob and has_fvg: score += 2; breakdown["execution"] = 2
    elif has_ob or has_fvg: score += 1; breakdown["execution"] = 1
    else: breakdown["execution"] = 0
    
    # Alert if High Quality (PRO Channel)
    if score >= 6:
        try:
            msg = (f"🔥 <b>PRO SMC UPDATE</b>\n"
                   f"Symbol: {signal['symbol']}\n"
                   f"Setup: {signal['setup']} ({signal['direction']})\n"
                   f"Score: <b>{score}/10</b>\n"
                   f"Breakdown: {breakdown}")
            telegram_send(msg, chat_id=SMC_PRO_CHAT_ID)
        except Exception: pass
        
    return score, breakdown


# =====================================================
# Phase 6: SETUP-D SPECIFIC CONFLUENCE SCORING
# =====================================================
def smc_confluence_score_setup_d(signal: dict, ltf_data: list, htf_data: list) -> tuple:
    """
    Phase 6: Upgraded confluence scoring tuned for the Liquidity Sweep → CHoCH → BOS → OB+FVG pipeline.

    New weights (max 10):
      sweep   +2   — liquidity sweep present near CHoCH
      choch   +1   — CHoCH confirmed (always true for Setup-D, but capped at 1)
      bos     +2   — BOS stage completed before FVG wait
      ob      +2   — Order Block present and price tapped it
      fvg     +1   — Fair Value Gap present and tapped
      zone    +1   — price in discount (LONG) or premium (SHORT) zone
      volume  +1   — volume expansion on the displacement candle
    Total max = 10.  Approval threshold: 6.
    """
    score = 0
    breakdown = {}

    direction = signal.get("direction", "")
    entry = signal.get("entry", 0)

    # 0. Displacement (+2) — Phase 2 upgrade: institutional footprint before CHoCH
    disp_event = signal.get("displacement_event")
    if signal.get("displacement_detected") and disp_event is not None:
        score += 2
        breakdown["displacement"] = 2
    elif disp_event is not None:
        score += 1
        breakdown["displacement"] = 1
    elif detect_displacement(ltf_data, near_sweep=bool(signal.get("sweep_detected"))) is not None:
        score += 1
        breakdown["displacement"] = 1
    else:
        breakdown["displacement"] = 0

    # 1. Liquidity Sweep (+2)
    if signal.get("sweep_detected"):
        score += 2
        breakdown["sweep"] = 2
    elif liquidity_sweep_detected(ltf_data):
        score += 1
        breakdown["sweep"] = 1
    else:
        breakdown["sweep"] = 0

    # 2. CHoCH confirmed (+1) — always 1 for Setup-D (CHoCH is entry gate)
    score += 1
    breakdown["choch"] = 1

    # 3. BOS confirmed (+2)
    if signal.get("bos_confirmed"):
        score += 2
        breakdown["bos"] = 2
    else:
        breakdown["bos"] = 0

    # 4. Order Block (+2)
    has_ob = bool(signal.get("ob"))
    if has_ob:
        score += 2
        breakdown["ob"] = 2
    else:
        breakdown["ob"] = 0

    # 5. Fair Value Gap (+1)
    has_fvg = bool(signal.get("fvg"))
    if has_fvg:
        score += 1
        breakdown["fvg"] = 1
    else:
        breakdown["fvg"] = 0

    # 6. Zone location (+1) — discount for LONG, premium for SHORT
    if direction == "LONG":
        if is_discount_zone(htf_data, entry):
            score += 1
            breakdown["zone"] = 1
        else:
            breakdown["zone"] = 0
    elif direction == "SHORT":
        if is_premium_zone(htf_data, entry):
            score += 1
            breakdown["zone"] = 1
        else:
            breakdown["zone"] = 0
    else:
        breakdown["zone"] = 0

    # 7. Volume expansion on recent candles (+1)
    if volume_expansion(ltf_data):
        score += 1
        breakdown["volume"] = 1
    else:
        breakdown["volume"] = 0

    # PRO alert if high-quality
    if score >= 6:
        try:
            msg = (f"🔥 <b>PRO SMC UPDATE (Setup-D)</b>\n"
                   f"Symbol: {signal['symbol']}\n"
                   f"Setup: {signal['setup']} ({direction})\n"
                   f"Score: <b>{score}/10</b>\n"
                   f"Breakdown: {breakdown}\n"
                   f"Sweep: {'✅' if signal.get('sweep_detected') else '❌'} | "
                   f"BOS: {'✅' if signal.get('bos_confirmed') else '❌'}")
            telegram_send(msg, chat_id=SMC_PRO_CHAT_ID)
        except Exception:
            pass

    return score, breakdown


def smc_confluence_score_setup_e(signal: dict, ltf_data: list, htf_data: list) -> tuple:
    """
    Confluence scoring for Setup-E (Enhanced OB Reaction).

    Weights (max 10):
      choch_2tier  +2  — two-tier CHoCH (macro HTF context validated)
      bos_swing    +2  — BOS confirmed via swing point break
      ob_v2        +2  — Order Block v2 (wick zones, 50-bar lookback)
      fvg          +1  — Fair Value Gap present (BONUS, not required)
      sweep        +1  — Liquidity sweep detected near CHoCH
      reaction     +1  — Reaction entry quality (wick rejection, inside zone)
      zone         +1  — Price in discount (LONG) or premium (SHORT)
    Total max = 10.  Approval threshold: 5.
    """
    score = 0
    breakdown = {}
    direction = signal.get("direction", "")
    entry = signal.get("entry", 0)

    # 1. Two-tier CHoCH (+2) — always present for Setup-E
    score += 2
    breakdown["choch_2tier"] = 2

    # 2. BOS confirmed (+2)
    if signal.get("bos_confirmed"):
        score += 2
        breakdown["bos_swing"] = 2
    else:
        breakdown["bos_swing"] = 0

    # 3. Order Block v2 (+2) — always present (entry gate)
    if signal.get("ob"):
        score += 2
        breakdown["ob_v2"] = 2
    else:
        breakdown["ob_v2"] = 0

    # 4. FVG (+1) — bonus, not a gate
    if signal.get("has_fvg") or signal.get("fvg"):
        score += 1
        breakdown["fvg"] = 1
    else:
        breakdown["fvg"] = 0

    # 5. Liquidity Sweep (+1) — also awards for SWEEP entry mode
    entry_mode = signal.get("entry_mode", "")
    if entry_mode == "SWEEP" or signal.get("sweep_detected"):
        score += 1
        breakdown["sweep"] = 1
    elif liquidity_sweep_detected(ltf_data):
        score += 1
        breakdown["sweep"] = 1
    else:
        breakdown["sweep"] = 0

    # 6. Entry quality (+1) — SWEEP/DEEP modes are higher quality than DOUBLE_TAP
    if entry_mode in ("SWEEP", "DEEP"):
        score += 1
        breakdown["reaction"] = 1
    elif ltf_data:
        c = ltf_data[-1]
        body = abs(c["close"] - c["open"])
        lower_wick = min(c["open"], c["close"]) - c["low"]
        upper_wick = c["high"] - max(c["open"], c["close"])
        if direction == "LONG" and body > 0 and lower_wick > body * 0.6:
            score += 1
            breakdown["reaction"] = 1
        elif direction == "SHORT" and body > 0 and upper_wick > body * 0.6:
            score += 1
            breakdown["reaction"] = 1
        else:
            breakdown["reaction"] = 0
    else:
        breakdown["reaction"] = 0

    # 7. Zone location (+1) — discount for LONG, premium for SHORT
    if direction == "LONG":
        if is_discount_zone(htf_data, entry):
            score += 1
            breakdown["zone"] = 1
        else:
            breakdown["zone"] = 0
    elif direction == "SHORT":
        if is_premium_zone(htf_data, entry):
            score += 1
            breakdown["zone"] = 1
        else:
            breakdown["zone"] = 0
    else:
        breakdown["zone"] = 0

    return score, breakdown


# =====================================================
# MANUAL TRADE INTEGRATION
# =====================================================
def fetch_manual_orders():
    """
    Syncs completed manual orders from Kite and adopts them into the engine.
    """
    global ACTIVE_TRADES, MANUAL_ORDER_CACHE
    
    try:
        orders = kite.orders()
        if not orders: return

        for o in orders:
            oid = o["order_id"]
            if o["status"] != "COMPLETE": continue
            if oid in MANUAL_ORDER_CACHE: continue
            
            symbol = o["tradingsymbol"]
            
            # 1. Add to cache
            MANUAL_ORDER_CACHE.add(oid)
            
            # 2. Dedup against active trades
            with ACTIVE_TRADES_LOCK:
                if any(t["symbol"] == symbol for t in ACTIVE_TRADES):
                    continue
                
            # 3. Adopt
            direction = "LONG" if o["transaction_type"] == "BUY" else "SHORT"
            entry_price = o["average_price"]
            
            # 4. Auto-Pilot SL/TP (ATR Based)
            token = o["instrument_token"]
            
            try:
                # Fetch 2 days of 5m data for ATR
                candles = kite.historical_data(
                    token, 
                    now_ist() - timedelta(days=2), 
                    now_ist(), 
                    "5minute"
                )
                atr = calculate_atr(candles)
                if atr == 0: atr = entry_price * 0.01
            except Exception:
                atr = entry_price * 0.01
            
            # SL = 0.5 ATR, Target = 1.0 ATR (Conservative for manual)
            sl_buffer = atr * 0.5
            target_buffer = atr * 1.0 
            
            if direction == "LONG":
                sl = entry_price - sl_buffer
                target = entry_price + target_buffer
            else:
                sl = entry_price + sl_buffer
                target = entry_price - target_buffer
                
            trade = {
                "symbol": symbol,
                "setup": "MANUAL",
                "direction": direction,
                "entry": entry_price,
                "sl": round(sl, 2),
                "target": round(target, 2),
                "rr": 2.0,
                "risk_mult": 1.0,
                "start_time": now_ist(),
                "order_id": oid
            }
            
            with ACTIVE_TRADES_LOCK:
                ACTIVE_TRADES.append(trade)
            persist_active_trades()  # F1.2: crash recovery
            try:
                with ACTIVE_TRADES_LOCK:
                    update_engine_state(trades=list(ACTIVE_TRADES))
                print("API update:", get_engine_state_snapshot())
            except Exception:
                pass
            
            msg = (f"✋ <b>MANUAL TRADE DETECTED</b>\n"
                   f"Symbol: {symbol}\n"
                   f"Dir: {direction} @ {entry_price}\n"
                   f"Auto-SL: {round(sl, 2)}\n"
                   f"Auto-Tgt: {round(target, 2)}\n"
                   f"<i>Engine is now managing this trade.</i>")
            _sym = (symbol or "").replace(" ", "_").replace(":", "_").strip("_") or "unknown"
            signal_id = f"manual_0_{_sym}_{now_ist().timestamp():.0f}"
            telegram_send_signal(
                msg,
                signal_id=signal_id,
                signal_meta={
                    "signal_kind": "MANUAL_DETECT",
                    "symbol": symbol,
                    "direction": direction,
                    "strategy_name": "MANUAL",
                    "entry": entry_price,
                    "stop_loss": round(sl, 2),
                    "target1": round(target, 2),
                },
            )
            
    except Exception as e:
        print(f"Manual Sync Error: {e}")

def scan_symbol(symbol: str):
    signals = []
    
    if DEBUG_MODE:
        logging.debug(f"scanning {symbol}...")

    # ==========================================
    # 🔄 FETCH ALL DATA ONCE PER SYMBOL
    # ==========================================
    tf_data = fetch_multitf(symbol)
    if not tf_data:
        return []

    # 1. SETUP A
    if ACTIVE_STRATEGIES.get("SETUP_A"):
        try:
            s = detect_setup_a(symbol, tf_data)
            if s: 
                signals.append(s)
                logging.info(f"✅ {symbol} FOUND SETUP A")
            else:
                if DEBUG_MODE: logging.debug(f"{symbol} Setup A -> No Signal")
        except Exception as e:
            logging.error(f"Setup A Error {symbol}: {e}")
            if DEBUG_MODE: print(f"Setup A Error {symbol}: {e}")

    # 2. SETUP B
    if ACTIVE_STRATEGIES.get("SETUP_B"):
        try:
            sb = detect_setup_b(symbol, tf_data)
            if sb: 
                signals.extend(sb)
                logging.info(f"✅ {symbol} FOUND SETUP B ({len(sb)})")
            else:
                if DEBUG_MODE: logging.debug(f"{symbol} Setup B -> No Signal")
        except Exception as e:
            logging.error(f"Setup B Error {symbol}: {e}")
            if DEBUG_MODE: print(f"Setup B Error {symbol}: {e}")

    # 3. SETUP C (Universal)
    if ACTIVE_STRATEGIES.get("SETUP_C"):
        try:
            ltf = tf_data.get("5m")
            
            # 🛑 ATR FILTER FOR NIFTY/FIN
            if "NIFTY 50" in symbol or "FIN SERVICE" in symbol:
                 # Stricter for FIN (18) vs NIFTY (12)
                 threshold = 18 if "FIN SERVICE" in symbol else 12
                 if not index_atr_filter(ltf, min_atr=threshold):
                     if DEBUG_MODE: logging.debug(f"Skipping {symbol} due to Low ATR (Needs {threshold})")
                 else:
                     sc = detect_setup_c(symbol, tf_data)
                     if sc: 
                         signals.extend(sc)
                         logging.info(f"✅ {symbol} FOUND SETUP C ({len(sc)})")
                     else:
                         if DEBUG_MODE: logging.debug(f"{symbol} Setup C -> No Signal")
            else:
                 sc = detect_setup_c(symbol, tf_data)
                 if sc: 
                     signals.extend(sc)
                     logging.info(f"✅ {symbol} FOUND SETUP C ({len(sc)})")
                 
        except Exception as e:
            logging.error(f"Setup C Error {symbol}: {e}")
            if DEBUG_MODE: print(f"Setup C Error {symbol}: {e}")

    # 4. SETUP D  (Phase 1 gate: index instruments only)
    if ACTIVE_STRATEGIES.get("SETUP_D"):
        try:
            # Phase 8: explicit index gate in scan_symbol (belt-and-suspenders)
            if not is_index(symbol):
                if DEBUG_MODE: logging.debug(f"{symbol} Setup D → Skipped (not an index)")
            else:
                sd = detect_setup_d(symbol, tf_data)
                if sd:
                    signals.append(sd)
                    logging.info(f"✅ {symbol} FOUND SETUP D")
                else:
                    if DEBUG_MODE: logging.debug(f"{symbol} Setup D -> No Signal")
        except Exception as e:
            logging.error(f"Setup D Error {symbol}: {e}")
            if DEBUG_MODE: print(f"Setup D Error {symbol}: {e}")

    # 5. SETUP E  (Enhanced OB — two-tier CHoCH, wick zones, reaction entry)
    if ACTIVE_STRATEGIES.get("SETUP_E"):
        try:
            if not is_index(symbol):
                if DEBUG_MODE: logging.debug(f"{symbol} Setup E → Skipped (not an index)")
            else:
                se = detect_setup_e(symbol, tf_data)
                if se:
                    signals.append(se)
                    logging.info(f"✅ {symbol} FOUND SETUP E")
                else:
                    if DEBUG_MODE: logging.debug(f"{symbol} Setup E -> No Signal")
        except Exception as e:
            logging.error(f"Setup E Error {symbol}: {e}")
            if DEBUG_MODE: print(f"Setup E Error {symbol}: {e}")

    # 6. HIERARCHICAL (V3 Strict)
    if ACTIVE_STRATEGIES.get("HIERARCHICAL"):
        try:
            sh = detect_hierarchical(symbol)
            if sh: 
                signals.append(sh)
                logging.info(f"✅ {symbol} FOUND HIERARCHICAL")
            else:
                if DEBUG_MODE: logging.debug(f"{symbol} Hierarchical -> No Signal")
        except Exception as e:
            logging.error(f"Hierarchical Logic Error {symbol}: {e}")
            if DEBUG_MODE: print(f"Hierarchical Logic Error {symbol}: {e}")

    # ----------------------------------------------------
    # SMC CONFLUENCE SCORING & SIZING (PRESERVED)
    # ----------------------------------------------------
    if not signals:
         logging.debug(f"[scan_symbol] {symbol}: all setups returned None/empty")
         return []
    logging.info(f"[scan_symbol] {symbol}: {len(signals)} raw signal(s) -> {[s.get('setup','?') for s in signals]}")

    # Fetch Data for Scoring (Use tf_data)
    ltf_data = tf_data.get("5m")
    htf_data = tf_data.get("1h")

    valid_signals = []
    
    for sig in signals:
        # HANDLE PRE-ENTRY SIGNALS (Setup A Structure/Tap)
        if "entry" not in sig:
            if DEBUG_MODE:
                logging.info(f"⏳ {sig['symbol']} {sig.get('setup')} - Waiting for Entry Trigger")
            continue

        # Score the setup
        try:
            # Phase 8: Route Setup-D signals through the v2 scorer
            setup_name = sig.get("setup", "")
            if setup_name == "SETUP-D":
                score, breakdown = smc_confluence_score_setup_d(sig, ltf_data, htf_data)
            elif setup_name == "SETUP-E":
                score, breakdown = smc_confluence_score_setup_e(sig, ltf_data, htf_data)
            else:
                score, breakdown = smc_confluence_score(sig, ltf_data, htf_data)
            sig["smc_score"] = score
            sig["smc_breakdown"] = breakdown
            sig['risk_mult'] = 1.0

            # Phase 8: Append trace entry to SETUP_D_STRUCTURE_TRACE
            if setup_name == "SETUP-D":
                _sym = sig.get('symbol', 'UNKNOWN')
                _trace_entry = {
                    "timestamp": now_ist().isoformat(),
                    "symbol": _sym,
                    "direction": sig.get("direction"),
                    "entry": sig.get("entry"),
                    "sl": sig.get("sl"),
                    "target": sig.get("target"),
                    "rr": sig.get("rr"),
                    "choch_time": sig.get("choch_time").isoformat() if sig.get("choch_time") else None,
                    "bos_confirmed": sig.get("bos_confirmed", False),
                    "sweep_detected": sig.get("sweep_detected", False),
                    "ob": list(sig["ob"]) if sig.get("ob") else None,
                    "fvg": list(sig["fvg"]) if sig.get("fvg") else None,
                    "score": score,
                    "score_breakdown": breakdown,
                    "signal_fired": False,  # updated below
                    "block_reason": None,
                }
                if _sym not in SETUP_D_STRUCTURE_TRACE:
                    SETUP_D_STRUCTURE_TRACE[_sym] = []
                SETUP_D_STRUCTURE_TRACE[_sym].append(_trace_entry)
                # Circular buffer: keep last 50 per symbol
                if len(SETUP_D_STRUCTURE_TRACE[_sym]) > 50:
                    SETUP_D_STRUCTURE_TRACE[_sym] = SETUP_D_STRUCTURE_TRACE[_sym][-50:]

            # Default minimum confluence (env override). Lower = more signals.
            min_score_for_signal = int(os.getenv("SMC_MIN_SCORE_FOR_SIGNAL", "4"))

            # INDEX RELAXATION: Indices (NIFTY/BANKNIFTY) have inherent liquidity
            # and reliability — lower min score to 3 so trending days aren't missed.
            # Feb 27 analysis: All SHORT signals were correct but blocked at score 3-4.
            _sym = sig.get('symbol', '')
            _is_pure_index = ('NIFTY 50' in _sym or 'NIFTY BANK' in _sym)
            if _is_pure_index:
                min_score_for_signal = 3

            # SETUP-D: allow 2–3 legs to pass (see Phase 6 breakdown); default threshold 4
            if setup_name == "SETUP-D":
                min_score_for_signal = int(os.getenv("SETUP_D_MIN_CONFLUENCE_SCORE", "4"))

            # SETUP-E QUALITY FILTER: reaction entry has better win rate, threshold = 5
            # choch_2tier(2)+bos(2)+ob(2)+reaction(1) = 7 max; allow 5 to pass OB-only setups
            if setup_name == "SETUP-E":
                min_score_for_signal = int(os.getenv("SETUP_E_MIN_CONFLUENCE_SCORE", "5"))

            # SETUP-A QUALITY FILTER: Backtest shows Score 5-6 has negative expectancy
            # Score 5: WR=14.3%, E=-0.450R  |  Score 6: WR=38.1%, E=-0.143R
            # Score 7: WR=62.5%, E=+0.802R  |  Score 8: WR=71.4%, E=+1.397R
            # → Require score >= 7 for Setup A (cuts 28 losing trades, keeps 15 winners)
            # BUT: For indices, relax to 5 (they are inherently more reliable)
            if setup_name.startswith("A-") or setup_name == "SETUP-A" or setup_name == "HIERARCHICAL":
                if _is_pure_index:
                    min_score_for_signal = 5  # Relaxed for indices
                    logging.info(f"📊 Setup A INDEX filter: requiring score >= 5 for {sig['symbol']}")
                else:
                    min_score_for_signal = int(os.getenv("SETUP_A_MIN_CONFLUENCE_SCORE", "6"))
                    logging.info(
                        f"📊 Setup A quality filter: requiring score >= {min_score_for_signal} for {sig['symbol']}"
                    )

            if sig.get("_require_high_confluence"):
                if _is_pure_index:
                    min_score_for_signal = max(min_score_for_signal, 5)  # Still relaxed for index
                else:
                    min_score_for_signal = 7
                logging.info(f"⚠️ HTF UNCERTAIN for {sig['symbol']} — requiring score >= {min_score_for_signal}")

            # LOGIC: 
            # Score >= max(6, min_score) -> Full Size
            # Score >= min_score and score >= 3 -> Half Size for indices, or score >= 5 for stocks
            # Score < min_score -> IGNORE
            
            if score >= max(6, min_score_for_signal):
                sig['risk_mult'] = 1.0
                valid_signals.append(sig)
                # Phase 8: mark signal as fired in trace
                if setup_name == "SETUP-D" and SETUP_D_STRUCTURE_TRACE.get(sig.get('symbol', '')):
                    SETUP_D_STRUCTURE_TRACE[sig['symbol']][-1]["signal_fired"] = True
                logging.info(f"🚀 SIGNAL APPROVED: {sig['symbol']} {sig['setup']} Score={score}")
            elif score >= min_score_for_signal:
                # Index signals at score 3-5 pass at half size
                # Stock signals at score 5 pass at half size (existing behavior)
                if "FIN SERVICE" in sig['symbol'] and score < 6:
                    logging.info(f"🚫 BLOCKED (Score {score} on FIN): {sig['symbol']}")
                    if setup_name == "SETUP-D" and SETUP_D_STRUCTURE_TRACE.get(sig.get('symbol', '')):
                        SETUP_D_STRUCTURE_TRACE[sig['symbol']][-1]["block_reason"] = f"FIN score {score} < 6"
                else:
                    sig['risk_mult'] = 0.5
                    valid_signals.append(sig)
                    if setup_name == "SETUP-D" and SETUP_D_STRUCTURE_TRACE.get(sig.get('symbol', '')):
                        SETUP_D_STRUCTURE_TRACE[sig['symbol']][-1]["signal_fired"] = True
                    logging.info(f"⚠️ SIGNAL HALF-SIZE: {sig['symbol']} {sig['setup']} Score={score} (min={min_score_for_signal})")
            else:
                logging.info(f"🚫 BLOCKED LOW SCORE ({score}) [min={min_score_for_signal}]: {sig['symbol']} {sig['setup']}")
                if setup_name == "SETUP-D" and SETUP_D_STRUCTURE_TRACE.get(sig.get('symbol', '')):
                    SETUP_D_STRUCTURE_TRACE[sig['symbol']][-1]["block_reason"] = f"Score {score} < min {min_score_for_signal}"
                # ── STEP 1: Structured failure reason logging ──
                _log_signal_rejection(
                    sig, reason="LOW_SCORE",
                    detail=f"score={score} < min={min_score_for_signal}",
                    breakdown=breakdown,
                )
        except Exception as e:
            # W3 FIX: On scoring error, SKIP signal (don't pass unscored trades)
            logging.error(f"Scoring Error — SKIPPING {sig.get('symbol','?')}: {e}")
            _log_signal_rejection(
                sig, reason="SCORING_ERROR", detail=str(e),
            )
            continue  # W3: Do NOT pass unscored signals through

    return valid_signals

# =====================================================
# MARKET REGIME FILTER (STEP 2 — Suppress Counter-Trend)
# =====================================================

MARKET_REGIME = "NEUTRAL"  # Global: BULLISH / BEARISH / NEUTRAL
REGIME_LAST_UPDATE = None  # Track when regime was last computed

def detect_market_regime():
    """
    Determines overall market regime by analyzing NIFTY 50 and BANK NIFTY.
    Uses 4 signals:
      1. Session VWAP: Price vs VWAP (above = bullish, below = bearish)
      2. Opening Range: First 15-min candle direction
      3. 15m Structure: HTF bias on 15-minute chart
      4. OI Sentiment: PCR, OI change patterns, buildup/unwinding
    
    Returns: "BULLISH", "BEARISH", or "NEUTRAL"
    """
    global MARKET_REGIME, REGIME_LAST_UPDATE
    
    now = now_ist()
    
    # Refresh every 2 minutes (aligned with OI sentiment refresh)
    if REGIME_LAST_UPDATE and (now - REGIME_LAST_UPDATE).total_seconds() < 120:
        return MARKET_REGIME
    
    bull_score = 0
    bear_score = 0
    
    for index_sym in ["NSE:NIFTY 50", "NSE:NIFTY BANK"]:
        try:
            data_15m = fetch_ohlc(index_sym, "15minute", lookback=50)
            data_5m = fetch_ohlc(index_sym, "5minute", lookback=80)
            
            if not data_15m or len(data_15m) < 10:
                continue
            if not data_5m or len(data_5m) < 20:
                continue
            
            current_price = data_5m[-1]["close"]
            
            # --- Signal 1: Session VWAP Position ---
            # Approximate VWAP using today's candles (volume-weighted average)
            today_candles = [c for c in data_5m if c["date"].date() == now.date()]
            if today_candles:
                total_vwap_num = sum(c["close"] * c["volume"] for c in today_candles)
                total_volume = sum(c["volume"] for c in today_candles)
                if total_volume > 0:
                    vwap = total_vwap_num / total_volume
                    if current_price > vwap * 1.001:  # 0.1% above
                        bull_score += 1
                    elif current_price < vwap * 0.999:  # 0.1% below
                        bear_score += 1
            
            # --- Signal 2: Opening Range Direction ---
            # First 15-minute candle of the day determines opening bias
            today_15m = [c for c in data_15m if c["date"].date() == now.date()]
            if today_15m:
                opening_candle = today_15m[0]
                if opening_candle["close"] > opening_candle["open"]:
                    bull_score += 1  # Green opening = bullish
                elif opening_candle["close"] < opening_candle["open"]:
                    bear_score += 1  # Red opening = bearish
                
                # Check if current price is below opening low (strong bearish)
                if current_price < opening_candle["low"]:
                    bear_score += 1
                elif current_price > opening_candle["high"]:
                    bull_score += 1
            
            # --- Signal 3: 15m HTF Structure ---
            bias_15m = detect_htf_bias(data_15m)
            if bias_15m == "LONG":
                bull_score += 1
            elif bias_15m == "SHORT":
                bear_score += 1
                
        except Exception as e:
            if DEBUG_MODE:
                logging.warning(f"Regime scan error for {index_sym}: {e}")
            continue
    
    # --- Signal 4: OI Sentiment (PCR + OI Change + Buildup Pattern) ---
    try:
        oi_state = update_oi_sentiment(kite, fetch_ohlc_fn=fetch_ohlc)
        oi_bull, oi_bear = get_oi_scores()
        bull_score += oi_bull
        bear_score += oi_bear
        
        if oi_bull > 0 or oi_bear > 0:
            logging.info(
                f"📊 OI → Regime: Bull+{oi_bull} Bear+{oi_bear} | "
                f"{oi_state.get('details', '')}"
            )
    except Exception as e:
        if DEBUG_MODE:
            logging.warning(f"OI Sentiment error in regime: {e}")
    
    # --- Signal 5: Market State Engine (CHoCH, BOS, displacement, liquidity sweep) ---
    try:
        ms = update_market_state(
            fetch_ohlc_fn=fetch_ohlc,
            kite_obj=kite,
            oi_state=oi_state if 'oi_state' in dir() else None,
        )
        ms_bull = ms.get("score_breakdown", {}).get("bull_score", 0)
        ms_bear = ms.get("score_breakdown", {}).get("bear_score", 0)
        ms_state = ms.get("state", "RANGE")
        
        # Scale market state contribution: use net directional bias (capped)
        ms_net_bull = min(3, max(0, ms_bull - ms_bear))
        ms_net_bear = min(3, max(0, ms_bear - ms_bull))
        bull_score += ms_net_bull
        bear_score += ms_net_bear
        
        if ms_net_bull > 0 or ms_net_bear > 0:
            events_str = ", ".join(e["type"] for e in ms.get("events", [])[:5])
            logging.info(
                f"📊 MarketState → Regime: {ms_state} Bull+{ms_net_bull} Bear+{ms_net_bear} | "
                f"Events: {events_str}"
            )
    except Exception as e:
        if DEBUG_MODE:
            logging.warning(f"Market state engine error in regime: {e}")
    
    # Determine Regime (thresholds adjusted for 5 signal sources)
    # Max possible: 3 price signals × 2 indices = 6, OI up to ~5, market state up to ~3
    # Total max: ~14. Use proportional thresholds.
    total_signals = bull_score + bear_score
    
    if total_signals == 0:
        MARKET_REGIME = "NEUTRAL"
    elif bull_score >= 6 and bear_score <= 3:
        MARKET_REGIME = "BULLISH"
    elif bear_score >= 6 and bull_score <= 3:
        MARKET_REGIME = "BEARISH"
    elif bull_score >= 5 and bear_score <= 2:
        MARKET_REGIME = "BULLISH"
    elif bear_score >= 5 and bull_score <= 2:
        MARKET_REGIME = "BEARISH"
    elif bull_score >= 4 and bull_score > bear_score + 2:
        MARKET_REGIME = "BULLISH"
    elif bear_score >= 4 and bear_score > bull_score + 2:
        MARKET_REGIME = "BEARISH"
    else:
        MARKET_REGIME = "NEUTRAL"
    
    REGIME_LAST_UPDATE = now
    logging.info(f"📊 MARKET REGIME: {MARKET_REGIME} (Bull={bull_score}, Bear={bear_score})")
    
    return MARKET_REGIME


def should_suppress_signal(signal):
    """
    Returns True if signal should be BLOCKED based on market regime.
    
    Rules:
    - BEARISH regime → Block all STOCK LONGs (indices exempt)
    - BULLISH regime → Block all STOCK SHORTs (indices exempt)
    - NEUTRAL → Allow everything
    - Index signals are NEVER suppressed by regime
    """
    if is_index(signal.get("symbol", "")):
        return False  # Indices always trade
    
    direction = signal.get("direction", "")
    
    if MARKET_REGIME == "BEARISH" and direction == "LONG":
        # Phase 5: Allow LONGs if market state detects bullish reversal
        ms_label = get_market_state_label()
        if ms_label == "BULLISH_REVERSAL":
            logging.info(
                f"⚡ Allowing LONG despite BEARISH regime — "
                f"Market State = BULLISH_REVERSAL for {signal.get('symbol', '?')}"
            )
            return False  # Don't suppress
        return True
    if MARKET_REGIME == "BULLISH" and direction == "SHORT":
        ms_label = get_market_state_label()
        if ms_label == "BEARISH_REVERSAL":
            logging.info(
                f"⚡ Allowing SHORT despite BULLISH regime — "
                f"Market State = BEARISH_REVERSAL for {signal.get('symbol', '?')}"
            )
            return False
        return True
    
    return False

# =====================================================
# PART 3.7 — INTELLIGENCE LAYER (E1, E2, E6)
# =====================================================

# --- E1: VOLATILITY REGIME FILTER (ATR PERCENTILE RANK) ---
VOLATILITY_REGIME = "NORMAL"  # LOW / NORMAL / HIGH
VOL_REGIME_CACHE = {"regime": "NORMAL", "updated": None}

def detect_volatility_regime(symbol="NSE:NIFTY 50"):
    """
    E1: Classifies market into LOW/NORMAL/HIGH volatility using
    ATR percentile rank over 20 days on daily timeframe.
    
    LOW: ATR < 30th percentile → choppy, avoid trading
    NORMAL: 30th-70th percentile → ideal for OB/FVG setups
    HIGH: > 70th percentile → widen SL, reduce size
    """
    global VOLATILITY_REGIME, VOL_REGIME_CACHE
    
    # Cache for 30 minutes
    if VOL_REGIME_CACHE["updated"]:
        elapsed = (now_ist() - VOL_REGIME_CACHE["updated"]).total_seconds()
        if elapsed < 1800:
            return VOL_REGIME_CACHE["regime"]
    
    try:
        daily = fetch_ohlc(symbol, "day", lookback=50)
        if not daily or len(daily) < 25:
            return "NORMAL"
        
        # Calculate rolling 14-period ATR for last 20 days
        atr_values = []
        for i in range(14, len(daily)):
            trs = []
            for j in range(i-13, i+1):
                h = daily[j]["high"]
                l = daily[j]["low"]
                pc = daily[j-1]["close"]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            atr_values.append(sum(trs) / 14)
        
        if len(atr_values) < 5:
            return "NORMAL"
        
        current_atr = atr_values[-1]
        sorted_atrs = sorted(atr_values)
        rank = sorted_atrs.index(current_atr) / len(sorted_atrs) * 100 if current_atr in sorted_atrs else 50
        
        if rank < 30:
            regime = "LOW"
        elif rank > 70:
            regime = "HIGH"
        else:
            regime = "NORMAL"
        
        VOLATILITY_REGIME = regime
        VOL_REGIME_CACHE = {"regime": regime, "updated": now_ist()}
        logging.info(f"📊 VOLATILITY REGIME: {regime} (ATR Rank={rank:.0f}%, Current ATR={current_atr:.1f})")
        return regime
        
    except Exception as e:
        logging.error(f"Volatility regime error: {e}")
        return "NORMAL"

def volatility_score_adjustment(score, vol_regime):
    """
    Adjusts confluence score based on volatility regime.
    NORMAL: +1 bonus (ideal conditions)
    HIGH: -1 penalty but widen SL (compensated in SL logic)
    LOW: -2 penalty (avoid trading in chop)
    """
    if vol_regime == "NORMAL":
        return score + 1
    elif vol_regime == "HIGH":
        return score - 1
    elif vol_regime == "LOW":
        return score - 2
    return score


# --- E2: OPTION CHAIN INTEGRATION (PCR + MAX PAIN) ---
OPTION_CHAIN_DATA = {"pcr": None, "max_pain": None, "updated": None}

def fetch_option_chain_data(index_symbol="NSE:NIFTY 50"):
    """
    E2: Reads option chain data for Put-Call Ratio and Max Pain.
    Uses Kite API to get OI data for current weekly expiry.
    
    PCR > 1.2 → Bullish support (puts sold = floor)
    PCR < 0.7 → Bearish pressure (calls sold = ceiling) 
    Max Pain → Strike with most OI (acts as price magnet)
    """
    global OPTION_CHAIN_DATA
    
    # Cache for 30 minutes
    if OPTION_CHAIN_DATA["updated"]:
        elapsed = (now_ist() - OPTION_CHAIN_DATA["updated"]).total_seconds()
        if elapsed < 1800:
            return OPTION_CHAIN_DATA
    
    try:
        # Get current LTP for ATM reference
        ltp_data = fetch_ltp(index_symbol)
        if not ltp_data:
            return OPTION_CHAIN_DATA
        
        spot = ltp_data
        atm_strike = round(spot / 50) * 50  # Round to nearest 50
        
        # Scan ±10 strikes for OI
        total_call_oi = 0
        total_put_oi = 0
        strike_pain = {}  # {strike: total_pain}
        
        # Determine index prefix for option instruments
        if "BANK" in index_symbol.upper():
            prefix = "NFO:BANKNIFTY"
            step = 100
        else:
            prefix = "NFO:NIFTY"
            step = 50
        
        atm_strike = round(spot / step) * step
        
        for offset in range(-10, 11):
            strike = atm_strike + offset * step
            try:
                # Try to get CE and PE OI via positions/instruments
                ce_sym = f"{prefix}{strike}CE"
                pe_sym = f"{prefix}{strike}PE"
                
                # Use kite.ltp which includes OI for options
                ce_data = kite.ltp([ce_sym])
                pe_data = kite.ltp([pe_sym])
                
                ce_oi = ce_data.get(ce_sym, {}).get("oi", 0) if ce_data else 0
                pe_oi = pe_data.get(pe_sym, {}).get("oi", 0) if pe_data else 0
                
                total_call_oi += ce_oi
                total_put_oi += pe_oi
                
                # Max Pain calculation: total intrinsic value loss at each strike
                ce_pain = max(0, spot - strike) * ce_oi
                pe_pain = max(0, strike - spot) * pe_oi
                strike_pain[strike] = ce_pain + pe_pain
                
            except Exception:
                continue
        
        # Calculate PCR
        pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
        
        # Find Max Pain (strike with MINIMUM total pain)
        max_pain = min(strike_pain, key=strike_pain.get) if strike_pain else atm_strike
        
        OPTION_CHAIN_DATA = {
            "pcr": round(pcr, 2),
            "max_pain": max_pain,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "updated": now_ist()
        }
        
        logging.info(f"📊 OPTION CHAIN: PCR={pcr:.2f} | Max Pain={max_pain} | "
                     f"Call OI={total_call_oi:,} | Put OI={total_put_oi:,}")
        return OPTION_CHAIN_DATA
        
    except Exception as e:
        logging.error(f"Option chain fetch error: {e}")
        return OPTION_CHAIN_DATA

def pcr_bias():
    """Returns directional bias from PCR: BULLISH, BEARISH, or NEUTRAL."""
    pcr = OPTION_CHAIN_DATA.get("pcr")
    if pcr is None:
        return "NEUTRAL"
    if pcr > 1.2:
        return "BULLISH"  # Heavy put writing = floor support
    if pcr < 0.7:
        return "BEARISH"  # Heavy call writing = ceiling resistance
    return "NEUTRAL"


# --- E6: VOLUME PROFILE (ORDER FLOW PROXY) ---
def build_volume_profile(candles, num_bins=10):
    """
    E6: Builds volume-at-price histogram from candle data.
    Identifies High Volume Nodes (HVN) and Low Volume Nodes (LVN).
    
    HVN = strong support/resistance (price consolidates here)
    LVN = fast-move zones (price moves through quickly)
    
    Returns: {
        'bins': [(price_low, price_high, volume), ...],
        'hvn': [(price_low, price_high), ...],  # Top 20% by volume
        'lvn': [(price_low, price_high), ...],   # Bottom 20% by volume
        'poc': price  # Point of Control (highest volume price)
    }
    """
    if not candles or len(candles) < 10:
        return None
    
    # Find price range
    all_highs = [c["high"] for c in candles]
    all_lows = [c["low"] for c in candles]
    price_min = min(all_lows)
    price_max = max(all_highs)
    price_range = price_max - price_min
    
    if price_range <= 0:
        return None
    
    bin_size = price_range / num_bins
    bins = [(price_min + i * bin_size, price_min + (i + 1) * bin_size, 0) for i in range(num_bins)]
    
    # Distribute volume across bins
    volume_bins = [0] * num_bins
    for candle in candles:
        mid = (candle["high"] + candle["low"]) / 2
        vol = candle.get("volume", 0)
        idx = min(int((mid - price_min) / bin_size), num_bins - 1)
        volume_bins[idx] += vol
    
    # Rebuild bins with volume
    bins = [(price_min + i * bin_size, price_min + (i + 1) * bin_size, volume_bins[i]) 
            for i in range(num_bins)]
    
    # Sort by volume for HVN/LVN identification
    sorted_bins = sorted(bins, key=lambda x: x[2], reverse=True)
    total_bins = len(sorted_bins)
    hvn_count = max(1, total_bins // 5)  # Top 20%
    lvn_count = max(1, total_bins // 5)  # Bottom 20%
    
    hvn = [(b[0], b[1]) for b in sorted_bins[:hvn_count]]
    lvn = [(b[0], b[1]) for b in sorted_bins[-lvn_count:] if b[2] > 0]
    
    # Point of Control = mid-price of highest volume bin
    poc = (sorted_bins[0][0] + sorted_bins[0][1]) / 2 if sorted_bins else (price_min + price_max) / 2
    
    return {
        "bins": bins,
        "hvn": hvn,
        "lvn": lvn,
        "poc": round(poc, 2)
    }

def is_near_hvn(price, volume_profile, tolerance_pct=0.5):
    """Check if price is near a High Volume Node (support/resistance)."""
    if not volume_profile or not volume_profile.get("hvn"):
        return False
    for low, high in volume_profile["hvn"]:
        zone_range = high - low
        buffer = zone_range * tolerance_pct
        if low - buffer <= price <= high + buffer:
            return True
    return False

def is_in_lvn(price, volume_profile):
    """Check if price is in a Low Volume Node (fast-move zone)."""
    if not volume_profile or not volume_profile.get("lvn"):
        return False
    for low, high in volume_profile["lvn"]:
        if low <= price <= high:
            return True
    return False

# =====================================================
# PART 4 — PRIORITY SCORING ENGINE
# =====================================================

INDEX_KEYWORDS = ["NIFTY", "BANK", "FIN", "CNX"]

def is_index(symbol: str) -> bool:
    return any(k in symbol for k in INDEX_KEYWORDS)


def compute_priority(signal: dict) -> int:
    """
    Computes final priority score for a signal
    """

    score = 0

    # 🔥 INDEX FIRST
    if is_index(signal["symbol"]):
        score += 8

    # Setup strength
    if signal["setup"] == "SETUP-D":
        score += 7
    elif signal["setup"].startswith("A-"):
        score += 5
    elif signal["setup"] == "B":
        score += 4
    elif "UNIVERSAL" in signal["setup"]:
        score += 5

    # Risk Reward
    rr = signal.get("rr")
    if rr:
        if rr >= 4:
            score += 3
        elif rr >= 3:
            score += 2

    # Confluences
    if signal.get("fvg"):
        score += 2

    if signal.get("volume"):
        score += 2

    # W4 FIX: Removed blanket +3 HTF bonus — was inflating all non-Setup-D scores
    # without actually verifying multi-TF alignment. Each setup already checks HTF.

    # E4: Kill zone time-decay bonus
    kz_conf = killzone_confidence()
    if kz_conf >= 0.85:
        score += 2  # Morning rush or early afternoon
    elif kz_conf >= 0.6:
        score += 1  # Decent window
    elif kz_conf <= 0.3 and kz_conf > 0:
        score -= 1  # Lunch chop penalty

    # E1: Volatility regime adjustment
    score = volatility_score_adjustment(score, VOLATILITY_REGIME)

    # E2: PCR directional alignment bonus
    pcr_dir = pcr_bias()
    direction = signal.get("direction", "")
    if (pcr_dir == "BULLISH" and direction == "LONG") or \
       (pcr_dir == "BEARISH" and direction == "SHORT"):
        score += 1  # Option flow confirms direction

    return score
  # =====================================================
# CONFIDENCE GRADING
# =====================================================

def confidence_grade(priority: int) -> str:
    """
    Converts priority score into confidence label
    """
    if priority >= 18:
        return "A+"
    elif priority >= 14:
        return "A"
    elif priority >= 10:
        return "B"
    else:
        return "C"


def _normalize_setup_name(name: str) -> str:
    """Map setup aliases to stable buckets for adaptive statistics."""
    if not name:
        return "UNKNOWN"
    u = name.upper()
    if "UNIVERSAL" in u:
        return "SETUP_C"
    if "SETUP-D" in u or "SETUP_D" in u:
        return "SETUP_D"
    if "SETUP-B" in u or "SETUP_B" in u:
        return "SETUP_B"
    if "SETUP-A" in u or "SETUP_A" in u or u.startswith("A-"):
        return "SETUP_A"
    if "HIERARCHICAL" in u:
        return "HIERARCHICAL"
    return u


def _record_setup_outcome(setup: str, pnl_r: float):
    """Update in-memory live setup performance tracker for auto-tuning."""
    key = _normalize_setup_name(setup)
    row = LIVE_SETUP_STATS.setdefault(key, {"trades": 0, "wins": 0, "sum_r": 0.0})
    row["trades"] += 1
    if pnl_r > 0:
        row["wins"] += 1
    row["sum_r"] += pnl_r


def _load_recent_setup_performance(window_days: int = 20) -> dict:
    """
    Load recent closed trade performance by setup from yearly trade ledger.
    Returns: {setup: {trades, wins, sum_r, expectancy, win_rate}}
    """
    now = now_ist()
    cache_ts = ADAPTIVE_PERF_CACHE.get("updated")
    if cache_ts and (now - cache_ts).total_seconds() < 600 and ADAPTIVE_PERF_CACHE.get("stats"):
        return ADAPTIVE_PERF_CACHE["stats"]

    path = f"trade_ledger_{now.year}.csv"
    stats = {}
    cutoff = (now - timedelta(days=window_days)).date()
    if not os.path.exists(path):
        ADAPTIVE_PERF_CACHE["updated"] = now
        ADAPTIVE_PERF_CACHE["stats"] = stats
        return stats

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d_raw = str(row.get("date", ""))
                if not d_raw:
                    continue
                d_part = d_raw.split(" ")[0]
                try:
                    d_obj = datetime.strptime(d_part, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if d_obj < cutoff:
                    continue
                setup = _normalize_setup_name(str(row.get("setup", "")))
                pnl_r = float(row.get("pnl_r", 0.0))
                result = str(row.get("result", "")).upper()
                s = stats.setdefault(setup, {"trades": 0, "wins": 0, "sum_r": 0.0})
                s["trades"] += 1
                s["sum_r"] += pnl_r
                if result == "WIN" or pnl_r > 0:
                    s["wins"] += 1
    except Exception as e:
        logging.error(f"Adaptive perf load failed: {e}")

    for k, s in stats.items():
        t = max(1, s["trades"])
        s["expectancy"] = round(s["sum_r"] / t, 3)
        s["win_rate"] = round(s["wins"] / t, 3)

    ADAPTIVE_PERF_CACHE["updated"] = now
    ADAPTIVE_PERF_CACHE["stats"] = stats
    ADAPTIVE_PERF_CACHE["window_days"] = window_days
    return stats


def _get_adaptive_setup_multiplier(setup: str) -> float:
    """
    Tier 3 auto-optimization: dynamic risk multiplier from historical + live stats.
    """
    key = _normalize_setup_name(setup)
    stats = _load_recent_setup_performance(window_days=20).get(key, {})
    live = LIVE_SETUP_STATS.get(key, {})

    hist_exp = float(stats.get("expectancy", 0.0))
    hist_wr = float(stats.get("win_rate", 0.5))
    live_trades = int(live.get("trades", 0))
    live_exp = (float(live.get("sum_r", 0.0)) / live_trades) if live_trades > 0 else None

    blended_exp = hist_exp if live_exp is None else (0.7 * hist_exp + 0.3 * live_exp)
    # Risk schedule is intentionally conservative.
    if blended_exp >= 0.8 and hist_wr >= 0.55:
        return 1.2
    if blended_exp >= 0.3 and hist_wr >= 0.5:
        return 1.0
    if blended_exp >= 0.0:
        return 0.8
    if blended_exp >= -0.2:
        return 0.65
    return 0.5


def _adaptive_signal_gate(signal: dict) -> tuple[bool, str]:
    """
    Tier 3 smart filter: suppress weak setups in hostile regime/volatility context.
    Returns (is_allowed, reason).
    """
    setup = _normalize_setup_name(signal.get("setup", ""))
    direction = signal.get("direction", "")
    symbol = signal.get("symbol", "")
    regime = MARKET_REGIME
    perf = _load_recent_setup_performance(window_days=20).get(setup, {})
    exp = float(perf.get("expectancy", 0.0))
    wr = float(perf.get("win_rate", 0.5))

    if regime == "BEARISH" and direction == "LONG" and exp < 0.2:
        return False, f"Adaptive block: {setup} long against bearish regime (exp={exp:.2f})"
    if regime == "BULLISH" and direction == "SHORT" and exp < 0.2:
        return False, f"Adaptive block: {setup} short against bullish regime (exp={exp:.2f})"

    try:
        vol = get_volatility_regime(symbol)
    except Exception:
        vol = "NORMAL"

    # High volatility requires either strong score or positive setup expectancy.
    smc_score = int(signal.get("smc_score", 5))
    if vol == "HIGH" and smc_score < 7 and exp < 0.3:
        return False, f"Adaptive block: high vol + weak edge ({setup}, score={smc_score}, exp={exp:.2f})"

    # If a setup is statistically weak recently, require stronger confluence.
    if exp < -0.2 and wr < 0.4 and smc_score < 8:
        return False, f"Adaptive block: weak recent setup stats ({setup}, wr={wr:.0%}, exp={exp:.2f})"

    return True, "Adaptive pass"


def _ai_signal_score(signal: dict) -> tuple[int, list]:
    """
    Tier 3 explainable AI-like score (deterministic heuristic, no external API).
    Returns score [0..100] + reasons.
    """
    reasons = []
    score = 50

    rr = float(signal.get("rr", 0))
    smc_score = int(signal.get("smc_score", 5))
    priority = int(signal.get("priority", 0))
    direction = signal.get("direction", "")
    setup = _normalize_setup_name(signal.get("setup", ""))

    # Core quality
    if rr >= 2.5:
        score += 10; reasons.append("RR strong")
    elif rr >= 2.0:
        score += 6; reasons.append("RR acceptable")
    else:
        score -= 8; reasons.append("RR weak")

    score += max(-10, min(20, (smc_score - 5) * 4))
    reasons.append(f"SMC {smc_score}/10")

    score += max(-8, min(12, priority // 2))
    reasons.append(f"Priority {priority}")

    # Regime alignment
    if (MARKET_REGIME == "BULLISH" and direction == "LONG") or (MARKET_REGIME == "BEARISH" and direction == "SHORT"):
        score += 8; reasons.append("Regime aligned")
    elif MARKET_REGIME != "NEUTRAL":
        score -= 6; reasons.append("Regime conflict")

    # Setup expectancy impact
    perf = _load_recent_setup_performance(window_days=20).get(setup, {})
    exp = float(perf.get("expectancy", 0.0))
    if exp >= 0.5:
        score += 8; reasons.append(f"{setup} strong expectancy")
    elif exp < -0.2:
        score -= 10; reasons.append(f"{setup} weak expectancy")

    score = int(max(0, min(100, score)))
    return score, reasons
# =====================================================
# APPLY PRIORITY & SORT SIGNALS
# =====================================================

def rank_signals(signals: list) -> list:
    """
    Adds priority score + confidence grade
    Sorts signals from best → worst
    """
    ranked = []

    for sig in signals:
        p = compute_priority(sig)
        sig["priority"] = p
        sig["grade"] = confidence_grade(p)
        ranked.append(sig)

    ranked.sort(key=lambda x: x["priority"], reverse=True)
    return ranked
    # =====================================================
# MORNING 9 AM WATCHLIST
# =====================================================

def send_morning_watchlist():
    today = now_ist().date().isoformat()

    # Run only once per day
    if os.path.exists(MORNING_WATCHLIST_FLAG):
        if open(MORNING_WATCHLIST_FLAG).read().strip() == today:
            return

    all_signals = []

    # Index + limited stock scan (INDEX_ONLY: indices only)
    watchlist_symbols = INDEX_SYMBOLS if INDEX_ONLY else INDEX_SYMBOLS + get_stock_universe()[:50]

    for symbol in watchlist_symbols:
        try:
            signals = scan_symbol(symbol)
            if signals:
                all_signals.extend(signals)
        except Exception:
            continue

    if not all_signals:
        return

    ranked = rank_signals(all_signals)

    msg = "🌅 <b>MORNING WATCHLIST (SMC)</b>\n\n"

    # Filter actionable trades
    valid_signals = [s for s in ranked if "entry" in s and "sl" in s]

    for sig in valid_signals[:10]:
        msg += (
            f"<b>{sig['symbol']}</b> | {sig['direction']} | {sig['setup']}\n"
            f"Entry: {sig['entry']} | SL: {sig['sl']} | TG: {sig['target']}\n"
            f"Grade: {sig['grade']} | RR: {sig['rr']}\n\n"
        )

    telegram_send(msg)

    # Mark as sent for today
    with open(MORNING_WATCHLIST_FLAG, "w") as f:
        f.write(today)

# =====================================================
# INDEX-FIRST SCAN ORDER (VERY IMPORTANT)
# =====================================================

def build_scan_universe(stock_universe: list) -> list:
    """
    Index symbols are scanned every cycle
    Stocks are scanned every 6 minutes (disabled in INDEX_ONLY mode)
    """
    symbols = INDEX_SYMBOLS.copy()

    # Scan full stock universe every 6 minutes (skip in INDEX_ONLY mode)
    if not INDEX_ONLY and now_ist().minute % 6 == 0:
        symbols.extend(stock_universe)

    return symbols
# =====================================================
# TELEGRAM BUTTONS (INLINE KEYBOARD)
# =====================================================

def telegram_send_with_buttons(message: str, buttons: list, signal_id: str = None, signal_meta: dict = None):
    """
    Send signal message with inline buttons. 3 retries; falls back to plain text on failure.

    buttons format:
    [
        [{"text": "BUY", "callback_data": "BUY_NIFTY"}],
        [{"text": "IGNORE", "callback_data": "IGNORE"}]
    ]
    """
    if not BOT_TOKEN or not CHAT_ID:
        logging.critical("[SIGNAL LOST] Telegram credentials missing — button signal not delivered. Message: %s", message[:200])
        return
    meta_base = dict(signal_meta or {})
    payload = {
        "chat_id": CHAT_ID,
        "text": paper_prefix(message),
        "parse_mode": "HTML",
        "reply_markup": json.dumps({"inline_keyboard": buttons}),
    }
    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data=payload,
                timeout=10,
            )
            if resp.ok:
                try:
                    from utils.telegram_signal_log import persist_telegram_signal

                    m = {**meta_base, "delivery_format": "inline_buttons"}
                    persist_telegram_signal(message, signal_id, m)
                except Exception as _log_exc:
                    logging.warning("[Telegram] signal_log persist (buttons) failed: %s", _log_exc)
                return
            last_exc = Exception(f"HTTP {resp.status_code}: {resp.text[:100]}")
            logging.warning("[Telegram] sendMessage(buttons) attempt %d/%d failed: %s", attempt + 1, 3, last_exc)
        except Exception as exc:
            last_exc = exc
            logging.warning("[Telegram] sendMessage(buttons) attempt %d/%d error: %s", attempt + 1, 3, exc)
        if attempt < 2:
            t.sleep(2 ** attempt)
    # All retries failed — fall back to plain text signal
    logging.critical("[SIGNAL DEGRADED] Button send failed after 3 attempts (%s). Sending plain-text fallback.", last_exc)
    fb_meta = {**meta_base, "delivery_format": "text_fallback_after_buttons_fail"}
    telegram_send_signal(message, signal_id=signal_id, signal_meta=fb_meta)
# =====================================================
# HOURLY SUMMARY ENGINE
# =====================================================

def send_hourly_summary(signals: list):
    global LAST_HOURLY_SUMMARY

    current_hour = now_ist().strftime("%Y-%m-%d %H")

    if LAST_HOURLY_SUMMARY == current_hour:
        return

    if not signals:
        return

    msg = "🕐 <b>HOURLY SMC SUMMARY</b>\n\n"
    
    # Include OI sentiment in hourly summary
    oi_sent = get_oi_sentiment()
    if oi_sent.get("last_update"):
        msg += (
            f"📊 <b>OI Sentiment:</b> {oi_sent['sentiment']} | "
            f"PCR: {oi_sent['pcr_bias']} | Pattern: {oi_sent['price_oi_pattern']}\n"
            f"🏛️ <b>Regime:</b> {MARKET_REGIME}\n\n"
        )
    else:
        msg += f"🏛️ <b>Regime:</b> {MARKET_REGIME}\n\n"

    # Filter actionable trades
    valid_signals = [s for s in signals if "entry" in s and "sl" in s]

    for sig in valid_signals[:5]:
        msg += (
            f"<b>{sig['symbol']}</b> | {sig['direction']} | {sig['setup']}\n"
            f"Entry: {sig['entry']} | SL: {sig['sl']} | TG: {sig['target']}\n"
            f"RR: {sig['rr']} | Grade: {sig['grade']}\n\n"
        )

    telegram_send(msg)
    LAST_HOURLY_SUMMARY = current_hour
# =====================================================
# CHART GENERATION (PROFESSIONAL)
# =====================================================
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

def generate_chart(symbol, candles, direction, entry, sl, target, ob,
                   setup_name="", smc_score=None, regime=None, analysis=""):
    """
    Generate a professional candlestick chart with:
    - Entry (blue), SL (red), Target (green) horizontal lines with labels
    - OB zone shading (orange translucent)
    - Info panel: setup, direction, RR, score, regime
    - Clean dark theme for Telegram readability
    """
    try:
        df = pd.DataFrame(candles[-60:])  # Last 60 candles for clarity
        if "date" not in df.columns:
            return None

        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        for col in ["open", "high", "low", "close"]:
            if col not in df.columns:
                return None

        fname = f"chart_{uuid.uuid4().hex[:6]}.png"

        # Only plot volume if there is actual volume traded
        plot_volume = "volume" in df.columns and df["volume"].max() > 0

        # --- Dark style ---
        mc = mpf.make_marketcolors(
            up='#26a69a', down='#ef5350',
            edge={'up': '#26a69a', 'down': '#ef5350'},
            wick={'up': '#26a69a', 'down': '#ef5350'},
            volume={'up': '#26a69a80', 'down': '#ef535080'},
        )
        style = mpf.make_mpf_style(
            marketcolors=mc,
            base_mpf_style='nightclouds',
            gridstyle='-', gridcolor='#2a2a2a',
            facecolor='#1a1a2e', edgecolor='#1a1a2e',
            figcolor='#1a1a2e',
            rc={'font.size': 9}
        )

        # --- Horizontal level lines as addplots ---
        n = len(df)
        apds = [
            mpf.make_addplot([entry]*n, color="#2196F3", width=1.5, linestyle='--'),  # Entry blue
            mpf.make_addplot([sl]*n, color="#FF1744", width=1.5, linestyle='--'),       # SL red
            mpf.make_addplot([target]*n, color="#00E676", width=1.5, linestyle='--'),   # Target green
        ]

        fig, axes = mpf.plot(
            df, type="candle", style=style,
            volume=plot_volume, addplot=apds,
            returnfig=True, figsize=(12, 7),
            tight_layout=True,
        )
        ax = axes[0]

        # --- OB zone shading ---
        if ob and len(ob) == 2:
            ax.axhspan(ob[0], ob[1], alpha=0.18, color='#FF9800', zorder=0)
            # OB boundary lines
            ax.axhline(ob[0], color='#FF9800', linewidth=0.8, alpha=0.6, linestyle=':')
            ax.axhline(ob[1], color='#FF9800', linewidth=0.8, alpha=0.6, linestyle=':')

        # --- Price level labels on right edge ---
        right_x = n - 1
        label_props = dict(fontsize=9, fontweight='bold', va='center',
                           bbox=dict(boxstyle='round,pad=0.3', alpha=0.85))

        ax.text(right_x + 0.8, entry, f' ENTRY {entry:.1f}',
                color='white', backgroundcolor='#2196F3', **label_props)
        ax.text(right_x + 0.8, sl, f' SL {sl:.1f}',
                color='white', backgroundcolor='#FF1744', **label_props)
        ax.text(right_x + 0.8, target, f' TGT {target:.1f}',
                color='white', backgroundcolor='#00E676', **label_props)

        if ob and len(ob) == 2:
            ob_mid = (ob[0] + ob[1]) / 2
            ax.text(1, ob_mid, ' OB ZONE', color='#FF9800', fontsize=8,
                    fontweight='bold', va='center', alpha=0.9)

        # --- RR calculation ---
        risk = abs(entry - sl)
        reward = abs(target - entry)
        rr = round(reward / risk, 1) if risk > 0 else 0

        # --- Direction arrow + setup info ---
        dir_emoji = "▲ LONG" if direction == "LONG" else "▼ SHORT"
        dir_color = "#26a69a" if direction == "LONG" else "#ef5350"

        # Title
        clean_sym = symbol.replace("NSE:", "")
        title_text = f"{clean_sym}  |  {dir_emoji}  |  {setup_name}"
        ax.set_title(title_text, fontsize=14, fontweight='bold',
                     color=dir_color, loc='left', pad=12)

        # --- Info box (top-right) ---
        info_lines = [f"RR: 1:{rr}"]
        if smc_score is not None:
            info_lines.append(f"Score: {smc_score}/10")
        if regime:
            info_lines.append(f"Regime: {regime}")
        if analysis:
            # Truncate long analysis
            info_lines.append(analysis[:50])
        info_text = "\n".join(info_lines)

        ax.text(0.98, 0.97, info_text, transform=ax.transAxes,
                fontsize=9, color='white', verticalalignment='top',
                horizontalalignment='right',
                bbox=dict(boxstyle='round,pad=0.5', facecolor='#333355', alpha=0.85))

        # --- Legend ---
        legend_elements = [
            Line2D([0], [0], color='#2196F3', linewidth=2, linestyle='--', label=f'Entry: {entry:.1f}'),
            Line2D([0], [0], color='#FF1744', linewidth=2, linestyle='--', label=f'SL: {sl:.1f}'),
            Line2D([0], [0], color='#00E676', linewidth=2, linestyle='--', label=f'Target: {target:.1f}'),
            mpatches.Patch(color='#FF9800', alpha=0.3, label='Order Block'),
        ]
        ax.legend(handles=legend_elements, loc='lower left', fontsize=8,
                  facecolor='#1a1a2e', edgecolor='#444', labelcolor='white')

        # --- Watermark ---
        ax.text(0.5, 0.5, 'SMC ENGINE', transform=ax.transAxes,
                fontsize=40, color='white', alpha=0.03,
                ha='center', va='center', fontweight='bold')

        fig.savefig(fname, dpi=150, bbox_inches='tight',
                    facecolor='#1a1a2e', edgecolor='none')
        plt.close(fig)

        return fname
    except Exception as e:
        if DEBUG_MODE:
            print("Chart error:", e)
            import traceback
            traceback.print_exc()
        return None


def generate_oi_chart(signal):
    """
    Generate a professional chart for OI Short Covering signals.
    Shows:
    - OI drop pattern (bar chart)
    - Price rise pattern (line chart)
    - Trade levels: Entry, SL, Target
    - Score breakdown panel
    """
    try:
        from engine.oi_short_covering import get_strike_history

        tsym = signal["tradingsymbol"]
        history = get_strike_history(tsym)

        if not history or len(history) < 3:
            return None

        fname = f"oi_chart_{uuid.uuid4().hex[:6]}.png"

        # Extract data from history: (timestamp, oi, ltp, volume)
        timestamps = [h[0] for h in history]
        oi_values = [h[1] for h in history]
        ltp_values = [h[2] for h in history]

        # Time labels
        time_labels = [ts.strftime("%H:%M") for ts in timestamps]

        # Create figure with dark theme
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8),
                                        gridspec_kw={'height_ratios': [1.2, 1]},
                                        facecolor='#1a1a2e')

        # --- TOP PANEL: OI Bars ---
        ax1.set_facecolor('#1a1a2e')
        colors = []
        for i in range(len(oi_values)):
            if i == 0:
                colors.append('#FF9800')
            elif oi_values[i] < oi_values[i-1]:
                colors.append('#ef5350')  # Red = OI dropping (short covering)
            else:
                colors.append('#26a69a')  # Green = OI building

        ax1.bar(range(len(oi_values)), oi_values, color=colors, alpha=0.8, width=0.6)
        ax1.set_ylabel('Open Interest', color='white', fontsize=10)
        ax1.tick_params(colors='white', labelsize=8)
        ax1.set_xticks(range(len(time_labels)))
        ax1.set_xticklabels(time_labels, rotation=45, ha='right', fontsize=7, color='white')
        ax1.grid(axis='y', color='#2a2a2a', linestyle='-', alpha=0.5)

        # Peak OI line
        peak_oi = signal.get("peak_oi", max(oi_values))
        ax1.axhline(peak_oi, color='#FF9800', linewidth=1, linestyle='--', alpha=0.7)
        ax1.text(len(oi_values)-1, peak_oi, f' Peak: {peak_oi:,.0f}',
                 color='#FF9800', fontsize=8, va='bottom')

        # Current OI annotation
        current_oi = signal.get("current_oi", oi_values[-1])
        drop_pct = round((peak_oi - current_oi) / peak_oi * 100, 1) if peak_oi > 0 else 0
        ax1.text(len(oi_values)-1, current_oi, f' {current_oi:,.0f}\n ({drop_pct:.1f}% drop)',
                 color='#ef5350', fontsize=8, fontweight='bold', va='top')

        # Title for OI panel
        underlying = signal.get("underlying", "")
        strike = signal.get("strike", "")
        opt_type = signal.get("opt_type", "")
        bias = signal.get("underlying_bias", "")
        score = signal.get("score", 0)
        ax1.set_title(
            f"OI SHORT COVERING  |  {underlying} {strike} {opt_type}  |  Score: {score}/10  |  {bias}",
            fontsize=13, fontweight='bold', color='#FF9800', loc='left', pad=12
        )

        # --- BOTTOM PANEL: Price + Trade Levels ---
        ax2.set_facecolor('#1a1a2e')
        ax2.plot(range(len(ltp_values)), ltp_values, color='#2196F3',
                 linewidth=2, marker='o', markersize=3, label='Option LTP')

        ax2.set_ylabel('Option Premium (₹)', color='white', fontsize=10)
        ax2.tick_params(colors='white', labelsize=8)
        ax2.set_xticks(range(len(time_labels)))
        ax2.set_xticklabels(time_labels, rotation=45, ha='right', fontsize=7, color='white')
        ax2.grid(axis='y', color='#2a2a2a', linestyle='-', alpha=0.5)

        # Trade levels
        levels = signal.get("trade_levels", {})
        entry_price = levels.get("entry", signal.get("current_ltp", 0))
        sl_price = levels.get("sl", 0)
        target_price = levels.get("target", 0)
        rr = levels.get("rr", 2.0)

        x_range = range(len(ltp_values))
        if entry_price > 0:
            ax2.axhline(entry_price, color='#2196F3', linewidth=1.5, linestyle='--', alpha=0.8)
            ax2.text(len(ltp_values)-1, entry_price,
                     f'  ENTRY ₹{entry_price:.1f}',
                     color='white', fontsize=9, fontweight='bold', va='bottom',
                     bbox=dict(boxstyle='round,pad=0.2', facecolor='#2196F3', alpha=0.85))

        if sl_price > 0:
            ax2.axhline(sl_price, color='#FF1744', linewidth=1.5, linestyle='--', alpha=0.8)
            ax2.text(len(ltp_values)-1, sl_price,
                     f'  SL ₹{sl_price:.1f}',
                     color='white', fontsize=9, fontweight='bold', va='top',
                     bbox=dict(boxstyle='round,pad=0.2', facecolor='#FF1744', alpha=0.85))

        if target_price > 0:
            ax2.axhline(target_price, color='#00E676', linewidth=1.5, linestyle='--', alpha=0.8)
            ax2.text(len(ltp_values)-1, target_price,
                     f'  TGT ₹{target_price:.1f} (RR:{rr:.1f})',
                     color='white', fontsize=9, fontweight='bold', va='bottom',
                     bbox=dict(boxstyle='round,pad=0.2', facecolor='#00E676', alpha=0.85))

        # --- Score breakdown box ---
        bd = signal.get("score_breakdown", {})
        if bd:
            bd_lines = [f"{'─'*16}", "SCORE BREAKDOWN"]
            for k, v in bd.items():
                bar = "█" * v + "░" * (3 - v)
                bd_lines.append(f"  {k}: {bar} +{v}")
            bd_lines.append(f"{'─'*16}")
            bd_lines.append(f"  TOTAL: {score}/10")
            bd_text = "\n".join(bd_lines)

            ax2.text(0.02, 0.97, bd_text, transform=ax2.transAxes,
                     fontsize=8, color='white', verticalalignment='top',
                     family='monospace',
                     bbox=dict(boxstyle='round,pad=0.5', facecolor='#333355', alpha=0.85))

        # --- Pattern explanation ---
        if opt_type == "CE":
            pattern_text = "CALL writers covering → BULLISH"
        else:
            pattern_text = "PUT writers covering → BEARISH"
        ax2.text(0.98, 0.03, pattern_text, transform=ax2.transAxes,
                 fontsize=10, color='#FF9800', fontweight='bold',
                 ha='right', va='bottom', alpha=0.9,
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a1a2e', edgecolor='#FF9800', alpha=0.8))

        # --- Watermark ---
        fig.text(0.5, 0.5, 'SMC ENGINE', fontsize=50, color='white', alpha=0.03,
                 ha='center', va='center', fontweight='bold')

        fig.tight_layout(pad=2.0)
        fig.savefig(fname, dpi=150, bbox_inches='tight',
                    facecolor='#1a1a2e', edgecolor='none')
        plt.close(fig)

        return fname
    except Exception as e:
        if DEBUG_MODE:
            print(f"OI Chart error: {e}")
            import traceback
            traceback.print_exc()
        return None
# =====================================================
# LIVE TRADE MONITOR (SL/TP CHECK)
# =====================================================
def monitor_active_trades(symbol, current_price):
    """W1 UPGRADE: 3-stage trailing stop + circuit breaker integration."""
    global ACTIVE_TRADES, DAILY_LOG, DAILY_PNL_R, CONSECUTIVE_LOSSES, CIRCUIT_BREAKER_ACTIVE
    global COOLDOWN_UNTIL  # F1.4
    
    # Filter trades for this symbol (under lock for safe read)
    with ACTIVE_TRADES_LOCK:
        trades = [t for t in ACTIVE_TRADES if t["symbol"] == symbol]
    
    for trade in trades:
        r_mult = trade.get("risk_mult", 1.0)
        entry = trade["entry"]
        original_sl = trade.get("original_sl", trade["sl"])  # Preserve original SL
        current_sl = trade["sl"]
        target = trade["target"]
        direction = trade["direction"]
        risk = abs(entry - original_sl)
        
        if risk == 0:
            continue  # Safety: avoid division by zero
        
        # Store original SL on first check
        if "original_sl" not in trade:
            trade["original_sl"] = trade["sl"]
        
        # ── Calculate current R-multiple ──
        if direction == "LONG":
            current_r = (current_price - entry) / risk
        else:
            current_r = (entry - current_price) / risk
        
        # ── TRAILING STOP LOGIC (4 stages) ──
        if direction == "LONG":
            new_sl = current_sl
            if current_r >= 3.0:  # Stage 4: Dynamic trail — lock 75% of open profit
                new_sl = max(current_sl, entry + (current_price - entry) * 0.75)
                if new_sl != current_sl:
                    trade["trail_stage"] = 4
            elif current_r >= 2.5:  # Stage 3: SL to +2R
                new_sl = max(current_sl, entry + risk * 2.0)
                if new_sl != current_sl:
                    trade["trail_stage"] = 3
            elif current_r >= 2.0:  # Stage 2: SL to +1R
                new_sl = max(current_sl, entry + risk * 1.0)
                if new_sl != current_sl:
                    trade["trail_stage"] = 2
            elif current_r >= 1.0:  # Stage 1: SL to breakeven
                new_sl = max(current_sl, entry)
                if new_sl != current_sl:
                    trade["trail_stage"] = 1
            
            if new_sl > current_sl:
                trade["sl"] = round(new_sl, 2)
                stage = trade.get("trail_stage", 0)
                try:
                    update_trade_graph_trail(trade, stage, trade["sl"])
                except Exception:
                    pass
                logging.info(f"📈 TRAIL [{stage}]: {trade['symbol']} SL moved to {trade['sl']} (was {current_sl})")
                telegram_send(f"📈 <b>TRAILING STOP UPDATE</b>\n"
                              f"{trade['symbol']} LONG\n"
                              f"SL moved: {current_sl} → {trade['sl']}\n"
                              f"Stage {stage} | Current R: +{current_r:.1f}R")
        else:  # SHORT
            new_sl = current_sl
            if current_r >= 3.0:  # Stage 4: Dynamic trail — lock 75% of open profit
                new_sl = min(current_sl, entry - (entry - current_price) * 0.75)
                if new_sl != current_sl:
                    trade["trail_stage"] = 4
            elif current_r >= 2.5:
                new_sl = min(current_sl, entry - risk * 2.0)
                if new_sl != current_sl:
                    trade["trail_stage"] = 3
            elif current_r >= 2.0:
                new_sl = min(current_sl, entry - risk * 1.0)
                if new_sl != current_sl:
                    trade["trail_stage"] = 2
            elif current_r >= 1.0:
                new_sl = min(current_sl, entry)
                if new_sl != current_sl:
                    trade["trail_stage"] = 1
            
            if new_sl < current_sl:
                trade["sl"] = round(new_sl, 2)
                stage = trade.get("trail_stage", 0)
                try:
                    update_trade_graph_trail(trade, stage, trade["sl"])
                except Exception:
                    pass
                logging.info(f"📈 TRAIL [{stage}]: {trade['symbol']} SL moved to {trade['sl']} (was {current_sl})")
                telegram_send(f"📈 <b>TRAILING STOP UPDATE</b>\n"
                              f"{trade['symbol']} SHORT\n"
                              f"SL moved: {current_sl} → {trade['sl']}\n"
                              f"Stage {stage} | Current R: +{current_r:.1f}R")
        
        # ── CHECK TARGET HIT ──
        hit_target = (direction == "LONG" and current_price >= target) or \
                     (direction == "SHORT" and current_price <= target)
        
        # ── CHECK SL HIT (uses updated trailing SL) ──
        hit_sl = (direction == "LONG" and current_price <= trade["sl"]) or \
                 (direction == "SHORT" and current_price >= trade["sl"])
        
        if hit_target:
            final_r = round(abs(target - entry) / risk * r_mult, 2)
            msg = (f"✅ <b>TARGET HIT — EXIT NOW</b>\n"
                   f"<b>{trade['symbol']}</b> ({direction})\n"
                   f"Entry: {entry} ➔ Target: {target}\n"
                   f"Result: +{final_r}R Profit 💰\n"
                   f"Trail Stage: {trade.get('trail_stage', 0)}")
            _exit_sid = f"exit_tgt_{trade['symbol'].replace(':', '_')}_{now_ist().strftime('%Y%m%d%H%M%S')}"
            telegram_send_signal(
                msg,
                signal_id=_exit_sid,
                signal_meta={
                    "signal_kind": "EXIT_TARGET",
                    "symbol": trade.get("symbol"),
                    "direction": direction,
                    "strategy_name": trade.get("setup"),
                    "entry": entry,
                    "result": "WIN",
                    "pnl_r": final_r,
                },
            )
            trade["result"] = "WIN"
            trade["exit_price"] = current_price
            trade["exit_r"] = final_r
            log_paper_outcome(trade, current_price, "WIN", final_r)  # Phase 6
            DAILY_LOG.append(trade)
            log_trade_to_csv(trade)
            try:
                close_trade_graph(trade)
            except Exception:
                pass  # TradeGraph is non-critical
            # ── Feature 4: Auto Trade Close Screenshot ──
            if _TV_BRIDGE_AVAILABLE and not BACKTEST_MODE:
                try:
                    _close_ss = capture_trade_close_chart(trade)
                    if _close_ss:
                        trade["_close_screenshot"] = _close_ss
                        logging.info(f"📸 Trade close chart captured: {_close_ss}")
                except Exception as _css_e:
                    logging.debug(f"TV close screenshot failed (non-blocking): {_css_e}")
            # P0-R1: Single lock acquisition for remove + snapshot
            with ACTIVE_TRADES_LOCK:
                ACTIVE_TRADES.remove(trade)
                _remaining = list(ACTIVE_TRADES)
                _persist_snap = [_serialize_trade(t) for t in ACTIVE_TRADES]
            persist_active_trades(snapshot=_persist_snap)  # F1.2: crash recovery
            try:
                update_engine_state(trades=_remaining)
                print("API update:", get_engine_state_snapshot())
            except Exception:
                pass
            # W2: Update circuit breaker
            DAILY_PNL_R += final_r
            CONSECUTIVE_LOSSES = 0
            _record_setup_outcome(trade.get("setup", "UNKNOWN"), final_r)
            logging.info(f"📊 Daily PnL: {DAILY_PNL_R:.1f}R | Streak: {CONSECUTIVE_LOSSES}")
            
        elif hit_sl:
            # SL might be above entry (trailing) — could be a trail-win
            exit_r = round(current_r * r_mult, 2)
            is_trail_win = trade["sl"] > entry if direction == "LONG" else trade["sl"] < entry
            
            if is_trail_win:
                result_label = "TRAIL WIN"
                emoji = "🟡"
                trade["result"] = "WIN"  # Trailed profit
                CONSECUTIVE_LOSSES = 0
            else:
                result_label = "LOSS"
                emoji = "❌"
                trade["result"] = "LOSS"
                CONSECUTIVE_LOSSES += 1
            
            msg = (f"{emoji} <b>SL HIT — {result_label}</b>\n"
                   f"<b>{trade['symbol']}</b> ({direction})\n"
                   f"Entry: {entry} ➔ SL: {trade['sl']}\n"
                   f"Result: {'+' if exit_r > 0 else ''}{exit_r}R\n"
                   f"Trail Stage: {trade.get('trail_stage', 0)}")
            _res = "WIN" if trade.get("result") == "WIN" else "LOSS"
            _exit_sid = f"exit_sl_{trade['symbol'].replace(':', '_')}_{now_ist().strftime('%Y%m%d%H%M%S')}"
            telegram_send_signal(
                msg,
                signal_id=_exit_sid,
                signal_meta={
                    "signal_kind": "EXIT_STOP",
                    "symbol": trade.get("symbol"),
                    "direction": direction,
                    "strategy_name": trade.get("setup"),
                    "entry": entry,
                    "result": _res,
                    "pnl_r": exit_r,
                },
            )
            trade["exit_price"] = trade["sl"]
            trade["exit_r"] = exit_r
            log_paper_outcome(trade, trade["sl"], trade["result"], exit_r)  # Phase 6
            DAILY_LOG.append(trade)
            log_trade_to_csv(trade)
            try:
                close_trade_graph(trade)
            except Exception:
                pass  # TradeGraph is non-critical
            # ── Feature 4: Auto Trade Close Screenshot ──
            if _TV_BRIDGE_AVAILABLE and not BACKTEST_MODE:
                try:
                    _close_ss = capture_trade_close_chart(trade)
                    if _close_ss:
                        trade["_close_screenshot"] = _close_ss
                        logging.info(f"📸 Trade close chart captured: {_close_ss}")
                except Exception as _css_e:
                    logging.debug(f"TV close screenshot failed (non-blocking): {_css_e}")
            # P0-R1: Single lock acquisition for remove + snapshot
            with ACTIVE_TRADES_LOCK:
                ACTIVE_TRADES.remove(trade)
                _remaining = list(ACTIVE_TRADES)
                _persist_snap = [_serialize_trade(t) for t in ACTIVE_TRADES]
            persist_active_trades(snapshot=_persist_snap)  # F1.2: crash recovery
            try:
                update_engine_state(trades=_remaining)
                print("API update:", get_engine_state_snapshot())
            except Exception:
                pass
            # W2: Update circuit breaker
            DAILY_PNL_R += exit_r
            _record_setup_outcome(trade.get("setup", "UNKNOWN"), exit_r)
            logging.info(f"📊 Daily PnL: {DAILY_PNL_R:.1f}R | Streak: {CONSECUTIVE_LOSSES}")
            
            # W2: Check circuit breaker
            if DAILY_PNL_R <= MAX_DAILY_LOSS_R:
                CIRCUIT_BREAKER_ACTIVE = True
                save_engine_states()  # P0-3: Persist immediately so restart can't bypass
                telegram_send(f"🛑 <b>CIRCUIT BREAKER ACTIVATED</b>\n"
                              f"Daily PnL: {DAILY_PNL_R:.1f}R (limit: {MAX_DAILY_LOSS_R}R)\n"
                              f"No new entries for rest of day.")
                logging.warning(f"🛑 CIRCUIT BREAKER: Daily PnL {DAILY_PNL_R:.1f}R — halting entries")
            
            if CONSECUTIVE_LOSSES >= COOLDOWN_AFTER_STREAK:
                # F1.4: Set proper 60-minute cooldown timer
                COOLDOWN_UNTIL = now_ist() + timedelta(minutes=60)
                telegram_send(f"⚠️ <b>{CONSECUTIVE_LOSSES} CONSECUTIVE LOSSES</b>\n"
                              f"Entering observation mode until {COOLDOWN_UNTIL.strftime('%H:%M')}.")
                logging.warning(f"⚠️ COOLDOWN: {CONSECUTIVE_LOSSES} losses — paused until {COOLDOWN_UNTIL.strftime('%H:%M')}")

def log_trade_to_csv(trade_dict: dict):
    """Appends a closed trade to the yearly ledger"""
    file_exists = os.path.exists("trade_ledger_2026.csv")
    
    # Calculate PnL (Approximate based on R-multiples for now, or raw points)
    # Fields: date,symbol,product,qty,buy,buy_price,sell_price,days_held,gross_pnl,interest,net_pnl
    # We will map our specific dict keys to this schema or create a new flexible one.
    # For now, let's just dump the dict keys that matter for analysis.
    
    # Simplified CSV format for our Engine
    keys = ["date", "symbol", "direction", "setup", "entry", "exit_price", "result", "pnl_r"]
    
    # F1.1 FIX: Use actual exit_r from trade monitoring (accounts for trailing stops)
    # Previously always logged full target RR for wins, inflating PnL
    if "exit_r" in trade_dict:
        pnl_r = trade_dict["exit_r"]
    else:
        # Fallback for legacy trades without exit_r
        rr = trade_dict.get("rr", 2.0)
        risk_mult = trade_dict.get("risk_mult", 1.0)
        if trade_dict["result"] == "WIN":
            pnl_r = rr * risk_mult
        else:
            pnl_r = -1.0 * risk_mult

    trade_data = {
        "date": now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": trade_dict["symbol"],
        "direction": trade_dict["direction"],
        "setup": trade_dict["setup"],
        "entry": trade_dict["entry"],
        "exit_price": trade_dict["exit_price"],
        "result": trade_dict["result"],
        "pnl_r": round(pnl_r, 2)
    }
    
    try:
        df = pd.DataFrame([trade_data])
        df.to_csv("trade_ledger_2026.csv", mode='a', header=not file_exists, index=False)
    except Exception as e:
        print(f"Failed to log trade: {e}")

    # Sync to dashboard web service (Railway cross-container)
    try:
        from services.dashboard_sync import sync_trade_to_dashboard
        sync_trade_to_dashboard(trade_data)
    except Exception:
        pass

# =====================================================
# F4.3: MULTI-DAY DRAWDOWN CHECK
# =====================================================
def check_multi_day_drawdown() -> bool:
    """
    Check if rolling 5-day PnL is below -10R.
    If so, set MULTI_DAY_HALT_UNTIL to 48 hours from now.
    Returns True if halt is active (should not trade).
    """
    global MULTI_DAY_HALT_UNTIL
    
    # Check if already in halt period
    if MULTI_DAY_HALT_UNTIL and now_ist() < MULTI_DAY_HALT_UNTIL:
        return True
    elif MULTI_DAY_HALT_UNTIL and now_ist() >= MULTI_DAY_HALT_UNTIL:
        MULTI_DAY_HALT_UNTIL = None  # Expired, clear it
        logging.info("✅ Multi-day drawdown halt expired — resuming trading")
    
    try:
        if not os.path.exists("trade_ledger_2026.csv"):
            return False
        df = pd.read_csv("trade_ledger_2026.csv")
        if df.empty or "pnl_r" not in df.columns or "date" not in df.columns:
            return False
        
        df["date"] = pd.to_datetime(df["date"], format="mixed", dayfirst=False)
        cutoff = now_ist() - timedelta(days=MULTI_DAY_DD_WINDOW)
        recent = df[df["date"] >= cutoff]
        
        rolling_pnl = recent["pnl_r"].sum()
        
        if rolling_pnl <= MULTI_DAY_DD_LIMIT:
            MULTI_DAY_HALT_UNTIL = now_ist() + timedelta(hours=MULTI_DAY_HALT_HOURS)
            msg = (f"🛑 <b>MULTI-DAY DRAWDOWN BREAKER</b>\n"
                   f"Rolling {MULTI_DAY_DD_WINDOW}-day PnL: {rolling_pnl:.1f}R "
                   f"(limit: {MULTI_DAY_DD_LIMIT}R)\n"
                   f"Trading halted until {MULTI_DAY_HALT_UNTIL.strftime('%Y-%m-%d %H:%M')}")
            telegram_send(msg)
            logging.critical(msg.replace("<b>", "").replace("</b>", ""))
            
            # Persist to SQLite so it survives restarts
            try:
                db.set_value("engine_state", "multi_day_halt", {
                    "halt_until": MULTI_DAY_HALT_UNTIL.isoformat(),
                    "rolling_pnl": rolling_pnl,
                    "triggered_at": now_ist().isoformat()
                })
            except Exception:
                pass
            return True
    except Exception as e:
        logging.error(f"Multi-day drawdown check failed: {e}")
    
    return False


# =====================================================
# EOD REPORT (4 PM)
# =====================================================
def send_eod_report():
    """Generates Daily PnL Summary from CSV (Persistent)"""
    global EOD_SENT
    
    # Try to load from CSV first (Robust against restarts)
    today_trades = []
    try:
        if os.path.exists("trade_ledger_2026.csv"):
            df = pd.read_csv("trade_ledger_2026.csv")
            if not df.empty and "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                today = now_ist().date()
                today_mask = df["date"].dt.date == today
                today_df = df[today_mask]
                today_trades = today_df.to_dict("records")
    except Exception as e:
        print(f"EOD CSV Read Error: {e}")
    
    # Fallback to Memory if CSV empty/failed but Memory has data
    if not today_trades and DAILY_LOG:
        today_trades = DAILY_LOG

    if not today_trades:
        telegram_send("📊 <b>EOD REPORT:</b> No trades triggered today.")
        EOD_SENT = True
        return

    wins = [t for t in today_trades if t.get("result") == "WIN"]
    losses = [t for t in today_trades if t.get("result") == "LOSS"]
    
    total = len(today_trades)
    win_rate = (len(wins) / total) * 100 if total > 0 else 0
    
    # Calculate Net PnL (Use recorded pnl_r if available, else standard 1:2)
    net_r = 0
    for t in today_trades:
        if "pnl_r" in t:
            net_r += float(t["pnl_r"])
        else:
            net_r += 2.0 if t["result"] == "WIN" else -1.0
    
    report = (
        f"📊 <b>MARKET CLOSING REPORT</b>\n\n"
        f"Total Signals: {total}\n"
        f"✅ Wins: {len(wins)}\n"
        f"❌ Losses: {len(losses)}\n"
        f"📈 Win Rate: {round(win_rate, 1)}%\n"
        f"💰 Net Profit: {round(net_r, 2)}R\n\n"
        f"<b>📝 TRADES:</b>\n"
    )

    for t in today_trades:
        icon = "✅" if t.get('result') == "WIN" else "❌"
        # Handle float/string conversion safely
        entry = t.get('entry', '0')
        exit_p = t.get('exit_price', '0')
        
        report += (
            f"{icon} <b>{t['symbol']}</b> ({t['direction']})\n"
            f"   🔮 {t['setup']} | {t['result']}\n"
            f"   entry: {entry} ➔ exit: {exit_p}\n\n"
        )

    telegram_send(report)
    
    # Phase 6: Paper mode daily summary
    if PAPER_MODE:
        telegram_send(paper_daily_summary())
    
    EOD_SENT = True

# =====================================================
# MAIN EXECUTION LOOP (PRODUCTION SAFE)
# =====================================================

def cleanup_structure_state(max_age_seconds=3600):
    now = now_ist()
    for k in list(STRUCTURE_STATE.keys()):
        state = STRUCTURE_STATE.get(k)
        if not state:
            continue
        if (now - state.get("time", now)).total_seconds() > max_age_seconds:
            STRUCTURE_STATE.pop(k, None)

def _attempt_auto_login() -> bool:
    """
    Cloud auto-refresh: call auto_login.auto_login() to get a fresh Kite token.
    Runs entirely via env vars (KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET, REDIS_URL).
    Returns True if a new token was obtained and stored in Redis; False on failure.
    Called by run_engine_main() whenever run_live_mode() exits (token expired / invalid).
    """
    required = ("KITE_USER_ID", "KITE_PASSWORD", "KITE_TOTP_SECRET")
    if not all(os.getenv(k, "").strip() for k in required):
        logging.warning(
            "[auto_login] Skipping — KITE_PASSWORD or KITE_TOTP_SECRET not set. "
            "Add these to Railway engine service variables to enable cloud auto-refresh."
        )
        return False
    try:
        import auto_login as _al  # Import here to avoid module-level logging conflict at startup
        logging.info("[auto_login] Attempting cloud token refresh...")
        success = _al.auto_login()
        if success:
            logging.info("[auto_login] ✅ Token refreshed successfully — engine will pick it up on next reinit")
        else:
            logging.error("[auto_login] ❌ Token refresh failed — will retry after backoff")
        return success
    except Exception as e:
        logging.error("[auto_login] Token refresh error: %s", e)
        return False


def check_token_age(max_hours=20) -> bool:
    """
    F4.4: Check if Kite token is stale (>20 hours old OR from a previous calendar day).
    Zerodha tokens expire at midnight IST (00:00) regardless of when they were issued,
    so a token issued yesterday is ALWAYS expired even if it's only a few hours old.
    - Redis token: check kite:token_ts. Reject if age > max_hours OR date != today.
    - Env var / file: assume user manages freshness (no calendar check).
    Returns True if token is fresh enough, False if expired.
    """
    try:
        # Redis timestamp (set alongside token by auto_login.py / zerodha_login.py)
        redis_url = os.getenv("REDIS_URL", "").strip()
        if redis_url:
            try:
                import redis as _redis
                r = _redis.from_url(redis_url, decode_responses=True)
                tok = r.get("kite:access_token")
                if tok and tok.strip():
                    ts = r.get("kite:token_ts")
                    if ts:
                        from datetime import datetime as _dt
                        token_time = _dt.fromisoformat(ts)
                        now_ist_dt = now_ist()
                        age_hours = (now_ist_dt - token_time).total_seconds() / 3600
                        # Zerodha kills tokens at midnight IST — reject if from a previous day
                        if token_time.date() < now_ist_dt.date():
                            print(f"❌ Redis token is from {token_time.date()} (today is {now_ist_dt.date()}) — EXPIRED at midnight")
                            logging.critical("Token from previous day — expired at midnight IST")
                            return False
                        if age_hours > max_hours:
                            print(f"❌ Redis token is {age_hours:.1f}h old (max {max_hours}h) — STALE")
                            logging.critical("Token expired: %.1fh old (Redis)", age_hours)
                            return False
                        print(f"✅ Token age (Redis): {age_hours:.1f}h (max: {max_hours}h, date: {token_time.date()})")
                        return True
                    # No timestamp stored — trust the profile() call in _reinit_kite
                    print("✅ Token from Redis (no age info — validated by profile())")
                    return True
            except Exception as redis_e:
                logging.debug("Redis token age check: %s", redis_e)

        if os.getenv("KITE_ACCESS_TOKEN", "").strip():
            print("✅ Token from KITE_ACCESS_TOKEN env (refresh daily via zerodha_login)")
            return True
        token_file = "access_token.txt"
        if not os.path.exists(token_file):
            print("❌ access_token.txt NOT FOUND — run zerodha_login.py or set KITE_ACCESS_TOKEN")
            telegram_send("🛑 <b>TOKEN MISSING</b>\naccess_token.txt not found. Run zerodha_login.py!")
            return False
        mod_time = datetime.fromtimestamp(os.path.getmtime(token_file))
        age_hours = (now_ist() - mod_time).total_seconds() / 3600
        if age_hours > max_hours:
            msg = (f"🛑 <b>TOKEN EXPIRED</b>\n"
                   f"access_token.txt is {age_hours:.1f} hours old (max: {max_hours}h)\n"
                   f"Run zerodha_login.py to refresh!")
            print(f"❌ Token is {age_hours:.1f} hours old (max {max_hours}h) — STALE")
            telegram_send(msg)
            logging.critical("Token expired: %.1fh old", age_hours)
            return False
        print(f"✅ Token age: {age_hours:.1f}h (max: {max_hours}h)")
        return True
    except Exception as e:
        logging.error("Token age check failed: %s", e)
        return True

def test_data_connection():
    """
    Startup Health Check.
    Verifies that we can actually fetch data from Kite.
    """
    print("\n" + "-"*30)
    print("🏥 SYSTEM HEALTH CHECK")
    print("-" * 30)
    
    try:
        # Test 1: LTP Fetch
        test_symbol = "NSE:NIFTY 50"
        ltp = fetch_ltp(test_symbol)
        
        if ltp:
            print(f"✅ Data Connection Active: {test_symbol} @ {ltp}")
            logging.info(f"Health Check Passed: {test_symbol} LTP={ltp}")
            return True
        else:
            print(f"❌ Data Connection FAILED: Could not fetch LTP for {test_symbol}")
            print("⚠️ POSSIBLE CAUSES: Token Expired, API Down, or Invalid Symbol")
            logging.critical("Health Check Failed: LTP Fetch returned None")
            return False
            
    except Exception as e:
        print(f"❌ CRITICAL ERROR during Health Check: {e}")
        logging.critical(f"Health Check Exception: {e}")
        return False

# =====================================================
# =====================================================
# PART 6 — SWING SCANNER (delegated to engine.swing)
# =====================================================

def run_swing_scan():
    """Thin wrapper — delegates to engine.swing module."""
    if INDEX_ONLY:
        return  # Skip swing scan in INDEX_ONLY mode
    _swing_run_scan(fetch_ohlc, get_stock_universe, telegram_send)

def monitor_swing_trades():
    """Thin wrapper — delegates to engine.swing module."""
    _swing_monitor(fetch_ltp, telegram_send)

# =====================================================
# OPTIONS SIGNAL ENGINE (delegated to engine.options)
# LiveTickStore and BankNiftySignalEngine imported at top
# =====================================================

# =====================================================
# GLOBAL STATE PERSISTENCE (ZONE & STRUCTURE)
# =====================================================
def save_engine_states():
    try:
        with open("engine_states_backup.pkl", "wb") as f:
            pickle.dump({
                "STRUCTURE_STATE": STRUCTURE_STATE,
                "ZONE_STATE": ZONE_STATE,
                "SETUP_D_STATE": SETUP_D_STATE,
                "SETUP_E_STATE": SETUP_E_STATE
            }, f)
    except Exception as e:
        if DEBUG_MODE: print(f"Failed to save engine states: {e}")
    
    # F1.3: Persist circuit breaker state to SQLite
    try:
        db.set_value("engine_state", "circuit_breaker", {
            "daily_pnl_r": DAILY_PNL_R,
            "consecutive_losses": CONSECUTIVE_LOSSES,
            "circuit_breaker_active": CIRCUIT_BREAKER_ACTIVE,
            "daily_signal_count": DAILY_SIGNAL_COUNT,
            "saved_date": now_ist().date().isoformat()
        })
    except Exception as e:
        logging.error(f"Failed to persist circuit breaker state: {e}")

def load_engine_states():
    global STRUCTURE_STATE, ZONE_STATE, SETUP_D_STATE, SETUP_E_STATE
    global DAILY_PNL_R, CONSECUTIVE_LOSSES, CIRCUIT_BREAKER_ACTIVE, DAILY_SIGNAL_COUNT
    try:
        import os
        if os.path.exists("engine_states_backup.pkl"):
            with open("engine_states_backup.pkl", "rb") as f:
                data = pickle.load(f)
                STRUCTURE_STATE.update(data.get("STRUCTURE_STATE", {}))
                ZONE_STATE.update(data.get("ZONE_STATE", {}))
                SETUP_D_STATE.update(data.get("SETUP_D_STATE", {}))
                SETUP_E_STATE.update(data.get("SETUP_E_STATE", {}))
            print("[INFO] Reloaded internal engine state from disk.")
    except Exception as e:
        if DEBUG_MODE: print(f"Failed to load engine states: {e}")
    
    # F1.2: Restore active trades from SQLite
    load_active_trades()
    
    # F1.3: Restore circuit breaker state (only if same trading day)
    try:
        cb_data = db.get_value("engine_state", "circuit_breaker", default=None)
        if cb_data and cb_data.get("saved_date") == now_ist().date().isoformat():
            DAILY_PNL_R = cb_data.get("daily_pnl_r", 0.0)
            CONSECUTIVE_LOSSES = cb_data.get("consecutive_losses", 0)
            CIRCUIT_BREAKER_ACTIVE = cb_data.get("circuit_breaker_active", False)
            DAILY_SIGNAL_COUNT = cb_data.get("daily_signal_count", 0)
            logging.info(f"💾 Restored circuit breaker: PnL={DAILY_PNL_R:.1f}R, "
                         f"Streak={CONSECUTIVE_LOSSES}, Signals={DAILY_SIGNAL_COUNT}, Active={CIRCUIT_BREAKER_ACTIVE}")
        else:
            logging.info("Circuit breaker state from previous day — starting fresh.")
    except Exception as e:
        logging.error(f"Failed to load circuit breaker state: {e}")

    # F4.3: Restore multi-day halt state
    global MULTI_DAY_HALT_UNTIL
    try:
        halt_data = db.get_value("engine_state", "multi_day_halt", default=None)
        if halt_data and halt_data.get("halt_until"):
            halt_until = datetime.fromisoformat(halt_data["halt_until"])
            if now_ist() < halt_until:
                MULTI_DAY_HALT_UNTIL = halt_until
                logging.warning(f"💾 Restored multi-day halt: until {halt_until.strftime('%Y-%m-%d %H:%M')}")
            else:
                logging.info("Multi-day halt expired — cleared.")
    except Exception as e:
        logging.error(f"Failed to load multi-day halt state: {e}")

def _reinit_kite():
    """Re-read token from Redis/env/file and re-create the kite instance.
    Called by run_live_mode on each attempt so Railway retry loops work
    after morning_login.bat updates the token in Redis."""
    global kite, _current_kite_token, manual_handler, option_monitor, bn_signal_engine
    try:
        api_key = get_api_key()
        access_token = get_access_token()
        if not api_key or not access_token:
            logging.warning("[reinit_kite] No api_key or access_token available")
            return False
        if kite is not None and access_token == _current_kite_token:
            return True
        new_kite = KiteConnect(api_key=api_key)
        new_kite.set_access_token(access_token)
        _kite_call(new_kite.profile, timeout=_KITE_TIMEOUT_SEC)
        kite = new_kite
        _current_kite_token = access_token
        mask_tok = access_token[:6] + "..." + access_token[-4:] if len(access_token) >= 10 else "****"
        logging.info("[reinit_kite] Kite session refreshed (token=%s)", mask_tok)
        try:
            manual_handler = ManualTradeHandlerV2(kite)
        except Exception:
            pass
        try:
            option_monitor = OptionMonitor(kite)
            option_monitor.initialize()
        except Exception:
            pass
        try:
            bn_signal_engine = BankNiftySignalEngine(kite, telegram_fn=telegram_send)
            bn_signal_engine.initialize()
        except Exception:
            pass
        # Refresh instruments cache opportunistically so token→data recovery is complete.
        try:
            _load_kite_instruments_cache(force=True)
        except Exception:
            pass
        return True
    except Exception as e:
        logging.warning("[reinit_kite] Failed: %s", e)
        return False


def _kite_health_check() -> tuple[bool, str]:
    """
    Strict Kite health probe.
    Returns (connected, reason). Uses profile() every check (as requested).
    """
    if kite is None:
        return False, "kite_not_initialized"
    try:
        _kite_call(kite.profile, timeout=_KITE_TIMEOUT_SEC)
        return True, ""
    except Exception as e:
        return False, f"profile_failed: {e}"


def _load_kite_instruments_cache(force: bool = False) -> int:
    """
    Load NSE instruments into INSTRUMENT_TOKEN_MAP.
    This removes dependency on per-symbol ltp() for instrument_token resolution,
    which is fragile during/after token refresh.
    """
    global INSTRUMENT_TOKEN_MAP, _INSTRUMENTS_LOADED_AT
    if kite is None:
        return 0
    now = now_ist()
    if not force and _INSTRUMENTS_LOADED_AT and (now - _INSTRUMENTS_LOADED_AT).total_seconds() < 3600:
        return len(INSTRUMENT_TOKEN_MAP)
    instruments = _kite_call(kite.instruments, "NSE", timeout=_KITE_TIMEOUT_SEC)
    token_map: dict[str, int] = {}
    for ins in instruments or []:
        try:
            exch = ins.get("exchange")
            tsym = ins.get("tradingsymbol")
            tok = ins.get("instrument_token")
            if not (exch and tsym and tok):
                continue
            # Canonical map key used across this engine: "NSE:FOO"
            token_map[f"{exch}:{tsym}"] = int(tok)
        except Exception:
            continue
    INSTRUMENT_TOKEN_MAP = token_map
    _INSTRUMENTS_LOADED_AT = now
    logging.info("[instruments] Loaded %d NSE instruments into token map", len(INSTRUMENT_TOKEN_MAP))
    return len(INSTRUMENT_TOKEN_MAP)


_FORCE_FULL_SCAN_ONCE = False


def reinitialize_engine(reason: str) -> None:
    """
    FULL engine reinitialization after token refresh / recovery.
    - reconnect kite (via _reinit_kite)
    - reload instruments cache
    - rebuild stock universe (cloud-safe)
    - reset internal caches/state that can preserve a failed scan state
    """
    global _FORCE_FULL_SCAN_ONCE, STOCK_UNIVERSE
    logging.warning("[reinit_engine] Starting full reinitialization (%s)", reason)

    # 1) Ensure Kite session is fresh (token may have changed)
    _reinit_kite()

    # 2) Reload instruments + universe
    try:
        _load_kite_instruments_cache(force=True)
    except Exception as e:
        logging.warning("[reinit_engine] instruments reload failed: %s", e)

    try:
        if not INDEX_ONLY and kite is not None:
            STOCK_UNIVERSE = load_stock_universe(kite)
    except Exception as e:
        logging.warning("[reinit_engine] universe rebuild failed: %s", e)

    # 3) Reset caches/state (non-strategy; purely lifecycle hygiene)
    try:
        TOKEN_CACHE.clear()
    except Exception:
        pass
    try:
        with OHLC_CACHE_LOCK:
            OHLC_CACHE.clear()
    except Exception:
        pass
    try:
        HTF_CACHE.clear()
        HTF_CACHE_TIME.clear()
    except Exception:
        pass
    try:
        EARLY_WARNING_STATE.clear()
    except Exception:
        pass
    try:
        reset_oi_state()
    except Exception:
        pass

    # 4) Re-init dependent sub-engines (connection state)
    try:
        if option_monitor:
            option_monitor.initialize()
    except Exception as e:
        logging.warning("[reinit_engine] option_monitor init failed: %s", e)
    try:
        if bn_signal_engine:
            bn_signal_engine.initialize()
    except Exception as e:
        logging.warning("[reinit_engine] bn_signal_engine init failed: %s", e)

    # 5) Force full scan next loop
    _FORCE_FULL_SCAN_ONCE = True
    logging.warning(
        "[reinit_engine] Done. kite=%s instruments=%d universe=%d force_full_scan=%s",
        "ok" if kite is not None else "none",
        len(INSTRUMENT_TOKEN_MAP),
        len(STOCK_UNIVERSE),
        _FORCE_FULL_SCAN_ONCE,
    )


def run_live_mode():
    global EOD_SENT
    global DAILY_PNL_R, CONSECUTIVE_LOSSES, CIRCUIT_BREAKER_ACTIVE
    global DAILY_SIGNAL_COUNT
    global OI_BIAS_SCANNED_920, OI_BIAS_LOCKED
    global ENGINE_LAST_LOOP_AT
    global COOLDOWN_UNTIL  # F1.4
    OI_BIAS_SCANNED_920 = False
    OI_BIAS_LOCKED = False
    
    # F4.5 & F4.6: Heartbeat (dedicated thread) and error tracking state
    _last_heartbeat = now_ist()
    _last_lock_refresh = t.time()
    _last_token_refresh = t.time()
    _TOKEN_REFRESH_INTERVAL_SEC = 120
    _consecutive_loop_errors = 0
    _HEARTBEAT_INTERVAL_MIN = 60
    _MAX_CONSECUTIVE_ERRORS = 5

    # Token-triggered signal activation:
    # _fresh_token_today  = True after user runs RUN_ENGINE_ON_RAILWAY.bat during the day.
    #                       Overrides the 09:00 signal-window gate so signals fire immediately
    #                       regardless of what time the token was refreshed.
    # _catch_up_scan_done = ensures the missed-signals catch-up runs only once per token refresh.
    _fresh_token_today: bool = False
    _catch_up_scan_done: bool = False
    _token_activated_at: str = ""       # IST time string shown in Telegram
    _last_known_token_ts: str = ""      # Redis kite:token_ts value at last check
    
    # 💾 LOAD PERSISTED MEMORY
    load_engine_states()

    # Strict bootstrap: do not proceed unless Kite is healthy.
    if not _reinit_kite():
        logging.warning("Kite init failed — token may not be set yet. Will retry via Railway recovery loop.")
        return

    if not check_token_age(max_hours=20):
        logging.critical("TOKEN TOO OLD — awaiting refresh")
        return

    ok, reason = _kite_health_check()
    if not ok:
        logging.critical("Kite unhealthy at startup (%s) — awaiting refresh", reason)
        return

    if not test_data_connection():
        logging.critical("DATA CONNECTION FAILED at startup — token may be expired or invalid")
        return

    # Phase 6: Paper mode banner
    if PAPER_MODE:
        print(paper_mode_banner())
    
    mode_label = "PAPER" if PAPER_MODE else "LIVE"
    active_labels = [k for k, v in ACTIVE_STRATEGIES.items() if v]
    active_text = ", ".join(active_labels) if active_labels else "None"
    print("\n" + "="*50)
    print(f"🚀 V4 INSTITUTIONAL ENGINE :: {mode_label}")
    print(f"💎 STATUS: {ENGINE_MODE} MODE | Active Setups: {active_text}")
    print("⚡ EMA 10/20 CROSSOVER: ON (NIFTY/BANKNIFTY 5m)")
    print("📊 BANK NIFTY OPTIONS SIGNAL ENGINE: ON")
    print("="*50 + "\n")
    _send_startup_telegram_bundle()

    while True:
        try:
            ENGINE_LAST_LOOP_AT = now_ist()
            # Notify watchdog that the main loop is alive + set stage
            try:
                import engine_runtime
                engine_runtime.set_engine_stage("LOOP_START")
                engine_runtime.write_last_cycle()
            except Exception:
                pass
            cleanup_structure_state()
            now = now_ist().time()

            # ── Kite health gate (every cycle) ───────────────────────────────
            # Prevent silent failure: if Kite is not connected, do NOT scan.
            _kite_ok, _kite_reason = _kite_health_check()
            logging.info(
                "[kite] connected=%s reason=%s token_map=%d universe=%d",
                _kite_ok,
                _kite_reason or "-",
                len(INSTRUMENT_TOKEN_MAP),
                len(get_stock_universe()),
            )
            if not _kite_ok:
                logging.error("[kite] DISCONNECTED — pausing scans and attempting recovery (%s)", _kite_reason)
                try:
                    import engine_runtime
                    engine_runtime.set_engine_stage("KITE_DISCONNECTED")
                except Exception:
                    pass

                # Trigger token refresh (cloud automation) then wait until Kite is valid.
                _attempt_auto_login()
                _recovered = False
                for _i in range(12):  # up to ~60s (12 * 5s)
                    try:
                        _reinit_kite()
                        _ok2, _r2 = _kite_health_check()
                        if _ok2 and check_token_age(max_hours=20):
                            _recovered = True
                            break
                        logging.warning("[kite] still unhealthy (%s) — retrying...", _r2 or "unknown")
                    except Exception:
                        pass
                    try:
                        import engine_runtime
                        engine_runtime.safe_sleep(5)
                    except Exception:
                        t.sleep(5)

                if not _recovered:
                    logging.error("[kite] Recovery failed — skipping this cycle")
                    continue

                # Full reinit after recovery, then force a full scan next cycle
                reinitialize_engine(reason="kite_recovered")
                telegram_send("✅ <b>KITE RECOVERED</b>\nReinitialized engine state. Forcing a full scan now.")
                # Do not run partial scanning in the same cycle; start clean next loop
                continue
            # ─────────────────────────────────────────────────────────────────

            # Railway 24/7: lock refresh every 2 min (heartbeat runs in dedicated thread)
            try:
                import engine_runtime
                if t.time() - _last_lock_refresh >= engine_runtime.ENGINE_LOCK_REFRESH_INTERVAL_SEC:
                    if not engine_runtime.refresh_engine_lock():
                        logging.warning("Lost engine lock; another instance may have taken over. Exiting.")
                        print("🛑 Engine exiting: Redis lock lost (another instance may be running). Check Railway or other running engine.")
                        _shutdown_handler("redis_lock_lost")
                        return
                    _last_lock_refresh = t.time()
            except Exception as _e:
                logging.debug("Engine runtime lock refresh: %s", _e)

            # NOTE: Pre-midnight token refresh is handled by GitHub Actions now.
            # Keeping a second independent re-login path here causes race conditions
            # (cron delays, token_ts/date dedup, partial engine state) and is disabled.

            # Kite token refresh: re-read from Redis/env/file so morning_login.bat takes effect without restart
            global _current_kite_token
            if kite and (t.time() - _last_token_refresh) >= _TOKEN_REFRESH_INTERVAL_SEC:
                _last_token_refresh = t.time()
                try:
                    new_token = get_access_token()
                    if new_token and new_token != _current_kite_token:
                        kite.set_access_token(new_token)
                        _kite_call(kite.profile, timeout=_KITE_TIMEOUT_SEC)
                        _current_kite_token = new_token
                        logging.info("Kite token refreshed from central store (Redis/env/file) — OI/signals will use new token")

                        # ── Token-triggered signal activation ───────────────────
                        # Read kite:token_ts from Redis to confirm this is a fresh
                        # login done TODAY (not a stale token from yesterday).
                        try:
                            _redis_url = os.getenv("REDIS_URL", "").strip()
                            if _redis_url:
                                import redis as _redis_mod
                                _r = _redis_mod.from_url(_redis_url, decode_responses=True)
                                _tok_ts = _r.get("kite:token_ts") or ""
                                if _tok_ts and _tok_ts != _last_known_token_ts:
                                    from datetime import datetime as _dt2
                                    _ts_obj = _dt2.fromisoformat(_tok_ts)
                                    _today = now_ist().date()
                                    _in_day = time(9, 0) <= now_ist().time() <= time(16, 10)
                                    if _ts_obj.date() == _today and _in_day:
                                        _last_known_token_ts = _tok_ts
                                        _fresh_token_today = True
                                        # After token refresh we force a FULL reinit + FULL scan,
                                        # so we explicitly skip the catch-up scan path.
                                        _catch_up_scan_done = True
                                        _token_activated_at = now_ist().strftime("%H:%M")
                                        logging.info(
                                            "Fresh token detected (ts=%s) — signal window unlocked at %s IST",
                                            _tok_ts, _token_activated_at,
                                        )
                                        # Full lifecycle recovery: reset engine state + force full scan.
                                        reinitialize_engine(reason="token_refreshed_in_loop")
                        except Exception as _ts_e:
                            logging.debug("Token-ts Redis check failed: %s", _ts_e)
                        # ────────────────────────────────────────────────────────

                except Exception as _te:
                    logging.warning("Kite token refresh failed (using existing): %s", _te)

            # F4.6: Reset error counter on successful cycle start
            _consecutive_loop_errors = 0

            # 📊 EOD REPORT (4:00 PM) - Check BEFORE Market Guard
            if now >= time(16, 0) and not EOD_SENT:
                send_eod_report()

            # -----------------------------
            # MARKET HOURS GUARD
            # -----------------------------
            if not is_market_open():
                # Reset EOD flag for next day if it's early morning
                if now < time(9, 0):
                     EOD_SENT = False
                     SETUP_C_DAILY_COUNT.clear()
                     TRADED_TODAY.clear()  # P0 FIX: Reset daily trade dedup
                     DAILY_SIGNAL_COUNT = 0
                     eng_cfg.SWING_SCAN_SENT = False  # Reset swing scan flag
                     eng_cfg.ACTIVE_SWING_TRADES.clear()  # Reset swing monitors
                     # W2: Reset circuit breaker for new day
                     DAILY_PNL_R = 0.0
                     CONSECUTIVE_LOSSES = 0
                     CIRCUIT_BREAKER_ACTIVE = False
                     OI_BIAS_SCANNED_920 = False
                     OI_BIAS_LOCKED = False
                     reset_zone_tap_state()  # Reset zone-tap state for new day
                     ZONE_STATE.clear()
                     STRUCTURE_STATE.clear()
                     SETUP_D_STATE.clear()
                     SETUP_E_STATE.clear()
                     EARLY_WARNING_STATE.clear()
                     EMA_LAST_PROCESSED.clear()
                     DAILY_LOG.clear()
                     MANUAL_ORDER_CACHE.clear()
                     with OHLC_CACHE_LOCK:
                         OHLC_CACHE.clear()  # Flush stale candle data from previous day
                     reset_market_state()
                     reset_oi_state()
                     reset_oi_sc_state()
                
                try:
                    update_engine_state(market="CLOSED")
                except Exception:
                    pass
                _publish_redis_snapshot()
                print(f"[{now_ist().strftime('%H:%M:%S')}] 🛑 Market Closed. Engine sleeping for 5 minutes...")
                try:
                    import engine_runtime
                    engine_runtime.set_engine_stage("MARKET_CLOSED_SLEEP")
                    engine_runtime.safe_sleep(300)
                except Exception:
                    t.sleep(10)
                continue

            # F4.5: HEARTBEAT — send Telegram alive ping every 30 minutes (market hours only)
            try:
                update_engine_state(market="OPEN")
                print("API update:", get_engine_state_snapshot())
            except Exception:
                pass
            if (now_ist() - _last_heartbeat).total_seconds() >= _HEARTBEAT_INTERVAL_MIN * 60:
                trades_count = len(ACTIVE_TRADES)
                hb_msg = (f"💚 <b>Engine Alive</b> | {now_ist().strftime('%H:%M')}\n"
                          f"Active Trades: {trades_count} | PnL: {DAILY_PNL_R:.1f}R | "
                          f"Signals: {DAILY_SIGNAL_COUNT}/{MAX_DAILY_SIGNALS}")
                telegram_send(hb_msg)
                _last_heartbeat = now_ist()

            # ─── SIGNAL WINDOW GATE ──────────────────────────────────────────────
            # Signals are active during 09:00–16:10 IST on trading days.
            # EXCEPTION: if the user ran the login bat at any time today and we
            # detected a fresh token, _fresh_token_today overrides the clock check
            # so signals fire immediately without waiting for 09:00 tomorrow.
            _in_signal_window = is_signal_window() or _fresh_token_today

            if not _in_signal_window:
                if not hasattr(run_live_mode, '_signal_sleep_notified'):
                    run_live_mode._signal_sleep_notified = False
                if not run_live_mode._signal_sleep_notified:
                    run_live_mode._signal_sleep_notified = True
                    telegram_send(
                        f"🌙 <b>Signal system PAUSED</b> — {now_ist().strftime('%H:%M')} IST\n"
                        "No new trade signals until 09:00 tomorrow.\n"
                        "Website & health endpoint remain live."
                    )
                    logging.info("Signal window closed at %s IST — engine alive, signals paused.", now_ist().strftime("%H:%M"))
                    # EOD: refresh TTL on today's signals so they survive 30 days in Redis
                    try:
                        from dashboard.backend.cache import _get_redis
                        from datetime import date as _date
                        _r = _get_redis()
                        if _r:
                            _sig_key = f"signals:today:{_date.today().isoformat()}"
                            _r.expire(_sig_key, 2592000)
                            logging.info("[EOD] Refreshed Redis TTL for %s to 30 days.", _sig_key)
                    except Exception as _eod_exc:
                        logging.debug("[EOD] Redis TTL refresh failed: %s", _eod_exc)
                _publish_redis_snapshot()
                try:
                    import engine_runtime
                    engine_runtime.set_engine_stage("SIGNAL_WINDOW_SLEEP")
                    engine_runtime.safe_sleep(60)
                except Exception:
                    t.sleep(10)
                continue
            else:
                # Reset the "paused" notification flag when window re-opens (clock OR fresh token)
                if hasattr(run_live_mode, '_signal_sleep_notified') and run_live_mode._signal_sleep_notified:
                    run_live_mode._signal_sleep_notified = False

                # ── Change 3: Telegram notification on fresh-token activation ──
                if _fresh_token_today and not _catch_up_scan_done:
                    _elapsed_min = int(
                        (now_ist() - now_ist().replace(hour=9, minute=0, second=0, microsecond=0)).total_seconds() / 60
                    )
                    _act_msg = (
                        f"🔑 <b>TOKEN REFRESHED — Signals ACTIVE</b>\n"
                        f"Token updated at {_token_activated_at} IST\n"
                        f"Scanning from now until 16:10 IST.\n"
                    )
                    if _elapsed_min > 15:
                        _act_msg += f"⚠️ Market has been open {_elapsed_min} min — running catch-up scan now."
                    telegram_send(_act_msg)
                    logging.info("Signal window unlocked by fresh token at %s IST.", _token_activated_at)

                # Clock-based 09:00 re-open notification (non-token path)
                elif not _fresh_token_today:
                    if not hasattr(run_live_mode, '_clock_open_notified'):
                        run_live_mode._clock_open_notified = False
                    if not run_live_mode._clock_open_notified:
                        run_live_mode._clock_open_notified = True
                        telegram_send(
                            f"🌅 <b>Signal system ACTIVE</b> — {now_ist().strftime('%H:%M')} IST\n"
                            "Trade signals are now enabled. Good morning!"
                        )
                        logging.info("Signal window opened at %s IST.", now_ist().strftime("%H:%M"))
            # ─────────────────────────────────────────────────────────────────────

            # ── Change 4: CATCH-UP SCAN — runs once when token is refreshed late ──
            # Triggers an immediate scan so setups already forming are not missed.
            # Signals are tagged [CATCH-UP] and ranked the same as normal signals.
            if _fresh_token_today and not _catch_up_scan_done:
                _catch_up_scan_done = True   # mark immediately so a slow scan doesn't re-trigger
                _catchup_elapsed = int(
                    (now_ist() - now_ist().replace(hour=9, minute=0, second=0, microsecond=0)).total_seconds() / 60
                )
                if _catchup_elapsed > 15:    # only worth running if market has been open >15 min
                    try:
                        logging.info("[CATCH-UP] Running immediate scan (%d min since 09:00)", _catchup_elapsed)
                        _cu_universe = build_scan_universe(get_stock_universe())
                        _cu_signals = []
                        for _cu_sym in _cu_universe:
                            try:
                                _cu_sigs = scan_symbol(_cu_sym)
                                if _cu_sigs:
                                    _cu_signals.extend(_cu_sigs)
                            except Exception:
                                pass
                        if _cu_signals:
                            # Rank the same way as the normal signal loop
                            _cu_ranked = sorted(
                                _cu_signals,
                                key=lambda s: (s.get("smc_score", 0) + s.get("ai_score", 0) / 10),
                                reverse=True,
                            )
                            _cu_sent = 0
                            for _cu_sig in _cu_ranked[:5]:
                                try:
                                    _cu_text = (
                                        f"⏰ <b>[CATCH-UP SCAN]</b> — token activated {_token_activated_at} IST\n"
                                        f"Market open for {_catchup_elapsed} min — setup already forming:\n\n"
                                        f"<b>{_cu_sig['symbol']}</b>  |  {_cu_sig['direction']}  |  {_cu_sig['setup']}\n"
                                        f"Entry: {_cu_sig['entry']}  |  SL: {_cu_sig['sl']}  |  Target: {_cu_sig['target']}\n"
                                        f"RR: {_cu_sig['rr']}  |  SMC Score: {_cu_sig.get('smc_score', 'N/A')}/10\n"
                                        f"<i>⚠️ Price-based on current market — verify before acting.</i>"
                                    )
                                    _cu_sid = f"catchup_{_cu_sig['symbol'].replace(':', '_')}_{_cu_sig['setup']}_{now_ist().strftime('%Y%m%d')}"
                                    telegram_send_signal(
                                        _cu_text,
                                        signal_id=_cu_sid,
                                        signal_meta={
                                            "signal_kind": "CATCHUP",
                                            "symbol": _cu_sig.get("symbol"),
                                            "direction": _cu_sig.get("direction"),
                                            "strategy_name": _cu_sig.get("setup"),
                                            "entry": _cu_sig.get("entry"),
                                            "stop_loss": _cu_sig.get("sl"),
                                            "target1": _cu_sig.get("target"),
                                            "score": _cu_sig.get("smc_score"),
                                            "confidence": _cu_sig.get("ai_score"),
                                        },
                                    )
                                    _cu_sent += 1
                                except Exception as _cu_e:
                                    logging.warning("[CATCH-UP] Signal send error: %s", _cu_e)
                            if _cu_sent == 0:
                                telegram_send(
                                    f"🔍 <b>Catch-up scan complete</b> — no setups found at {now_ist().strftime('%H:%M')} IST.\n"
                                    "Engine scanning normally from here."
                                )
                            logging.info("[CATCH-UP] Sent %d/%d signals", _cu_sent, len(_cu_ranked))
                        else:
                            telegram_send(
                                f"🔍 <b>Catch-up scan complete</b> — no setups found at {now_ist().strftime('%H:%M')} IST.\n"
                                "Engine scanning normally from here."
                            )
                            logging.info("[CATCH-UP] No signals found on immediate scan")
                    except Exception as _cu_err:
                        logging.error("[CATCH-UP] Scan failed: %s", _cu_err)
            # ─────────────────────────────────────────────────────────────────────

            # 🌅 MORNING WATCHLIST (9:15 – 9:20)
            if time(9, 15) <= now <= time(9, 20):
                send_morning_watchlist()

            # 💎 SWING SCANNER (9:30 AM — live market, runs once daily)
            if time(9, 30) <= now <= time(9, 45) and not eng_cfg.SWING_SCAN_SENT:
                try:
                    run_swing_scan()
                except Exception as e:
                    print(f"[SWING] Scan failed: {e}")
                    logging.error(f"Swing scan failed: {e}")

            # 📊 OI BIAS SCAN (9:20 AM — first options flow read)
            if time(9, 20) <= now <= time(9, 21) and not OI_BIAS_SCANNED_920:
                if bn_signal_engine:
                    try:
                        print("[OI BIAS] Running 9:20 AM first scan...")
                        bn_signal_engine.run_bias_scan()
                        OI_BIAS_SCANNED_920 = True
                        bias = bn_signal_engine.directional_bias
                        print(f"[OI BIAS] Preliminary: {bias}")
                    except Exception as e:
                        print(f"[OI BIAS] 9:20 scan failed: {e}")

            # 📊 OI BIAS LOCK (9:30 AM — confirm and freeze bias)
            if time(9, 30) <= now <= time(9, 31) and not OI_BIAS_LOCKED:
                if bn_signal_engine:
                    try:
                        print("[OI BIAS] Locking 9:30 AM bias...")
                        bn_signal_engine.lock_bias()
                        OI_BIAS_LOCKED = True
                        bias = bn_signal_engine.directional_bias
                        print(f"[OI BIAS] LOCKED for today: {bias}")
                    except Exception as e:
                        print(f"[OI BIAS] 9:30 lock failed: {e}")
            # 💎 SWING TRADE MONITOR (every cycle — check SL/Target)
            if eng_cfg.ACTIVE_SWING_TRADES:
                try:
                    monitor_swing_trades()
                except Exception as e:
                    logging.error(f"Swing monitor error: {e}")
        
            all_signals = []

            # F4.3: Multi-day drawdown check (runs once per cycle, lightweight)
            if check_multi_day_drawdown():
                remaining = (MULTI_DAY_HALT_UNTIL - now_ist()).total_seconds() / 3600
                print(f"[{now_ist().strftime('%H:%M:%S')}] 🛑 Multi-day halt: {remaining:.1f}hrs remaining — monitoring only")
                for sym in [t['symbol'] for t in ACTIVE_TRADES]:
                    price = fetch_ltp(sym)
                    if price:
                        monitor_active_trades(sym, price)
                wait_for_next_minute()
                continue

            # F4.1: RISK MANAGEMENT — check daily limits before scanning
            can_trade, risk_reason = risk_mgr.can_trade_today()
            if not can_trade:
                logging.info(f"🛡️ RISK BLOCKED: {risk_reason}")
                # Still monitor existing trades
                for sym in [t['symbol'] for t in ACTIVE_TRADES]:
                    price = fetch_ltp(sym)
                    if price:
                        monitor_active_trades(sym, price)
                wait_for_next_minute()
                continue

            # 🛑 CIRCUIT BREAKER CHECK (W2: skip scanning if daily loss exceeded)
            if CIRCUIT_BREAKER_ACTIVE:
                print(f"[{now.strftime('%H:%M:%S')}] 🛑 Circuit breaker active (PnL: {DAILY_PNL_R:.1f}R) — monitoring only")
                # Still monitor existing trades for SL/Target
                for symbol in [t['symbol'] for t in ACTIVE_TRADES]:
                    price = fetch_ltp(symbol)
                    if price:
                        monitor_active_trades(symbol, price)
                wait_for_next_minute()
                continue

            # 🛑 CONSECUTIVE LOSS COOLDOWN (E5) — F1.4: proper 60-minute timer
            if COOLDOWN_UNTIL and now_ist() < COOLDOWN_UNTIL:
                remaining = int(max(0, (COOLDOWN_UNTIL - now_ist()).total_seconds() // 60))
                print(f"[{now.strftime('%H:%M:%S')}] ⚠️ Cooldown active: {remaining}min remaining ({CONSECUTIVE_LOSSES} consecutive losses)")
                for symbol in [t['symbol'] for t in ACTIVE_TRADES]:
                    price = fetch_ltp(symbol)
                    if price:
                        monitor_active_trades(symbol, price)
                wait_for_next_minute()
                continue
            elif COOLDOWN_UNTIL and now_ist() >= COOLDOWN_UNTIL:
                # Cooldown expired — resume trading
                logging.info(f"✅ Cooldown expired. Resuming signal generation.")
                telegram_send(f"✅ <b>COOLDOWN EXPIRED</b>\nResuming signal generation.")
                COOLDOWN_UNTIL = None
                CONSECUTIVE_LOSSES = 0
    
            # 🏛️ MARKET REGIME CHECK (Step 2)
            current_regime = detect_market_regime()
            
            # 📊 INTELLIGENCE LAYER (E1 + E2) — cached 30min, low overhead
            vol_regime = detect_volatility_regime()
            if is_index("NIFTY"):
                oc_data = fetch_option_chain_data("NSE:NIFTY 50")
            
            # 🔥 INDEX-FIRST SCAN ORDER
            try:
                import engine_runtime
                engine_runtime.set_engine_stage("DATA_FETCH")
            except Exception:
                pass
            # 0. Sync Manual Trades (Once per cycle)
            fetch_manual_orders()
            
            global _FORCE_FULL_SCAN_ONCE
            if _FORCE_FULL_SCAN_ONCE:
                _FORCE_FULL_SCAN_ONCE = False
                # Full scan = indices + full stock universe (if enabled) regardless of minute gating
                scan_universe = INDEX_SYMBOLS.copy()
                if not INDEX_ONLY:
                    scan_universe.extend(get_stock_universe())
                logging.warning("[recovery] Forcing FULL scan: %d symbols", len(scan_universe))
            else:
                scan_universe = build_scan_universe(get_stock_universe())
            print(f"[{now.strftime('%H:%M:%S')}] 📡 Scanning {len(scan_universe)} symbols...")
    
            # ==================================
            # 🚀 EMA CROSSOVER SCAN (EVERY 5 MIN)
            # ==================================
            if now_ist().minute % 5 == 0:
                print("⚡ Checking EMA Crossover (Nifty/Bank Nifty)...")
                for index_sym in INDEX_SYMBOLS:
                    scan_ema_crossover(index_sym)

            # ====================================
            # 🎯 SMC ZONE TAP SCAN (EVERY 5 MIN)
            # ====================================
            if now_ist().minute % 5 == 1:
                for index_sym in INDEX_SYMBOLS:
                    try:
                        zt_candles = fetch_ohlc(index_sym, "5minute", 80)
                        if zt_candles:
                            zt_ltp = kite.ltp([index_sym])
                            zt_spot = zt_ltp[index_sym]["last_price"] if zt_ltp else zt_candles[-1]["close"]
                            zt_signals = scan_zone_taps(index_sym, zt_candles, zt_spot)
                            for zt_sig in zt_signals:
                                zt_msg = format_zone_tap_alert(zt_sig)
                                zt_msg = paper_prefix(zt_msg)
                                if _TRADE_BUTTONS_AVAILABLE:
                                    _send_trade_buttons(zt_sig, zt_msg)
                                else:
                                    _under = (zt_sig.get("underlying") or "").replace(" ", "_").replace(":", "_").strip("_") or "unknown"
                                    _dir = (zt_sig.get("direction") or "").lower()[:5]
                                    sid = f"zt_5m_{_under}_{_dir}_{t.time():.0f}"
                                    telegram_send(zt_msg, signal_id=sid)
                                print(f"  🎯 ZONE TAP: {zt_sig['underlying']} {zt_sig['direction']} "
                                      f"@ {zt_sig['zone_type']} | {zt_sig['pattern']} | "
                                      f"score {zt_sig['score']}")
                    except Exception as e:
                        print(f"  ⚠️ Zone tap scan error ({index_sym}): {e}")

            # ========================================
            # 🔴 SECOND RED BREAK SCAN (EVERY 5 MIN)
            # ========================================
            _srb_current_5m = now_ist().minute // 5
            if not hasattr(scan_second_red_break, '_last_5m_slot'):
                scan_second_red_break._last_5m_slot = -1
            if _SRB_AVAILABLE and _srb_current_5m != scan_second_red_break._last_5m_slot:
                try:
                    # Bypass OHLC cache (15-min TTL) — SRB needs fresh 5m candles every scan
                    _srb_token = get_token("NSE:NIFTY 50")
                    _srb_candles = []
                    if _srb_token:
                        try:
                            _respect_api_throttle()
                            _srb_candles = _kite_call(
                                kite.historical_data,
                                _srb_token,
                                now_ist() - timedelta(days=1),
                                now_ist(),
                                "5minute",
                                timeout=_KITE_TIMEOUT_SEC,
                            )
                        except Exception as _srb_fetch_err:
                            logging.warning("SRB fresh fetch failed, falling back to cache: %s", _srb_fetch_err)
                            _srb_candles = fetch_ohlc("NSE:NIFTY 50", "5minute", 80)
                    if _srb_candles:
                        _srb_sig = scan_second_red_break(_srb_candles, "NIFTY")
                        if _srb_sig:
                            # ── Auto-execute: place PUT order immediately ──
                            _srb_result = _execute_srb_trade(
                                signal=_srb_sig,
                                kite=kite,
                                paper_mode=PAPER_MODE,
                            )
                            _srb_success = _srb_result.get("success", False)
                            _srb_tsym = _srb_result.get("tradingsymbol", "?")
                            _srb_qty = _srb_result.get("qty", 0)
                            _srb_oid = _srb_result.get("order_id", "")
                            _srb_gid = _srb_result.get("gtt_id", "")

                            # ── Register in ACTIVE_TRADES for monitoring ──
                            _srb_sig["setup"] = "SECOND-RED-BREAK"
                            _srb_sig["_registered_today"] = True
                            _srb_sig["option"] = _srb_tsym
                            _srb_sig["srb_order_id"] = _srb_oid
                            _srb_sig["srb_gtt_id"] = _srb_gid
                            _srb_sig["srb_executed"] = _srb_success
                            _srb_sig["srb_qty"] = _srb_qty
                            _srb_sig["srb_opt_entry"] = _srb_result.get("opt_ltp", 0)
                            _srb_sig["srb_opt_sl"] = _srb_result.get("opt_sl", 0)
                            _srb_sig["srb_opt_target"] = _srb_result.get("opt_target", 0)
                            _srb_sig["srb_opt_risk"] = max(_srb_result.get("opt_ltp", 0) - _srb_result.get("opt_sl", 0), 0)
                            _srb_sig["srb_sl_trailed"] = False
                            _srb_sig["srb_trailing_active"] = False
                            _srb_sig["srb_peak_r"] = 0.0
                            _srb_sig["srb_last_trail_sl"] = 0.0

                            with ACTIVE_TRADES_LOCK:
                                ACTIVE_TRADES.append(_srb_sig)
                            DAILY_SIGNAL_COUNT += 1
                            log_paper_trade(_srb_sig)
                            persist_active_trades()

                            # ── Telegram notification ──
                            _srb_status = "✅ EXECUTED" if _srb_success else "❌ FAILED"
                            _srb_sl_method = _srb_result.get("sl_method", "?")
                            _exec_detail = ""
                            if _srb_success:
                                _exec_detail = (
                                    f"\n\n💰 <b>AUTO-EXECUTED:</b>\n"
                                    f"Option: {_srb_tsym}\n"
                                    f"Qty: {_srb_qty}\n"
                                    f"LTP: {_srb_result.get('opt_ltp', '?')}\n"
                                    f"Opt SL: {_srb_result.get('opt_sl', '?')}\n"
                                    f"Opt TGT: {_srb_result.get('opt_target', '?')}\n"
                                    f"SL Method: {_srb_sl_method}\n"
                                    f"Order: {_srb_oid}\n"
                                    f"GTT: {_srb_gid}"
                                )
                            else:
                                _exec_detail = f"\n\n❌ Error: {_srb_result.get('error', 'unknown')}"

                            _srb_msg = (
                                f"🔴 <b>SECOND RED BREAK — {_srb_status}</b>\n"
                                f"{'📝 [PAPER] ' if PAPER_MODE else ''}"
                                f"<b>{_srb_sig['symbol']}</b> | PUT\n\n"
                                f"2nd Red: {_srb_sig.get('srb_second_red_time', '?')}\n"
                                f"2nd Red Low: {_srb_sig.get('srb_second_red_low', '?')}\n"
                                f"Entry (index): {_srb_sig['entry']}\n"
                                f"SL (index): {_srb_sig['sl']}\n"
                                f"Target (index): {_srb_sig['target']}\n"
                                f"RR: {_srb_sig['rr']}"
                                f"{_exec_detail}"
                            )
                            _srb_sid = f"srb_NIFTY_{now_ist().strftime('%Y%m%d%H%M%S')}"
                            telegram_send_signal(
                                paper_prefix(_srb_msg),
                                signal_id=_srb_sid,
                                signal_meta={
                                    "signal_kind": "ENTRY",
                                    "symbol": _srb_sig["symbol"],
                                    "direction": "SHORT",
                                    "strategy_name": "SECOND-RED-BREAK",
                                    "entry": _srb_sig["entry"],
                                    "stop_loss": _srb_sig["sl"],
                                    "target1": _srb_sig["target"],
                                    "score": _srb_sig.get("smc_score"),
                                    "confidence": _srb_sig.get("ai_score"),
                                    "grade": _srb_sig.get("grade"),
                                    "rr": _srb_sig["rr"],
                                },
                            )
                            print(f"  🔴 SRB: NIFTY ENTRY @ {_srb_sig['entry']} | SL={_srb_sig['sl']} "
                                  f"TGT={_srb_sig['target']} | {_srb_status}")
                    else:
                        # No signal — log diagnostic state
                        try:
                            from strategies.second_red_break.live_scanner import get_scanner as _get_srb_scanner
                            _srb_diag = _get_srb_scanner().get_state_summary()
                            _srb_nifty = _srb_diag.get("NIFTY", {})
                            logging.info(
                                "SRB scan (no signal): red_count=%s 2nd_red=%s trade_done=%s emitted=%s candles=%d",
                                _srb_nifty.get("red_count", "?"),
                                _srb_nifty.get("second_red_found", "?"),
                                _srb_nifty.get("trade_done", "?"),
                                _srb_nifty.get("signal_emitted", "?"),
                                len(_srb_candles),
                            )
                        except Exception:
                            pass
                    scan_second_red_break._last_5m_slot = _srb_current_5m
                except Exception as _srb_err:
                    logging.error("SRB scan/execute error: %s", _srb_err)
                    print(f"  ⚠️ SRB error: {_srb_err}")
                    scan_second_red_break._last_5m_slot = _srb_current_5m

            try:
                import engine_runtime
                engine_runtime.set_engine_stage("STRATEGY_SCAN")
            except Exception:
                pass
            _scan_errors = []
            _scan_data_ok = 0
            _scan_data_empty = 0
            _scan_raw_signals = 0
            _setup_results = {}
            for symbol in scan_universe:
                try:
                    signals = scan_symbol(symbol)
                    if signals:
                        _scan_raw_signals += len(signals)
                        all_signals.extend(signals)

                    tf_check = fetch_multitf(symbol)
                    if tf_check and tf_check.get("5m"):
                        _scan_data_ok += 1
                    else:
                        _scan_data_empty += 1

                    price = fetch_ltp(symbol)
                    if price:
                         monitor_active_trades(symbol, price)

                except Exception as e:
                    _scan_errors.append(f"{symbol}: {e}")
                    if DEBUG_MODE:
                        logging.error(f"Scan Exception {symbol}: {e}")
                        print(f"Scan Exception {symbol}: {e}")

            # Periodic diagnostic telegram (first cycle only + on errors)
            if not hasattr(run_live_mode, '_diag_count'):
                run_live_mode._diag_count = 0
                run_live_mode._last_diag = 0
                run_live_mode._session_signals = 0  # cumulative count this session
            run_live_mode._session_signals += _scan_raw_signals

            _has_errors = len(_scan_errors) > 0
            _should_diag = (run_live_mode._diag_count == 0 or _has_errors)
            if _should_diag:
                run_live_mode._diag_count += 1
                run_live_mode._last_diag = t.time()
                _nifty_ltp = fetch_ltp("NSE:NIFTY 50")
                _bn_ltp = fetch_ltp("NSE:NIFTY BANK")
                _time_ist = now_ist().strftime('%H:%M:%S')

                # ── API latency quick probe ──────────────────────────────────
                _api_latency_ms = "?"
                try:
                    _lat_start = t.time()
                    fetch_ltp("NSE:NIFTY 50")
                    _api_latency_ms = f"{int((t.time() - _lat_start) * 1000)}ms"
                except Exception:
                    _api_latency_ms = "ERR"

                # ── Last cycle age (from watchdog) ───────────────────────────
                try:
                    import engine_runtime as _er
                    _cycle_age = int(t.time() - _er._last_cycle_local)
                    _loop_ok = "✅" if _cycle_age < 120 else "⚠️"
                    _loop_label = f"{_loop_ok} {_cycle_age}s ago"
                except Exception:
                    _loop_label = "?"

                _kite_status = "✅ OK" if kite else "❌ NONE"
                _circuit_status = "🔴 ON (HALTED)" if CIRCUIT_BREAKER_ACTIVE else "✅ OFF"
                _regime_label = MARKET_REGIME or "NEUTRAL"

                # ── Engine health summary header (structured) ────────────────
                _diag_lines = [
                    f"📊 <b>ENGINE STATUS</b> — {_time_ist} IST",
                    f"",
                    f"🔄 Loop running: {_loop_label}",
                    f"📡 API latency: {_api_latency_ms}",
                    f"🪙 Kite: {_kite_status} | Token: {(_current_kite_token[:8] + '...') if _current_kite_token else '❌ NONE'}",
                    f"",
                    f"📈 Nifty: {_nifty_ltp or '–'}  |  BankNifty: {_bn_ltp or '–'}",
                    f"📊 Regime: {_regime_label}  |  Circuit: {_circuit_status}",
                    f"",
                    f"🎯 Signals today: {run_live_mode._session_signals} total (last cycle: {_scan_raw_signals})",
                    f"🏦 Active trades: {len(ACTIVE_TRADES)}  |  Daily cap: {DAILY_SIGNAL_COUNT}/{MAX_DAILY_SIGNALS}",
                    f"🔍 Scanned: {len(scan_universe)} symbols  |  Data OK: {_scan_data_ok}  |  Empty: {_scan_data_empty}",
                    f"❌ Errors this cycle: {len(_scan_errors)}",
                ]

                if _scan_errors:
                    _diag_lines.append(f"   ↳ {_scan_errors[0][:120]}")

                # ── Per-symbol setup status ──────────────────────────────────
                _diag_lines.append("")
                _diag_lines.append("<b>Per-symbol setup status:</b>")
                for sym in scan_universe:
                    _sym_short = sym.split(":")[-1][:12]
                    parts = []
                    tf = fetch_multitf(sym)
                    if not tf or not tf.get("5m"):
                        parts.append("NO DATA")
                        _diag_lines.append(f"  {_sym_short}: {' | '.join(parts)}")
                        continue
                    parts.append(f"5m={len(tf.get('5m') or [])} 1h={len(tf.get('1h') or [])} 15m={len(tf.get('15m') or [])}")

                    if ACTIVE_STRATEGIES.get("SETUP_A"):
                        bias = detect_htf_bias(tf.get("1h"))
                        key_a = f"{sym}_{bias}" if bias else None
                        st_a = STRUCTURE_STATE.get(key_a) if key_a else None
                        if not bias:
                            parts.append("A=no_htf_bias")
                        elif st_a:
                            parts.append(f"A={st_a['stage']}")
                        else:
                            ob = detect_order_block(tf["5m"], bias)
                            fvg = detect_fvg(tf["5m"], bias)
                            parts.append(f"A={'OB+FVG' if ob and fvg else 'no_ob' if not ob else 'no_fvg'}")

                    if ACTIVE_STRATEGIES.get("SETUP_C"):
                        zs = ZONE_STATE.get(sym, {})
                        zl = zs.get("LONG")
                        zs_short = zs.get("SHORT")
                        c_parts = []
                        if zl:
                            c_parts.append(f"L:{zl['state']}")
                        if zs_short:
                            c_parts.append(f"S:{zs_short['state']}")
                        parts.append(f"C={','.join(c_parts) if c_parts else 'no_zones'}")

                    if ACTIVE_STRATEGIES.get("HIERARCHICAL"):
                        try:
                            df_15m = pd.DataFrame(tf["15m"])
                            df_5m = pd.DataFrame(tf["5m"])
                            if not df_15m.empty: df_15m.columns = [c.lower() for c in df_15m.columns]
                            if not df_5m.empty: df_5m.columns = [c.lower() for c in df_5m.columns]
                            setup_h, reject_h = evaluate_entry(sym, df_15m, df_5m, current_time=now_ist().time())
                            if setup_h:
                                parts.append("H=SIG!")
                            elif reject_h:
                                parts.append(f"H=REJ:{reject_h.reason}")
                            else:
                                parts.append("H=none")
                        except Exception as he:
                            parts.append(f"H=ERR:{str(he)[:20]}")

                    _diag_lines.append(f"  {_sym_short}: {' | '.join(parts)}")

                # ── Sub-engine status ────────────────────────────────────────
                _diag_lines.append("")
                _diag_lines.append("<b>Sub-engines:</b>")
                _diag_lines.append(f"  BN Signal Engine: {'✅' if bn_signal_engine else '❌'}")
                _diag_lines.append(f"  Option Monitor: {'✅' if option_monitor else '❌'}")
                try:
                    from engine.smc_zone_tap import _state as _zt_state_dict
                    _zt_state_info = []
                    for _zt_sym in INDEX_SYMBOLS:
                        _zt_s = _zt_state_dict.get(_zt_sym)
                        _zt_state_info.append(f"{_zt_sym.split(':')[-1][:6]}={'active' if _zt_s else 'init'}")
                    _diag_lines.append(f"  Zone Tap: {', '.join(_zt_state_info)}")
                except Exception:
                    _diag_lines.append("  Zone Tap: unknown")

                telegram_send("\n".join(_diag_lines))

            if not all_signals and not ACTIVE_TRADES:
                t.sleep(1) # Tiny sleep to prevent CPU burn if list empty, but wait_for_next will handle it.
    
            # -----------------------------
            # PRIORITY + SORTING
            # -----------------------------
            ranked_signals = []
            if all_signals:
                try:
                    # Maintain capped recent signals list for API
                    try:
                        MAX_SIGNALS = 20
                        recent = list(ENGINE_STATE.get("signals", []))
                        recent.extend(all_signals)
                        recent = recent[-MAX_SIGNALS:]
                        update_engine_state(signals=recent)
                        print("API update:", get_engine_state_snapshot())
                    except Exception:
                        pass
                    for s in rank_signals(all_signals):
                        # DEFENSIVE CHECK: Ensure 'entry' key exists and is valid
                        if all(k in s for k in ["entry", "sl", "target", "rr"]) and s["entry"] is not None:
                            # STEP 2: Regime Filter — suppress counter-trend stock signals
                            if 'should_suppress_signal' in globals() and should_suppress_signal(s):
                                logging.info(f"REGIME BLOCKED: {s['symbol']} {s['direction']} (Market={MARKET_REGIME})")
                                continue

                            # STEP 3: OI Bias Filter — block index trades against OI flow
                            if bn_signal_engine and bn_signal_engine.bias_locked:
                                sym_clean = s['symbol'].replace('NSE:', '')
                                bias_ul = None
                                if 'NIFTY BANK' in s['symbol'] or 'BANKNIFTY' in s['symbol']:
                                    bias_ul = 'BANKNIFTY'
                                elif 'NIFTY' in s['symbol']:
                                    bias_ul = 'NIFTY'
                                
                                if bias_ul:
                                    oi_bias = bn_signal_engine.directional_bias.get(bias_ul)
                                    if oi_bias and oi_bias != 'NEUTRAL':
                                        if s['direction'] == 'LONG' and oi_bias == 'BEARISH':
                                            logging.info(f"OI BIAS BLOCKED: {s['symbol']} LONG (OI={oi_bias})")
                                            print(f"[OI FILTER] Blocked {s['symbol']} LONG - OI bias is BEARISH")
                                            continue
                                        if s['direction'] == 'SHORT' and oi_bias == 'BULLISH':
                                            logging.info(f"OI BIAS BLOCKED: {s['symbol']} SHORT (OI={oi_bias})")
                                            print(f"[OI FILTER] Blocked {s['symbol']} SHORT - OI bias is BULLISH")
                                            continue

                            # STEP 4: Tier 3 Adaptive filter (context + setup expectancy)
                            try:
                                adaptive_ok, adaptive_reason = _adaptive_signal_gate(s)
                                s["adaptive_pass"] = adaptive_ok
                                s["adaptive_reason"] = adaptive_reason
                                if not adaptive_ok:
                                    ADAPTIVE_BLOCK_LOG.append({
                                        "ts": now_ist(),
                                        "symbol": s.get("symbol", ""),
                                        "setup": s.get("setup", ""),
                                        "direction": s.get("direction", ""),
                                        "reason": adaptive_reason,
                                    })
                                    logging.info(f"ADAPTIVE BLOCKED: {s['symbol']} {s['setup']} — {adaptive_reason}")
                                    continue
                            except Exception as adaptive_e:
                                logging.error(f"Adaptive gate error (allow through): {adaptive_e}")

                            # STEP 5: Tier 3 explainable AI-style score
                            try:
                                ai_score, ai_reasons = _ai_signal_score(s)
                                s["ai_score"] = ai_score
                                s["ai_reasons"] = ai_reasons
                                ADAPTIVE_SCORE_LOG.append({
                                    "ts": now_ist(),
                                    "symbol": s.get("symbol", ""),
                                    "setup": s.get("setup", ""),
                                    "direction": s.get("direction", ""),
                                    "ai_score": ai_score,
                                })
                            except Exception:
                                s["ai_score"] = 50
                                s["ai_reasons"] = ["AI scorer fallback"]

                            ranked_signals.append(s)
                except Exception as e:
                    print(f"Ranking Error: {e}")

            # -----------------------------
            # SEND TOP ALERTS + REGISTER TRADE
            # -----------------------------
            try:
                import engine_runtime
                engine_runtime.set_engine_stage("SIGNAL_DISPATCH")
            except Exception:
                pass
            for sig in ranked_signals[:5]:
                # MORNING-ONLY STOCK TRADING CUTOFF
                # Backtest evidence: afternoon trades lose -15.75R over 2 months
                cutoff_h = getattr(eng_cfg, "STOCK_SIGNAL_CUTOFF_HOUR", 24)
                cutoff_m = getattr(eng_cfg, "STOCK_SIGNAL_CUTOFF_MIN", 0)
                now_t = now_ist().time()
                if now_t >= time(cutoff_h, cutoff_m) and not is_index(sig.get("symbol", "")):
                    logging.info(f"🕐 AFTERNOON CUTOFF: {sig['symbol']} blocked — no new stock entries after {cutoff_h}:{cutoff_m:02d}")
                    _log_signal_rejection(
                        sig, reason="AFTERNOON_CUTOFF",
                        detail=f"after {cutoff_h}:{cutoff_m:02d}",
                    )
                    continue

                # F1.6: Global daily trade cap
                if DAILY_SIGNAL_COUNT >= MAX_DAILY_SIGNALS:
                    logging.info(f"🛑 DAILY CAP: {MAX_DAILY_SIGNALS} signals reached for today — blocking new entries")
                    break
                # E7: On expiry days, limit max new trades to 2
                if is_expiry_day() and len([t for t in ACTIVE_TRADES if t.get('_registered_today')]) >= 2:
                    logging.info("📅 EXPIRY: Max 2 trades reached for today")
                    break
                try:
                    # F4.8: Dedup key — NO entry price (prevents same symbol re-entry at different prices)
                    # Per-symbol daily cap: 1 signal per symbol regardless of setup type
                    sym_clean = clean_symbol(sig['symbol'])
                    
                    # F4.8: Block if this symbol already has a signal today (any setup type)
                    symbol_key = f"{sym_clean}_ANY"
                    if already_alerted_today(symbol_key):
                        logging.info(f"🚫 DEDUP: {sig['symbol']} already alerted today (any setup)")
                        continue
                    
                    # Also check specific setup key (without entry price)
                    key = f"{sym_clean}_{sig['setup']}_{sig['direction']}"
        
                    if already_alerted_today(key):
                        continue
                    
                    # -------------------------------------------------
                    # F4.1: RISK MANAGEMENT APPROVAL
                    # -------------------------------------------------
                    try:
                        approved, reason, quality = risk_mgr.is_signal_approved(
                            sig, smc_score=sig.get('smc_score', 5)
                        )
                        sig['risk_approved'] = approved
                        sig['risk_reason'] = reason
                        sig['quality_score'] = quality
                        if not approved:
                            logging.info(f"🛡️ RISK REJECTED: {sig['symbol']} {sig['setup']} — {reason}")
                            _log_signal_rejection(
                                sig, reason="RISK_MANAGER",
                                detail=reason,
                                breakdown=sig.get("smc_breakdown"),
                            )
                            continue
                    except Exception as risk_e:
                        logging.error(f"Risk check failed (allowing through): {risk_e}")
                    # -------------------------------------------------
                    
                    # REGISTER AS ACTIVE TRADE
                    setup_mult = position_risk_multiplier(sig["setup"])
                    score_mult = sig.get("risk_mult", 1.0)
                    adaptive_mult = _get_adaptive_setup_multiplier(sig["setup"])
                    
                    # E7: Expiry day adjustment — widen SL + reduce size
                    if is_index(sig["symbol"]) and is_expiry_day(sig["symbol"]):
                        atr_est = abs(sig["entry"] - sig["sl"]) * 2  # Rough ATR estimate
                        sig["sl"], expiry_mult = expiry_day_risk_adjustment(
                            sig["symbol"], sig["sl"], sig["entry"], atr_est
                        )
                        score_mult *= expiry_mult
                        logging.info(f"📅 EXPIRY: {sig['symbol']} size reduced to {score_mult:.1f}x")
                    
                    # W12: Apply slippage to entry price for tracking accuracy
                    if is_index(sig["symbol"]):
                        slip = SLIPPAGE_INDEX_PTS
                        sig["entry"] = sig["entry"] + slip if sig["direction"] == "LONG" else sig["entry"] - slip
                    else:
                        slip = sig["entry"] * SLIPPAGE_STOCK_PCT / 100
                        sig["entry"] = sig["entry"] + slip if sig["direction"] == "LONG" else sig["entry"] - slip
                    sig["entry"] = round(sig["entry"], 2)
                    
                    sig["risk_mult"] = setup_mult * score_mult * adaptive_mult
                    sig["adaptive_risk_mult"] = adaptive_mult
                    sig["_registered_today"] = True  # E7: for expiry trade cap
    
                    with ACTIVE_TRADES_LOCK:
                        ACTIVE_TRADES.append(sig)
                    DAILY_SIGNAL_COUNT += 1
                    try:
                        build_trade_graph(sig, MARKET_REGIME, get_oi_sentiment())
                    except Exception:
                        pass  # TradeGraph is non-critical
                    
                    # ── Feature 1: Visual Signal Validation (TradingView screenshot) ──
                    # ── Feature 2: Pine Cross-Validation (compare engine vs Pine levels) ──
                    _tv_screenshot = None
                    _pine_xval = None
                    if _TV_BRIDGE_AVAILABLE and not BACKTEST_MODE:
                        try:
                            _tv_screenshot = capture_signal_chart(sig)
                            if _tv_screenshot:
                                sig["_tv_screenshot"] = _tv_screenshot
                                logging.info(f"📸 Signal chart captured: {_tv_screenshot}")
                        except Exception as _tv_e:
                            logging.debug(f"TV screenshot failed (non-blocking): {_tv_e}")
                        try:
                            _pine_xval = get_pine_cross_validation(sig)
                            if _pine_xval:
                                sig["_pine_xval"] = _pine_xval
                                _adj = _pine_xval.get("confidence_adjustment", 0)
                                
                                # STEP 6: Apply Pine confidence to smc_score
                                if _adj != 0 and "smc_score" in sig:
                                    _old_score = sig["smc_score"]
                                    sig["smc_score"] = max(0, min(10, _old_score + _adj))
                                    logging.info(
                                        f"📊 Pine confidence: score {_old_score} → {sig['smc_score']} "
                                        f"(adj={_adj:+d}, confirms={_pine_xval.get('confirms',0)}, "
                                        f"contradicts={_pine_xval.get('contradicts',0)})"
                                    )
                                
                                # Log per-component results
                                _ob_s = "✅" if _pine_xval.get("match_ob") else ("❌" if _pine_xval.get("match_ob") is False else "—")
                                _fvg_s = "✅" if _pine_xval.get("match_fvg") else ("❌" if _pine_xval.get("match_fvg") is False else "—")
                                _bos_s = "✅" if _pine_xval.get("match_bos") else ("❌" if _pine_xval.get("match_bos") is False else "—")
                                logging.info(
                                    f"🔍 Pine XVal: OB={_ob_s} FVG={_fvg_s} BOS={_bos_s} "
                                    f"delta={_pine_xval.get('delta')}pts"
                                )
                        except Exception as _xv_e:
                            logging.debug(f"Pine cross-validation failed (non-blocking): {_xv_e}")

                    log_paper_trade(sig)  # Phase 6: paper trade log
                    persist_active_trades()  # F1.2: crash recovery
        
                    chart = None
                    if all(k in sig for k in ["ltf", "entry", "sl", "target"]):
                        chart = generate_chart(
                            sig["symbol"],
                            sig["ltf"],
                            sig["direction"],
                            sig["entry"],
                            sig["sl"],
                            sig["target"],
                            sig.get("ob", (sig["sl"], sig["entry"])),
                            setup_name=sig.get("setup", ""),
                            smc_score=sig.get("smc_score"),
                            regime=MARKET_REGIME,
                            analysis=sig.get("analysis", "")
                        )
                except Exception as loop_e:
                    print(f"Skipping Bad Signal: {loop_e}")
                    continue
    
                oi_sent = get_oi_sentiment()
                oi_line = f"📊 OI: {oi_sent.get('sentiment', 'N/A')} (PCR: {oi_sent.get('pcr_bias', '-')}, Pattern: {oi_sent.get('price_oi_pattern', '-')})"

                # --- Build causal chain summary for Telegram ---
                _chain_parts = []
                if sig.get("sweep_detected"):
                    _chain_parts.append("Liquidity sweep")
                if sig.get("displacement_event"):
                    _chain_parts.append("Displacement")
                if sig.get("choch_time") or sig.get("smc_breakdown", {}).get("choch", 0) > 0:
                    _chain_parts.append("CHoCH")
                if sig.get("bos_confirmed") or sig.get("smc_breakdown", {}).get("bos", 0) > 0:
                    _chain_parts.append("BOS")
                if sig.get("ob"):
                    _chain_parts.append("OB")
                if sig.get("fvg"):
                    _chain_parts.append("FVG")
                _chain_str = " → ".join(_chain_parts) if _chain_parts else sig.get("setup", "SMC")
                _reason = "Retail trapped → Smart money entry" if sig.get("sweep_detected") else "Structure shift → Institutional zone"

                text = (
                    f"🔥 <b>{sig['grade']} SETUP</b>\n\n"
                    f"<b>{sig['symbol']}</b> — {sig['direction']}\n"
                    f"🔮 <b>{sig['setup']}</b>\n\n"
                    f"📍 {_chain_str}\n"
                    f"🎯 Entry: {sig['entry']}\n"
                    f"🛑 SL: {sig['sl']}\n"
                    f"🚀 Target: {sig['target']}\n"
                    f"📊 RR: {sig['rr']} | Score: {sig.get('smc_score', 'N/A')}/10\n"
                    f"🤖 AI: {sig.get('ai_score', 'N/A')}/100 | Risk: {sig.get('risk_mult', 1.0):.2f}x\n\n"
                    f"🏛️ Regime: {MARKET_REGIME}\n"
                    f"{oi_line}\n\n"
                    f"🧠 <b>Reason:</b>\n{_reason}\n\n"
                    f"<i>{sig.get('analysis','')}</i>"
                )
    
                buttons = [
                    [
                        {"text": "📈 TRADE", "callback_data": "TRADE"},
                        {"text": "❌ IGNORE", "callback_data": "IGNORE"}
                    ]
                ]

                _entry_sid = (
                    f"smc_{sig['symbol'].replace(':', '_').replace(' ', '_')}_"
                    f"{sig.get('setup', 'UNK')}_{now_ist().strftime('%Y%m%d%H%M%S')}"
                )
                _entry_meta = {
                    "signal_kind": "ENTRY",
                    "symbol": sig.get("symbol"),
                    "direction": sig.get("direction"),
                    "strategy_name": sig.get("setup"),
                    "entry": sig.get("entry"),
                    "stop_loss": sig.get("sl"),
                    "target1": sig.get("target"),
                    "score": sig.get("smc_score"),
                    "confidence": sig.get("ai_score"),
                    "grade": sig.get("grade"),
                    "rr": sig.get("rr"),
                }

                if chart:
                    telegram_send_image(chart, text, signal_id=_entry_sid, signal_meta=_entry_meta)
                    if os.path.exists(chart):
                        os.remove(chart)
                else:
                    telegram_send_with_buttons(text, buttons, signal_id=_entry_sid, signal_meta=_entry_meta)
    
            # -----------------------------
            # SMART WAIT (ZERO LATENCY MONITOR)
            # -----------------------------
            try:
                import engine_runtime
                engine_runtime.set_engine_stage("TRADE_MONITOR")
            except Exception:
                pass
            # Instead of sleeping blindly, we poll active trades every second.
            # CRITICAL: call write_last_cycle() at least every 10s so the watchdog
            # (threshold 180s) never kills the engine during a normal trading minute.
            _tm_last_cycle_ping = t.time()
            while True:
                # ── Watchdog keep-alive (every 10s) ──────────────────────────
                if t.time() - _tm_last_cycle_ping >= 10:
                    try:
                        engine_runtime.write_last_cycle()
                    except Exception:
                        pass
                    _tm_last_cycle_ping = t.time()

                # 1. Update Active Trades (Fast Exit)
                if ACTIVE_TRADES:
                    for t_obj in list(ACTIVE_TRADES):
                        try:
                            # Use lightweight quote/ltp (OPTIMIZED)
                            price = fetch_ltp(t_obj["symbol"])
                            if price:
                                monitor_active_trades(t_obj["symbol"], price)
                        except Exception: pass

                    # ── SRB Strategy 6: Full trail from 3R (1.5R gap) ──
                    if _SRB_AVAILABLE:
                        _SRB_TRAIL_GAP_R = 1.5
                        _SRB_TRAIL_STEP_R = 0.5  # Only update GTT when SL moves by 0.5R+
                        for t_obj in list(ACTIVE_TRADES):
                            if t_obj.get("setup") != "SECOND-RED-BREAK":
                                continue
                            if not t_obj.get("srb_executed"):
                                continue
                            _opt_sym = t_obj.get("option", "")
                            _opt_entry = t_obj.get("srb_opt_entry", 0)
                            _opt_risk = t_obj.get("srb_opt_risk", 0)
                            if not _opt_sym or _opt_entry <= 0 or _opt_risk <= 0:
                                continue
                            try:
                                _opt_ltp_data = kite.ltp([f"NFO:{_opt_sym}"])
                                _opt_ltp = _opt_ltp_data[f"NFO:{_opt_sym}"]["last_price"]
                                _opt_profit_r = (_opt_ltp - _opt_entry) / _opt_risk

                                # Update peak R tracking
                                _prev_peak = t_obj.get("srb_peak_r", 0.0)
                                if _opt_profit_r > _prev_peak:
                                    t_obj["srb_peak_r"] = _opt_profit_r

                                _peak_r = t_obj["srb_peak_r"]

                                # Activate trailing once 3R is reached
                                if _peak_r >= 3.0 and not t_obj.get("srb_trailing_active"):
                                    t_obj["srb_trailing_active"] = True
                                    _new_trail_sl = _opt_entry + (_peak_r - _SRB_TRAIL_GAP_R) * _opt_risk
                                    _trail_res = _modify_srb_gtt(kite, t_obj, new_trail_sl=_new_trail_sl)
                                    if _trail_res.get("success"):
                                        t_obj["srb_gtt_id"] = _trail_res["new_gtt_id"]
                                        t_obj["srb_opt_sl"] = _trail_res["new_sl"]
                                        t_obj["srb_last_trail_sl"] = _trail_res["new_sl"]
                                        persist_active_trades()
                                        _trail_msg = (
                                            f"🚀 <b>SRB 3R TRAIL ACTIVATED</b>\n"
                                            f"<b>{t_obj['symbol']}</b> | {_opt_sym}\n\n"
                                            f"Option LTP: {_opt_ltp:.2f}\n"
                                            f"Peak R: {_peak_r:.1f}R\n"
                                            f"Trail SL: {_trail_res['new_sl']:.2f} (locks {_peak_r - _SRB_TRAIL_GAP_R:.1f}R)\n"
                                            f"💰 Minimum profit locked: {_peak_r - _SRB_TRAIL_GAP_R:.1f}R"
                                        )
                                        telegram_send_signal(_trail_msg, signal_id=f"srb_trail3r_{now_ist().strftime('%H%M%S')}")
                                        print(f"  🚀 SRB 3R Trail activated: {_opt_sym} SL → {_trail_res['new_sl']}")

                                # Update trailing SL as peak grows (only if SL moves by 0.5R+)
                                elif t_obj.get("srb_trailing_active") and _peak_r >= 3.0:
                                    _new_trail_sl = _opt_entry + (_peak_r - _SRB_TRAIL_GAP_R) * _opt_risk
                                    _last_sl = t_obj.get("srb_last_trail_sl", 0)
                                    _sl_move_r = (_new_trail_sl - _last_sl) / _opt_risk if _opt_risk > 0 else 0
                                    if _sl_move_r >= _SRB_TRAIL_STEP_R:
                                        _trail_res = _modify_srb_gtt(kite, t_obj, new_trail_sl=_new_trail_sl)
                                        if _trail_res.get("success"):
                                            t_obj["srb_gtt_id"] = _trail_res["new_gtt_id"]
                                            t_obj["srb_opt_sl"] = _trail_res["new_sl"]
                                            t_obj["srb_last_trail_sl"] = _trail_res["new_sl"]
                                            persist_active_trades()
                                            _trail_msg = (
                                                f"📈 <b>SRB TRAIL UPDATE</b>\n"
                                                f"<b>{t_obj['symbol']}</b> | {_opt_sym}\n\n"
                                                f"Option LTP: {_opt_ltp:.2f}\n"
                                                f"Peak R: {_peak_r:.1f}R\n"
                                                f"Trail SL: {_trail_res['new_sl']:.2f} (locks {_peak_r - _SRB_TRAIL_GAP_R:.1f}R)\n"
                                                f"Old SL: {_last_sl:.2f}"
                                            )
                                            telegram_send_signal(_trail_msg, signal_id=f"srb_trail_upd_{now_ist().strftime('%H%M%S')}")
                                            print(f"  📈 SRB Trail update: {_opt_sym} SL → {_trail_res['new_sl']} (peak {_peak_r:.1f}R)")

                            except Exception as _trail_err:
                                logging.debug("SRB trail check error: %s", _trail_err)
                        
                # 2. Check for Next Minute Start
                if now_ist().second == 0:
                    break
                
                # 3. POLL MANUAL TRADES (Every 5 seconds)
                if manual_handler and now_ist().second % 5 == 0:
                    try:
                        manual_handler.poll_manual_trades(ACTIVE_TRADES)
                    except Exception as e:
                        logging.warning(f"Manual Poll Error: {e}")

                # 4. POLL OPTION MONITOR (Independent Interval checked inside poll)
                if option_monitor:
                    try:
                        option_monitor.poll()
                    except Exception as e:
                        logging.warning(f"Option Monitor Poll Error: {e}")

                # 5. POLL BANK NIFTY SIGNAL ENGINE
                if bn_signal_engine:
                    try:
                        bn_signal_engine.poll()
                    except Exception as e:
                        logging.warning(f"BN Signal Engine Poll Error: {e}")

                # 6. POLL OI SHORT-COVERING DETECTOR
                # Heavy work (kite.quote + chart generation) is offloaded to a
                # daemon thread so the monitor loop is never blocked by slow I/O.
                if kite and not BACKTEST_MODE:
                    try:
                        sc_signals, sc_structure_alerts = scan_short_covering(
                            kite,
                            telegram_fn=None,
                            fetch_ohlc_fn=fetch_ohlc
                        )
                        if sc_signals or sc_structure_alerts:
                            # Fire-and-forget: send alerts in background thread
                            def _send_oi_alerts(sigs, s_alerts):
                                for sc_sig in sigs:
                                    try:
                                        from engine.oi_short_covering import _format_alert
                                        sc_msg = _format_alert(sc_sig)
                                        sc_msg = paper_prefix(sc_msg)
                                        sc_chart = generate_oi_chart(sc_sig)
                                        if sc_chart:
                                            telegram_send_image(sc_chart, sc_msg)
                                            if os.path.exists(sc_chart):
                                                os.remove(sc_chart)
                                        if _TRADE_BUTTONS_AVAILABLE:
                                            _oi_btn_sig = {
                                                "underlying": sc_sig.get("underlying", "NIFTY"),
                                                "direction": "LONG" if sc_sig.get("opt_type") == "CE" else "SHORT",
                                                "spot": sc_sig.get("spot"),
                                                "strike": sc_sig.get("strike"),
                                                "opt_type": sc_sig.get("opt_type"),
                                                "trade_levels": sc_sig.get("trade_levels"),
                                                "entry": sc_sig.get("trade_levels", {}).get("entry"),
                                                "sl": sc_sig.get("trade_levels", {}).get("sl"),
                                                "tp1": sc_sig.get("trade_levels", {}).get("target"),
                                            }
                                            _send_trade_buttons(_oi_btn_sig, sc_msg)
                                        elif not sc_chart:
                                            telegram_send(sc_msg)
                                    except Exception as sc_e:
                                        logging.error(f"OI SC send error: {sc_e}")
                                for sa in s_alerts:
                                    try:
                                        from engine.oi_short_covering import _format_structure_alert
                                        sa_msg = paper_prefix(_format_structure_alert(sa))
                                        telegram_send(sa_msg)
                                    except Exception as sa_e:
                                        logging.error(f"OI SC structure alert error: {sa_e}")
                            import threading as _threading
                            _oi_t = _threading.Thread(
                                target=_send_oi_alerts,
                                args=(sc_signals, sc_structure_alerts),
                                daemon=True,
                                name="oi-alert-sender",
                            )
                            _oi_t.start()
                            logging.info("OI SC: %d signal(s), %d structure alert(s) — sending async",
                                         len(sc_signals), len(sc_structure_alerts))
                    except Exception as e:
                        logging.error(f"OI Short-Covering Poll Error: {e}")

                # Sleep briefly to prevent CPU burn
                t.sleep(1)
        
            # 💾 SAVE PERSISTED MEMORY AFTER BATCH
            save_engine_states()
            # Cycle monitoring: dashboard can detect stuck engine (heartbeat fresh but last_cycle stale)
            try:
                import engine_runtime
                engine_runtime.write_last_cycle()
            except Exception as _e:
                logging.debug("write_last_cycle: %s", _e)
            _publish_redis_snapshot()

        except Exception as e:
            # F4.6: Error escalation — track consecutive main loop errors
            _consecutive_loop_errors += 1
            logging.error(f"Main loop error #{_consecutive_loop_errors}: {e}")
            
            if _consecutive_loop_errors >= _MAX_CONSECUTIVE_ERRORS:
                emergency_msg = (f"🚨 <b>EMERGENCY: {_consecutive_loop_errors} CONSECUTIVE ERRORS</b>\n"
                                 f"Last: {str(e)[:200]}\n"
                                 f"Engine HALTING. Manual intervention required.\n"
                                 f"Active trades: {len(ACTIVE_TRADES)}")
                telegram_send(emergency_msg)
                logging.critical(f"EMERGENCY HALT: {_consecutive_loop_errors} consecutive errors. Last: {e}")
                _shutdown_handler("emergency_error_limit")
                import sys
                sys.exit(1)
            else:
                telegram_send(f"⚠️ <b>SYSTEM ERROR ({_consecutive_loop_errors}/{_MAX_CONSECUTIVE_ERRORS})</b>\n{str(e)[:200]}")
                t.sleep(60)  # Wait 1 min on error then retry

# =====================================================
# F1.7: GRACEFUL SHUTDOWN HANDLERS
# =====================================================
import atexit
import signal as signal_module

LOCK_FILE_PATH = "engine.lock"

def _acquire_process_lock():
    """F1.8 + Railway: Prevent multiple engine instances — Redis lock first (if REDIS_URL), else file lock.
    Returns True if lock acquired, False if another instance holds it (caller should stay alive for healthcheck).
    """
    try:
        import engine_runtime
        if os.getenv("REDIS_URL", "").strip():
            if not engine_runtime.acquire_engine_lock():
                print("🛑 Another engine instance already running (Redis lock held). Entering standby — /health will still respond.")
                logging.warning("Engine lock not acquired (Redis). Another instance running. Standby mode.")
                return False
            engine_runtime.set_engine_version(ENGINE_VERSION)
            logging.info("Engine started with Redis lock (24/7 safe mode)")
            return True
    except ImportError:
        pass
    if os.path.exists(LOCK_FILE_PATH):
        try:
            with open(LOCK_FILE_PATH, "r") as f:
                old_pid = int(f.read().strip())
            # Check if the old process is still running
            import psutil
            if psutil.pid_exists(old_pid):
                print(f"🛑 Another engine instance is running (PID {old_pid}). Entering standby.")
                logging.warning("Process lock held by PID %s. Standby mode.", old_pid)
                return False
            else:
                print(f"⚠️ Stale lock file found (PID {old_pid} not running). Overwriting.")
        except (ImportError, ValueError):
            # psutil not installed — try OS-level PID check
            try:
                old_pid = int(open(LOCK_FILE_PATH).read().strip())
                os.kill(old_pid, 0)  # signal 0 = check if alive
                print(f"🛑 Another engine instance may be running (PID {old_pid}). Entering standby.")
                return False
            except (OSError, ValueError):
                # Process not running or PID invalid — stale lock
                print(f"⚠️ Stale lock file found. Overwriting.")
    
    # Write our PID
    with open(LOCK_FILE_PATH, "w") as f:
        f.write(str(os.getpid()))
    logging.info("🔒 Process lock acquired (PID=%s)", os.getpid())
    return True

def _release_process_lock():
    """Remove lock file on shutdown; also release Redis lock if held."""
    try:
        import engine_runtime
        engine_runtime.release_engine_lock()
    except Exception as e:
        logging.debug("Engine runtime release_engine_lock: %s", e)
    try:
        if os.path.exists(LOCK_FILE_PATH):
            os.remove(LOCK_FILE_PATH)
            logging.info("🔓 Process lock released.")
    except Exception as e:
        logging.error(f"Failed to release lock file: {e}")

_shutdown_telegram_sent = False  # Prevent duplicate Telegram when atexit runs after explicit _shutdown_handler

def _shutdown_handler(reason="unknown"):
    """Save all state on exit — called by atexit, SIGINT, SIGTERM. Releases Redis lock."""
    global _shutdown_telegram_sent
    logging.info("🛑 Engine shutting down (reason: %s). Saving state...", reason)
    try:
        save_engine_states()
        persist_active_trades()
        _release_process_lock()  # F1.8
        if not _shutdown_telegram_sent:
            _shutdown_telegram_sent = True
            if ACTIVE_TRADES:
                symbols = [t.get("symbol", "?") for t in ACTIVE_TRADES]
                telegram_send(f"🛑 <b>ENGINE SHUTDOWN</b>\n"
                              f"Reason: {reason}\n"
                              f"💾 Saved {len(ACTIVE_TRADES)} active trades:\n"
                              f"{', '.join(symbols)}\n"
                              f"<i>Trades will resume on restart.</i>")
            else:
                telegram_send(f"🛑 <b>ENGINE SHUTDOWN</b>\nReason: {reason}\nNo active trades.")
    except Exception as e:
        logging.error("Shutdown save failed: %s", e)
    logging.info("💾 State saved. Goodbye.")

def _sigint_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    _shutdown_handler("SIGINT (Ctrl+C)")
    import sys
    sys.exit(0)

atexit.register(lambda: _shutdown_handler("atexit"))
signal_module.signal(signal_module.SIGINT, _sigint_handler)
try:
    signal_module.signal(signal_module.SIGTERM, lambda s, f: (_shutdown_handler("SIGTERM"), __import__('sys').exit(0)))
except (OSError, AttributeError):
    pass  # SIGTERM not available on Windows in all contexts

def run_engine_main():
    """Entry point for bootstrap (run_engine_railway.py). Runs lock + trading loop. Skip HTTP server if SKIP_ENGINE_HTTP is set."""
    if os.environ.get("SKIP_ENGINE_HTTP"):
        try:
            from dashboard.backend.engine_api import set_state_reader
            set_state_reader(get_engine_state_snapshot)
        except Exception as e:
            logging.debug("set_state_reader: %s", e)
    else:
        # Standalone: start our own /health server first
        try:
            from dashboard.backend.engine_api import start_api_server, set_state_reader
            set_state_reader(get_engine_state_snapshot)
            api_port = int(os.environ.get("PORT", 8000))
            threading.Thread(target=start_api_server, kwargs={"port": api_port}, daemon=True).start()
            print(f"[ENGINE] API server started on http://0.0.0.0:{api_port}")
        except Exception as e:
            logging.debug("Engine API server not started: %s", e)
        t.sleep(1)
    if not _acquire_process_lock():
        logging.info("Engine in standby (lock held by another instance). Retrying every 2 min.")
        while True:
            t.sleep(120)
            if _acquire_process_lock():
                logging.info("Lock acquired. Starting trading loop.")
                break
    try:
        import engine_runtime
        engine_runtime.start_heartbeat_thread()
        engine_runtime.start_watchdog_thread()
        engine_runtime.write_last_cycle()  # Seed so watchdog doesn't trigger during startup
    except Exception as e:
        logging.debug("Heartbeat/watchdog thread not started: %s", e)
    start_data_prefetcher()
    update_engine_state(engine="ON")
    _railway = bool(os.getenv("RAILWAY_ENVIRONMENT", ""))
    _auto_login_backoff = 120  # seconds — doubles on repeated failure, max 600s
    while True:
        run_live_mode()
        if not _railway:
            logging.info("run_live_mode returned (token/data failure or redis_lock_lost). Exiting.")
            break
        # ── Cloud auto-refresh: attempt Kite re-login before waiting ──────────
        # This loop fires whenever run_live_mode() exits (token expired / invalid
        # data connection / Redis lock lost). If KITE_PASSWORD + KITE_TOTP_SECRET
        # are set in Railway env, a fresh token is obtained automatically with no
        # laptop or manual intervention required.
        try:
            import engine_runtime
            engine_runtime.set_engine_stage("TOKEN_REFRESH")
        except Exception:
            pass
        login_ok = _attempt_auto_login()
        if login_ok:
            _auto_login_backoff = 120  # reset on success
            logging.info("run_live_mode: token refreshed — restarting in 5s")
            t.sleep(5)
        else:
            logging.info("run_live_mode exited — retrying in %ds (Railway auto-recovery)", _auto_login_backoff)
            try:
                import engine_runtime
                engine_runtime.set_engine_stage("RECOVERY_WAIT")
                engine_runtime.safe_sleep(_auto_login_backoff)
            except Exception:
                t.sleep(_auto_login_backoff)
            _auto_login_backoff = min(_auto_login_backoff * 2, 600)  # exponential backoff, cap 10min


if __name__ == "__main__":
    print("Starting SMC trading engine...")
    run_engine_main()
