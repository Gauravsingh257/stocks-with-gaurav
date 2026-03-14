"""
agents/oi_intelligence_agent.py — OI Intelligence Visual Agent
================================================================
Read-only aggregator that pulls from:
  • option_monthly_lows.json / option_tap_state.json / option_notified_taps.json
  • engine.oi_sentiment (PCR, buildup, scores)
  • engine.oi_short_covering (strike-level signals)

Produces a unified OI intelligence snapshot for the dashboard.
Does NOT mutate engine state, place trades, or send Telegram alerts.

Update frequency: every 60 seconds during market hours.
"""

import json
import logging
import os
from datetime import datetime, time, date
from pathlib import Path
from collections import deque

try:
    from engine.market_state_engine import get_market_state
except ImportError:
    get_market_state = lambda: {"state": "RANGE", "events": [], "score_breakdown": {}, "confidence": 0}

logger = logging.getLogger("agents.oi_intelligence")

WORKSPACE = str(Path(__file__).resolve().parents[1])

# ── File paths ───────────────────────────────────────────────
MONTHLY_LOWS_FILE    = os.path.join(WORKSPACE, "option_monthly_lows.json")
TAP_STATE_FILE       = os.path.join(WORKSPACE, "option_tap_state.json")
NOTIFIED_TAPS_FILE   = os.path.join(WORKSPACE, "option_notified_taps.json")
SNAPSHOT_FILE        = os.path.join(WORKSPACE, "oi_intelligence_snapshot.json")
OI_SC_SNAPSHOT_FILE  = os.path.join(WORKSPACE, "oi_sc_snapshot.json")
OI_SC_ACTIVE_TRADES_FILE = os.path.join(WORKSPACE, "oi_sc_active_trades.json")

# -- Rolling history (30-min window at 60s intervals = 30 entries)
_pcr_history: deque = deque(maxlen=30)
_bias_history: deque = deque(maxlen=30)
_last_scan: datetime | None = None

# -- Previous close & day-start OI baseline for gap/trend detection
_prev_close: dict = {}       # {"NIFTY": 25200, "BANKNIFTY": 61000}
_day_start_oi: dict = {}     # {"NIFTY": {"call_oi": X, "put_oi": Y}, ...}
_prev_snapshots: dict = {}   # previous call's snapshots for OI change detection
_baseline_date: str = ""    # date string for daily reset
_prev_strike_oi: dict = {}   # {"NIFTY": {"24500_CE": 1000000, "24500_PE": 800000, ...}}

# -- Cached snapshot
_cached_snapshot: dict | None = None


def _is_market_hours() -> bool:
    """True during 09:15–15:31 IST Mon–Fri."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    return time(9, 15) <= t <= time(15, 31)


def _load_json(path: str, default=None):
    """Safely load JSON file."""
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


# ── Kite direct fallback ──────────────────────────────────────────────────────
def _get_kite_direct_data() -> dict | None:
    """
    Fallback: fetch spot prices + option chain OI directly via Kite API
    when the engine's oi_sentiment module has no data (e.g. after restart,
    outside market hours, or before first tick).
    Returns a dict matching _get_oi_sentiment_data() shape, or None on failure.
    """
    try:
        from kiteconnect import KiteConnect
        from kite_credentials import API_KEY

        token_file = os.path.join(WORKSPACE, "access_token.txt")
        if not os.path.exists(token_file):
            return None

        access_token = open(token_file).read().strip()
        if not access_token:
            return None

        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)

        # Fetch spot prices
        indices = kite.ltp(["NSE:NIFTY 50", "NSE:NIFTY BANK"])
        nifty_spot = indices.get("NSE:NIFTY 50", {}).get("last_price", 0)
        bn_spot = indices.get("NSE:NIFTY BANK", {}).get("last_price", 0)

        if not nifty_spot and not bn_spot:
            return None

        snapshots = {}
        total_call_oi = 0
        total_put_oi = 0

        for name, spot, step in [("NIFTY", nifty_spot, 50), ("BANKNIFTY", bn_spot, 100)]:
            if not spot:
                continue

            atm = round(spot / step) * step
            strikes = [atm + (i * step) for i in range(-5, 6)]  # 11 strikes around ATM

            # Build Kite quote instrument strings for CE and PE
            # Use NFO exchange, need to find current expiry from instruments cache
            nfo_instruments = []
            try:
                inst_cache = os.path.join(WORKSPACE, "nfo_instruments.json")
                cache_valid = False
                if os.path.exists(inst_cache):
                    # Cache is valid only if created today
                    mtime = datetime.fromtimestamp(os.path.getmtime(inst_cache)).date()
                    if mtime == date.today():
                        all_inst = _load_json(inst_cache, [])
                        cache_valid = bool(all_inst)
                if not cache_valid:
                    all_inst = kite.instruments("NFO")
                    # Save cache
                    try:
                        with open(inst_cache, "w") as f:
                            json.dump([{k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in i.items()} for i in all_inst], f)
                    except Exception:
                        pass

                # Find nearest monthly expiry
                today = date.today()
                matching = []
                for inst in all_inst:
                    if inst.get("name") == name and inst.get("instrument_type") in ("CE", "PE") and inst.get("segment") == "NFO-OPT":
                        exp = inst.get("expiry")
                        if isinstance(exp, str):
                            exp = datetime.strptime(exp, "%Y-%m-%d").date()
                        if exp and exp >= today:
                            matching.append(inst)

                # Group by expiry, pick the nearest
                if matching:
                    nearest_exp = min(set(m.get("expiry") if isinstance(m.get("expiry"), date) else datetime.strptime(m["expiry"], "%Y-%m-%d").date() for m in matching))
                    nfo_instruments = [m for m in matching
                                       if (m.get("expiry") if isinstance(m.get("expiry"), date) else datetime.strptime(m["expiry"], "%Y-%m-%d").date()) == nearest_exp
                                       and m.get("strike") in strikes]
            except Exception as e:
                logger.debug(f"Instruments lookup failed for {name}: {e}")
                continue

            if not nfo_instruments:
                # Manual symbol construction as fallback
                # For now just set spot with no OI data
                snapshots[name] = {
                    "spot": spot,
                    "call_oi": 0, "put_oi": 0,
                    "call_oi_by_strike": {}, "put_oi_by_strike": {},
                    "max_call_strike": 0, "max_put_strike": 0,
                    "max_call_oi": 0, "max_put_oi": 0,
                }
                continue

            # Fetch quotes for these instruments (max ~22 at a time)
            symbols = [f"NFO:{inst['tradingsymbol']}" for inst in nfo_instruments]
            call_oi_by_strike = {}
            put_oi_by_strike = {}
            max_call_oi = 0
            max_put_oi = 0
            max_call_strike = 0
            max_put_strike = 0

            try:
                # Kite allows max 500 symbols per quote call
                batch_size = 200
                for i in range(0, len(symbols), batch_size):
                    batch = symbols[i:i + batch_size]
                    quotes = kite.quote(batch)
                    for sym_key, q in quotes.items():
                        oi = q.get("oi", 0)
                        inst_type = q.get("instrument_type") or ("CE" if "CE" in sym_key else "PE" if "PE" in sym_key else "")

                        # Extract strike from tradingsymbol
                        strike_val = 0
                        for inst in nfo_instruments:
                            if inst["tradingsymbol"] in sym_key:
                                strike_val = int(inst.get("strike", 0))
                                inst_type = inst.get("instrument_type", inst_type)
                                break

                        strike_str = str(strike_val)
                        if inst_type == "CE":
                            call_oi_by_strike[strike_str] = oi
                            total_call_oi += oi
                            if oi > max_call_oi:
                                max_call_oi = oi
                                max_call_strike = strike_val
                        elif inst_type == "PE":
                            put_oi_by_strike[strike_str] = oi
                            total_put_oi += oi
                            if oi > max_put_oi:
                                max_put_oi = oi
                                max_put_strike = strike_val

            except Exception as e:
                logger.debug(f"Quote fetch failed for {name}: {e}")

            snapshots[name] = {
                "spot": spot,
                "call_oi": sum(call_oi_by_strike.values()),
                "put_oi": sum(put_oi_by_strike.values()),
                "call_oi_by_strike": call_oi_by_strike,
                "put_oi_by_strike": put_oi_by_strike,
                "max_call_strike": max_call_strike,
                "max_put_strike": max_put_strike,
                "max_call_oi": max_call_oi,
                "max_put_oi": max_put_oi,
            }

        if not snapshots:
            return None

        # Compute PCR
        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 1.0

        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # --- Daily reset for baseline tracking ---
        global _prev_close, _day_start_oi, _prev_snapshots, _baseline_date
        if _baseline_date != today_str:
            _baseline_date = today_str
            _day_start_oi = {}
            _prev_snapshots = {}
            _prev_close = {}
            # Fetch previous close from Kite
            try:
                for idx_sym, idx_name in [("NSE:NIFTY 50", "NIFTY"), ("NSE:NIFTY BANK", "BANKNIFTY")]:
                    ohlc = kite.ohlc([idx_sym])
                    if idx_sym in ohlc:
                        _prev_close[idx_name] = ohlc[idx_sym].get("ohlc", {}).get("close", 0)
            except Exception as e:
                logger.debug(f"Failed to fetch prev close: {e}")

        # Store day-start OI baseline (first reading of the day)
        if not _day_start_oi:
            for sname, sdata in snapshots.items():
                _day_start_oi[sname] = {
                    "call_oi": sdata["call_oi"],
                    "put_oi": sdata["put_oi"],
                }

        # --- SPOT DIRECTION ANALYSIS ---
        spot_direction = 0  # -2 to +2
        gap_down = False
        gap_up = False
        spot_details = []

        for sname, sdata in snapshots.items():
            prev_cl = _prev_close.get(sname, 0)
            if prev_cl > 0:
                change_pct = (sdata["spot"] - prev_cl) / prev_cl * 100
                if change_pct < -1.0:
                    spot_direction -= 1
                    spot_details.append(f"{sname} {change_pct:+.1f}%")
                    if change_pct < -0.5:
                        gap_down = True
                elif change_pct > 1.0:
                    spot_direction += 1
                    spot_details.append(f"{sname} {change_pct:+.1f}%")
                    if change_pct > 0.5:
                        gap_up = True

        # --- PRICE + OI MATRIX (compare with previous snapshot) ---
        price_oi_pattern = "NONE"
        oi_change_bias = "NEUTRAL"

        if _prev_snapshots:
            total_prev_call = sum(s.get("call_oi", 0) for s in _prev_snapshots.values())
            total_prev_put = sum(s.get("put_oi", 0) for s in _prev_snapshots.values())
            call_oi_change = total_call_oi - total_prev_call
            put_oi_change = total_put_oi - total_prev_put
            oi_expanding = (call_oi_change + put_oi_change) > 0
            price_up = spot_direction > 0
            price_down = spot_direction < 0

            if price_up and oi_expanding:
                price_oi_pattern = "LONG_BUILDUP"
            elif price_up and not oi_expanding:
                price_oi_pattern = "SHORT_COVERING"
            elif price_down and oi_expanding:
                price_oi_pattern = "SHORT_BUILDUP"
            elif price_down and not oi_expanding:
                price_oi_pattern = "LONG_UNWINDING"

            # OI change directional bias
            if total_prev_call > 0 and total_prev_put > 0:
                call_chg_pct = call_oi_change / total_prev_call * 100
                put_chg_pct = put_oi_change / total_prev_put * 100
                if put_chg_pct > call_chg_pct + 3:
                    oi_change_bias = "BULLISH"
                elif call_chg_pct > put_chg_pct + 3:
                    oi_change_bias = "BEARISH"

        # Save current snapshots for next comparison
        _prev_snapshots = {k: dict(v) for k, v in snapshots.items()}

        # --- SMART BIAS COMPUTATION (PCR + Price Direction + OI Pattern) ---
        bull_score = 0
        bear_score = 0

        # PCR level (weight: 1) -- PCR > 1.2 is only bullish if price is NOT falling
        if pcr > 1.2:
            if spot_direction >= 0:  # Price flat/up: put writing = support
                bull_score += 1
            else:  # Price falling: high PCR = put BUYING = hedging/fear
                bear_score += 1
        elif pcr < 0.7:
            if spot_direction <= 0:  # Price flat/down: call writing = ceiling
                bear_score += 1
            else:  # Price rising despite low PCR
                bull_score += 1

        # Price direction (weight: 2 -- strongest signal)
        if spot_direction >= 2:
            bull_score += 2
        elif spot_direction == 1:
            bull_score += 1
        elif spot_direction <= -2:
            bear_score += 2
        elif spot_direction == -1:
            bear_score += 1

        # Price+OI pattern (weight: 1-2)
        if price_oi_pattern == "LONG_BUILDUP":
            bull_score += 2
        elif price_oi_pattern == "SHORT_COVERING":
            bull_score += 1
        elif price_oi_pattern == "SHORT_BUILDUP":
            bear_score += 2
        elif price_oi_pattern == "LONG_UNWINDING":
            bear_score += 1

        # OI change bias (weight: 1)
        if oi_change_bias == "BULLISH":
            bull_score += 1
        elif oi_change_bias == "BEARISH":
            bear_score += 1

        # Gap override: strong gap overrides PCR
        if gap_down and spot_direction < 0:
            bear_score += 1
        if gap_up and spot_direction > 0:
            bull_score += 1

        # Determine PCR bias label (for display)
        if pcr > 1.2:
            pcr_bias = "HIGH_PCR"
        elif pcr < 0.7:
            pcr_bias = "LOW_PCR"
        elif pcr > 1.0:
            pcr_bias = "ABOVE_1"
        elif pcr < 0.9:
            pcr_bias = "BELOW_1"
        else:
            pcr_bias = "NEUTRAL"

        # Final sentiment
        if bull_score >= 2 and bull_score > bear_score + 1:
            sentiment = "BULLISH"
        elif bear_score >= 2 and bear_score > bull_score + 1:
            sentiment = "BEARISH"
        else:
            sentiment = "NEUTRAL"

        # PCR trend from rolling history
        pcr_trend = "FLAT"
        if len(_pcr_history) >= 3:
            recent_pcrs = [h["pcr"] for h in list(_pcr_history)[-3:] if h.get("pcr")]
            older_pcrs = [h["pcr"] for h in list(_pcr_history)[-6:-3] if h.get("pcr")]
            if recent_pcrs and older_pcrs:
                avg_r = sum(recent_pcrs) / len(recent_pcrs)
                avg_o = sum(older_pcrs) / len(older_pcrs)
                if avg_o > 0:
                    pct = (avg_r - avg_o) / avg_o
                    if pct > 0.05:
                        pcr_trend = "RISING"
                    elif pct < -0.05:
                        pcr_trend = "FALLING"

        details = (f"Direct Kite: PCR={pcr} ({pcr_bias}) | "
                   f"Spot: {', '.join(spot_details) if spot_details else 'FLAT'} | "
                   f"Pattern={price_oi_pattern} | "
                   f"Bull={bull_score} Bear={bear_score}")

        return {
            "sentiment": sentiment,
            "pcr_bias": pcr_bias,
            "pcr_trend": pcr_trend,
            "oi_change_bias": oi_change_bias,
            "price_oi_pattern": price_oi_pattern,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "pcr_history": [{"time": now.strftime("%H:%M"), "pcr": pcr, "call_oi": total_call_oi, "put_oi": total_put_oi}],
            "snapshots": snapshots,
            "last_update": now.isoformat(),
            "details": details,
            "spot_direction": spot_direction,
            "gap_down": gap_down,
            "gap_up": gap_up,
        }

    except Exception as e:
        logger.debug(f"Kite direct fallback failed: {e}")
        return None


def _get_oi_sentiment_data() -> dict:
    """Pull from engine.oi_sentiment safely."""
    try:
        from engine.oi_sentiment import get_oi_sentiment
        state = get_oi_sentiment()
        # Convert deque/datetime for JSON
        pcr_history = []
        for entry in state.get("pcr_history", []):
            ts, pcr_val, call_oi, put_oi = entry
            pcr_history.append({
                "time": ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts),
                "pcr": round(pcr_val, 3),
                "call_oi": call_oi,
                "put_oi": put_oi,
            })

        snapshots = {}
        for name, snap in state.get("snapshots", {}).items():
            snapshots[name] = {
                "spot": snap.get("spot", 0),
                "call_oi": snap.get("call_oi", 0),
                "put_oi": snap.get("put_oi", 0),
                "call_oi_by_strike": {str(k): v for k, v in snap.get("call_oi_by_strike", {}).items()},
                "put_oi_by_strike": {str(k): v for k, v in snap.get("put_oi_by_strike", {}).items()},
                "max_call_strike": snap.get("max_call_strike", 0),
                "max_put_strike": snap.get("max_put_strike", 0),
                "max_call_oi": snap.get("max_call_oi", 0),
                "max_put_oi": snap.get("max_put_oi", 0),
            }

        return {
            "sentiment": state.get("sentiment", "NEUTRAL"),
            "pcr_bias": state.get("pcr_bias", "NEUTRAL"),
            "pcr_trend": state.get("pcr_trend", "FLAT"),
            "oi_change_bias": state.get("oi_change_bias", "NEUTRAL"),
            "price_oi_pattern": state.get("price_oi_pattern", "NONE"),
            "bull_score": state.get("bull_score", 0),
            "bear_score": state.get("bear_score", 0),
            "pcr_history": pcr_history,
            "snapshots": snapshots,
            "last_update": state["last_update"].isoformat() if state.get("last_update") else None,
            "details": state.get("details", ""),
        }
    except Exception as e:
        logger.debug(f"OI sentiment not available: {e}")
        return {
            "sentiment": "NEUTRAL", "pcr_bias": "NEUTRAL", "pcr_trend": "FLAT",
            "oi_change_bias": "NEUTRAL", "price_oi_pattern": "NONE",
            "bull_score": 0, "bear_score": 0, "pcr_history": [],
            "snapshots": {}, "last_update": None, "details": "",
        }


def _get_short_covering_data() -> list:
    """
    Read short covering data from the shared snapshot file written by
    the engine process (oi_sc_snapshot.json).

    Falls back to in-memory import if the file is missing (same-process case).
    """
    import json as _json

    # Primary: read from shared file (cross-process)
    if os.path.exists(OI_SC_SNAPSHOT_FILE):
        try:
            with open(OI_SC_SNAPSHOT_FILE, "r") as f:
                snapshot = _json.load(f)

            # Check freshness — ignore if older than 5 minutes
            updated_at = snapshot.get("updated_at", "")
            if updated_at:
                try:
                    ts = datetime.fromisoformat(updated_at)
                    age = (datetime.now() - ts).total_seconds()
                    if age > 300:
                        logger.debug(f"SC snapshot stale ({age:.0f}s old), skipping")
                except (ValueError, TypeError):
                    pass

            signals = []
            history = snapshot.get("strike_history", {})
            for symbol, data in history.items():
                signals.append({
                    "symbol": data.get("symbol", symbol),
                    "underlying": data.get("underlying", "?"),
                    "opt_type": data.get("opt_type", "?"),
                    "current_oi": data.get("current_oi", 0),
                    "current_ltp": data.get("current_ltp", 0),
                    "oi_change_pct": data.get("oi_change_pct", 0),
                    "price_change_pct": data.get("price_change_pct", 0),
                    "volume": data.get("volume", 0),
                    "is_short_covering": data.get("is_short_covering", False),
                    "score": data.get("score", 0),
                    "time": data.get("time", ""),
                    "peak_oi": data.get("peak_oi", 0),
                })

            # Also include any qualified trade signals from the engine's
            # own detection (these have proper score, trade levels, etc.)
            for sig in snapshot.get("trade_signals", []):
                oi_drop = sig.get("oi_drop") or {}
                price_rise = sig.get("price_rise") or {}
                signals.append({
                    "symbol": sig.get("tradingsymbol", ""),
                    "tradingsymbol": sig.get("tradingsymbol", ""),
                    "underlying": sig.get("underlying", "?"),
                    "opt_type": sig.get("opt_type", "?"),
                    "current_oi": sig.get("current_oi", 0),
                    "current_ltp": sig.get("current_ltp", 0),
                    "oi_change_pct": -oi_drop.get("drop_pct", 0),
                    "price_change_pct": price_rise.get("rise_pct", 0),
                    "volume": 0,
                    "is_short_covering": True,
                    "score": sig.get("score", 0),
                    "time": sig.get("timestamp", ""),
                    "spot": sig.get("spot", 0),
                    "trade_action": sig.get("trade_action", ""),
                    "trade_levels": sig.get("trade_levels"),
                    "signal_type": sig.get("signal_type", "OI_SHORT_COVERING"),
                    "peak_oi": sig.get("peak_oi", 0),
                })

            return signals
        except Exception as e:
            logger.debug(f"Failed to read SC snapshot file: {e}")

    # Fallback: in-memory import (works when dashboard runs in same process)
    try:
        from engine.oi_short_covering import get_strike_history
        history = get_strike_history()
        signals = []
        if history:
            for symbol, readings in history.items():
                if not readings:
                    continue
                latest = readings[-1]
                ts, oi, ltp, vol = latest
                if len(readings) >= 3:
                    old_oi = readings[-3][1]
                    old_ltp = readings[-3][2]
                    oi_change_pct = ((oi - old_oi) / old_oi * 100) if old_oi > 0 else 0
                    price_change_pct = ((ltp - old_ltp) / old_ltp * 100) if old_ltp > 0 else 0
                else:
                    oi_change_pct = 0
                    price_change_pct = 0

                is_sc = oi_change_pct < -3 and price_change_pct > 2
                opt_type = "CE" if "CE" in symbol else "PE" if "PE" in symbol else "?"
                underlying = (
                    "BANKNIFTY" if symbol.startswith("BANKNIFTY")
                    else "NIFTY" if symbol.startswith("NIFTY") and not symbol.startswith("BANKNIFTY")
                    else "?"
                )
                signals.append({
                    "symbol": symbol,
                    "underlying": underlying,
                    "opt_type": opt_type,
                    "current_oi": oi,
                    "current_ltp": round(ltp, 2),
                    "oi_change_pct": round(oi_change_pct, 2),
                    "price_change_pct": round(price_change_pct, 2),
                    "volume": vol,
                    "is_short_covering": is_sc,
                    "score": min(10, max(0, int(abs(oi_change_pct) + price_change_pct))),
                    "time": ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts),
                })
        return signals
    except Exception as e:
        logger.debug(f"Short covering data not available: {e}")
        return []


def _get_monthly_low_taps() -> list:
    """Read option monthly low tap data from JSON files."""
    monthly_lows = _load_json(MONTHLY_LOWS_FILE, {})
    tap_state = _load_json(TAP_STATE_FILE, {})
    notified_taps = _load_json(NOTIFIED_TAPS_FILE, [])

    taps = []
    for symbol, low_data in monthly_lows.items():
        state = tap_state.get(symbol, {})
        is_tapped = state.get("state", "ACTIVE") == "TAPPED"
        is_confirmed = state.get("state", "ACTIVE") == "CONFIRMED"
        tap_price = state.get("tap_price")
        tap_time = state.get("tap_time")

        # Check if notified today
        today_str = datetime.now().strftime("%Y%m%d")
        tap_id = f"{symbol}_{today_str}"
        was_notified = tap_id in notified_taps

        opt_type = low_data.get("type", "CE" if "CE" in symbol else "PE")
        underlying = "NIFTY" if "NIFTY" in symbol and "BANKNIFTY" not in symbol else "BANKNIFTY" if "BANKNIFTY" in symbol else "OTHER"

        taps.append({
            "symbol": symbol,
            "underlying": underlying,
            "opt_type": opt_type,
            "strike": low_data.get("strike", 0),
            "monthly_low": low_data.get("monthly_low", 0),
            "last_update": low_data.get("last_update", ""),
            "expiry": low_data.get("expiry", ""),
            "state": state.get("state", "ACTIVE"),
            "tap_price": tap_price,
            "tap_time": tap_time,
            "was_notified": was_notified,
            "is_active_tap": is_tapped,
            "is_confirmed": is_confirmed,
        })

    return taps


def _compute_confidence(oi_data: dict, sc_signals: list, taps: list) -> int:
    """Compute overall OI confidence score (0-100).
    
    Confidence = how strongly the signals agree on a direction.
    High confidence requires MULTIPLE signals pointing the same way.
    Counter-trend signals reduce confidence.
    """
    score = 50  # Base

    bull = oi_data.get("bull_score", 0)
    bear = oi_data.get("bear_score", 0)

    # Bull/bear score difference -- primary driver
    diff = bull - bear
    score += min(20, max(-20, diff * 5))

    # PCR trend adds confidence when aligned
    if oi_data.get("pcr_trend") == "RISING" and diff > 0:
        score += 10  # Trend confirms bullish
    elif oi_data.get("pcr_trend") == "FALLING" and diff < 0:
        score += 10  # Trend confirms bearish
    elif oi_data.get("pcr_trend") == "RISING" and diff < 0:
        score -= 5   # Trend contradicts -- reduce confidence
    elif oi_data.get("pcr_trend") == "FALLING" and diff > 0:
        score -= 5

    # Price+OI pattern agreement
    pattern = oi_data.get("price_oi_pattern", "NONE")
    if pattern in ("LONG_BUILDUP", "SHORT_COVERING") and diff > 0:
        score += 10  # Price+OI confirms bullish
    elif pattern in ("SHORT_BUILDUP", "LONG_UNWINDING") and diff < 0:
        score += 10  # Price+OI confirms bearish
    elif pattern in ("LONG_BUILDUP", "SHORT_COVERING") and diff < 0:
        score -= 10  # Contradiction!
    elif pattern in ("SHORT_BUILDUP", "LONG_UNWINDING") and diff > 0:
        score -= 10  # Contradiction!

    # Short covering signals (only boost if aligned with direction)
    active_sc = [s for s in sc_signals if s.get("is_short_covering")]
    ce_sc = sum(1 for s in active_sc if s.get("opt_type") == "CE")
    pe_sc = sum(1 for s in active_sc if s.get("opt_type") == "PE")
    # CE short covering = bullish, PE short covering = bearish
    if ce_sc > pe_sc and diff >= 0:
        score += min(10, ce_sc * 3)
    elif pe_sc > ce_sc and diff <= 0:
        score += min(10, pe_sc * 3)
    elif active_sc:  # SC signals contradict overall bias
        score -= 5

    # Active taps boost (small)
    active_taps = [t for t in taps if t.get("is_active_tap") or t.get("is_confirmed")]
    score += min(5, len(active_taps) * 2)

    # Gap events increase directional confidence
    if oi_data.get("gap_down") and diff < 0:
        score += 10
    elif oi_data.get("gap_up") and diff > 0:
        score += 10

    return max(0, min(100, score))


def _compute_overall_bias(oi_data: dict, sc_signals: list) -> str:
    """Determine overall directional bias.
    
    Uses bull/bear scores from OI data (which now include spot direction,
    price+OI matrix, and gap detection) plus short covering signals.
    """
    bull_weight = oi_data.get("bull_score", 0)
    bear_weight = oi_data.get("bear_score", 0)

    # Factor in short covering signals
    for sig in sc_signals:
        if sig.get("is_short_covering"):
            if sig.get("opt_type") == "CE":
                bull_weight += 1  # CE short covering = bullish
            elif sig.get("opt_type") == "PE":
                bear_weight += 1  # PE short covering = bearish

    if bull_weight > bear_weight + 1:
        return "BULLISH"
    elif bear_weight > bull_weight + 1:
        return "BEARISH"
    return "NEUTRAL"


def _find_dominant_strike(oi_data: dict, sc_signals: list) -> str | None:
    """Find the strike with strongest OI activity."""
    active_sc = [s for s in sc_signals if s.get("is_short_covering")]
    if active_sc:
        top = max(active_sc, key=lambda x: x.get("score", 0))
        return top.get("symbol", "")

    # Fall back to max OI strike from snapshots
    for name, snap in oi_data.get("snapshots", {}).items():
        mc = snap.get("max_call_strike", 0)
        mp = snap.get("max_put_strike", 0)
        if mc or mp:
            return f"{name} {mc}CE/{mp}PE"
    return None


def _extract_strike_from_symbol(symbol: str) -> int:
    """Parse strike price from tradingsymbol like BANKNIFTY26MAR61000CE."""
    import re
    m = re.search(r"(\d+)(CE|PE)$", symbol)
    if not m:
        return 0
    # Symbol tail contains expiry digits + strike digits.
    # For index options, strike is the last 5 digits (e.g. 23400, 54300).
    digits = m.group(1)
    strike_digits = digits[-5:] if len(digits) >= 5 else digits
    return int(strike_digits)


def _transform_sc_for_frontend(sc_signals: list) -> list:
    """
    Transform raw short covering data into the shape expected by the
    frontend ShortCoveringSignal interface:
      { tradingsymbol, underlying, strike, opt_type, spot, score,
        oi_drop_pct, price_rise_pct, signal_type, trade_action }
    """
    out = []
    for s in sc_signals:
        if not s.get("is_short_covering"):
            continue

        opt_type = s.get("opt_type", "?")
        underlying = s.get("underlying", "?")
        oi_change = s.get("oi_change_pct", 0)
        price_change = s.get("price_change_pct", 0)

        trade_action = s.get("trade_action", "")
        if not trade_action:
            trade_action = f"BUY_{opt_type}" if opt_type in ("CE", "PE") else ""

        strike = s.get("strike", 0)
        if not strike:
            sym = s.get("tradingsymbol") or s.get("symbol", "")
            strike = _extract_strike_from_symbol(sym)

        signal_time = (
            s.get("signal_time")
            or s.get("time")
            or s.get("timestamp")
            or ""
        )

        out.append({
            "tradingsymbol": s.get("tradingsymbol") or s.get("symbol", ""),
            "underlying": underlying,
            "strike": strike,
            "opt_type": opt_type,
            "spot": s.get("spot", 0),
            "score": s.get("score", 0),
            "oi_drop_pct": abs(oi_change) / 100 if abs(oi_change) > 1 else abs(oi_change),
            "price_rise_pct": price_change / 100 if price_change > 1 else price_change,
            "signal_type": s.get("signal_type", "OI_SHORT_COVERING"),
            "trade_action": trade_action,
            "signal_time": signal_time,
        })
    return out


def _resolve_trade_ledger_file() -> str | None:
    """Return latest trade ledger csv path, e.g. trade_ledger_2026.csv."""
    candidates = sorted(
        Path(WORKSPACE).glob("trade_ledger_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def _get_execution_quality_stats(sc_signals: list | None = None) -> dict:
    """
    Compute execution quality stats for today's index trades and OI-SC trades.
    Returned in snapshot as `execution_quality`.
    """
    import csv

    today = datetime.now().date().isoformat()
    ledger_path = _resolve_trade_ledger_file()
    if not ledger_path:
        return {
            "date": today,
            "total_trades_today": 0,
            "index_trades_today": 0,
            "oi_sc_trades_today": 0,
            "win_rate_today": 0.0,
            "net_r_today": 0.0,
            "avg_r_today": 0.0,
            "oi_sc_mfe_r_avg": 0.0,
            "oi_sc_mae_r_avg": 0.0,
            "top_signal_time": None,
            "top_signal_symbol": None,
            "last_oi_sc_exit_time": None,
            "last_oi_sc_outcome": None,
            "last_oi_sc_symbol": None,
        }

    rows_today = []
    try:
        with open(ledger_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if str(row.get("date", "")).startswith(today):
                    rows_today.append(row)
    except Exception:
        rows_today = []

    index_trades = []
    for r in rows_today:
        sym = str(r.get("symbol", ""))
        if (
            sym.startswith("NSE:NIFTY 50")
            or sym.startswith("NSE:NIFTY BANK")
            or sym.startswith("NFO:NIFTY")
            or sym.startswith("NFO:BANKNIFTY")
        ):
            index_trades.append(r)

    oi_sc_today = [r for r in rows_today if str(r.get("setup", "")) == "OI-SC"]

    def _safe_float(v, d=0.0):
        try:
            return float(v)
        except Exception:
            return d

    net_r = sum(_safe_float(r.get("pnl_r")) for r in rows_today)
    wins = sum(1 for r in rows_today if str(r.get("result", "")).upper() == "WIN")
    win_rate = (wins / len(rows_today) * 100.0) if rows_today else 0.0
    avg_r = (net_r / len(rows_today)) if rows_today else 0.0

    # OI-SC execution quality from trade monitor (MFE/MAE in R units)
    mfe_vals = []
    mae_vals = []
    top_signal_time = None
    top_signal_symbol = None
    last_oi_sc_exit_time = None
    last_oi_sc_outcome = None
    last_oi_sc_symbol = None

    if sc_signals:
        active = [s for s in sc_signals if s.get("is_short_covering")]
        if active:
            top = sorted(active, key=lambda x: x.get("score", 0), reverse=True)[0]
            top_signal_time = (
                top.get("signal_time")
                or top.get("time")
                or top.get("timestamp")
                or None
            )
            top_signal_symbol = top.get("tradingsymbol") or top.get("symbol")

    def _parse_dt(value):
        if not value:
            return None
        sval = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(sval, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(sval)
        except Exception:
            return None

    latest_closed_trade = None
    latest_closed_dt = None
    try:
        oi_sc_closed = _load_json(OI_SC_ACTIVE_TRADES_FILE, [])
        for t in oi_sc_closed:
            if t.get("status") != "CLOSED":
                continue
            if not str(t.get("entry_time", "")).startswith(today):
                continue
            entry = _safe_float(t.get("entry"))
            sl = _safe_float(t.get("sl"))
            peak = _safe_float(t.get("peak_price"), entry)
            trough = _safe_float(t.get("trough_price"), entry)
            direction = str(t.get("direction", "LONG")).upper()
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            if direction == "SHORT":
                mfe_r = (entry - trough) / risk
                mae_r = (entry - peak) / risk
            else:
                mfe_r = (peak - entry) / risk
                mae_r = (trough - entry) / risk
            mfe_vals.append(mfe_r)
            mae_vals.append(mae_r)

        # Latest OI-SC closure info: prefer today's close, else latest historical close.
        todays_closed = [
            t for t in oi_sc_closed
            if t.get("status") == "CLOSED" and str(t.get("exit_time", "")).startswith(today)
        ]
        candidate_pool = todays_closed if todays_closed else [t for t in oi_sc_closed if t.get("status") == "CLOSED"]
        for t in candidate_pool:
            exit_dt = _parse_dt(t.get("exit_time"))
            if exit_dt is None:
                continue
            if latest_closed_dt is None or exit_dt > latest_closed_dt:
                latest_closed_dt = exit_dt
                latest_closed_trade = t

        if latest_closed_trade:
            last_oi_sc_exit_time = latest_closed_trade.get("exit_time")
            last_oi_sc_symbol = latest_closed_trade.get("symbol")
            result = str(latest_closed_trade.get("result", "")).upper()
            last_oi_sc_outcome = "TARGET_HIT" if result == "WIN" else "SL_HIT"
    except Exception:
        pass

    return {
        "date": today,
        "total_trades_today": len(rows_today),
        "index_trades_today": len(index_trades),
        "oi_sc_trades_today": len(oi_sc_today),
        "win_rate_today": round(win_rate, 2),
        "net_r_today": round(net_r, 2),
        "avg_r_today": round(avg_r, 3),
        "oi_sc_mfe_r_avg": round(sum(mfe_vals) / len(mfe_vals), 3) if mfe_vals else 0.0,
        "oi_sc_mae_r_avg": round(sum(mae_vals) / len(mae_vals), 3) if mae_vals else 0.0,
        "top_signal_time": top_signal_time,
        "top_signal_symbol": top_signal_symbol,
        "last_oi_sc_exit_time": last_oi_sc_exit_time,
        "last_oi_sc_outcome": last_oi_sc_outcome,
        "last_oi_sc_symbol": last_oi_sc_symbol,
    }


def generate_snapshot() -> dict:
    """
    Generate the complete OI Intelligence snapshot.
    Called every 60s during market hours by the scheduler,
    and on-demand via API.
    """
    global _cached_snapshot, _last_scan

    now = datetime.now()

    # Gather all data
    oi_data = _get_oi_sentiment_data()
    sc_signals = _get_short_covering_data()
    taps = _get_monthly_low_taps()

    # Fallback: if engine has no OI snapshots, try direct Kite API
    if not oi_data.get("snapshots"):
        kite_data = _get_kite_direct_data()
        if kite_data:
            oi_data = kite_data
            logger.info("Using Kite direct fallback for OI data")

    # Compute derived metrics
    overall_bias = _compute_overall_bias(oi_data, sc_signals)
    confidence = _compute_confidence(oi_data, sc_signals, taps)
    dominant_strike = _find_dominant_strike(oi_data, sc_signals)

    # Get current PCR
    pcr_val = None
    if oi_data.get("pcr_history"):
        pcr_val = oi_data["pcr_history"][-1]["pcr"]

    # Rolling history
    _pcr_history.append({
        "time": now.strftime("%H:%M:%S"),
        "pcr": pcr_val,
        "bias": overall_bias,
        "confidence": confidence,
    })

    # Build per-underlying summaries
    underlying_summaries = {}
    for name in ["NIFTY", "BANKNIFTY"]:
        snap = oi_data.get("snapshots", {}).get(name, {})
        ul_sc = [s for s in sc_signals if s.get("underlying") == name and s.get("is_short_covering")]
        ul_taps = [t for t in taps if t.get("underlying") == name]
        active_taps = [t for t in ul_taps if t.get("is_active_tap") or t.get("is_confirmed")]

        # Compute per-underlying PCR
        ul_call_oi = snap.get("call_oi", 0)
        ul_put_oi = snap.get("put_oi", 0)
        ul_pcr = round(ul_put_oi / ul_call_oi, 3) if ul_call_oi > 0 else 0

        # Per-underlying bull/bear score from OI patterns
        ul_bull = 0
        ul_bear = 0
        # Use global scores scaled by this UL's contribution
        if name == "NIFTY":
            ul_bull = oi_data.get("bull_score", 0)
            ul_bear = oi_data.get("bear_score", 0)
        elif name == "BANKNIFTY":
            ul_bull = oi_data.get("bull_score", 0)
            ul_bear = oi_data.get("bear_score", 0)

        # Per-underlying bias
        if ul_bull > ul_bear + 1:
            ul_bias = "BULLISH"
        elif ul_bear > ul_bull + 1:
            ul_bias = "BEARISH"
        else:
            ul_bias = "NEUTRAL"

        # Per-underlying PCR trend
        ul_pcr_trend = oi_data.get("pcr_trend", "FLAT")

        underlying_summaries[name] = {
            "spot": snap.get("spot", 0),
            "call_oi": ul_call_oi,
            "put_oi": ul_put_oi,
            "max_call_strike": snap.get("max_call_strike", 0),
            "max_put_strike": snap.get("max_put_strike", 0),
            "max_call_oi": snap.get("max_call_oi", 0),
            "max_put_oi": snap.get("max_put_oi", 0),
            # Frontend-expected fields
            "pcr": ul_pcr,
            "pcr_trend": ul_pcr_trend,
            "bull_score": ul_bull,
            "bear_score": ul_bear,
            "bias": ul_bias,
            "sc_active": len(ul_sc) > 0,
            "sc_count": len(ul_sc),
            # Legacy fields
            "short_covering_count": len(ul_sc),
            "short_covering_signals": ul_sc[:5],
            "active_taps": len(active_taps),
            "tap_details": active_taps[:5],
            "call_oi_by_strike": snap.get("call_oi_by_strike", {}),
            "put_oi_by_strike": snap.get("put_oi_by_strike", {}),
        }

    # Strike heatmap data  (enriched with spot distance, PCR, OI change momentum)
    strike_heatmap = []
    global _prev_strike_oi
    for name, snap in oi_data.get("snapshots", {}).items():
        call_strikes = snap.get("call_oi_by_strike", {})
        put_strikes  = snap.get("put_oi_by_strike",  {})
        all_strikes  = sorted(set(list(call_strikes.keys()) + list(put_strikes.keys())), key=lambda x: int(x))
        spot         = snap.get("spot", 0)
        prev_for_ul  = _prev_strike_oi.get(name, {})

        for strike in all_strikes:
            ce_oi = call_strikes.get(strike, 0)
            pe_oi = put_strikes.get(strike, 0)

            # OI change vs previous scan (fraction, e.g. 0.12 = +12%)
            prev_ce = prev_for_ul.get(f"{strike}_CE", ce_oi)
            prev_pe = prev_for_ul.get(f"{strike}_PE", pe_oi)
            ce_change = (ce_oi - prev_ce) / prev_ce if prev_ce > 0 else 0.0
            pe_change = (pe_oi - prev_pe) / prev_pe if prev_pe > 0 else 0.0

            # Per-strike PCR  (put OI / call OI)
            strike_pcr = round(pe_oi / ce_oi, 2) if ce_oi > 0 else 0.0

            # SC signal lookup
            sc_for_strike = [s for s in sc_signals if str(s.get("symbol", "")).find(strike) != -1]
            ce_sc = any(s.get("opt_type") == "CE" and s.get("is_short_covering") for s in sc_for_strike)
            pe_sc = any(s.get("opt_type") == "PE" and s.get("is_short_covering") for s in sc_for_strike)

            # Enhanced status: buildup / unwind / short_cover / normal
            if ce_sc:
                ce_status = "short_cover"
            elif ce_change < -0.05:
                ce_status = "unwind"
            elif ce_oi > 500_000 or (ce_oi > 200_000 and ce_change > 0.03):
                ce_status = "buildup"
            else:
                ce_status = "normal"

            if pe_sc:
                pe_status = "short_cover"
            elif pe_change < -0.05:
                pe_status = "unwind"
            elif pe_oi > 500_000 or (pe_oi > 200_000 and pe_change > 0.03):
                pe_status = "buildup"
            else:
                pe_status = "normal"

            strike_heatmap.append({
                "underlying":  name,
                "strike":      int(strike),
                "ce_oi":       ce_oi,
                "pe_oi":       pe_oi,
                "ce_change":   round(ce_change, 4),
                "pe_change":   round(pe_change, 4),
                "ce_status":   ce_status,
                "pe_status":   pe_status,
                "spot":        spot,
                "strike_pcr":  strike_pcr,
            })

        # Persist for next scan — per-strike baseline
        if name not in _prev_strike_oi:
            _prev_strike_oi[name] = {}
        for strike in all_strikes:
            _prev_strike_oi[name][f"{strike}_CE"] = call_strikes.get(strike, 0)
            _prev_strike_oi[name][f"{strike}_PE"] = put_strikes.get(strike, 0)

    # Check high conviction
    high_conviction = confidence >= 80

    snapshot = {
        # Core metrics
        "overall_bias": overall_bias,
        "confidence": confidence,
        "high_conviction": high_conviction,
        "pcr": pcr_val,
        "pcr_trend": oi_data.get("pcr_trend", "FLAT"),
        "pcr_bias": oi_data.get("pcr_bias", "NEUTRAL"),
        "dominant_strike": dominant_strike,

        # OI sentiment
        "sentiment": oi_data.get("sentiment", "NEUTRAL"),
        "price_oi_pattern": oi_data.get("price_oi_pattern", "NONE"),
        "bull_score": oi_data.get("bull_score", 0),
        "bear_score": oi_data.get("bear_score", 0),

        # Sub-components
        "monthly_taps": taps,
        "short_covering_signals": _transform_sc_for_frontend(sc_signals),
        "strike_heatmap": strike_heatmap,
        "underlying_summaries": underlying_summaries,

        # History (rolling 30 min)
        "pcr_history": list(oi_data.get("pcr_history", [])),
        "bias_history": list(_pcr_history),

        # Meta
        "market_hours": _is_market_hours(),
        "last_update": now.isoformat(),
        "oi_sentiment_update": oi_data.get("last_update"),

        # Market State Engine
        "market_state": _get_market_state_for_dashboard(),
        "execution_quality": _get_execution_quality_stats(sc_signals),
    }

    _cached_snapshot = snapshot
    _last_scan = now

    # Persist to disk so snapshot survives backend restarts
    try:
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(snapshot, f, default=str)
    except Exception as e:
        logger.debug(f"Failed to persist OI snapshot: {e}")

    if high_conviction:
        logger.info(f"🎯 HIGH CONVICTION OI signal: {overall_bias} @ {confidence}%")

    return snapshot


def _get_market_state_for_dashboard() -> dict:
    """Serialize market state engine output for JSON/dashboard consumption."""
    try:
        ms = get_market_state()
        events = []
        for ev in ms.get("events", []):
            events.append({
                "type": ev.get("type", ""),
                "direction": ev.get("direction", ""),
                "weight": ev.get("weight", 0),
                "detail": ev.get("detail", ""),
            })
        return {
            "state": ms.get("state", "RANGE"),
            "prev_state": ms.get("prev_state", "RANGE"),
            "confidence": ms.get("confidence", 0),
            "events": events,
            "bull_score": ms.get("score_breakdown", {}).get("bull_score", 0),
            "bear_score": ms.get("score_breakdown", {}).get("bear_score", 0),
            "net": ms.get("score_breakdown", {}).get("net", 0),
            "last_update": str(ms["last_update"]) if ms.get("last_update") else None,
            "transition_time": str(ms["transition_time"]) if ms.get("transition_time") else None,
        }
    except Exception as e:
        logger.debug(f"Market state retrieval error: {e}")
        return {
            "state": "RANGE", "prev_state": "RANGE", "confidence": 0,
            "events": [], "bull_score": 0, "bear_score": 0, "net": 0,
            "last_update": None, "transition_time": None,
        }


def get_cached_snapshot() -> dict:
    """Return last cached snapshot (for WebSocket broadcast).
    Falls back to disk if in-memory cache is empty (e.g. after restart).
    """
    if _cached_snapshot:
        return _cached_snapshot

    # Try loading last persisted snapshot from disk
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r") as f:
                disk_snap = json.load(f)
            if disk_snap:
                logger.info("Loaded OI snapshot from disk cache")
                return disk_snap
        except Exception:
            pass

    return generate_snapshot()
