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
                self._oi_history[token].append((datetime.now(), oi))

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
            cutoff = datetime.now() - timedelta(seconds=window)
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
        self.current_date = datetime.now().date()

        self.active_expiry_map = {}   # {underlying: set(expiry_dates)}
        self._cached_atm = {}         # {underlying: int} — last computed ATM strike
        self._last_atm_check = None   # datetime — last ATM drift check

        self.tick_store = LiveTickStore()
        self._ws_thread = None
        self._kws = None
        self.initialized = False

        # Exit tracking — {signal_id: {symbol, entry_price, sl_price, target_price, ...}}
        self.active_option_trades = {}
        self._load_active_trades()

    # --- Telegram ---
    def send_alert(self, message):
        if self.telegram_fn:
            self.telegram_fn(message)
            return
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
                    return
                self.logger.warning("[Telegram] API %s: %s (attempt %d)", resp.status_code, resp.text[:200], attempt + 1)
            except requests.exceptions.Timeout:
                self.logger.warning("[Telegram] Timeout on attempt %d", attempt + 1)
            except Exception as e:
                self.logger.error("[Telegram] Error on attempt %d: %s", attempt + 1, e)
        self.logger.error("[Telegram] Failed to send alert after 2 attempts")

    # --- Contract Management ---
    def get_weekly_expiry(self, instruments, instr_name):
        today = datetime.now().date()
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
        today = datetime.now().date()
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

            self.last_atm_refresh = datetime.now()
            
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
                    datetime.now() - timedelta(hours=2),
                    datetime.now(),
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
            to_date = datetime.now()
            
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
    def _build_signal_reasoning(self, tap, oi_detail, momentum, score, bullish_oi):
        """
        Build institutional-grade reasoning for the signal.
        Returns (signal_component, what_it_means, trade_implication, action)
        """
        opt_type = tap["type"]
        bounce_pct = tap["bounce_pct"]

        # Determine signal component
        components = []
        if opt_type == "PE" and "PE OI:" in oi_detail:
            components.append("PE monthly low break + OI surge")
        elif opt_type == "CE" and "CE OI:" in oi_detail:
            components.append("CE monthly low break + Call unwind")
        elif opt_type == "CE" and momentum == "BULLISH":
            components.append("CE monthly low break + BULLISH momentum")
        elif opt_type == "PE" and momentum == "BEARISH":
            components.append("PE monthly low break + BEARISH momentum")
        else:
            components.append(f"{opt_type} monthly low break")

        if oi_detail and "PE OI:" in oi_detail:
            # Extract OI change percentage
            try:
                oi_pct = oi_detail.split("+")[1].split("%")[0].strip() if "+" in oi_detail else ""
            except:
                oi_pct = ""
            if oi_pct:
                components.append(f"PE OI +{oi_pct}%")
        elif oi_detail and "CE OI:" in oi_detail:
            try:
                oi_pct = oi_detail.split(":")[1].split("%")[0].strip()
            except:
                oi_pct = ""
            if oi_pct:
                components.append(f"CE OI {oi_pct}%")

        # What it means (institutional reasoning)
        # PE low break + OI surge = BEARISH (premium dropping while OI builds = institutions writing puts at lower levels)
        # CE low break + OI unwind = BEARISH (call premium dropping + institutions exiting bullish bets)
        # Momentum CONFLICTING with OI = reduce conviction, add warning
        momentum_conflict = (opt_type == "PE" and momentum == "BULLISH") or \
                            (opt_type == "CE" and momentum == "BULLISH" and "CE OI:" in oi_detail)

        if opt_type == "PE" and momentum == "BULLISH":
            # PE premium dying + underlying going UP = strong bullish confirmation
            meaning = "PUT premium at monthly low while underlying momentum is BULLISH. Puts becoming worthless as market rallies — strong bullish signal."
            if "PE OI:" in oi_detail:
                meaning += " PE OI surge shows fresh put writing (sellers confident market won't drop further)."
            implication = "BUY the underlying OR Buy CALLs — bullish reversal/continuation"
            bias = "BULLISH"
        elif opt_type == "PE" and "PE OI:" in oi_detail:
            meaning = "PE premium breaking lows while OI surges = institutions adding fresh PUT positions. BEARISH conviction from smart money."
            implication = "Sell/Short the underlying OR Buy PUTs"
            bias = "BEARISH"
        elif opt_type == "CE" and "CE OI:" in oi_detail:
            meaning = "CALL OI unwinding at this strike. Institutions exiting bullish bets = weakening upside. Smart money turning cautious."
            if momentum == "BULLISH":
                meaning += " \u26a0\ufe0f CAUTION: Underlying momentum is BULLISH — OI conflict. Wait for momentum to align."
            implication = "Caution on LONGS. Consider closing CE positions."
            bias = "BEARISH"
        elif opt_type == "CE" and momentum == "BULLISH":
            meaning = "CALL option at monthly low premium but underlying momentum BULLISH. Strike may be deep ITM or far OTM — monitor carefully."
            implication = "Underlying bias BULLISH despite call premium weakness."
            bias = "BULLISH"
        elif opt_type == "CE" and momentum == "BEARISH":
            meaning = "CALL option broke structural monthly low with BEARISH momentum confirmation. Weakness accelerating."
            implication = "Sell/Short the underlying OR Buy PUTs"
            bias = "BEARISH"
        elif opt_type == "PE" and momentum == "BEARISH":
            meaning = "PUT activity with bearish momentum confirmation. Sellers are dominating and institutions are hedging."
            implication = "Sell/Short the underlying OR Buy PUTs"
            bias = "BEARISH"
        elif opt_type == "CE":
            meaning = "CALL option broke structural monthly low. Early sign of weakness."
            implication = "Monitor momentum closely. Downside acceleration possible."
            bias = "BEARISH"
        elif opt_type == "PE":
            meaning = "PUT option at monthly low = underlying showing strength (puts losing value). Early bullish sign."
            implication = "Monitor for bullish confirmation. Consider CALL positions on momentum alignment."
            bias = "BULLISH"
        else:
            meaning = "Standard low break setup"
            implication = "Monitor for confirmation"
            bias = "NEUTRAL"

        # Score-based conviction
        if score >= 8:
            conviction = "HIGHEST - All 3 factors (Monthly Low + OI + Momentum) align."
        elif score >= 6:
            conviction = "HIGH - Multiple factors confirm break."
        elif score >= 4:
            conviction = "MEDIUM - Confirming factors mixed. Careful risk management."
        else:
            conviction = "LOW - Single factor only. Monitor, don't trade."

        # Trade action
        if bias == "BEARISH":
            action = f"BUY {tap['strike']} PE (Bearish Play)"
        elif bias == "BULLISH":
            action = f"BUY {tap['strike']} CE (Bullish Play)"
        else:
            action = "Monitor for direction confirmation"

        return {
            "components": " + ".join(components),
            "meaning": meaning,
            "implication": implication,
            "conviction": conviction,
            "bias": bias,
            "action": action
        }

    # --- Composite Signal Evaluation ---
    def evaluate_signals(self, snapshot):
        now = datetime.now()
        low_taps = self.detect_monthly_low(snapshot)
        session_taps = self.detect_session_low_break(snapshot)
        all_taps = low_taps + session_taps
        oi_signals = self.detect_oi_delta(snapshot)
        momentum_map = self.detect_momentum()

        # -----------------------------------------------------------
        # PHASE 1: Register new breaks as PENDING (info alert only)
        # -----------------------------------------------------------
        grouped_taps = {}
        for tap in all_taps:
            symbol = tap["symbol"]
            ul_name = snapshot[symbol].get("underlying", "")
            opt_type = tap["type"]
            strike = tap["strike"]
            st_key = f"{ul_name}_{strike}_{opt_type}"
            if st_key not in grouped_taps:
                grouped_taps[st_key] = []
            grouped_taps[st_key].append(tap)

        for st_key, taps in grouped_taps.items():
            primary_tap = taps[0]
            symbol = primary_tap["symbol"]
            ul_name = snapshot[symbol].get("underlying", "")

            alert_key = f"{primary_tap['strike']}_{primary_tap['type']}_{now.strftime('%Y-%m-%d')}"
            if alert_key in self.alerted:
                continue

            # If already pending, update the low if it went lower
            if alert_key in self.pending_breaks:
                existing = self.pending_breaks[alert_key]
                if primary_tap["new_low"] < existing["low_price"]:
                    existing["low_price"] = primary_tap["new_low"]
                    existing["taps"] = taps
                    self.logger.info(
                        f"[BREAK UPDATE] {st_key}: new low {primary_tap['new_low']:.2f} "
                        f"(waiting for bounce)"
                    )
                continue

            # Score the break for filtering
            is_session_break = primary_tap.get("signal_type") == "SESSION_LOW"
            base_score = OPT_SCORE_SESSION_BREAK if is_session_break else OPT_SCORE_SESSION_LOW
            score = base_score

            for cu in oi_signals["call_unwind"]:
                if cu["strike"] == primary_tap["strike"]:
                    score += OPT_SCORE_CALL_UNWIND
                    if abs(cu["change_pct"]) >= OPT_OI_LARGE_THRESHOLD * 100:
                        score += OPT_SCORE_OI_LARGE_BONUS

            for ps in oi_signals["put_surge"]:
                if ps["strike"] == primary_tap["strike"]:
                    score += OPT_SCORE_PUT_SURGE
                    if abs(ps["change_pct"]) >= OPT_OI_LARGE_THRESHOLD * 100:
                        score += OPT_SCORE_OI_LARGE_BONUS

            momentum = momentum_map.get(ul_name)
            if momentum:
                if primary_tap["type"] == "CE" and momentum == "BEARISH":
                    score += OPT_SCORE_MOMENTUM
                elif primary_tap["type"] == "PE" and momentum == "BEARISH":
                    score += OPT_SCORE_MOMENTUM
                elif (primary_tap["type"] == "PE" and momentum == "BULLISH") or \
                     (primary_tap["type"] == "CE" and momentum == "BULLISH"):
                    score -= 1

            if time(9, 15) <= now.time() <= time(9, 35):
                score += OPT_SCORE_EARLY_SESSION

            if score < OPT_ALERT_THRESHOLD_MED:
                self.logger.info(
                    f"[BREAK FILTERED] {st_key}: score {score} < {OPT_ALERT_THRESHOLD_MED} "
                    f"(session_break={is_session_break})"
                )
                continue  # Not even worth tracking

            # Register as pending break — wait for bounce confirmation
            self.pending_breaks[alert_key] = {
                "symbol": symbol,
                "underlying": ul_name,
                "strike": primary_tap["strike"],
                "opt_type": primary_tap["type"],
                "taps": taps,
                "low_price": primary_tap["new_low"],
                "break_time": now,
                "score": score,
                "is_session_break": is_session_break,
            }

            self.logger.info(
                f"[BREAK DETECTED] {ul_name} {primary_tap['strike']} "
                f"{primary_tap['type']}: low={primary_tap['new_low']:.2f} "
                f"score={score} — waiting for {OPT_BOUNCE_CONFIRM_PCT*100:.0f}% bounce"
            )

            # Send informational break alert (NO trade plan)
            if OPT_BREAK_INFO_ALERT:
                break_msg_lines = []
                for tap in taps:
                    if tap.get("signal_type") == "SESSION_LOW":
                        swing = tap.get("swing_pct", 0)
                        data_days = tap.get("data_days", 0)
                        break_msg_lines.append(
                            f"<b>Session Break:</b> High {tap['previous_low']:.2f} ➡ "
                            f"Low {tap['new_low']:.2f} (-{swing}%) "
                            f"[Day {data_days + 1} of contract]"
                        )
                    else:
                        exp_date = tap.get("expiry_date")
                        try:
                            exp_str = exp_date.strftime("%d %b") if hasattr(exp_date, 'strftime') else str(exp_date)
                        except:
                            exp_str = str(exp_date)
                        break_msg_lines.append(
                            f"<b>{exp_str} Expiry Break:</b> "
                            f"{tap['previous_low']:.2f} ➡ {tap['new_low']:.2f}"
                        )

                spot_ltp = self.spot_prices.get(ul_name, 0)
                spot_line = f"\n📊 <b>{ul_name} Spot:</b> {spot_ltp:.0f}" if spot_ltp else ""

                info_msg = (
                    f"📊 <b>LOW BREAK DETECTED</b>\n"
                    f"{'=' * 30}\n\n"
                    f"<b>{ul_name} {primary_tap['strike']} {primary_tap['type']}</b>"
                    f"{spot_line}\n\n"
                    f"{chr(10).join(break_msg_lines)}\n\n"
                    f"<i>⏳ Watching for reversal bounce "
                    f"(need {OPT_BOUNCE_CONFIRM_PCT*100:.0f}% recovery)...</i>\n"
                    f"Score: {score}/10\n"
                    f"Time: {now.strftime('%H:%M:%S')}"
                )
                self.send_alert(info_msg)

        # -----------------------------------------------------------
        # PHASE 2: Check pending breaks for BOUNCE CONFIRMATION
        # -----------------------------------------------------------
        self._check_bounce_confirmations(snapshot, oi_signals, momentum_map, now)

        # -----------------------------------------------------------
        # Standalone OI signals (independent of price breaks)
        # -----------------------------------------------------------
        self._check_standalone_oi_signals(oi_signals, momentum_map, now)

    def _check_bounce_confirmations(self, snapshot, oi_signals, momentum_map, now):
        """Check all pending breaks for bounce reversal confirmation.
        
        A bounce is confirmed when:
        - Current LTP >= low_price * (1 + OPT_BOUNCE_CONFIRM_PCT)
        - i.e., premium has recovered at least 2% from the break low
        
        Expired breaks (>30min with no bounce) are discarded.
        """
        expired_keys = []

        for alert_key, break_data in list(self.pending_breaks.items()):
            symbol = break_data["symbol"]
            low_price = break_data["low_price"]
            break_time = break_data["break_time"]
            ul_name = break_data["underlying"]

            # Check expiry: no bounce within max wait time → cancel
            elapsed_min = (now - break_time).total_seconds() / 60
            if elapsed_min > OPT_BOUNCE_MAX_WAIT_MIN:
                expired_keys.append(alert_key)
                self.logger.info(
                    f"[BREAK EXPIRED] {ul_name} {break_data['strike']} "
                    f"{break_data['opt_type']}: no bounce after {elapsed_min:.0f}min — cancelled"
                )
                continue

            # Get current LTP
            if symbol not in snapshot:
                continue
            ltp = snapshot[symbol]["ltp"]
            if ltp <= 0:
                continue

            # Update low if price went further down
            if ltp < low_price:
                break_data["low_price"] = ltp
                continue

            # Check bounce: has premium recovered enough from the low?
            bounce_pct = (ltp - low_price) / low_price if low_price > 0 else 0
            if bounce_pct < OPT_BOUNCE_CONFIRM_PCT:
                continue

            # ============================================
            # BOUNCE CONFIRMED → Generate TRADE SIGNAL
            # ============================================
            self.logger.info(
                f"[BOUNCE CONFIRMED] {ul_name} {break_data['strike']} "
                f"{break_data['opt_type']}: low={low_price:.2f} → "
                f"bounce={ltp:.2f} (+{bounce_pct*100:.1f}%) after {elapsed_min:.0f}min"
            )

            self._send_trade_signal(
                break_data, snapshot, oi_signals, momentum_map,
                bounce_price=ltp, bounce_pct=bounce_pct, now=now
            )

            # Mark as alerted so it won't re-trigger
            self.alerted[alert_key] = now
            expired_keys.append(alert_key)

        # Clean up expired/confirmed breaks
        for key in expired_keys:
            self.pending_breaks.pop(key, None)

    def _send_trade_signal(self, break_data, snapshot, oi_signals, momentum_map,
                           bounce_price, bounce_pct, now):
        """Send the actual trade signal after bounce confirmation."""
        symbol = break_data["symbol"]
        ul_name = break_data["underlying"]
        taps = break_data["taps"]
        primary_tap = taps[0]
        low_price = break_data["low_price"]
        is_session_break = break_data["is_session_break"]

        # Re-score with current OI and momentum
        base_score = OPT_SCORE_SESSION_BREAK if is_session_break else OPT_SCORE_SESSION_LOW
        score = base_score
        bullish_oi = False
        oi_detail = ""

        for cu in oi_signals["call_unwind"]:
            if cu["strike"] == break_data["strike"]:
                score += OPT_SCORE_CALL_UNWIND
                if abs(cu["change_pct"]) >= OPT_OI_LARGE_THRESHOLD * 100:
                    score += OPT_SCORE_OI_LARGE_BONUS
                bullish_oi = True
                oi_detail += f"CE OI: {cu['change_pct']:.1f}% "

        for ps in oi_signals["put_surge"]:
            if ps["strike"] == break_data["strike"]:
                score += OPT_SCORE_PUT_SURGE
                if abs(ps["change_pct"]) >= OPT_OI_LARGE_THRESHOLD * 100:
                    score += OPT_SCORE_OI_LARGE_BONUS
                bullish_oi = True
                oi_detail += f"PE OI: +{ps['change_pct']:.1f}% "

        momentum = momentum_map.get(ul_name)
        momentum_conflict = False
        if momentum:
            if primary_tap["type"] == "CE" and momentum == "BEARISH":
                score += OPT_SCORE_MOMENTUM
            elif primary_tap["type"] == "PE" and momentum == "BEARISH":
                score += OPT_SCORE_MOMENTUM
            elif (primary_tap["type"] == "PE" and momentum == "BULLISH") or \
                 (primary_tap["type"] == "CE" and momentum == "BULLISH"):
                score -= 1
                momentum_conflict = True

        if time(9, 15) <= now.time() <= time(9, 35):
            score += OPT_SCORE_EARLY_SESSION

        if score < OPT_ALERT_THRESHOLD_MED:
            self.logger.info(
                f"[BOUNCE SIGNAL DROPPED] Score {score} < {OPT_ALERT_THRESHOLD_MED} "
                f"after re-scoring — OI may have shifted"
            )
            return

        confidence = "HIGH" if score >= OPT_ALERT_THRESHOLD_HIGH else "MEDIUM"
        emoji = "\U0001f534" if confidence == "HIGH" else "\U0001f7e1"

        # Build break details
        break_msg_lines = []
        for tap in taps:
            if tap.get("signal_type") == "SESSION_LOW":
                swing = tap.get("swing_pct", 0)
                data_days = tap.get("data_days", 0)
                break_msg_lines.append(
                    f"<b>Session Break:</b> High {tap['previous_low']:.2f} ➡ "
                    f"Low {tap['new_low']:.2f} (-{swing}%) "
                    f"[Day {data_days + 1} of contract]"
                )
            else:
                exp_date = tap.get("expiry_date")
                try:
                    exp_str = exp_date.strftime("%d %b") if hasattr(exp_date, 'strftime') else str(exp_date)
                except:
                    exp_str = str(exp_date)
                break_msg_lines.append(
                    f"<b>{exp_str} Expiry Break:</b> "
                    f"{tap['previous_low']:.2f} ➡ {tap['new_low']:.2f}"
                )
        break_details = "\n".join(break_msg_lines)

        # TRADE PLAN: Entry = bounce price (confirmed reversal)
        # SL = the break low (the dump bottom) minus buffer
        # Target = entry + risk * RR
        entry_price = bounce_price
        rr = 2.0

        # SL below the confirmed low with small buffer
        sl_price = round(low_price * 0.99, 2)  # 1% below the break low
        risk = entry_price - sl_price

        # Floor: minimum risk = 0.5% of entry
        min_risk = entry_price * 0.005
        if risk < min_risk:
            risk = min_risk
            sl_price = round(entry_price - risk, 2)

        target_price = round(entry_price + (risk * rr), 2)

        # Collect for morning bias
        if time(9, 15) <= now.time() <= time(9, 35):
            self.morning_signals.append({
                "symbol": symbol, "underlying": ul_name,
                "strike": break_data["strike"], "type": break_data["opt_type"],
                "score": score,
                "oi_direction": "put_surge" if "PE OI:" in oi_detail else
                                "call_unwind" if "CE OI:" in oi_detail else "",
                "session_low": low_price+0, "bounce_price": bounce_price,
                "bounce_pct": round(bounce_pct * 100, 1), "momentum": momentum,
                "ltp": bounce_price
            })

        mock_tap = {
            "type": break_data["opt_type"],
            "strike": break_data["strike"],
            "bounce_pct": round(bounce_pct * 100, 1)
        }
        reasoning = self._build_signal_reasoning(mock_tap, oi_detail, momentum, score, bullish_oi)

        # Build spot price context
        spot_ltp = self.spot_prices.get(ul_name, 0)
        spot_line = f"\n📊 <b>{ul_name} Spot:</b> {spot_ltp:.0f}" if spot_ltp else ""

        # Build Entry/SL/TP block
        trade_block = (
            f"\n\n📋 <b>TRADE PLAN:</b>\n"
            f"Entry: <b>{entry_price:.2f}</b> (bounce confirmed)\n"
            f"SL: <b>{sl_price:.2f}</b> (below dump low {low_price:.2f})\n"
            f"Target: <b>{target_price:.2f}</b> (RR: {rr:.1f})\n"
        )

        # Signal header
        if is_session_break:
            signal_header = "📉 <b>Session Low Break + Bounce (Early Cycle)</b>"
        else:
            signal_header = "🚨 <b>Monthly Low Break + Reversal Confirmed!</b>"

        elapsed_min = (now - break_data["break_time"]).total_seconds() / 60

        msg = (
            f"{emoji} <b>OPTIONS SIGNAL ({confidence})</b>\n"
            f"Score: <b>{score}/10</b>\n"
            f"{'=' * 30}\n\n"
            f"<b>{ul_name} {break_data['strike']} {break_data['opt_type']}</b>"
            f"{spot_line}\n\n"
            f"{signal_header}\n"
            f"{break_details}\n\n"
            f"✅ <b>BOUNCE CONFIRMED:</b> "
            f"+{bounce_pct*100:.1f}% from low {low_price:.2f} → {bounce_price:.2f} "
            f"({elapsed_min:.0f}min after break)\n\n"
            f"{'=' * 30}\n\n"
            f"<b>SIGNAL:</b> {reasoning['components']}\n\n"
            f"<b>WHAT IT MEANS:</b>\n"
            f"<i>{reasoning['meaning']}</i>\n\n"
            f"<b>TRADE IMPLICATION:</b>\n"
            f"{reasoning['implication']}\n\n"
            f"<b>CONVICTION:</b> {reasoning['conviction']}\n"
        )

        if oi_detail:
            msg += f"\nOI Delta: {oi_detail.strip()}"
        if momentum:
            if momentum_conflict:
                msg += f"\nMomentum: {momentum} ⚠️ CONFLICTS with signal bias"
            else:
                msg += f"\nMomentum: {momentum} ✅ Confirms signal"

        msg += trade_block

        msg += (
            f"\n<b>ACTION:</b> {reasoning['action']}\n"
            f"\nTime: {now.strftime('%H:%M:%S')}"
        )

        self.send_alert(msg)
        self._register_option_trade(symbol, break_data, entry_price, sl_price, target_price, now)
        self.logger.info(
            f"BN SIGNAL: {ul_name} {break_data['strike']} {break_data['opt_type']} "
            f"Conf={confidence} Entry={bounce_price:.1f} "
            f"(bounced +{bounce_pct*100:.1f}% from low {low_price:.1f})"
        )

    def _check_standalone_oi_signals(self, oi_signals, momentum_map, now):
        # --- Standalone CALL UNWIND ---
        for cu in oi_signals["call_unwind"]:
            if abs(cu["change_pct"]) > 10:
                symbol = cu["symbol"]
                alert_key = f"{cu['strike']}_CE_{now.strftime('%Y-%m-%d')}"
                if alert_key in self.alerted:
                    elapsed = (now - self.alerted[alert_key]).total_seconds()
                    if elapsed < OPT_ALERT_COOLDOWN_SECS:
                        continue
                score = OPT_SCORE_CALL_UNWIND
                ul_name = cu.get("underlying", "")
                # Call unwind = bearish signal, BEARISH momentum confirms
                if momentum_map.get(ul_name) == "BEARISH":
                    score += OPT_SCORE_MOMENTUM
                if score >= OPT_ALERT_THRESHOLD_MED:
                    msg = (
                        f"<b>OI ALERT: CALL UNWINDING</b>\n\n"
                        f"<b>{symbol}</b>\n"
                        f"Strike: {cu['strike']} CE\n"
                        f"OI Change: {cu['change_pct']:.1f}%\n"
                        f"({cu['prev_oi']:,} -> {cu['cur_oi']:,})\n\n"
                        f"<b>WHAT IT MEANS:</b>\n"
                        f"<i>Institutions exiting CALL positions. Bullish conviction weakening.</i>\n\n"
                        f"Momentum: {momentum_map.get(ul_name, 'Flat')}\n"
                        f"Time: {now.strftime('%H:%M:%S')}"
                    )
                    self.send_alert(msg)
                    self.alerted[alert_key] = now

        # --- Standalone PUT SURGE (NEW — fills early-cycle gap) ---
        for ps in oi_signals["put_surge"]:
            if abs(ps["change_pct"]) > 10:
                symbol = ps["symbol"]
                alert_key = f"{ps['strike']}_PE_OI_{now.strftime('%Y-%m-%d')}"
                if alert_key in self.alerted:
                    elapsed = (now - self.alerted[alert_key]).total_seconds()
                    if elapsed < OPT_ALERT_COOLDOWN_SECS:
                        continue
                score = OPT_SCORE_PUT_SURGE
                ul_name = ps.get("underlying", "")
                # Put surge = bearish signal, BEARISH momentum confirms
                if momentum_map.get(ul_name) == "BEARISH":
                    score += OPT_SCORE_MOMENTUM
                if score >= OPT_ALERT_THRESHOLD_MED:
                    msg = (
                        f"<b>OI ALERT: PUT OI SURGING</b>\n\n"
                        f"<b>{symbol}</b>\n"
                        f"Strike: {ps['strike']} PE\n"
                        f"OI Change: +{ps['change_pct']:.1f}%\n"
                        f"({ps['prev_oi']:,} -> {ps['cur_oi']:,})\n\n"
                        f"<b>WHAT IT MEANS:</b>\n"
                        f"<i>Institutions building heavy PUT positions. Strong bearish bias.</i>\n\n"
                        f"Momentum: {momentum_map.get(ul_name, 'Flat')}\n"
                        f"Time: {now.strftime('%H:%M:%S')}"
                    )
                    self.send_alert(msg)
                    self.alerted[alert_key] = now

    # --- State Management ---
    def save_state(self):
        try:
            state = {
                "historical_lows": self.historical_lows,
                "alerted": {k: v.isoformat() for k, v in self.alerted.items()},
                "last_update": datetime.now().isoformat()
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

    def _load_active_trades(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "option_active_trades.json")
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    self.active_option_trades = json.load(f)
                self.logger.info(f"Loaded {len(self.active_option_trades)} active option trades from disk")
        except Exception as e:
            self.logger.warning(f"Could not load option_active_trades.json: {e}")

    def _save_active_trades(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "option_active_trades.json")
        try:
            with open(path, "w") as f:
                json.dump(self.active_option_trades, f, indent=2, default=str)
        except Exception as e:
            self.logger.warning(f"Could not save option_active_trades.json: {e}")

    def _register_option_trade(self, symbol, break_data, entry_price, sl_price, target_price, now):
        """Register a newly alerted signal for exit tracking."""
        signal_id = (
            f"{break_data['underlying']}_{break_data['strike']}"
            f"_{break_data['opt_type']}_{now.strftime('%Y%m%d_%H%M%S')}"
        )
        self.active_option_trades[signal_id] = {
            "symbol": symbol,
            "underlying": break_data["underlying"],
            "strike": break_data["strike"],
            "opt_type": break_data["opt_type"],
            "entry_price": entry_price,
            "sl_price": sl_price,
            "target_price": target_price,
            "entry_time": now.isoformat(),
            "status": "active"
        }
        self._save_active_trades()
        self.logger.info(
            f"[EXIT TRACK] Registered {signal_id}: "
            f"Entry={entry_price:.2f} SL={sl_price:.2f} Target={target_price:.2f}"
        )

    def _check_option_exits(self, snapshot, now):
        """Check active option trades for SL/Target hits and send notifications."""
        if not self.active_option_trades:
            return

        exited = []
        for signal_id, trade in list(self.active_option_trades.items()):
            if trade.get("status") != "active":
                exited.append(signal_id)
                continue

            symbol = trade["symbol"]
            if symbol not in snapshot:
                continue

            ltp = snapshot[symbol]["ltp"]
            if not ltp or ltp <= 0:
                continue

            entry = trade["entry_price"]
            sl = trade["sl_price"]
            target = trade["target_price"]
            risk = entry - sl if entry > sl else 1

            # Determine exit condition
            hit = None
            if ltp >= target:
                hit = "TARGET"
            elif ltp <= sl:
                hit = "SL"

            if not hit:
                continue

            elapsed_sec = (now - datetime.fromisoformat(trade["entry_time"])).total_seconds()
            elapsed_str = f"{int(elapsed_sec // 60)}m {int(elapsed_sec % 60)}s"
            pnl_r = (ltp - entry) / risk

            if hit == "TARGET":
                emoji = "\U0001f3af"
                header = "TARGET HIT"
                pnl_str = f"+{pnl_r:.1f}R \u2705"
            else:
                emoji = "\U0001f6d1"
                header = "SL HIT"
                pnl_str = f"{pnl_r:.1f}R \u274c"

            msg = (
                f"{emoji} <b>OPTIONS EXIT: {header}</b>\n"
                f"{'=' * 30}\n\n"
                f"<b>{trade['underlying']} {trade['strike']} {trade['opt_type']}</b>\n\n"
                f"Entry: <b>{entry:.2f}</b>\n"
                f"Exit LTP: <b>{ltp:.2f}</b>\n"
                f"SL: {sl:.2f} | Target: {target:.2f}\n\n"
                f"P&L: <b>{pnl_str}</b>\n"
                f"Duration: {elapsed_str}\n"
                f"\nTime: {now.strftime('%H:%M:%S')}"
            )

            self.send_alert(msg)
            self.logger.info(
                f"[EXIT] {signal_id}: {hit} @ {ltp:.2f} | P&L {pnl_r:.2f}R"
            )

            trade["status"] = hit.lower() + "_hit"
            trade["exit_price"] = ltp
            trade["exit_time"] = now.isoformat()
            exited.append(signal_id)

        if exited:
            for k in exited:
                self.active_option_trades.pop(k, None)
            self._save_active_trades()

    def poll(self):
        now = datetime.now()
        
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

        self.evaluate_signals(snapshot)
        self._check_option_exits(snapshot, now)

        ce_count = sum(1 for d in snapshot.values() if d["type"] == "CE")
        pe_count = sum(1 for d in snapshot.values() if d["type"] == "PE")
        src = "WS" if self.tick_store.has_data() else "API"
        ticks = self.tick_store.tick_count
        print(f"[{now.strftime('%H:%M:%S')}] Opt Scan [{src}]: {ce_count} CE + {pe_count} PE | Ticks: {ticks}")

        if now.minute % 5 == 0:
            self.save_state()


import pickle
