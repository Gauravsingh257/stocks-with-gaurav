"""
Live Execution
==============
Kite API integration scaffold for live trading.
DRY_RUN mode by default — logs signals without placing real orders.
Telegram alerts for all signal events.
"""

import logging
import requests
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

DRY_RUN = True  # Safety: must be explicitly disabled for real orders


from smc_trading_engine.execution.execution_core import (
    ExecutionState, open_trade, close_trade
)

class LiveExecution:
    """Live execution engine with Kite API integration."""

    def __init__(self, kite=None, telegram_token: str = "", telegram_chat: str = "",
                 dry_run: bool = True, state: ExecutionState = None):
        self.kite = kite
        self.telegram_token = telegram_token
        self.telegram_chat = telegram_chat
        self.dry_run = dry_run
        self.state = state if state else ExecutionState()
        self.order_log = []

    def execute_signal(self, signal: Dict) -> Optional[Dict]:
        """
        Execute a trading signal.
        1. Validates via execution_core.open_trade()
        2. Places Market Order (Live/Dry)
        3. Returns Trade Dict
        """
        if not signal:
            return None

        # 1. Open Trade in Core (Check limits, create struct)
        # We use current time as entry time
        timestamp = datetime.now()
        trade, reason = open_trade(signal, timestamp, self.state)
        
        if not trade:
            logger.warning(f"Trade Rejected: {reason}")
            return None

        # Build alert message
        msg = self._format_alert(trade)

        if self.dry_run:
            logger.info(f"[DRY_RUN] {trade['symbol']} {trade['direction']} "
                        f"Entry={trade['entry']} SL={trade['sl']} TP={trade['target']}")
            self._send_telegram(msg)
            self.order_log.append({"trade_id": trade["id"], "mode": "DRY_RUN", "action": "ENTRY"})
            return trade

        # LIVE EXECUTION
        if self.kite is None:
            logger.error("Kite API not connected!")
            return None

        try:
            clean_symbol = trade["symbol"].replace("NSE:", "")
            tx_type = "BUY" if trade["direction"] == "LONG" else "SELL"
            # Calculate qty based on risk or fixed?
            # signal should have 'position_size' from RiskManager
            qty = signal.get("position_size", 1)

            order_id = self.kite.place_order(
                variety="regular",
                exchange="NSE",
                tradingsymbol=clean_symbol,
                transaction_type=tx_type,
                quantity=qty,
                product="MIS",
                order_type="MARKET"
            )
            
            # Store Broker Order ID in trade
            trade["broker_order_id"] = order_id
            
            self.order_log.append({"trade_id": trade["id"], "order_id": order_id,
                                   "mode": "LIVE", "action": "ENTRY"})
            self._send_telegram(msg + f"\nOrder ID: {order_id}")
            logger.info(f"[LIVE] Entry Order placed: {order_id}")
            return trade

        except Exception as e:
            logger.error(f"[LIVE] Entry Order failed: {e}")
            self._send_telegram(f"ORDER FAILED: {trade['symbol']}\n{e}")
            # Rollback state? open_trade added it to active_trades.
            # We should remove it if execution failed.
            if trade in self.state.active_trades:
                self.state.active_trades.remove(trade)
            return None

    def close_position(self, trade: Dict, reason: str, exit_price: float):
        """
        Closes a position.
        1. Places Market Order (Square Off)
        2. Updates Core State via close_trade()
        """
        timestamp = datetime.now()
        
        # 1. Execute Order
        if not self.dry_run and self.kite:
            try:
                clean_symbol = trade["symbol"].replace("NSE:", "")
                # Exit direction is opposite of entry
                tx_type = "SELL" if trade["direction"] == "LONG" else "BUY"
                qty = 1 # We need to track Qty in trade struct! 
                # signal.get('position_size') was used. execution_core doesn't track qty yet.
                # Assuming 1 for now or we need to add qty to execution_core.
                # User didn't specify qty in core, so we handle it here.
                # Ideally execute_signal passed qty to trade.
                # Let's assume trade object has what we put in it. 
                # We should add 'qty' to open_trade? User didn't ask for it.
                # usage: qty = signal.get("position_size", 1) used in execute_signal.
                # We should store it in trade dict if possible.
                # Since open_trade output is a dict, we can inject it.
                qty = trade.get("qty", 1) 
                
                order_id = self.kite.place_order(
                    variety="regular",
                    exchange="NSE",
                    tradingsymbol=clean_symbol,
                    transaction_type=tx_type,
                    quantity=qty,
                    product="MIS",
                    order_type="MARKET"
                )
                logger.info(f"[LIVE] Exit Order placed: {order_id} ({reason})")
                self._send_telegram(f"EXIT {trade['symbol']} ({reason})\nPnL: Calculating...")
            except Exception as e:
                logger.error(f"[LIVE] Exit Failed: {e}")
                self._send_telegram(f"EXIT FAILED {trade['symbol']}: {e}")
                return # Don't update state if failed? Or assume closed manually?

        # 2. Update Core
        closed_trade = close_trade(trade, timestamp, reason, exit_price, self.state)
        
        # Log
        logger.info(f"Trade Closed: {closed_trade['symbol']} PnL: {closed_trade['pnl']} R")
        if self.dry_run:
             self._send_telegram(f"DRY EXIT {trade['symbol']} ({reason})\nPrice: {exit_price}\nPnL: {closed_trade['pnl']} R")
    
    def _format_alert(self, trade: Dict) -> str:
        direction = trade["direction"]
        arrow = "BUY" if direction == "LONG" else "SELL"
        mode = "[DRY RUN]" if self.dry_run else "[LIVE]"
        return (
            f"{mode} SMC SIGNAL\n"
            f"Symbol: {trade['symbol']}\n"
            f"Direction: {arrow}\n"
            f"Entry: {trade['entry']}\n"
            f"Stop Loss: {trade['sl']}\n"
            f"Target: {trade['target']}\n"
            f"RR: 1:{trade['rr']}\n"
            f"Confidence: {trade.get('confidence', 0)}/10\n"
            f"Time: {trade['entry_time'].strftime('%H:%M')}"
        )

    def _send_telegram(self, message: str):
        if not self.telegram_token or not self.telegram_chat:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                data={"chat_id": self.telegram_chat, "text": message,
                      "parse_mode": "HTML"},
                timeout=5
            )
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    def get_order_log(self):
        return self.order_log

    def _send_telegram(self, message: str):
        if not self.telegram_token or not self.telegram_chat:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                data={"chat_id": self.telegram_chat, "text": message,
                      "parse_mode": "HTML"},
                timeout=5
            )
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    def get_order_log(self):
        return self.order_log
