"""
Live Trading Loop (V3 Institutional)
====================================
Main driver for the SMC Trading Engine in LIVE mode.
Strict Institutional Logic:
- Session: 09:45 - 13:45 (Strict)
- One Trade Per Symbol
- Max 2 Trades Per Day
"""

import time as t
import logging
import sys
import os
import pandas as pd
from datetime import datetime, timedelta, time
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None

# Internal Imports
from smc_trading_engine.data.data_fetcher import DataFetcher
from smc_trading_engine.strategy.risk_management import RiskManager
from smc_trading_engine.strategy.signal_generator import SignalGenerator
from smc_trading_engine.execution.live_execution import LiveExecution, DRY_RUN
from smc_trading_engine.execution.execution_core import ExecutionState, check_exit

# Regime Imports
from smc_trading_engine.regime import (
    PremarketClassifier, RegimeController, RegimeControlFlags,
    confirm_regime, compute_opening_range, OpeningRange
)
from smc_trading_engine.regime.global_data import compute_global_score
from smc_trading_engine.regime.oi_analyzer import compute_oi_bias_score
from smc_trading_engine.regime.volatility_model import compute_volatility_regime

# Configuration
from kite_credentials import API_KEY

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("live_trading.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("live_loop")

# ─── CONFIGURATION ────────────────────────────────
WATCHLIST = [
    "NSE:NIFTY 50",
    "NSE:NIFTY BANK",
    "NSE:NIFTY FIN SERVICE",
]

# ─── HELPER FUNCTIONS ─────────────────────────────

def fetch_global_sentiment_proxies(kite):
    return {
        "sp500_change_pct": 0.0,
        "nasdaq_change_pct": 0.0,
        "dow_change_pct": 0.0,
        "nikkei_change_pct": 0.0,
        "hangseng_change_pct": 0.0,
        "sgx_change_pct": 0.0, 
    }

def get_nifty_gap(fetcher):
    try:
        df = fetcher.fetch_ohlc("NSE:NIFTY 50", "day", lookback=2)
        if df is None or len(df) < 2:
            return 0.0
        prev_close = df['close'].iloc[-2]
        today_open = df['open'].iloc[-1]
        return today_open - prev_close
    except Exception as e:
        logger.error(f"Error calculating gap: {e}")
        return 0.0

def get_india_vix(kite):
    try:
        quote = kite.quote("NSE:INDIA VIX")
        if "NSE:INDIA VIX" in quote:
            return quote["NSE:INDIA VIX"]["last_price"]
    except:
        pass
    return 15.0

# ─── MAIN LOOP ────────────────────────────────────

def run_live_session(args):
    """Main execution loop"""
    
    logger.info("STARTING SMC LIVE ENGINE (V3 INSTITUTIONAL)")
    
    # 1. Connect Kite
    if not os.path.exists("access_token.txt"):
        logger.error("access_token.txt not found! Run login first.")
        return

    with open("access_token.txt", "r") as f:
        access_token = f.read().strip()
        
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(access_token)
    logger.info("Kite Connected")

    # 2. Initialize Modules
    fetcher = DataFetcher(kite)
    risk_mgr = RiskManager() 
    
    # Execution State
    exec_state = ExecutionState()
    
    executor = LiveExecution(kite, args.telegram_token, args.telegram_chat, dry_run=args.dry_run, state=exec_state)
    generator = SignalGenerator(risk_mgr)
    
    # Regime Modules
    classifier = PremarketClassifier()
    controller = RegimeController()
    
    # State
    regime_flags = None
    premarket_done = False
    confirmation_done = False
    
    fetcher.fetch_instruments()
    
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE TRADING'}")
    logger.info(f"Watchlist: {WATCHLIST}")

    # ─── LOOP ─────────────────────────────────────
    try:
        while True:
            now = datetime.now()
            current_time = now.time()
            
            # 09:15 - PRE-MARKET ANALYSIS
            if current_time >= time(9, 15) and not premarket_done:
                logger.info("Running Pre-Market Classification...")
                try:
                    gap = get_nifty_gap(fetcher)
                    vix = get_india_vix(kite)
                    nifty_atr = 150 
                    chain_df = fetcher.fetch_option_chain_snapshot("NIFTY")
                    
                    global_data = compute_global_score(**fetch_global_sentiment_proxies(kite), 
                                                     gift_nifty_price=22000, 
                                                     prev_nifty_close=22000)
                    
                    oi_data = compute_oi_bias_score(chain_df, 22000) if chain_df is not None else \
                              {'oi_bias': 'NEUTRAL', 'oi_score': 50, 'pcr': 1.0, 'call_wall': 0, 'put_wall': 0, 'max_pain': 0}
                    
                    vol_data = compute_volatility_regime(nifty_atr, vix, vix, gap, 200)
                    
                    # Classify
                    result = classifier.classify(global_data, oi_data, vol_data, gap, nifty_atr)
                    regime_flags = controller.get_control_flags(result)
                    
                    logger.info(f"REGIME: {result['regime']} ({result['confidence']}%) | Bias: {result['directional_bias']}")
                    executor.execute_signal({
                        "symbol": "REGIME_ALERT", "direction": "INFO", 
                        "entry": 0, "stop_loss": 0, "target": 0, "RR": 0, "confidence_score": result['confidence'],
                        "position_size": f"{result['regime']}"
                    }) 
                    
                    premarket_done = True
                except Exception as e:
                    logger.error(f"Pre-market failed: {e}")
                    traceback.print_exc()

            # 09:45 - MORNING CONFIRMATION
            if current_time >= time(9, 45) and premarket_done and not confirmation_done:
                 logger.info("Finalizing Morning Confirmation (09:45)...")
                 try:
                     nifty_data = fetcher.fetch_ohlc("NSE:NIFTY 50", "15minute", lookback=2)
                     if len(nifty_data) >= 2:
                         candle_915 = nifty_data.iloc[-2].to_frame().T 
                         candle_930 = nifty_data.iloc[-1].to_frame().T 
                         
                         regime_flags = controller.apply_morning_confirmation(
                             regime_flags, candle_915, candle_930
                         )
                         logger.info(f"Confirmed Regime: {regime_flags.regime}")
                         confirmation_done = True
                 except Exception as e:
                     logger.error(f"Confirmation error: {e}")
            
            
            # ─── MONITOR ACTIVE TRADES ───
            if exec_state.active_trades:
                for trade in list(exec_state.active_trades):
                    symbol = trade["symbol"]
                    try:
                        ohlc = fetcher.fetch_ohlc(symbol, "5minute", lookback=1)
                        if ohlc is not None and not ohlc.empty:
                            last_candle = ohlc.iloc[-1]
                            should_exit, reason, price = check_exit(trade, last_candle)
                            
                            if should_exit:
                                executor.close_position(trade, reason, price)
                    except Exception as e:
                        logger.error(f"Error monitoring trade {symbol}: {e}")

            # ─── SIGNAL SCANNING LOOP (V3) ───
            # Strict Window: 09:45 - 13:45
            if time(9, 45) <= current_time <= time(13, 45):
                # logger.info(f"Scanning markets... {now.strftime('%H:%M:%S')}")
                
                symbol_data = {}
                for symbol in WATCHLIST:
                    df_5m = fetcher.fetch_ohlc(symbol, "5minute", lookback=200)
                    df_15m = fetcher.fetch_ohlc(symbol, "15minute", lookback=200)
                    if df_5m is not None and df_15m is not None:
                        symbol_data[symbol] = {"ltf": df_5m, "htf": df_15m}
                
                # generate signals with REGIME FLAGS
                signals = generator.scan_symbols(symbol_data, regime_flags=regime_flags)
                
                for signal in signals:
                    executor.execute_signal(signal)
            
            # Sleep
            sleep_sec = 60 - datetime.now().second
            t.sleep(sleep_sec)

    except KeyboardInterrupt:
        logger.info("Engine stopped by user")
    except Exception as e:
        logger.critical(f"Engine crashed: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Run without placing orders")
    parser.add_argument("--telegram-token", default="")
    parser.add_argument("--telegram-chat", default="")
    args = parser.parse_args()
    
    run_live_session(args)
