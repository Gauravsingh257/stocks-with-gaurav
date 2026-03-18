"""
engine/swing.py — Premium Swing Scanner.
Extracted from smc_mtf_engine_v4.py lines 3279–3810 (Phase 5).

Dependencies:
  - engine.config (constants, mutable state)
  - engine.indicators (calculate_atr)
  - Caller must pass fetch_ohlc_fn, telegram_fn, get_universe_fn
"""

import os
import json as _json
import time as t
import logging
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

from engine import config as cfg
from engine.indicators import calculate_atr

# =====================================================
# SECTOR MAP
# =====================================================

SECTOR_MAP = {
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "KOTAKBANK": "Banking",
    "SBIN": "Banking", "AXISBANK": "Banking", "INDUSINDBK": "Banking",
    "BAJFINANCE": "Finance", "BAJAJFINSV": "Finance", "IDBI": "Banking",
    "BANDHANBNK": "Banking", "FEDERALBNK": "Banking", "PNB": "Banking",
    "IDFCFIRSTB": "Banking", "AUBANK": "Banking", "RBLBANK": "Banking",
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "TECHM": "IT", "LTIM": "IT", "MPHASIS": "IT", "COFORGE": "IT",
    "PERSISTENT": "IT", "LTTS": "IT",
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "AUROPHARMA": "Pharma", "BIOCON": "Pharma",
    "LUPIN": "Pharma", "ALKEM": "Pharma", "TORNTPHARM": "Pharma",
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto",
    "BAJAJ-AUTO": "Auto", "HEROMOTOCO": "Auto", "EICHERMOT": "Auto",
    "ASHOKLEY": "Auto", "BALKRISIND": "Auto", "MOTHERSON": "Auto",
    "TATASTEEL": "Metal", "HINDALCO": "Metal", "JSWSTEEL": "Metal",
    "COALINDIA": "Metal", "VEDL": "Metal", "NMDC": "Metal",
    "NATIONALUM": "Metal", "SAIL": "Metal", "JINDALSTEL": "Metal",
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy",
    "IOC": "Energy", "NTPC": "Energy", "POWERGRID": "Energy",
    "ADANIGREEN": "Energy", "TATAPOWER": "Energy", "GAIL": "Energy",
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "DABUR": "FMCG", "MARICO": "FMCG",
    "COLPAL": "FMCG", "GODREJCP": "FMCG", "EMAMILTD": "FMCG",
    "ULTRACEMCO": "Cement", "SHREECEM": "Cement", "AMBUJACEM": "Cement",
    "ACC": "Cement", "INDIACEM": "Cement", "RAMCOCEM": "Cement",
    "LT": "Infra", "ADANIENT": "Infra", "ADANIPORTS": "Infra",
    "PIDILITIND": "Chemicals", "SRF": "Chemicals", "AARTIIND": "Chemicals",
    "DEEPAKNTR": "Chemicals", "ATUL": "Chemicals", "CLEAN": "Chemicals",
    "DLF": "Realty", "GODREJPROP": "Realty", "OBEROIRLTY": "Realty",
    "PRESTIGE": "Realty", "BRIGADE": "Realty", "SOBHA": "Realty",
    "BHARTIARTL": "Telecom", "IDEA": "Telecom",
    "SBILIFE": "Insurance", "HDFCLIFE": "Insurance", "ICICIPRULI": "Insurance",
}


def get_sector(symbol: str) -> str:
    clean = symbol.replace("NSE:", "").replace(" ", "")
    return SECTOR_MAP.get(clean, "Others")


# =====================================================
# ANALYSIS FUNCTIONS
# =====================================================

def detect_weekly_trend(weekly_data) -> str:
    if not weekly_data or len(weekly_data) < 12:
        return "NEUTRAL"
    recent = weekly_data[-12:]
    current_close = recent[-1]["close"]
    prev_window = recent[:-1]
    prev_high = max(c["high"] for c in prev_window)
    prev_low = min(c["low"] for c in prev_window)
    last_4 = weekly_data[-4:]
    hh_count = sum(1 for i in range(1, len(last_4)) if last_4[i]["high"] > last_4[i - 1]["high"])
    ll_count = sum(1 for i in range(1, len(last_4)) if last_4[i]["low"] < last_4[i - 1]["low"])
    if current_close > prev_high:
        return "STRONG_BULL"
    elif hh_count >= 2 and current_close > prev_window[-1]["close"]:
        return "BULLISH"
    elif current_close < prev_low:
        return "STRONG_BEAR"
    elif ll_count >= 2 and current_close < prev_window[-1]["close"]:
        return "BEARISH"
    return "NEUTRAL"


def detect_daily_structure(daily_data):
    if not daily_data or len(daily_data) < 30:
        return "NEUTRAL", None
    swing_highs, swing_lows = [], []
    for i in range(2, len(daily_data) - 2):
        if (daily_data[i]["high"] > daily_data[i - 1]["high"]
                and daily_data[i]["high"] > daily_data[i + 1]["high"]
                and daily_data[i]["high"] > daily_data[i - 2]["high"]):
            swing_highs.append((i, daily_data[i]["high"]))
        if (daily_data[i]["low"] < daily_data[i - 1]["low"]
                and daily_data[i]["low"] < daily_data[i + 1]["low"]
                and daily_data[i]["low"] < daily_data[i - 2]["low"]):
            swing_lows.append((i, daily_data[i]["low"]))
    if not swing_highs or not swing_lows:
        return "NEUTRAL", None
    last_close = daily_data[-1]["close"]
    sh_i, sh_v = swing_highs[-1]
    sl_i, sl_v = swing_lows[-1]
    if last_close > sh_v and sh_i > sl_i:
        return "BULLISH_BOS", {"level": sh_v, "swing_low": sl_v}
    if last_close < sl_v and sl_i > sh_i:
        return "BEARISH_BOS", {"level": sl_v, "swing_high": sh_v}
    if len(swing_lows) >= 2 and len(swing_highs) >= 2:
        if swing_lows[-1][1] < swing_lows[-2][1] and last_close > swing_highs[-1][1]:
            return "BULLISH_CHOCH", {"level": swing_highs[-1][1], "reversal_from": swing_lows[-1][1]}
        if swing_highs[-1][1] > swing_highs[-2][1] and last_close < swing_lows[-1][1]:
            return "BEARISH_CHOCH", {"level": swing_lows[-1][1], "reversal_from": swing_highs[-1][1]}
    return "NEUTRAL", None


def detect_daily_ob(daily_data, direction):
    if not daily_data or len(daily_data) < 15:
        return None
    for i in range(-15, -3):
        ob = daily_data[i]
        impulse = daily_data[i + 1:i + 5]
        if not impulse:
            continue
        imp_r = max(c["high"] for c in impulse) - min(c["low"] for c in impulse)
        ob_r = ob["high"] - ob["low"]
        if ob_r == 0 or imp_r < ob_r * 1.8:
            continue
        if direction == "LONG" and ob["close"] < ob["open"]:
            return (ob["low"], ob["high"])
        if direction == "SHORT" and ob["close"] > ob["open"]:
            return (ob["low"], ob["high"])
    return None


def detect_daily_fvg(daily_data, direction):
    if not daily_data or len(daily_data) < 5:
        return None
    for i in range(-8, -2):
        if i + 2 >= len(daily_data):
            continue
        c1, c3 = daily_data[i], daily_data[i + 2]
        if direction == "LONG" and c3["low"] > c1["high"]:
            return (c1["high"], c3["low"])
        if direction == "SHORT" and c3["high"] < c1["low"]:
            return (c3["high"], c1["low"])
    return None


def calculate_relative_strength(stock_data, nifty_data, period=10) -> float:
    if not stock_data or not nifty_data or len(stock_data) < period or len(nifty_data) < period:
        return 0.0
    s = (stock_data[-1]["close"] - stock_data[-period]["close"]) / stock_data[-period]["close"]
    n = (nifty_data[-1]["close"] - nifty_data[-period]["close"]) / nifty_data[-period]["close"]
    return round((s - n) * 100, 2)


def swing_volume_signal(daily_data, period=10) -> str:
    if not daily_data or len(daily_data) < period + 5:
        return "NEUTRAL"
    recent = daily_data[-period:]
    older = daily_data[-period * 2:-period] if len(daily_data) >= period * 2 else daily_data[:period]
    r_avg = sum(c["volume"] for c in recent) / len(recent)
    o_avg = sum(c["volume"] for c in older) / len(older) if older else r_avg
    p_chg = (recent[-1]["close"] - recent[0]["close"]) / recent[0]["close"]
    v_chg = (r_avg - o_avg) / o_avg if o_avg > 0 else 0
    if v_chg > 0.2 and p_chg > 0:
        return "STRONG_ACCUMULATION"
    elif v_chg > 0.1 and p_chg >= 0:
        return "ACCUMULATION"
    elif v_chg > 0.2 and p_chg < -0.02:
        return "DISTRIBUTION"
    return "NEUTRAL"


def is_near_demand_zone(price, daily_data):
    if not daily_data or len(daily_data) < 20:
        return False
    for i in range(2, len(daily_data) - 2):
        if daily_data[i]["low"] < daily_data[i - 1]["low"] and daily_data[i]["low"] < daily_data[i + 1]["low"]:
            if abs(price - daily_data[i]["low"]) / daily_data[i]["low"] < 0.03:
                return True
    return False


def is_near_supply_zone(price, daily_data):
    if not daily_data or len(daily_data) < 20:
        return False
    for i in range(2, len(daily_data) - 2):
        if daily_data[i]["high"] > daily_data[i - 1]["high"] and daily_data[i]["high"] > daily_data[i + 1]["high"]:
            if abs(price - daily_data[i]["high"]) / daily_data[i]["high"] < 0.03:
                return True
    return False


def build_stock_research(symbol, daily_data, weekly_data, direction, rs, vol_sig):
    """Generates detailed research summary for each swing pick."""
    price = daily_data[-1]["close"]
    hi_52w = max(c["high"] for c in daily_data[-min(252, len(daily_data)):])
    lo_52w = min(c["low"] for c in daily_data[-min(252, len(daily_data)):])
    pct_from_high = round((hi_52w - price) / hi_52w * 100, 1)
    pct_from_low = round((price - lo_52w) / lo_52w * 100, 1)
    atr_d = calculate_atr(daily_data, 14)
    adr_pct = round(atr_d / price * 100, 2)
    avg_vol_20 = sum(c["volume"] for c in daily_data[-20:]) / 20
    last_vol = daily_data[-1]["volume"]
    vol_ratio = round(last_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
    w_body = abs(weekly_data[-1]["close"] - weekly_data[-1]["open"])
    w_range = weekly_data[-1]["high"] - weekly_data[-1]["low"]
    w_body_pct = round(w_body / w_range * 100, 1) if w_range > 0 else 0
    chg_10d = round((price - daily_data[-10]["close"]) / daily_data[-10]["close"] * 100, 1) if len(daily_data) >= 10 else 0
    chg_20d = round((price - daily_data[-20]["close"]) / daily_data[-20]["close"] * 100, 1) if len(daily_data) >= 20 else 0

    analysis = []
    analysis.append(f"CMP: {price}")
    analysis.append(f"52W Range: {lo_52w:.1f} - {hi_52w:.1f}")
    if direction == "LONG":
        analysis.append(f"{pct_from_low:.0f}% above 52W Low | {pct_from_high:.0f}% below 52W High")
        if pct_from_high < 10:
            analysis.append("Near 52W High - Momentum play")
        elif pct_from_high > 30:
            analysis.append("Deep pullback - Value zone")
    else:
        analysis.append(f"{pct_from_high:.0f}% below 52W High - Weakness")
    analysis.append(f"ADR: {adr_pct}% | Vol Ratio: {vol_ratio}x (vs 20d avg)")
    if vol_ratio > 1.5:
        analysis.append("HIGH VOLUME SPIKE - Institutional interest")
    analysis.append(f"10D Chg: {chg_10d:+.1f}% | 20D Chg: {chg_20d:+.1f}%")
    analysis.append(f"RS vs NIFTY: {rs:+.1f}% | Volume: {vol_sig}")
    analysis.append(f"Weekly Body: {w_body_pct}% of range")
    return analysis, {"hi_52w": hi_52w, "lo_52w": lo_52w, "adr_pct": adr_pct, "vol_ratio": vol_ratio, "chg_10d": chg_10d}


def score_swing_candidate(symbol, daily_data, weekly_data, nifty_daily):
    """Score a stock as swing candidate. Returns dict or None."""
    if not daily_data or len(daily_data) < 30 or not weekly_data or len(weekly_data) < 12:
        return None
    price = daily_data[-1]["close"]
    if price < 100 or price > 15000:
        return None
    avg_vol = sum(c["volume"] for c in daily_data[-20:]) / 20
    if avg_vol < 500000:
        return None

    score = 0
    reasons = []
    breakdown = {}
    direction = None

    # 1. Weekly Trend (0-2)
    wt = detect_weekly_trend(weekly_data)
    if wt == "STRONG_BULL":
        score += 2; breakdown["weekly"] = 2; direction = "LONG"; reasons.append("Strong weekly BOS uptrend")
    elif wt == "BULLISH":
        score += 1; breakdown["weekly"] = 1; direction = "LONG"; reasons.append("Weekly HH pattern")
    elif wt == "STRONG_BEAR":
        score += 2; breakdown["weekly"] = 2; direction = "SHORT"; reasons.append("Strong weekly BOS downtrend")
    elif wt == "BEARISH":
        score += 1; breakdown["weekly"] = 1; direction = "SHORT"; reasons.append("Weekly LL pattern")
    else:
        return None

    # 2. Daily Structure (0-2)
    ds, ds_info = detect_daily_structure(daily_data)
    if direction == "LONG" and ds in ("BULLISH_BOS", "BULLISH_CHOCH"):
        score += 2; breakdown["daily"] = 2; reasons.append(f"Daily {ds} confirmed")
    elif direction == "SHORT" and ds in ("BEARISH_BOS", "BEARISH_CHOCH"):
        score += 2; breakdown["daily"] = 2; reasons.append(f"Daily {ds} confirmed")
    else:
        return None

    # 3. OB + FVG (0-2)
    ob = detect_daily_ob(daily_data, direction)
    fvg = detect_daily_fvg(daily_data, direction)
    ob_pts = 0
    if ob:
        ob_pts += 1; reasons.append(f"Daily OB: {ob[0]:.1f}-{ob[1]:.1f}")
    if fvg:
        ob_pts += 1; reasons.append(f"Daily FVG: {fvg[0]:.1f}-{fvg[1]:.1f}")
    score += ob_pts; breakdown["ob_fvg"] = ob_pts

    # 4. Relative Strength (0-2)
    rs = calculate_relative_strength(daily_data, nifty_daily)
    if (direction == "LONG" and rs > 5.0) or (direction == "SHORT" and rs < -5.0):
        score += 2; breakdown["rs"] = 2; reasons.append(f"STRONG RS: {rs:+.1f}%")
    elif (direction == "LONG" and rs > 2.0) or (direction == "SHORT" and rs < -2.0):
        score += 1; breakdown["rs"] = 1; reasons.append(f"RS: {rs:+.1f}%")
    else:
        breakdown["rs"] = 0

    # 5. Volume (0-1)
    vs = swing_volume_signal(daily_data)
    if (direction == "LONG" and vs in ("ACCUMULATION", "STRONG_ACCUMULATION")) or \
       (direction == "SHORT" and vs == "DISTRIBUTION"):
        score += 1; breakdown["vol"] = 1; reasons.append(f"Vol: {vs}")
    else:
        breakdown["vol"] = 0

    # 6. Entry / SL / Target
    atr = calculate_atr(daily_data)
    if direction == "LONG":
        entry = price
        sl = (ob[0] - atr * 0.3) if ob else ((ds_info.get("swing_low", price - atr * 2)) - atr * 0.3)
        target = entry + (entry - sl) * 3
    else:
        entry = price
        sl = (ob[1] + atr * 0.3) if ob else ((ds_info.get("swing_high", price + atr * 2)) + atr * 0.3)
        target = entry - (sl - entry) * 3
    risk = abs(entry - sl)
    reward = abs(target - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0
    potential_pct = round(reward / entry * 100, 1)
    if rr >= 3.0:
        score += 1; breakdown["rr"] = 1; reasons.append(f"RR: 1:{rr}")
    else:
        breakdown["rr"] = 0

    # 7. Zone Proximity (0-1)
    if (direction == "LONG" and is_near_demand_zone(price, daily_data)) or \
       (direction == "SHORT" and is_near_supply_zone(price, daily_data)):
        score += 1; breakdown["zone"] = 1; reasons.append("Near key demand/supply zone")
    else:
        breakdown["zone"] = 0

    # 8. Volume Spike Bonus (0-1)
    last_vol = daily_data[-1]["volume"]
    if last_vol > avg_vol * 1.5:
        score += 1; breakdown["vol_spike"] = 1; reasons.append(f"Volume spike: {round(last_vol / avg_vol, 1)}x avg")
    else:
        breakdown["vol_spike"] = 0

    # QUALITY GATE
    if score < cfg.MIN_SWING_QUALITY or rr < 2.5:
        return None

    research, fundamentals = build_stock_research(symbol, daily_data, weekly_data, direction, rs, vs)

    return {
        "symbol": symbol, "direction": direction, "score": score,
        "entry": round(entry, 2), "sl": round(sl, 2), "target": round(target, 2),
        "rr": rr, "potential_pct": potential_pct,
        "sector": get_sector(symbol), "weekly_trend": wt,
        "daily_structure": ds, "rs": rs, "volume": vs,
        "reasons": reasons, "breakdown": breakdown,
        "research": research, "fundamentals": fundamentals
    }


# =====================================================
# REPORTING
# =====================================================

def format_swing_report(picks, market_regime="UNKNOWN"):
    msg = "<b>PREMIUM SWING PICKS</b>\n"
    msg += f"{datetime.now(_IST).replace(tzinfo=None).strftime('%d %b %Y %I:%M %p')}\n"
    msg += f"Top {len(picks)} out of 500 stocks scanned\n"
    msg += f"Market Regime: {market_regime}\n"
    msg += "-" * 30 + "\n"
    for idx, c in enumerate(picks, 1):
        arrow = "UP" if c["direction"] == "LONG" else "DN"
        sl_pct = round(abs(c["entry"] - c["sl"]) / c["entry"] * 100, 1)
        msg += f"\n{arrow} <b>#{idx} {c['symbol'].replace('NSE:', '')}</b> | {c['sector']}\n"
        msg += f"Quality: {c['score']}/12\n\n"
        msg += f"<b>TRADE PLAN:</b>\n"
        msg += f"  Direction: {c['direction']}\n"
        msg += f"  Entry: {c['entry']}\n"
        msg += f"  Stoploss: {c['sl']} (-{sl_pct}%)\n"
        msg += f"  Target: {c['target']} (+{c['potential_pct']}%)\n"
        msg += f"  Risk:Reward: 1:{c['rr']}\n\n"
        msg += f"<b>WHY THIS STOCK:</b>\n"
        for r in c["reasons"]:
            msg += f"  [OK] {r}\n"
        msg += f"\n<b>RESEARCH:</b>\n"
        for line in c.get("research", []):
            msg += f"  {line}\n"
        msg += "\n" + "-" * 28 + "\n"
    msg += "\n<i>Monitoring active. SL/Target alerts will follow.</i>"
    return msg


# =====================================================
# MONITORING
# =====================================================

def monitor_swing_trades(fetch_ltp_fn, telegram_fn):
    """Check LTP for all active swing trades, alert on SL/Target hits."""
    if not cfg.ACTIVE_SWING_TRADES:
        return

    pro_chat = os.environ.get("SMC_PRO_CHAT_ID", cfg.SMC_PRO_CHAT_ID)

    for trade in list(cfg.ACTIVE_SWING_TRADES):
        try:
            price = fetch_ltp_fn(trade["symbol"])
            if not price:
                continue

            sym_clean = trade["symbol"].replace("NSE:", "")
            direction = trade["direction"]

            # Entry Trigger
            if trade.get("status") == "WAITING":
                triggered = False
                if direction == "LONG" and price <= trade["entry"] * 1.002:
                    triggered = True
                elif direction == "SHORT" and price >= trade["entry"] * 0.998:
                    triggered = True
                if triggered:
                    trade["status"] = "ACTIVE"
                    trade["triggered_at"] = datetime.now(_IST).replace(tzinfo=None).strftime("%H:%M")
                    telegram_fn(
                        f"<b>SWING ENTRY TRIGGERED</b>\n\n"
                        f"<b>{sym_clean}</b> | {direction}\n"
                        f"Entry: {trade['entry']} (CMP: {price})\n"
                        f"SL: {trade['sl']} | Target: {trade['target']}\n"
                        f"RR: 1:{trade['rr']}",
                        chat_id=pro_chat
                    )
                continue

            if trade.get("status") != "ACTIVE":
                continue

            target_hit = (direction == "LONG" and price >= trade["target"]) or \
                         (direction == "SHORT" and price <= trade["target"])
            sl_hit = (direction == "LONG" and price <= trade["sl"]) or \
                     (direction == "SHORT" and price >= trade["sl"])

            if target_hit:
                telegram_fn(
                    f"<b>SWING TARGET HIT!</b>\n\n"
                    f"<b>{sym_clean}</b> | {direction}\n"
                    f"Entry: {trade['entry']} -> Exit: {price}\n"
                    f"P&L: +{trade['potential_pct']}%\n"
                    f"RR: 1:{trade['rr']}",
                    chat_id=pro_chat
                )
                cfg.ACTIVE_SWING_TRADES.remove(trade)
            elif sl_hit:
                sl_pct = round(abs(trade["entry"] - trade["sl"]) / trade["entry"] * 100, 1)
                telegram_fn(
                    f"<b>SWING SL HIT</b>\n\n"
                    f"<b>{sym_clean}</b> | {direction}\n"
                    f"Entry: {trade['entry']} -> SL: {price}\n"
                    f"Loss: -{sl_pct}%",
                    chat_id=pro_chat
                )
                cfg.ACTIVE_SWING_TRADES.remove(trade)
            else:
                if direction == "LONG":
                    progress = (price - trade["entry"]) / (trade["target"] - trade["entry"]) * 100
                else:
                    progress = (trade["entry"] - price) / (trade["entry"] - trade["target"]) * 100
                trade["last_price"] = price
                trade["progress_pct"] = round(progress, 1)
        except Exception:
            continue


# =====================================================
# MAIN RUNNER
# =====================================================

def run_swing_scan(fetch_ohlc_fn, get_universe_fn, telegram_fn):
    """Run the premium swing scanner. Call once per day."""
    if cfg.SWING_SCAN_SENT:
        return

    pro_chat = os.environ.get("SMC_PRO_CHAT_ID", cfg.SMC_PRO_CHAT_ID)
    print("\n" + "=" * 50)
    print("PREMIUM SWING SCANNER - LIVE MARKET SCAN")
    print("=" * 50)
    telegram_fn("<b>PREMIUM SWING SCANNER</b> scanning 500 stocks...", chat_id=pro_chat)

    try:
        universe = get_universe_fn()
        if not universe:
            print("[SWING] No stocks in universe")
            return

        nifty_daily = fetch_ohlc_fn("NSE:NIFTY 50", "day", lookback=180)
        candidates = []
        scanned = 0

        for i, symbol in enumerate(universe):
            try:
                if i % 50 == 0 and i > 0:
                    print(f"  [SWING] {i}/{len(universe)} scanned... {len(candidates)} found")
                daily = fetch_ohlc_fn(symbol, "day", lookback=180)
                weekly = fetch_ohlc_fn(symbol, "week", lookback=365)
                if not daily or len(daily) < 30 or not weekly or len(weekly) < 12:
                    continue
                result = score_swing_candidate(symbol, daily, weekly, nifty_daily)
                if result:
                    candidates.append(result)
                scanned += 1
            except Exception:
                continue

        print(f"\n[SWING] Done: {scanned} scanned | {len(candidates)} qualifying")
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top_picks = candidates[:cfg.MAX_SWING_PICKS]

        if top_picks:
            report = format_swing_report(top_picks, cfg.MARKET_REGIME)
            if len(report) > 4000:
                for part in [report[i:i + 4000] for i in range(0, len(report), 4000)]:
                    telegram_fn(part, chat_id=pro_chat)
                    t.sleep(1)
            else:
                telegram_fn(report, chat_id=pro_chat)

            for pick in top_picks:
                cfg.ACTIVE_SWING_TRADES.append({
                    "symbol": pick["symbol"], "direction": pick["direction"],
                    "entry": pick["entry"], "sl": pick["sl"], "target": pick["target"],
                    "rr": pick["rr"], "potential_pct": pick["potential_pct"],
                    "score": pick["score"], "sector": pick["sector"],
                    "status": "WAITING",
                    "scan_time": datetime.now(_IST).replace(tzinfo=None).strftime("%H:%M"),
                    "last_price": pick["entry"], "progress_pct": 0
                })

            with open("swing_scan_results.json", "w") as f:
                _json.dump(top_picks, f, indent=2, default=str)
        else:
            telegram_fn("Swing Scanner: No stocks meet premium quality bar today.", chat_id=pro_chat)

        cfg.SWING_SCAN_SENT = True
    except Exception as e:
        logging.error(f"Swing Scanner Error: {e}")
