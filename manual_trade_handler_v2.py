# =====================================================
# ENHANCED MANUAL TRADE HANDLER WITH RISK MANAGEMENT
# =====================================================

import time as t
import requests
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

import json
import os
import risk_management as rm

BOT_TOKEN = "8388602985:AAEiombJFTGv0Dx9UZeeKkpKeo0hem9hv8I"
CHAT_ID = "-1003268636791"

DRY_RUN = False          # Set to False to enable real orders
USE_SL_M = True         # Use SL-M (Market) or SL-L (Limit)

def telegram_send(message: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=5
        )
    except Exception as e:
        print("Telegram error:", e)


class ManualTradeHandlerV2:
    """
    Enhanced manual trade handler with strict 1:3 RR enforcement
    """
    
    def __init__(self, kite):
        self.kite = kite
        self.managed_positions = {}
        self.ignored_symbols = set()
        print("[OK] Manual Trade Handler V2 (Risk Management) Initialized")

    def poll_manual_trades(self, active_algo_trades):
        """
        Main loop with risk management checks
        """
        try:
            # Daily risk check FIRST
            can_trade, status = rm.can_trade_today()
            if not can_trade:
                print(f"[RISK] {status} - Skipping manual trade adoption")
                return
            
            # Fetch current positions
            positions = self.kite.positions()['net']
            
            open_positions = {}
            for p in positions:
                if p['quantity'] != 0:
                    key = f"{p['exchange']}:{p['tradingsymbol']}"
                    open_positions[key] = p

            algo_symbols = {t['symbol'] for t in active_algo_trades}

            # Detect new trades
            for symbol, pos in open_positions.items():
                if symbol in algo_symbols or symbol in self.managed_positions or symbol in self.ignored_symbols:
                    continue

                self.adopt_trade_with_risk(symbol, pos)

            # Check for closed trades
            active_managed = list(self.managed_positions.keys())
            for symbol in active_managed:
                if symbol not in open_positions:
                    self.stop_managing(symbol)

            # Monitor active positions
            self.monitor_active_positions(open_positions)

        except Exception as e:
            print(f"[ERR] Manual Poll Error: {e}")

    def adopt_trade_with_risk(self, symbol, pos):
        """
        Adopts trade with STRICT 1:3 RR enforcement
        """
        qty = pos['quantity']
        entry = pos['average_price']
        
        if entry == 0:
            try:
                entry = self.kite.ltp(symbol)[symbol]['last_price']
            except:
                print(f"[WARN] Cannot get price for {symbol}")
                return

        direction = "LONG" if qty > 0 else "SHORT"
        
        print(f"[NEW] TRADE FOUND: {symbol} | {direction} | Qty: {abs(qty)} | Entry: {entry:.2f}")

        # Calculate technical SL
        sl_price, base_target = self.calculate_structure_levels(symbol, entry, direction)
        
        if sl_price == 0:
            self.ignored_symbols.add(symbol)
            print(f"[ERR] Cannot calculate SL for {symbol}")
            return

        # STRICT: Enforce 1:3 RR target
        risk = abs(entry - sl_price)
        target_price = entry + (risk * rm.MIN_RR_RATIO) if direction == "LONG" else entry - (risk * rm.MIN_RR_RATIO)
        
        # Validate RR
        rr = rm.calculate_rr_ratio(entry, sl_price, target_price, direction)
        
        print(f"   Entry: {entry:.2f} | SL: {sl_price:.2f} | Target: {target_price:.2f} (RR: 1:{rr})")

        # Register position
        self.managed_positions[symbol] = {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "qty": qty,
            "sl_price": sl_price,
            "target_price": target_price,
            "sl_order_id": None,
            "state": "ACTIVE",
            "highest_price": entry if direction == "LONG" else 999999,
            "lowest_price": entry if direction == "SHORT" else 0,
            "rr_ratio": rr
        }
        
        # Place SL order
        order_id = self.place_sl_order(symbol, qty, sl_price, direction)
        if order_id:
            self.managed_positions[symbol]['sl_order_id'] = order_id
            
        # Send alert
        msg = f"👉 <b>NEW POSITION MANAGED</b>\n" \
              f"Symbol: {symbol}\n" \
              f"Entry: {entry:.2f} | SL: {sl_price:.2f}\n" \
              f"Target: {target_price:.2f} (1:3 RR)\n" \
              f"Risk: {risk:.2f} per unit"
        telegram_send(msg)

    def calculate_structure_levels(self, symbol, entry, direction):
        """
        Calculates SL and Target:
        - For INDEX/STOCK (NSE:): Use swing structure (lowest low / highest high)
        - For OPTIONS (NFO:): Use fixed percentage-based SL (1.5% risk)
        """
        try:
            # Check if it's an options contract
            is_option = "NFO:" in symbol
            
            # =============================================
            # OPTION TRADING: Fixed Percentage-Based SL
            # =============================================
            if is_option:
                # For options, use 10% fixed risk for better targets
                # Entry: 200 → SL: 180, Target: 260 (1:3 RR)
                OPTION_RISK_PCT = 0.10  # 10%
                OPTION_RR = 3.0  # 1:3 RR for options
                
                if direction == "LONG":
                    sl = entry * (1 - OPTION_RISK_PCT)  # 1.5% below entry
                else:  # SHORT
                    sl = entry * (1 + OPTION_RISK_PCT)  # 1.5% above entry
                
                risk = abs(entry - sl)
                target = entry + (risk * OPTION_RR) if direction == "LONG" else entry - (risk * OPTION_RR)
                
                return round(sl, 2), round(target, 2)
            
            # =============================================
            # INDEX/STOCK TRADING: Swing Structure Logic
            # =============================================
            ltp_response = self.kite.ltp([symbol])
            if symbol not in ltp_response:
                return 0, 0

            token = ltp_response[symbol]['instrument_token']
            to_date = datetime.now(_IST).replace(tzinfo=None)
            from_date = to_date - timedelta(days=5)
            
            candles = self.kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="15minute"
            )
            
            if not candles:
                return 0, 0

            recent = candles[-20:]
            
            if direction == "LONG":
                lowest = min(c['low'] for c in recent)
                sl = lowest * 0.999
                if sl >= entry:
                    sl = entry * 0.995
            else:
                highest = max(c['high'] for c in recent)
                sl = highest * 1.001
                if sl <= entry:
                    sl = entry * 1.005

            # Target will be adjusted to 1:3 RR in adopt_trade_with_risk
            risk = abs(entry - sl)
            target = entry + (risk * 2)  # Placeholder, will be adjusted

            return round(sl, 2), round(target, 2)

        except Exception as e:
            print(f"[ERR] Structure calculation failed: {e}")
            return 0, 0

    def place_sl_order(self, symbol, qty, trigger_price, direction):
        """Places SL order with risk management context"""
        
        if DRY_RUN:
            msg = f"🧪 [DRY] SL-M Order\nSymbol: {symbol}\nTrigger: {trigger_price:.2f}"
            print(f"[DRY] Would place SL: {symbol} @ {trigger_price:.2f}")
            return "DRY_ORDER_ID"

        try:
            txn_type = self.kite.TRANSACTION_TYPE_SELL if direction == "LONG" else self.kite.TRANSACTION_TYPE_BUY
            
            order_id = self.kite.place_order(
                tradingsymbol=symbol.split(":")[-1],
                exchange=self.kite.EXCHANGE_NSE,
                transaction_type=txn_type,
                quantity=abs(qty),
                order_type=self.kite.ORDER_TYPE_SLM if USE_SL_M else self.kite.ORDER_TYPE_SL,
                price=trigger_price if not USE_SL_M else 0,
                trigger_price=trigger_price,
                product=self.kite.PRODUCT_MIS,
                variety=self.kite.VARIETY_REGULAR
            )
            
            print(f"[OK] SL Order: {symbol} | Trigger: {trigger_price:.2f} | ID: {order_id}")
            return order_id

        except Exception as e:
            print(f"[ERR] SL Order failed: {e}")
            return None

    def monitor_active_positions(self, open_positions):
        """Monitors and trails SL as price moves favorably"""
        for symbol, data in self.managed_positions.items():
            if symbol not in open_positions:
                continue

            pos = open_positions[symbol]
            current_price = pos.get('last_price', 0)
            
            if current_price == 0:
                continue

            # Update extremes
            if data["direction"] == "LONG":
                if current_price > data["highest_price"]:
                    data["highest_price"] = current_price
                    self.trail_sl_long(symbol, current_price, data)
            else:
                if current_price < data["lowest_price"]:
                    data["lowest_price"] = current_price
                    self.trail_sl_short(symbol, current_price, data)

    def trail_sl_long(self, symbol, current_price, data):
        """Trails SL for LONG positions"""
        risk = data["entry"] - data["sl_price"]
        breakeven_sl = data["entry"]
        
        # If profit >= 50% of risk, move SL to breakeven
        if current_price >= (data["entry"] + risk * 0.5):
            if data["sl_price"] < breakeven_sl - 0.01:
                new_sl = breakeven_sl
                self.modify_sl_order(symbol, new_sl, data)

    def trail_sl_short(self, symbol, current_price, data):
        """Trails SL for SHORT positions"""
        risk = data["sl_price"] - data["entry"]
        breakeven_sl = data["entry"]
        
        if current_price <= (data["entry"] - risk * 0.5):
            if data["sl_price"] > breakeven_sl + 0.01:
                new_sl = breakeven_sl
                self.modify_sl_order(symbol, new_sl, data)

    def modify_sl_order(self, symbol, new_trigger, data):
        """Modifies SL order"""
        if not data.get('sl_order_id'):
            return

        if DRY_RUN:
            print(f"[DRY] Trail SL: {symbol} → {new_trigger:.2f}")
            data["sl_price"] = new_trigger
            return

        try:
            self.kite.modify_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=data['sl_order_id'],
                trigger_price=new_trigger,
                order_type=self.kite.ORDER_TYPE_SLM if USE_SL_M else self.kite.ORDER_TYPE_SL,
                price=new_trigger if not USE_SL_M else 0
            )
            print(f"[UPD] SL Trailed: {symbol} → {new_trigger:.2f}")
            data["sl_price"] = new_trigger
        except Exception as e:
            print(f"[ERR] Trail failed: {e}")

    def stop_managing(self, symbol):
        """Removes symbol from management"""
        if symbol in self.managed_positions:
            del self.managed_positions[symbol]
            print(f"[END] Position closed: {symbol}")
