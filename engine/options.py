"""
engine/options.py - LiveTickStore + BankNiftySignalEngine.
Extracted from smc_mtf_engine_v4.py (Phase 5).

The largest single class in the codebase (~1100 lines).
"""

import os
import json
import pickle
import logging
import threading
import requests
import time as t
from datetime import datetime, time, timedelta
from collections import deque

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")


def _now_ist():
    return datetime.now(_IST).replace(tzinfo=None)

try:
    from kiteconnect import KiteTicker
except ImportError:
    KiteTicker = None

from config.kite_auth import get_api_key
from engine import config as cfg
from engine.expiry_manager import (
    get_atm_strikes, get_atm, get_target_expiries, get_rollover_state,
)

# Import option constants from config
OPT_UNDERLYINGS = cfg.OPT_UNDERLYINGS
OPT_OI_DELTA_THRESHOLD = cfg.OPT_OI_DELTA_THRESHOLD
OPT_SESSION_LOW_BOUNCE_PCT = cfg.OPT_SESSION_LOW_BOUNCE_PCT
OPT_MOMENTUM_BODY_RATIO = cfg.OPT_MOMENTUM_BODY_RATIO
OPT_ALERT_COOLDOWN_SECS = cfg.OPT_ALERT_COOLDOWN_SECS
OPT_SCAN_INTERVAL = cfg.OPT_SCAN_INTERVAL
OPT_ATM_REFRESH_INTERVAL = cfg.OPT_ATM_REFRESH_INTERVAL
OPT_OI_SNAPSHOT_INTERVAL = cfg.OPT_OI_SNAPSHOT_INTERVAL
OPT_SCORE_SESSION_LOW = cfg.OPT_SCORE_SESSION_LOW
OPT_SCORE_CALL_UNWIND = cfg.OPT_SCORE_CALL_UNWIND
OPT_SCORE_PUT_SURGE = cfg.OPT_SCORE_PUT_SURGE
OPT_SCORE_OI_LARGE_BONUS = cfg.OPT_SCORE_OI_LARGE_BONUS
OPT_OI_LARGE_THRESHOLD = cfg.OPT_OI_LARGE_THRESHOLD
OPT_SCORE_MOMENTUM = cfg.OPT_SCORE_MOMENTUM
OPT_SCORE_EARLY_SESSION = cfg.OPT_SCORE_EARLY_SESSION
OPT_ALERT_THRESHOLD_HIGH = cfg.OPT_ALERT_THRESHOLD_HIGH
OPT_ALERT_THRESHOLD_MED = cfg.OPT_ALERT_THRESHOLD_MED
OPT_SCORE_SESSION_BREAK = cfg.OPT_SCORE_SESSION_BREAK
OPT_SESSION_SWING_MIN_PCT = cfg.OPT_SESSION_SWING_MIN_PCT
OPT_MIN_HISTORY_DAYS = cfg.OPT_MIN_HISTORY_DAYS
OPT_BOUNCE_CONFIRM_PCT = cfg.OPT_BOUNCE_CONFIRM_PCT
OPT_BOUNCE_MAX_WAIT_MIN = cfg.OPT_BOUNCE_MAX_WAIT_MIN
OPT_BREAK_INFO_ALERT = cfg.OPT_BREAK_INFO_ALERT
OPT_OI_HISTORY_SIZE = cfg.OPT_OI_HISTORY_SIZE
OPT_OI_DELTA_WINDOW_SECS = cfg.OPT_OI_DELTA_WINDOW_SECS
OPT_CACHE_PKL = cfg.OPT_CACHE_PKL
OPT_BN_STATE_FILE = cfg.OPT_BN_STATE_FILE
BOT_TOKEN = cfg.BOT_TOKEN
CHAT_ID = cfg.CHAT_ID

class LiveTickStore:
    """Thread-safe storage for real-time tick data from KiteTicker."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ticks = {}
        self._oi_history = {}
        self._connected = False
        self._tick_count = 0

    def update_tick(self, tick):
        token = tick.get("instrument_token")
        if not token:
            return
        with self._lock:
            self._ticks[token] = tick
            self._tick_count += 1
            oi = tick.get("oi", 0)
            if oi > 0:
                if token not in self._oi_history:
                    self._oi_history[token] = deque(maxlen=OPT_OI_HISTORY_SIZE)
                self._oi_history[token].append((_now_ist(), oi))

    def get_tick(self, token):
        with self._lock:
            return self._ticks.get(token)

    def get_all_ticks(self):
        with self._lock:
            return dict(self._ticks)

    def get_oi_history(self, token, window_secs=None):
        window = window_secs or OPT_OI_DELTA_WINDOW_SECS
        with self._lock:
            history = self._oi_history.get(token)
            if not history:
                return []
            cutoff = _now_ist() - timedelta(seconds=window)
            return [(ts, oi) for ts, oi in history if ts >= cutoff]

    @property
    def connected(self):
        return self._connected

    @connected.setter
    def connected(self, val):
        self._connected = val

    @property
    def tick_count(self):
        return self._tick_count

    def has_data(self):
        with self._lock:
            return len(self._ticks) > 0


class BankNiftySignalEngine:
    """Real-time Options Signal Engine (NIFTY + BANK NIFTY). Polled from main loop."""

    def __init__(self, kite_instance, telegram_fn=None):
        self.kite = kite_instance
        self.logger = logging.getLogger("OptSignal")
        self.telegram_fn = telegram_fn

        self.contracts = {}
        self.symbol_to_token = {}
        self.last_atm_refresh = None
        self.last_scan_time = None
        self.spot_prices = {}          # {"BANKNIFTY": 61364, "NIFTY": 25514}

        self.historical_lows = {}
        self.session_highs = {}       # {symbol: highest_ltp_today}
        self.session_lows = {}        # {symbol: lowest_ltp_today}
        self.contract_data_days = {}  # {symbol: num_historical_days}
        self.pending_breaks = {}      # {alert_key: {break_data, timestamp, low_price}}
        self.oi_snapshots = []
        self.alerted = {}
        self.underlying_candles = []

        # Directional Bias
        self.directional_bias = {}
        self.bias_locked = False
        self.morning_signals = []
        self.bias_scan_done = False
        self.current_date = _now_ist().date()

        self.active_expiry_map = {}   # {underlying: set(expiry_dates)}
        self._cached_atm = {}         # {underlying: int} — last computed ATM strike
        self._last_atm_check = None   # datetime — last ATM drift check

        self.tick_store = LiveTickStore()
        self._ws_thread = None
        self._kws = None
        self.initialized = False


    # --- Telegram ---
    def send_alert(self, message, signal_meta=None, signal_id=None):
        # Enforce signal window — no alerts outside 09:00-16:10 IST
        try:
            from smc_mtf_engine_v4 import is_signal_window
            if not is_signal_window():
                self.logger.info("BankNifty signal suppressed (outside signal window)")
                return
        except ImportError:
            pass
        sent = False
        if self.telegram_fn:
            result = self.telegram_fn(message)
            sent = result is not False  # telegram_send returns True on success
        else:
            if not BOT_TOKEN or not CHAT_ID:
                self.logger.error("[Telegram] BOT_TOKEN or CHAT_ID not set — cannot send alert")
                return
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
            for attempt in range(2):
                try:
                    resp = requests.post(url, data=payload, timeout=8)
                    if resp.ok:
                        self.logger.info("[Telegram] Alert sent (attempt %d)", attempt + 1)
                        sent = True
                        break
                    self.logger.warning("[Telegram] API %s: %s (attempt %d)", resp.status_code, resp.text[:200], attempt + 1)
                except requests.exceptions.Timeout:
                    self.logger.warning("[Telegram] Timeout on attempt %d", attempt + 1)
                except Exception as e:
                    self.logger.error("[Telegram] Error on attempt %d: %s", attempt + 1, e)
            if not sent:
                self.logger.error("[Telegram] Failed to send alert after 2 attempts")
        if sent and signal_meta:
            try:
                from utils.telegram_signal_log import persist_telegram_signal
                persist_telegram_signal(message, signal_id, signal_meta)
            except Exception as exc:
                self.logger.warning("[Options] signal_log persist failed: %s", exc)

    # --- Contract Management ---
    def get_weekly_expiry(self, instruments, instr_name):
        today = _now_ist().date()
        opts = [i for i in instruments
                if i["name"] == instr_name
                and i["instrument_type"] in ("CE", "PE")]
        expiries = set()
        for i in opts:
            exp = i["expiry"]
            if isinstance(exp, datetime):
                exp = exp.date()
            if exp >= today:
                expiries.add(exp)
        return min(expiries) if expiries else None

    def get_monthly_expiry(self, instruments, instr_name):
        today = _now_ist().date()
        opts = [i for i in instruments
                if i["name"] == instr_name
                and i["instrument_type"] in ("CE", "PE")]
        
        future_expiries = []
        for i in opts:
            exp = i["expiry"]
            if isinstance(exp, datetime):
                exp = exp.date()
            if exp >= today:
                future_expiries.append(exp)
        
        if not future_expiries:
            return None
            
        future_expiries = sorted(list(set(future_expiries)))
        nearest_exp = future_expiries[0]
        
        # Find the last expiry in the same month as nearest_expr
        month_expiries = [e for e in future_expiries if e.year == nearest_exp.year and e.month == nearest_exp.month]
        return max(month_expiries) if month_expiries else None

    def refresh_contracts(self):
        try:
            # Load instruments cache — invalidate daily to avoid stale strikes
            cache_valid = False
            if os.path.exists(OPT_CACHE_PKL):
                from datetime import date
                cache_mtime = datetime.fromtimestamp(os.path.getmtime(OPT_CACHE_PKL)).date()
                if cache_mtime == date.today():
                    try:
                        with open(OPT_CACHE_PKL, "rb") as f:
                            instruments = pickle.load(f)
                        if len(instruments) > 100:
                            cache_valid = True
                    except Exception:
                        pass
                if not cache_valid:
                    self.logger.info(f"Instruments cache stale (date={cache_mtime}), re-downloading...")

            if not cache_valid:
                instruments = self.kite.instruments(exchange="NFO")
                with open(OPT_CACHE_PKL, "wb") as f:
                    pickle.dump(instruments, f)
                self.logger.info(f"Downloaded {len(instruments)} NFO instruments")

            # Save old contract info for smart rollover (before clearing)
            old_contracts = dict(self.contracts)

            self.contracts.clear()
            self.symbol_to_token.clear()
            for ul in OPT_UNDERLYINGS:
                sym, name, step, rng = ul["symbol"], ul["name"], ul["step"], ul["range"]
                spot = self.kite.ltp(sym)
                if not spot or sym not in spot:
                    self.logger.error(f"Could not fetch {name} LTP")
                    continue
                ltp = spot[sym]["last_price"]
                self.spot_prices[name] = ltp

                # ATM±1 strike selection (replaces single-ATM logic)
                ce_strikes, pe_strikes = get_atm_strikes(ltp, step)
                ce_strike_set = set(ce_strikes)
                pe_strike_set = set(pe_strikes)
                atm = get_atm(ltp, step)
                self._cached_atm[name] = atm

                self.logger.info(
                    f"{name} LTP: {ltp:.0f} | ATM: {atm} | "
                    f"CE strikes: {ce_strikes} | PE strikes: {pe_strikes}"
                )

                # Smart expiry selection with preload
                target_expiry_info = get_target_expiries(instruments, name)
                if not target_expiry_info:
                    self.logger.error(f"No {name} expiries found")
                    continue

                target_expiry_dates = [e["expiry"] for e in target_expiry_info]
                for ei in target_expiry_info:
                    label = f"{'[PRELOAD] ' if ei['preload'] else ''}{ei['type']}"
                    self.logger.info(f"  {name} expiry: {ei['expiry']} ({label})")

                for instr in instruments:
                    if instr["name"] != name:
                        continue
                    if instr["instrument_type"] not in ("CE", "PE"):
                        continue

                    exp = instr["expiry"]
                    if isinstance(exp, datetime):
                        exp = exp.date()

                    if exp not in target_expiry_dates:
                        continue

                    opt_type = instr["instrument_type"]
                    strike = instr["strike"]

                    # ATM±1 filter: CE uses ce_strike_set, PE uses pe_strike_set
                    if opt_type == "CE" and strike not in ce_strike_set:
                        continue
                    if opt_type == "PE" and strike not in pe_strike_set:
                        continue

                    self.contracts[instr["instrument_token"]] = {
                        "symbol": instr["tradingsymbol"],
                        "strike": strike,
                        "type": opt_type,
                        "expiry": str(instr["expiry"]),
                        "expiry_date": exp,
                        "underlying": name
                    }

            # --- Smart Expiry Rollover (per-contract, not global wipe) ---
            old_expiries_by_ul = {}
            for info in old_contracts.values():
                ul = info["underlying"]
                if ul not in old_expiries_by_ul:
                    old_expiries_by_ul[ul] = set()
                old_expiries_by_ul[ul].add(info["expiry_date"])

            new_expiries_by_ul = {}
            for info in self.contracts.values():
                ul = info["underlying"]
                if ul not in new_expiries_by_ul:
                    new_expiries_by_ul[ul] = set()
                new_expiries_by_ul[ul].add(info["expiry_date"])

            all_uls = set(list(old_expiries_by_ul.keys()) + list(new_expiries_by_ul.keys()))
            for ul in all_uls:
                old_exps = old_expiries_by_ul.get(ul, set())
                new_exps = new_expiries_by_ul.get(ul, set())

                dropped_exps = old_exps - new_exps
                added_exps = new_exps - old_exps

                if dropped_exps:
                    self.logger.info(f"{ul}: expiry(s) dropped: {sorted(dropped_exps)}")
                    # Clean only state for symbols belonging to dropped expiries
                    for token, info in old_contracts.items():
                        if info["underlying"] == ul and info["expiry_date"] in dropped_exps:
                            sym = info["symbol"]
                            self.historical_lows.pop(sym, None)
                            self.session_highs.pop(sym, None)
                            self.session_lows.pop(sym, None)
                            self.contract_data_days.pop(sym, None)

                if added_exps:
                    self.logger.info(f"{ul}: new expiry(s) added: {sorted(added_exps)}")

                # Update rollover state tracker
                rollover = get_rollover_state()
                rollover.update(ul, new_exps)

                # Update legacy active_expiry_map
                self.active_expiry_map[ul] = new_exps

            self.last_atm_refresh = _now_ist()
            
            # Build reverse lookup map for O(1) performance
            for tkn, info in self.contracts.items():
                self.symbol_to_token[info["symbol"]] = tkn
                
            self.logger.info(f"Loaded {len(self.contracts)} total contracts")
            return len(self.contracts) > 0
        except Exception as e:
            self.logger.error(f"Refresh Contracts Error: {e}")
            return False

    # --- WebSocket ---
    def start_websocket(self):
        if self._ws_thread and self._ws_thread.is_alive():
            return
        if not KiteTicker:
            self.logger.warning("KiteTicker not available")
            return
        try:
            access_token = self.kite.access_token
            self._kws = KiteTicker(get_api_key(), access_token)
            tick_store = self.tick_store
            logger = self.logger
            contracts = self.contracts

            def on_ticks(ws, ticks):
                for tick in ticks:
                    tick_store.update_tick(tick)

            def on_connect(ws, response):
                tokens = list(contracts.keys())
                if tokens:
                    ws.subscribe(tokens)
                    ws.set_mode(ws.MODE_FULL, tokens)
                tick_store.connected = True

            def on_close(ws, code, reason):
                tick_store.connected = False

            def on_error(ws, code, reason):
                logger.error(f"WS Error: {code} - {reason}")

            def on_reconnect(ws, attempts):
                logger.info(f"WS Reconnecting... attempt {attempts}")

            self._kws.on_ticks = on_ticks
            self._kws.on_connect = on_connect
            self._kws.on_close = on_close
            self._kws.on_error = on_error
            self._kws.on_reconnect = on_reconnect

            self._ws_thread = threading.Thread(
                target=self._kws.connect,
                kwargs={"threaded": True},
                daemon=True
            )
            self._ws_thread.start()
        except Exception as e:
            self.logger.error(f"WebSocket Start Error: {e}")

    def resubscribe_tokens(self):
        if not self._kws or not self.tick_store.connected:
            return
        try:
            tokens = list(self.contracts.keys())
            if tokens:
                self._kws.subscribe(tokens)
                self._kws.set_mode(self._kws.MODE_FULL, tokens)
        except Exception as e:
            self.logger.error(f"WS Resubscribe Error: {e}")

    # --- Data Fetching ---
    def fetch_chain_snapshot(self):
        if not self.contracts:
            return {}

        # Priority 1: WebSocket
        if self.tick_store.has_data():
            snapshot = {}
            all_ticks = self.tick_store.get_all_ticks()
            for token, info in self.contracts.items():
                tick = all_ticks.get(token)
                if not tick:
                    continue
                snapshot[info["symbol"]] = {
                    "ltp": tick.get("last_price", 0),
                    "oi": tick.get("oi", 0),
                    "volume": tick.get("volume", 0),
                    "strike": info["strike"],
                    "type": info["type"],
                    "change": tick.get("change", 0),
                    "underlying": info.get("underlying", "")
                }
            if snapshot:
                return snapshot

        # Priority 2: API fallback
        try:
            symbols = []
            token_to_info = {}
            for token, info in self.contracts.items():
                sym = f"NFO:{info['symbol']}"
                symbols.append(sym)
                token_to_info[sym] = info

            quotes = self.kite.quote(symbols)
            snapshot = {}
            for sym, data in quotes.items():
                info = token_to_info.get(sym)
                if not info:
                    continue
                snapshot[info["symbol"]] = {
                    "ltp": data.get("last_price", 0),
                    "oi": data.get("oi", 0),
                    "volume": data.get("volume", 0),
                    "strike": info["strike"],
                    "type": info["type"],
                    "change": data.get("net_change", 0),
                    "underlying": info.get("underlying", "")
                }
            return snapshot
        except Exception as e:
            self.logger.error(f"Chain Snapshot Error: {e}")
            return {}

    def fetch_underlying_candles(self):
        candles = {}
        for ul in OPT_UNDERLYINGS:
            sym, name = ul["symbol"], ul["name"]
            try:
                quote = self.kite.quote(sym)
                if not quote or sym not in quote:
                    continue
                token = quote[sym].get("instrument_token")
                if not token:
                    continue
                data = self.kite.historical_data(
                    token,
                    _now_ist() - timedelta(hours=2),
                    _now_ist(),
                    "5minute"
                )
                if data:
                    candles[name] = data[-1]
            except Exception as e:
                self.logger.error(f"Underlying Candle Error {name}: {e}")
        return candles

    def _fetch_monthly_low(self, token, expiry_date):
        """Fetch historical low for the given option token for its expiry month.
        Returns (low, num_days) tuple. num_days = number of unique historical days available.
        Returns (None, 0) if no historical data exists.
        """
        try:
            to_date = _now_ist()
            
            if isinstance(expiry_date, str):
                try:
                    expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
                except Exception:
                    pass
            
            if isinstance(expiry_date, datetime):
                expiry_date = expiry_date.date()
                
            first_day = expiry_date.replace(day=1)
            
            if first_day > to_date.date():
                from_date = to_date.date()
            else:
                from_date = first_day

            data = self.kite.historical_data(
                token,
                from_date,
                to_date,
                "5minute"
            )

            if not data:
                return None, 0

            # Exclude today for a strict historical structure baseline
            historical_data = [candle for candle in data if candle['date'].date() < to_date.date()]
            if historical_data:
                unique_days = len(set(c['date'].date() for c in historical_data))
                low = min(candle["low"] for candle in historical_data)
                return low, unique_days
                
            return None, 0

        except Exception as e:
            self.logger.error(f"Monthly low fetch error for token {token}: {e}")
            return None, 0

    # --- Signal 1: Monthly Low Break Detection ---
    def detect_monthly_low(self, snapshot):
        tapped = []
        for symbol, data in snapshot.items():
            ltp = data["ltp"]
            if ltp <= 0:
                continue

            # Always track session highs/lows for session break detection
            if symbol not in self.session_lows or ltp < self.session_lows[symbol]:
                self.session_lows[symbol] = ltp
            if symbol not in self.session_highs or ltp > self.session_highs[symbol]:
                self.session_highs[symbol] = ltp

            token = self.symbol_to_token.get(symbol)
            contract_info = self.contracts.get(token) if token else None

            if symbol not in self.historical_lows:
                if token and contract_info:
                    exp_date = contract_info.get("expiry_date")
                    hist_low, num_days = self._fetch_monthly_low(token, exp_date)
                    self.contract_data_days[symbol] = num_days
                    if hist_low is not None and num_days >= OPT_MIN_HISTORY_DAYS:
                        self.historical_lows[symbol] = hist_low
                    else:
                        # Not enough history — skip monthly low detection
                        # Session break logic will handle early-cycle signals
                        self.logger.info(
                            f"[EARLY CYCLE] {symbol}: {num_days} day(s) of history — "
                            f"using session break mode"
                        )
                        continue
                else:
                    continue
                continue
                
            stored_monthly_low = self.historical_lows[symbol]
            
            if ltp < stored_monthly_low:
                # Immediate structural break!
                tapped.append({
                    "symbol": symbol,
                    "previous_low": stored_monthly_low, 
                    "new_low": ltp,
                    "strike": data["strike"],
                    "type": data["type"],
                    "expiry_date": contract_info.get("expiry_date") if contract_info else "",
                    "signal_type": "MONTHLY_LOW"
                })
                # Update stored low
                self.historical_lows[symbol] = ltp
                
        return tapped

    # --- Signal 1B: Session Low Break (Early Cycle) ---
    def detect_session_low_break(self, snapshot):
        """Detect intraday session low breaks for early-cycle contracts.
        
        Fires when:
        1. Contract has insufficient monthly history (< OPT_MIN_HISTORY_DAYS)
        2. Premium has dropped >= OPT_SESSION_SWING_MIN_PCT from session high
        3. Current LTP is at or near session low (making new lows)
        
        This provides actionable signals from Day 1 of new contracts.
        """
        tapped = []
        for symbol, data in snapshot.items():
            ltp = data["ltp"]
            if ltp <= 0:
                continue

            # Only use session breaks for early-cycle contracts
            # (monthly low logic handles symbols with sufficient history)
            if symbol in self.historical_lows:
                continue

            session_high = self.session_highs.get(symbol)
            session_low = self.session_lows.get(symbol)

            if not session_high or not session_low or session_high <= 0:
                continue

            # Need minimum swing from session high to current price
            swing_pct = (session_high - ltp) / session_high
            if swing_pct < OPT_SESSION_SWING_MIN_PCT:
                continue

            # LTP must be at or near session low (within 0.5%)
            if session_low > 0 and ltp > session_low * 1.005:
                continue

            token = self.symbol_to_token.get(symbol)
            contract_info = self.contracts.get(token) if token else None
            num_days = self.contract_data_days.get(symbol, 0)

            tapped.append({
                "symbol": symbol,
                "previous_low": session_high,  # Reference: session high
                "new_low": ltp,
                "strike": data["strike"],
                "type": data["type"],
                "expiry_date": contract_info.get("expiry_date") if contract_info else "",
                "signal_type": "SESSION_LOW",
                "swing_pct": round(swing_pct * 100, 1),
                "data_days": num_days
            })

            self.logger.info(
                f"[SESSION BREAK] {symbol}: dropped {swing_pct*100:.1f}% from "
                f"session high {session_high:.2f} → {ltp:.2f} "
                f"(contract age: {num_days} day(s))"
            )

            # Update session low so we don't re-fire on same level
            self.session_lows[symbol] = ltp

        return tapped

    # --- Signal 2: OI Delta ---
    def detect_oi_delta(self, snapshot):
        signals = {
            "call_unwind": [], "put_surge": [],
            "call_build": [], "put_unwind": []
        }
        use_ws = self.tick_store.has_data()

        for symbol, data in snapshot.items():
            cur_oi = data["oi"]
            if cur_oi <= 0:
                continue
            prev_oi = 0

            if use_ws:
                token = None
                for t_key, info in self.contracts.items():
                    if info["symbol"] == symbol:
                        token = t_key
                        break
                if token:
                    history = self.tick_store.get_oi_history(token, OPT_OI_DELTA_WINDOW_SECS)
                    if history:
                        prev_oi = history[0][1]
            else:
                if self.oi_snapshots:
                    prev_data = self.oi_snapshots[0]["data"].get(symbol)
                    if prev_data:
                        prev_oi = prev_data.get("oi", 0)

            if prev_oi <= 0:
                continue

            oi_change_pct = (cur_oi - prev_oi) / prev_oi
            if abs(oi_change_pct) < OPT_OI_DELTA_THRESHOLD:
                continue

            entry_data = {
                "symbol": symbol, "strike": data["strike"],
                "type": data["type"], "prev_oi": prev_oi,
                "cur_oi": cur_oi, "change_pct": oi_change_pct * 100
            }

            if data["type"] == "CE":
                if oi_change_pct < -OPT_OI_DELTA_THRESHOLD:
                    signals["call_unwind"].append(entry_data)
                elif oi_change_pct > OPT_OI_DELTA_THRESHOLD:
                    signals["call_build"].append(entry_data)
            elif data["type"] == "PE":
                if oi_change_pct > OPT_OI_DELTA_THRESHOLD:
                    signals["put_surge"].append(entry_data)
                elif oi_change_pct < -OPT_OI_DELTA_THRESHOLD:
                    signals["put_unwind"].append(entry_data)

        return signals

    # --- Signal 3: Momentum ---
    def detect_momentum(self):
        candles = self.fetch_underlying_candles()
        result = {}
        for name, candle in candles.items():
            o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
            full_range = h - l
            if full_range <= 0:
                result[name] = None
                continue
            body = abs(c - o)
            body_ratio = body / full_range
            if body_ratio >= OPT_MOMENTUM_BODY_RATIO:
                result[name] = "BULLISH" if c > o else "BEARISH"
            else:
                result[name] = None
        return result

    # --- Signal Reasoning ---
    def save_state(self):
        try:
            state = {
                "historical_lows": self.historical_lows,
                "alerted": {k: v.isoformat() for k, v in self.alerted.items()},
                "last_update": _now_ist().isoformat()
            }
            with open(OPT_BN_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self.logger.error(f"Save State Error: {e}")

    def reset_session(self):
        self.historical_lows.clear()
        self.session_highs.clear()
        self.session_lows.clear()
        self.contract_data_days.clear()
        self.pending_breaks.clear()
        self.oi_snapshots.clear()
        self.alerted.clear()
        self.directional_bias.clear()
        self.bias_locked = False
        self.morning_signals.clear()
        self.bias_scan_done = False
        self.logger.info("BN Signal: Session data reset")

    # --- Directional Bias ---
    def compute_directional_bias(self):
        ul_scores = {}
        for sig in self.morning_signals:
            ul = sig.get("underlying", "")
            if not ul:
                continue
            if ul not in ul_scores:
                ul_scores[ul] = 0
            score_val = sig.get("score", 0)
            opt_type = sig.get("type", "")
            oi_dir = sig.get("oi_direction", "")
            if opt_type == "PE":
                ul_scores[ul] -= score_val * 2 if oi_dir == "put_surge" else score_val
            elif opt_type == "CE":
                ul_scores[ul] += score_val * 2 if oi_dir == "call_unwind" else score_val

        for ul, sc in ul_scores.items():
            if sc > 0:
                self.directional_bias[ul] = "BULLISH"
            elif sc < 0:
                self.directional_bias[ul] = "BEARISH"
            else:
                self.directional_bias[ul] = "NEUTRAL"
        self.logger.info(f"OI Bias computed: {self.directional_bias}")

    def run_bias_scan(self):
        if self.bias_scan_done:
            return
        if not self.initialized:
            self.initialize()
            if not self.initialized:
                return
        self.refresh_contracts()
        self.resubscribe_tokens()
        if not self.contracts:
            return
        snapshot = self.fetch_chain_snapshot()
        if not snapshot:
            return
        self._collect_morning_signals(snapshot)
        self.compute_directional_bias()
        self.bias_scan_done = True
        self.logger.info(f"Morning bias scan: {len(self.morning_signals)} signals, bias={self.directional_bias}")

    def _collect_morning_signals(self, snapshot):
        low_taps = self.detect_session_low(snapshot)
        oi_signals = self.detect_oi_delta(snapshot)
        momentum_map = self.detect_momentum()

        for tap in low_taps:
            symbol = tap["symbol"]
            score = OPT_SCORE_SESSION_LOW
            oi_dir = ""
            for cu in oi_signals["call_unwind"]:
                if cu["strike"] == tap["strike"]:
                    score += OPT_SCORE_CALL_UNWIND
                    oi_dir = "call_unwind"
            for ps in oi_signals["put_surge"]:
                if ps["strike"] == tap["strike"]:
                    score += OPT_SCORE_PUT_SURGE
                    oi_dir = "put_surge"

            ul_name = snapshot[symbol].get("underlying", "")
            momentum = momentum_map.get(ul_name)
            if momentum and tap["type"] == "CE" and momentum == "BULLISH":
                score += OPT_SCORE_MOMENTUM
            elif momentum and tap["type"] == "PE" and momentum == "BEARISH":
                score += OPT_SCORE_MOMENTUM

            self.morning_signals.append({
                "symbol": symbol, "underlying": ul_name,
                "strike": tap["strike"], "type": tap["type"],
                "score": score, "oi_direction": oi_dir,
                "session_low": tap["session_low"],
                "bounce_price": tap["bounce_price"],
                "bounce_pct": tap["bounce_pct"],
                "momentum": momentum,
                "ltp": snapshot[symbol]["ltp"]
            })

    def lock_bias(self):
        if self.bias_locked:
            return
        if self.contracts:
            snapshot = self.fetch_chain_snapshot()
            if snapshot:
                self._collect_morning_signals(snapshot)
                self.compute_directional_bias()
        self.bias_locked = True

        if self.directional_bias:
            lines = ["<b>OI DIRECTIONAL BIAS (Locked for Today)</b>\n"]
            for ul, bias in self.directional_bias.items():
                icon = "+" if bias == "BULLISH" else "-" if bias == "BEARISH" else "~"
                lines.append(f"  [{icon}] {ul}: <b>{bias}</b>")
            lines.append(f"\nSignals collected: {len(self.morning_signals)}")
            lines.append(f"\n<i>SMC trades will be filtered to match this bias.</i>")
            self.send_alert("\n".join(lines))
        self.logger.info(f"Bias LOCKED: {self.directional_bias}")

    # --- Init & Poll ---
    def initialize(self):
        try:
            success = self.refresh_contracts()
            if success:
                self.initialized = True
                self.logger.info(f"Options Signal Engine: {len(self.contracts)} contracts")
                self.start_websocket()
            else:
                self.logger.error("Options Signal Engine: Init failed")
        except Exception as e:
            self.logger.error(f"Signal Engine Init Error: {e}")

    # ---------------------------------------------------------------
    # EXIT TRACKING — register entry, monitor SL/Target each poll
    # ---------------------------------------------------------------

    def poll(self):
        now = _now_ist()
        
        # Date rollover check
        if hasattr(self, 'current_date') and self.current_date != now.date():
            self.reset_session()
            self.current_date = now.date()
        elif not hasattr(self, 'current_date'):
            self.current_date = now.date()

        if not (time(9, 15) <= now.time() <= time(15, 15)):
            return
        if not self.initialized:
            self.initialize()
            if not self.initialized:
                return
        if self.last_scan_time:
            elapsed = (now - self.last_scan_time).total_seconds()
            if elapsed < OPT_SCAN_INTERVAL:
                return
        self.last_scan_time = now

        # --- ATM drift detection (check every EXPIRY_ATM_DRIFT_CHECK_SECS) ---
        atm_drifted = False
        drift_check_secs = getattr(cfg, 'EXPIRY_ATM_DRIFT_CHECK_SECS', 120)
        if self._last_atm_check is None or (now - self._last_atm_check).total_seconds() > drift_check_secs:
            self._last_atm_check = now
            for ul in OPT_UNDERLYINGS:
                name, sym, step = ul["name"], ul["symbol"], ul["step"]
                try:
                    q = self.kite.ltp([sym])
                    if q and sym in q:
                        new_spot = q[sym]["last_price"]
                        self.spot_prices[name] = new_spot
                        new_atm = get_atm(new_spot, step)
                        old_atm = self._cached_atm.get(name)
                        if old_atm is not None and new_atm != old_atm:
                            self.logger.info(
                                f"⚡ {name} ATM drift: {old_atm} → {new_atm} "
                                f"(spot {new_spot:.0f})"
                            )
                            atm_drifted = True
                except Exception:
                    pass

        if atm_drifted or not self.last_atm_refresh or (now - self.last_atm_refresh).total_seconds() > OPT_ATM_REFRESH_INTERVAL:
            self.refresh_contracts()
            self.resubscribe_tokens()
        if not self.contracts:
            return

        snapshot = self.fetch_chain_snapshot()
        if not snapshot:
            return

        if not self.tick_store.has_data():
            self.oi_snapshots.append({
                "timestamp": now,
                "data": {sym: {"oi": d["oi"], "ltp": d["ltp"]} for sym, d in snapshot.items()}
            })
            cutoff = now - timedelta(seconds=OPT_OI_SNAPSHOT_INTERVAL)
            self.oi_snapshots = [s for s in self.oi_snapshots if s["timestamp"] >= cutoff]


        ce_count = sum(1 for d in snapshot.values() if d["type"] == "CE")
        pe_count = sum(1 for d in snapshot.values() if d["type"] == "PE")
        src = "WS" if self.tick_store.has_data() else "API"
        ticks = self.tick_store.tick_count
        print(f"[{now.strftime('%H:%M:%S')}] Opt Scan [{src}]: {ce_count} CE + {pe_count} PE | Ticks: {ticks}")

        if now.minute % 5 == 0:
            self.save_state()


import pickle
