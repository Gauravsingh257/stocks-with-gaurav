"""
Diagnostic Backtest — January 2026 (SHORT focus)
================================================
Investigates why SHORT setups are missing in a bearish market.
Check detect_bias output and SHORT setup rejections.
"""

import os, sys
# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import datetime, timedelta, time
from kiteconnect import KiteConnect
# Create a dummy credentials module if not exists or just use empty string, 
# but the script already has connection logic. 
# We'll assume the environment is set up or access_token.txt exists.

from smc_trading_engine.smc.market_structure import calculate_atr, is_ranging_market
from smc_trading_engine.smc.bos_choch import detect_bos, detect_bias, get_latest_bos, is_weak_bos
from smc_trading_engine.smc.order_blocks import detect_order_blocks, is_price_in_ob, get_nearest_ob, update_ob_status
from smc_trading_engine.smc.fvg import detect_fvg, is_price_in_fvg, get_nearest_fvg
from smc_trading_engine.smc.liquidity import detect_all_liquidity
from smc_trading_engine.strategy.entry_model import (
    has_confirmation_candle, is_in_session, is_mid_range_entry,
    passes_volatility_filter, is_near_ob_or_fvg
)
from kite_credentials import API_KEY


def connect_kite():
    if not os.path.exists("access_token.txt"):
        print("Error: access_token.txt missing")
        sys.exit(1)
    with open("access_token.txt", "r") as f:
        token = f.read().strip()
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


def fetch(kite, symbol, interval, from_d, to_d):
    ltp = kite.ltp(symbol)
    token = list(ltp.values())[0]["instrument_token"]
    data = kite.historical_data(token, from_d, to_d, interval)
    if not data: return pd.DataFrame()
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def main():
    kite = connect_kite()
    symbol = "NSE:NIFTY 50"
    from_d = datetime(2026, 1, 1)
    to_d = datetime(2026, 1, 31)

    print("Fetching data...")
    import time as _t
    htf_df = fetch(kite, symbol, "15minute", from_d, to_d)
    _t.sleep(0.5)
    ltf_df = fetch(kite, symbol, "5minute", from_d, to_d)
    print(f"HTF: {len(htf_df)} bars, LTF: {len(ltf_df)} bars")

    # Diagnostic: check each filter independently
    from collections import Counter
    rejection_reasons = Counter()
    
    total_samples = 0
    bias_counts = Counter()
    
    min_bars = 50
    # Scan every bar
    scan_step = 1

    for i in range(min_bars, len(ltf_df), scan_step):
        ltf_slice = ltf_df.iloc[:i + 1]
        current = ltf_df.iloc[i]
        bar_time = ltf_df.index[i]
        
        # Sync HTF slice accurately
        htf_slice = htf_df[htf_df.index <= bar_time]
        if len(htf_slice) < 20: continue

        total_samples += 1

        # 1. Bias Check
        bias = detect_bias(htf_slice)
        bias_counts[str(bias)] += 1
        
        # We focus on SHORT bias diagnosis
        target_bias = "SHORT"
        
        # If bias is not SHORT, record why (it is None or LONG)
        if bias != target_bias:
            rejection_reasons[f"BIAS_{bias}"] += 1
            continue

        # If bias IS SHORT, check other filters
        
        # 2. Session
        ct = bar_time.time()
        if not is_in_session(ct):
            rejection_reasons["OUTSIDE_SESSION"] += 1
            continue

        # 3. Ranging
        if is_ranging_market(ltf_slice):
            rejection_reasons["RANGING"] += 1
            continue

        # 4. Volatility
        if not passes_volatility_filter(ltf_slice, symbol):
            rejection_reasons["LOW_VOLATILITY"] += 1
            continue

        # 5. Liquidity Sweep
        pools = detect_all_liquidity(ltf_slice)
        swept = [p for p in pools if p.is_swept]
        if not swept:
            rejection_reasons["NO_LIQ_SWEEP"] += 1
            continue

        # 6. BOS Confirmation
        ltf_bos = get_latest_bos(ltf_slice)
        if ltf_bos is None:
            rejection_reasons["NO_BOS"] += 1
            continue

        from smc_trading_engine.smc.bos_choch import is_weak_bos
        if is_weak_bos(ltf_bos):
            rejection_reasons["WEAK_BOS"] += 1
            continue
            
        if ltf_bos.direction != "BEARISH": # Must align with SHORT bias
            rejection_reasons["BOS_MISALIGN"] += 1
            continue

        # 7. OB/FVG
        direction = "BEARISH"
        ob_dir = "BEARISH"
        current_price = float(current['close'])
        atr = calculate_atr(ltf_slice)

        obs = detect_order_blocks(ltf_slice, ob_dir, min_displacement_mult=1.5)
        obs = update_ob_status(obs, ltf_slice, max_retests=2)
        nearest_ob = get_nearest_ob(obs, current_price, ob_dir)

        fvgs = detect_fvg(ltf_slice, ob_dir)
        nearest_fvg = get_nearest_fvg(fvgs, current_price, ob_dir)

        in_ob, in_fvg = is_near_ob_or_fvg(current_price, nearest_ob, nearest_fvg, atr)
        
        if not in_ob and not in_fvg:
            rejection_reasons["NOT_IN_OB_FVG"] += 1
            continue

        # 8. Mid Range
        if is_mid_range_entry(ltf_slice, current_price):
            rejection_reasons["MID_RANGE"] += 1
            continue

        # 9. Confirmation Candle
        if not has_confirmation_candle(ltf_slice, "SHORT"):
            rejection_reasons["NO_CONFIRM_CANDO"] += 1
            continue
            
        print(f"VALID SHORT SETUP at {bar_time}")


    print("\n--- DIAGNOSIS REPORT ---")
    print(f"Total bars: {total_samples}")
    print("Bias distribution:", bias_counts)
    print("Rejection reasons for SHORT setups:")
    for r, c in rejection_reasons.most_common():
        print(f"  {r}: {c}")

if __name__ == "__main__":
    main()
