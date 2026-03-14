# option_monitor_module.py
# MERGED OPTION MONITOR MODULE (Class-Based)

import os
import json
import time as t
import logging
import pickle
import requests
from datetime import datetime, timedelta, date

# =====================================================
# CONFIGURATION
# =====================================================
MONTHLY_LOWS_FILE = "option_monthly_lows.json"
NOTIFIED_TAPS_FILE = "option_notified_taps.json"
TAP_STATE_FILE = "option_tap_state.json"
CACHE_PKL = "instruments_nfo.pkl"
TOLERANCE_PCT = 0.002
MIN_BOUNCE_PCT = 0.02          # 2% bounce required to confirm (was 0.5%)
TAP_COOLDOWN_MINUTES = 60      # 60-min cooldown per strike after alert
MAX_TAPS_PER_STRIKE_DAY = 2    # Max 2 alerts per strike per day

class OptionMonitor:
    def __init__(self, kite):
        self.kite = kite
        self.logger = logging.getLogger("OptionMonitor")
        self.monthly_lows = {}
        self.notified_taps = set()
        self.tap_state = {}
        self.contracts = {}
        self.last_atm_refresh = datetime.now()
        self.last_scan_time = datetime.now()
        self.scan_interval = 60  # Seconds
        self.tap_cooldowns = {}    # {symbol: datetime} — cooldown expiry per strike
        self.tap_daily_count = {}  # {symbol: int} — alerts sent today per strike
        self._tap_count_date = datetime.now().date()  # reset counter each day
        
        # Load State
        self.load_state()

        # Telegram Secrets (Fallback to defaults if env not set)
        self.BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8388602985:AAEiombJFTGv0Dx9UZeeKkpKeo0hem9hv8I")
        self.CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003268636791")

    def telegram_send(self, message):
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": self.CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML"
                },
                timeout=5
            )
        except Exception as e:
            self.logger.error(f"Telegram error: {e}")

    # =====================================================
    # STATE MANAGEMENT
    # =====================================================
    def load_state(self):
        # Monthly Lows
        if os.path.exists(MONTHLY_LOWS_FILE):
            try:
                with open(MONTHLY_LOWS_FILE, "r") as f:
                    self.monthly_lows = json.load(f)
            except: self.monthly_lows = {}
        
        # Notified Taps
        if os.path.exists(NOTIFIED_TAPS_FILE):
            try:
                with open(NOTIFIED_TAPS_FILE, "r") as f:
                    self.notified_taps = set(json.load(f))
            except: self.notified_taps = set()

        # Tap State
        if os.path.exists(TAP_STATE_FILE):
            try:
                with open(TAP_STATE_FILE, "r") as f:
                    self.tap_state = json.load(f)
            except: self.tap_state = {}

    def save_monthly_lows(self):
        with open(MONTHLY_LOWS_FILE, "w") as f:
            json.dump(self.monthly_lows, f, indent=2, default=str)

    def save_notified_taps(self):
        with open(NOTIFIED_TAPS_FILE, "w") as f:
            json.dump(list(self.notified_taps), f, indent=2)

    def save_tap_state(self):
        with open(TAP_STATE_FILE, "w") as f:
            json.dump(self.tap_state, f, indent=2, default=str)

    # =====================================================
    # CORE LOGIC
    # =====================================================
    def get_target_expiry(self, instruments, name):
        today = datetime.now().date()
        futures = [i for i in instruments if i["name"] == name and i["instrument_type"] == "FUT"]
        futures.sort(key=lambda x: x["expiry"])
        for f in futures:
            expiry = f["expiry"]
            if isinstance(expiry, datetime): expiry = expiry.date()
            if expiry >= today: return expiry
        return None

    def refresh_contracts(self):
        """Refreshes available contracts based on ATM"""
        try:
            # 1. Get Strikes
            spot_data = self.kite.ltp(["NSE:NIFTY 50", "NSE:NIFTY BANK"])
            if not spot_data:
                self.logger.error("Could not fetch indices LTP")
                return

            nifty_ltp = spot_data.get("NSE:NIFTY 50", {}).get("last_price")
            bn_ltp = spot_data.get("NSE:NIFTY BANK", {}).get("last_price")

            # 2. Get Instruments
            if os.path.exists(CACHE_PKL):
                with open(CACHE_PKL, "rb") as f:
                    instruments = pickle.load(f)
            else:
                instruments = self.kite.instruments(exchange="NFO")
                with open(CACHE_PKL, "wb") as f:
                    pickle.dump(instruments, f)

            self.contracts = {}
            
            # --- PROCESS NIFTY ---
            if nifty_ltp:
                n_atm = round(nifty_ltp / 100) * 100
                n_strikes = [n_atm - 100, n_atm, n_atm + 100]
                n_exp = self.get_target_expiry(instruments, "NIFTY")
                self.logger.info(f"NIFTY LTP: {nifty_ltp:.0f} | ATM: {n_atm} | Monitoring: {n_strikes} | Expiry: {n_exp}")
                
                if n_exp:
                    for instr in instruments:
                        if instr["name"] == "NIFTY" and instr["strike"] in n_strikes:
                            expiry = instr["expiry"]
                            if isinstance(expiry, datetime): expiry = expiry.date()
                            if expiry == n_exp:
                                self.contracts[instr["instrument_token"]] = {
                                    "symbol": instr["tradingsymbol"],
                                    "strike": instr["strike"],
                                    "type": instr["instrument_type"],
                                    "expiry": instr["expiry"]
                                }

            # --- PROCESS BANKNIFTY ---
            if bn_ltp:
                b_atm = round(bn_ltp / 100) * 100
                b_strikes = [b_atm - 200, b_atm - 100, b_atm, b_atm + 100, b_atm + 200]
                b_exp = self.get_target_expiry(instruments, "BANKNIFTY")
                self.logger.info(f"BANKNIFTY LTP: {bn_ltp:.0f} | ATM: {b_atm} | Monitoring: {b_strikes} | Expiry: {b_exp}")
                
                if b_exp:
                    for instr in instruments:
                        if instr["name"] == "BANKNIFTY" and instr["strike"] in b_strikes:
                            expiry = instr["expiry"]
                            if isinstance(expiry, datetime): expiry = expiry.date()
                            if expiry == b_exp:
                                self.contracts[instr["instrument_token"]] = {
                                    "symbol": instr["tradingsymbol"],
                                    "strike": instr["strike"],
                                    "type": instr["instrument_type"],
                                    "expiry": instr["expiry"]
                                }
            
            self.logger.info(f"Loaded {len(self.contracts)} total combined contracts.")
            
        except Exception as e:
            self.logger.error(f"Refresh Contracts Error: {e}")

    def initialize(self):
        """Startup routine: Load contracts & Check for breakdowns"""
        self.logger.info("Initializing Option Monitor...")
        self.refresh_contracts()
        
        if not self.contracts:
            self.logger.error("No contracts found to monitor!")
            return

        start_date = datetime.now().replace(day=1)
        end_date = datetime.now()

        updates = False
        for token, info in self.contracts.items():
            symbol = info["symbol"]
            
            # Skip if already updated today
            if symbol in self.monthly_lows and self.monthly_lows[symbol].get("last_update") == datetime.now().date().isoformat():
                continue

            try:
                candles = self.kite.historical_data(token, start_date, end_date, "5minute")
                if not candles: continue

                monthly_low = min(c["low"] for c in candles)

                # STARTUP ALERT
                if symbol in self.monthly_lows:
                    stored_low = self.monthly_lows[symbol].get("monthly_low", 0)
                    if stored_low > 0 and monthly_low < stored_low:
                        break_time = "Unknown"
                        for c in candles:
                            if c["low"] < stored_low:
                                break_time = c["date"].strftime('%H:%M:%S')
                                break
                        
                        msg = (
                            f"⚠️ <b>STARTUP ALERT: LOW BROKEN</b>\n\n"
                            f"<b>{symbol}</b>\n"
                            f"Previous Low: {stored_low:.2f}\n"
                            f"New Low: {monthly_low:.2f}\n"
                            f"Time: {break_time}\n"
                        )
                        self.telegram_send(msg)

                self.monthly_lows[symbol] = {
                    "monthly_low": monthly_low,
                    "last_update": datetime.now().date().isoformat(),
                    "strike": info["strike"],
                    "type": info["type"],
                    "expiry": info["expiry"].isoformat() if isinstance(info["expiry"], (datetime, date)) else str(info["expiry"])

                }
                updates = True
                t.sleep(0.1)

            except Exception as e:
                self.logger.error(f"Init Error {symbol}: {e}")

        if updates:
            self.save_monthly_lows()
        
        self.logger.info("Option Monitor Initialized.")

    def update_monthly_low_if_new_low(self, symbol, current_price):
        if symbol in self.monthly_lows:
            existing_low = self.monthly_lows[symbol]["monthly_low"]
            if current_price < existing_low:
                self.logger.warning(f"[NEW LOW] {symbol}: {existing_low:.2f} -> {current_price:.2f}")
                self.monthly_lows[symbol]["monthly_low"] = current_price
                self.monthly_lows[symbol]["last_update"] = datetime.now().date().isoformat()
                self.save_monthly_lows()
                return True
        return False

    def poll(self):
        """Main polling function - Call this from the engine loop"""
        now = datetime.now()

        # 1. Market Hours Check
        if now.hour < 9 or (now.hour == 9 and now.minute < 15) or (now.hour > 15) or (now.hour == 15 and now.minute >= 30):
            return # Market Closed

        # 2. Interval Check (60s)
        if (now - self.last_scan_time).total_seconds() < self.scan_interval:
            return

        self.last_scan_time = now

        # 3. Refresh Strikes (Every 10 mins)
        if (now - self.last_atm_refresh).total_seconds() > 600:
            self.refresh_contracts()
            self.last_atm_refresh = now

        if not self.contracts: return

        # 4. Fetch Prices
        try:
            tokens = list(self.contracts.keys())
            quote = self.kite.ltp(tokens)
            
            for token, info in self.contracts.items():
                symbol = info["symbol"]
                str_token = str(token)
                
                if str_token not in quote: continue
                current_price = quote[str_token]["last_price"]

                if symbol not in self.monthly_lows: continue
                
                # Check New Low
                if self.update_monthly_low_if_new_low(symbol, current_price):
                    monthly_low = current_price
                else:
                    monthly_low = self.monthly_lows[symbol]["monthly_low"]

                # Check Taps
                if symbol not in self.tap_state:
                    self.tap_state[symbol] = {"state": "ACTIVE", "tap_price": None}
                
                state_data = self.tap_state[symbol]
                
                # --- Reset daily counter on new day ---
                if now.date() != self._tap_count_date:
                    self.tap_daily_count = {}
                    self.tap_cooldowns = {}
                    self._tap_count_date = now.date()

                if state_data["state"] == "ACTIVE":
                    # Skip if strike is in cooldown
                    if symbol in self.tap_cooldowns and now < self.tap_cooldowns[symbol]:
                        continue
                    # Skip if daily cap reached for this strike
                    if self.tap_daily_count.get(symbol, 0) >= MAX_TAPS_PER_STRIKE_DAY:
                        continue

                    if current_price <= monthly_low * (1 + TOLERANCE_PCT):
                        state_data["state"] = "TAPPED"
                        state_data["tap_price"] = current_price
                        state_data["tap_time"] = now.isoformat()
                        self.logger.info(f"{symbol}: TAPPED at {current_price:.2f}")

                elif state_data["state"] == "TAPPED":
                    tap_price = state_data.get("tap_price", monthly_low)
                    bounce_threshold = tap_price + (monthly_low * MIN_BOUNCE_PCT)

                    if current_price > bounce_threshold:
                        state_data["state"] = "CONFIRMED"
                        bounce_pct = ((current_price - tap_price) / tap_price) * 100
                        tap_id = f"{symbol}_{now.strftime('%Y%m%d')}"

                        if tap_id not in self.notified_taps:
                            msg = (
                                f"[TAP DETECTED] Strike Touched Monthly Low!\n\n"
                                f"<b>{symbol}</b>\n"
                                f"Monthly Low: {monthly_low:.2f}\n"
                                f"Tap Price: {tap_price:.2f}\n"
                                f"Bounce Price: {current_price:.2f} (+{bounce_pct:.1f}%)\n"
                                f"Time: {now.strftime('%H:%M:%S')}\n\n"
                                f"<b>Option Buying Opportunity?</b>"
                            )
                            self.telegram_send(msg)
                            self.notified_taps.add(tap_id)
                            self.save_notified_taps()

                            # Update cooldown & daily counter
                            self.tap_cooldowns[symbol] = now + timedelta(minutes=TAP_COOLDOWN_MINUTES)
                            self.tap_daily_count[symbol] = self.tap_daily_count.get(symbol, 0) + 1
                            self.logger.info(f"{symbol}: TAP CONFIRMED (bounce +{bounce_pct:.1f}%) | cooldown {TAP_COOLDOWN_MINUTES}m | day count {self.tap_daily_count[symbol]}/{MAX_TAPS_PER_STRIKE_DAY}")
                        
                        state_data["state"] = "ACTIVE"

            self.save_tap_state()

        except Exception as e:
            self.logger.error(f"Poll Error: {e}")
