"""
trade_executor_bot.py — Telegram Bot for 1-click trade execution on Kite
=========================================================================

Architecture:
─────────────
  1. When engine fires a signal, instead of plain text, it sends the alert
     WITH inline buttons: [Trade Live] [Observe]
  2. User clicks [Trade Live] → bot shows lot-count buttons (3/4/5/6)
  3. User clicks lot count → bot:
     a) Resolves the option tradingsymbol (NIFTY26MAR23800CE, etc.)
     b) Places BUY MARKET order on Kite for qty = lots × lot_size
     c) Places GTT OCO SELL order with SL trigger + Target trigger
     d) Updates the Telegram message with execution status

Telegram inline keyboard flow:
    Signal message  →  [Trade Live ✅]  [Observe 👁]
    → (Trade Live)  →  [3 lots]  [4 lots]  [5 lots]  [6 lots]
    → (3 lots)      →  ✅ ORDER PLACED: NIFTY26MAR23800CE BUY 195 qty
                        ✅ GTT OCO SET: SL 295.25 | TGT 360.90

Runs as a SEPARATE long-running process alongside the engine.
"""

import os, sys, json, time, logging, threading, traceback
from datetime import datetime, date as dt_date, timedelta

import requests
from kiteconnect import KiteConnect
from kite_credentials import API_KEY

# ─── Config ───────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8388602985:AAEiombJFTGv0Dx9UZeeKkpKeo0hem9hv8I")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "-1003268636791")
BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"

LOT_SIZES = {
    "NIFTY":     65,
    "BANKNIFTY": 30,
}

LOT_OPTIONS   = [3, 4, 5, 6]   # user picks from these
PRODUCT       = "NRML"          # NRML for options (overnight), MIS for intraday

# ─── Paper mode ──────────────────────────────────────────────
try:
    from engine.paper_mode import PAPER_MODE
except ImportError:
    PAPER_MODE = False

# ─── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trade_executor_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TradeBot")

# ─── Kite connection ─────────────────────────────────────────
def _resolve_access_token() -> str:
    """Resolve access token: Redis → env → file (same priority as config/kite_auth.py)."""
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            import redis as _redis
            r = _redis.from_url(redis_url, decode_responses=True, socket_timeout=5)
            tok = r.get("kite:access_token")
            if tok and tok.strip():
                return tok.strip()
        except Exception:
            pass
    tok = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if tok:
        return tok
    token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "access_token.txt")
    if os.path.exists(token_path):
        return open(token_path).read().strip()
    return ""


def connect_kite():
    k = KiteConnect(api_key=API_KEY)
    token = _resolve_access_token()
    if not token:
        logger.warning("No Kite access token found — trade execution will fail until token is available")
    else:
        k.set_access_token(token)
        logger.info("Kite connected for trade execution")
    return k

kite = connect_kite()

# ─── NFO instruments cache ────────────────────────────────────
_instruments_cache = None
_instruments_date  = None

def load_instruments():
    global _instruments_cache, _instruments_date
    today = dt_date.today()
    if _instruments_cache and _instruments_date == today:
        return _instruments_cache

    # Try local file first (saved by engine)
    pkl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instruments_nfo.pkl")
    if os.path.exists(pkl_path):
        import pickle
        with open(pkl_path, "rb") as f:
            _instruments_cache = pickle.load(f)
        _instruments_date = today
        logger.info(f"Loaded {len(_instruments_cache)} NFO instruments from pickle")
        return _instruments_cache

    # Fallback: download from Kite API
    try:
        _instruments_cache = kite.instruments("NFO")
        _instruments_date = today
        logger.info(f"Downloaded {len(_instruments_cache)} NFO instruments")
        return _instruments_cache
    except Exception as e:
        logger.error(f"Failed to load instruments: {e}")
        return []


def find_option_tradingsymbol(underlying: str, strike: float, opt_type: str,
                               expiry_hint: str = None):
    """
    Find the NFO tradingsymbol for an option.

    Args:
        underlying: "NIFTY" or "BANKNIFTY"
        strike:     e.g. 23800.0
        opt_type:   "CE" or "PE"
        expiry_hint: optional "2026-03-10" string; if None, picks nearest expiry

    Returns:
        dict with {tradingsymbol, instrument_token, lot_size, expiry, tick_size}
        or None
    """
    instruments = load_instruments()
    if not instruments:
        return None

    # Filter to matching options
    matches = []
    for inst in instruments:
        if (inst.get("name") == underlying
                and inst.get("instrument_type") == opt_type
                and inst.get("strike") == strike
                and inst.get("segment") == "NFO-OPT"):
            matches.append(inst)

    if not matches:
        logger.error(f"No instrument found: {underlying} {strike} {opt_type}")
        return None

    # Pick nearest expiry (or specific one)
    today = dt_date.today()
    if expiry_hint:
        target_exp = dt_date.fromisoformat(expiry_hint) if isinstance(expiry_hint, str) else expiry_hint
        exact = [m for m in matches if m["expiry"] == target_exp]
        if exact:
            return exact[0]

    # Nearest expiry >= today
    future = [m for m in matches if m["expiry"] >= today]
    if not future:
        return matches[-1]  # fallback

    future.sort(key=lambda x: x["expiry"])
    return future[0]


def enrich_signal_with_option(signal: dict) -> dict:
    """
    Enrich a zone-tap signal with real option data.
    Resolves ATM strike, fetches live option LTP, computes option SL/Target.

    Adds to signal dict:
        opt_tradingsymbol, opt_strike, opt_type, opt_ltp,
        opt_sl, opt_target1, opt_target2, opt_expiry, opt_lot_size

    Returns the enriched signal (mutated in-place).
    """
    underlying = signal.get("underlying", "NIFTY")
    direction  = signal.get("direction", "LONG")
    spot       = signal.get("spot") or signal.get("entry", 0)

    # ATM strike
    step = 100
    atm  = round(spot / step) * step
    opt_type = "CE" if direction == "LONG" else "PE"

    inst = find_option_tradingsymbol(underlying, float(atm), opt_type)
    if not inst:
        logger.warning(f"Option enrichment failed: no instrument for {underlying} {atm} {opt_type}")
        return signal

    tsym     = inst["tradingsymbol"]
    lot_size = inst.get("lot_size", LOT_SIZES.get(underlying, 65))
    tick     = inst.get("tick_size", 0.05)
    expiry   = inst.get("expiry")

    # Fetch live option LTP
    try:
        ltp_data = kite.ltp([f"NFO:{tsym}"])
        opt_ltp  = ltp_data[f"NFO:{tsym}"]["last_price"]
    except Exception as e:
        logger.warning(f"Option LTP fetch failed for {tsym}: {e}")
        return signal

    # Map index SL/TP to option price levels using delta ≈ 0.50 for ATM
    sl_spot  = signal.get("sl", 0)
    tp1_spot = signal.get("tp1", 0)
    tp2_spot = signal.get("tp2", 0)
    delta    = 0.50

    if direction == "LONG":
        spot_risk   = spot - sl_spot
        spot_rwd1   = tp1_spot - spot
        spot_rwd2   = tp2_spot - spot
        opt_sl      = max(opt_ltp - spot_risk * delta, tick)
        opt_target1 = opt_ltp + spot_rwd1 * delta
        opt_target2 = opt_ltp + spot_rwd2 * delta
    else:
        spot_risk   = sl_spot - spot
        spot_rwd1   = spot - tp1_spot
        spot_rwd2   = spot - tp2_spot
        opt_sl      = max(opt_ltp - spot_risk * delta, tick)
        opt_target1 = opt_ltp + spot_rwd1 * delta
        opt_target2 = opt_ltp + spot_rwd2 * delta

    # Round to tick
    opt_sl      = round(round(opt_sl / tick) * tick, 2)
    opt_target1 = round(round(opt_target1 / tick) * tick, 2)
    opt_target2 = round(round(opt_target2 / tick) * tick, 2)
    if opt_sl <= 0:
        opt_sl = tick

    # Attach to signal
    signal["opt_tradingsymbol"] = tsym
    signal["opt_strike"]        = atm
    signal["opt_type"]          = opt_type
    signal["opt_ltp"]           = opt_ltp
    signal["opt_sl"]            = opt_sl
    signal["opt_target1"]       = opt_target1
    signal["opt_target2"]       = opt_target2
    signal["opt_expiry"]        = expiry.strftime("%d %b") if expiry else "?"
    signal["opt_lot_size"]      = lot_size

    logger.info(f"Enriched: {tsym} LTP={opt_ltp:.2f} SL={opt_sl:.2f} "
                f"TP1={opt_target1:.2f} TP2={opt_target2:.2f}")
    return signal


# ═══════════════════════════════════════════════════════════════
# SIGNAL STORE  (pending signals waiting for user action)
# ═══════════════════════════════════════════════════════════════
# signal_id → signal dict
_pending_signals = {}
_signal_counter  = 0
_lock = threading.Lock()

def store_signal(signal: dict) -> str:
    """Store a signal and return its unique ID."""
    global _signal_counter
    with _lock:
        _signal_counter += 1
        sig_id = f"sig_{_signal_counter}_{int(time.time())}"
        _pending_signals[sig_id] = signal
    return sig_id


def get_signal(sig_id: str) -> dict:
    return _pending_signals.get(sig_id)


# ═══════════════════════════════════════════════════════════════
# TELEGRAM API HELPERS
# ═══════════════════════════════════════════════════════════════

def send_message_with_buttons(text: str, buttons: list, chat_id=None):
    """
    Send a Telegram message with inline keyboard buttons.

    Args:
        text:    HTML message body
        buttons: list of rows, each row is a list of {text, callback_data}
        chat_id: override chat ID

    Returns:
        message_id (int) or None
    """
    target = chat_id or CHAT_ID
    keyboard = {
        "inline_keyboard": [
            [{"text": btn["text"], "callback_data": btn["callback_data"]}
             for btn in row]
            for row in buttons
        ]
    }
    try:
        resp = requests.post(f"{BASE_URL}/sendMessage", json={
            "chat_id": target,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        }, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        else:
            logger.error(f"Send failed: {data}")
            return None
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return None


def edit_message_text(chat_id, message_id, text, buttons=None):
    """Edit an existing message, optionally update buttons."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if buttons is not None:
        payload["reply_markup"] = json.dumps({
            "inline_keyboard": [
                [{"text": btn["text"], "callback_data": btn["callback_data"]}
                 for btn in row]
                for row in buttons
            ]
        })
    else:
        # Remove keyboard
        payload["reply_markup"] = json.dumps({"inline_keyboard": []})
    try:
        requests.post(f"{BASE_URL}/editMessageText", json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Edit message error: {e}")


def answer_callback(callback_id, text=""):
    """Answer a callback query (dismiss the loading spinner)."""
    try:
        requests.post(f"{BASE_URL}/answerCallbackQuery", json={
            "callback_query_id": callback_id,
            "text": text,
        }, timeout=5)
    except:
        pass


# ═══════════════════════════════════════════════════════════════
# SEND SIGNAL WITH BUTTONS  (called by engine)
# ═══════════════════════════════════════════════════════════════

def send_signal_with_buttons(signal: dict, alert_text: str, chat_id=None):
    """
    Send the signal alert with [Trade Live] and [Observe] buttons.

    Args:
        signal:     the signal dict from zone tap / engine
        alert_text: the formatted HTML alert message
        chat_id:    Telegram chat ID

    Returns:
        message_id
    """
    sig_id = store_signal(signal)

    buttons = [[
        {"text": "✅ Trade Live", "callback_data": f"trade_{sig_id}"},
        {"text": "👁 Observe",    "callback_data": f"observe_{sig_id}"},
    ]]

    msg_id = send_message_with_buttons(alert_text, buttons, chat_id)
    if msg_id and sig_id in _pending_signals:
        _pending_signals[sig_id]["_msg_id"] = msg_id
        _pending_signals[sig_id]["_chat_id"] = chat_id or CHAT_ID

    return msg_id


# ═══════════════════════════════════════════════════════════════
# ORDER EXECUTION
# ═══════════════════════════════════════════════════════════════

def execute_trade(signal: dict, num_lots: int):
    """
    Execute a trade on Kite:
      1. Resolve option tradingsymbol
      2. Place BUY MARKET order
      3. Place GTT OCO (SL + Target)

    Handles TWO signal formats:
      A. Zone Tap signal: has spot, sl, tp1 (index levels) → compute ATM option
      B. OI SC signal: has strike, opt_type, trade_levels (option prices) → use directly

    Returns:
        dict with {success, order_id, gtt_id, tradingsymbol, qty, error}
    """
    underlying = signal.get("underlying", "NIFTY")
    direction  = signal.get("direction", "LONG")
    spot       = signal.get("spot") or signal.get("entry")

    # ── Detect signal type ──
    trade_levels = signal.get("trade_levels")  # OI SC signals have this
    has_direct_option = trade_levels and signal.get("strike") and signal.get("opt_type")
    pre_enriched = signal.get("opt_tradingsymbol")  # Zone Tap signals enriched at scan time

    if pre_enriched:
        # ── Already enriched by enrich_signal_with_option ──
        tsym     = signal["opt_tradingsymbol"]
        strike   = signal["opt_strike"]
        opt_type = signal["opt_type"]
        lot_size = signal.get("opt_lot_size", LOT_SIZES.get(underlying, 65))
        logger.info(f"Pre-enriched signal: {tsym}")
    elif has_direct_option:
        # ── OI SC signal: strike + opt_type already specified ──
        strike   = signal["strike"]
        opt_type = signal["opt_type"]
        logger.info(f"OI SC signal: {underlying} {strike} {opt_type}")
    else:
        # ── Zone Tap signal: compute ATM option from spot direction ──
        step = 100
        atm  = round(spot / step) * step
        if direction == "LONG":
            opt_type = "CE"
            strike   = atm
        else:
            opt_type = "PE"
            strike   = atm
        logger.info(f"Zone Tap signal: {underlying} {strike} {opt_type} (from spot {spot:.0f})")

    # ── Find tradingsymbol (skip if pre-enriched) ──
    if not pre_enriched:
        inst = find_option_tradingsymbol(underlying, float(strike), opt_type)
        if not inst:
            return {"success": False, "error": f"No instrument found: {underlying} {strike} {opt_type}"}
        tsym     = inst["tradingsymbol"]
        lot_size = inst.get("lot_size", LOT_SIZES.get(underlying, 65))

    qty  = num_lots * lot_size
    tick = 0.05

    logger.info(f"Executing: {tsym} BUY {qty} (lots={num_lots}, lot_size={lot_size})")

    # ── Get current option LTP for GTT ──
    try:
        ltp_data = kite.ltp([f"NFO:{tsym}"])
        opt_ltp  = ltp_data[f"NFO:{tsym}"]["last_price"]
    except Exception as e:
        return {"success": False, "error": f"LTP fetch failed: {e}"}

    # ── Compute option SL and target prices ──
    if pre_enriched:
        # Use pre-computed option SL/target from enrichment
        opt_sl     = signal["opt_sl"]
        opt_target = signal["opt_target1"]  # Use TP1 for GTT target
    elif has_direct_option:
        # OI SC: trade_levels already has option-level entry/sl/target
        opt_sl     = trade_levels["sl"]
        opt_target = trade_levels["target"]
    else:
        # Zone Tap: map index spot levels to option price levels
        # ATM delta ≈ 0.50 approximation
        sl_spot    = signal.get("sl")
        tp1_spot   = signal.get("tp1") or signal.get("target")
        delta = 0.50

        if direction == "LONG":
            spot_risk   = spot - sl_spot
            spot_reward = tp1_spot - spot
            opt_sl      = max(opt_ltp - spot_risk * delta, tick)
            opt_target  = opt_ltp + spot_reward * delta
        else:
            spot_risk   = sl_spot - spot
            spot_reward = spot - tp1_spot
            opt_sl      = max(opt_ltp - spot_risk * delta, tick)
            opt_target  = opt_ltp + spot_reward * delta

    # Round to tick size
    opt_sl     = round(round(opt_sl / tick) * tick, 2)
    opt_target = round(round(opt_target / tick) * tick, 2)

    # Safety: SL can't be 0 or negative
    if opt_sl <= 0:
        opt_sl = tick

    result = {
        "tradingsymbol": tsym,
        "qty": qty,
        "opt_ltp": opt_ltp,
        "opt_sl": opt_sl,
        "opt_target": opt_target,
        "strike": strike,
        "opt_type": opt_type,
    }

    # ── 5. Place BUY MARKET order ──
    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=tsym,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            product=PRODUCT,
            order_type=kite.ORDER_TYPE_MARKET,
        )
        result["order_id"] = order_id
        result["success"]  = True
        logger.info(f"BUY order placed: {order_id} | {tsym} qty={qty}")
    except Exception as e:
        result["success"] = False
        result["error"]   = f"BUY order failed: {e}"
        logger.error(f"BUY order failed: {e}")
        return result

    # ── 6. Place GTT OCO order (SL + Target) ──
    try:
        gtt_id = kite.place_gtt(
            trigger_type=kite.GTT_TYPE_OCO,
            tradingsymbol=tsym,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[opt_sl, opt_target],
            last_price=opt_ltp,
            orders=[
                {
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity": qty,
                    "price": opt_sl,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "product": PRODUCT,
                },
                {
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity": qty,
                    "price": opt_target,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "product": PRODUCT,
                }
            ]
        )
        result["gtt_id"] = gtt_id
        logger.info(f"GTT OCO placed: {gtt_id} | SL={opt_sl} TGT={opt_target}")
    except Exception as e:
        result["gtt_error"] = str(e)
        logger.error(f"GTT OCO failed: {e}")

    return result


# ═══════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ═══════════════════════════════════════════════════════════════

def handle_callback(callback_query):
    """
    Process inline button clicks.

    callback_data formats:
        trade_{sig_id}          →  show lot selection
        observe_{sig_id}        →  dismiss buttons, mark as observed
        lots_{sig_id}_{count}   →  execute trade with N lots
    """
    cb_id   = callback_query["id"]
    cb_data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id", CHAT_ID)
    msg_id  = message.get("message_id")
    user    = callback_query.get("from", {}).get("first_name", "User")

    logger.info(f"Callback: {cb_data} from {user}")

    # ── TRADE LIVE → show lot selection ──
    if cb_data.startswith("trade_"):
        sig_id = cb_data[6:]
        signal = get_signal(sig_id)
        if not signal:
            answer_callback(cb_id, "Signal expired!")
            return

        # Show lot buttons
        underlying = signal.get("underlying", "NIFTY")
        lot_size   = signal.get("opt_lot_size", LOT_SIZES.get(underlying, 65))
        opt_sym    = signal.get("opt_tradingsymbol", f"{underlying} ATM {signal.get('opt_type', 'CE/PE')}")
        buttons = [[
            {"text": f"{n} lots ({n * lot_size} qty)",
             "callback_data": f"lots_{sig_id}_{n}"}
            for n in LOT_OPTIONS
        ]]

        # Update message to show lot selection
        orig_text = message.get("text", "")
        new_text  = orig_text + f"\n\n⚡ <b>SELECT LOT COUNT:</b>\n  {opt_sym}  |  1 lot = {lot_size}"
        edit_message_text(chat_id, msg_id, new_text, buttons)
        answer_callback(cb_id, "Select number of lots")
        return

    # ── OBSERVE → dismiss ──
    if cb_data.startswith("observe_"):
        sig_id = cb_data[8:]
        orig_text = message.get("text", "")
        edit_message_text(chat_id, msg_id,
                          orig_text + "\n\n👁 <i>Marked as OBSERVE — no trade placed.</i>")
        answer_callback(cb_id, "Observing")
        return

    # ── LOT SELECTION → execute trade ──
    if cb_data.startswith("lots_"):
        parts  = cb_data.split("_")
        sig_id = f"{parts[1]}_{parts[2]}"  # sig_N_timestamp
        num    = int(parts[3])

        signal = get_signal(sig_id)
        if not signal:
            answer_callback(cb_id, "Signal expired!")
            return

        answer_callback(cb_id, f"Placing order for {num} lots...")

        # Update message: show "placing..."
        orig_text = message.get("text", "")
        # Remove the "SELECT LOT COUNT" line if present
        clean_text = orig_text.split("\n\n⚡")[0]

        # Paper mode — show what would happen but don't place real orders
        if PAPER_MODE:
            underlying = signal.get("underlying", "NIFTY")
            lot_size = signal.get("opt_lot_size", LOT_SIZES.get(underlying, 65))
            qty = num * lot_size
            edit_message_text(chat_id, msg_id,
                              clean_text + f"\n\n📝 <b>[PAPER] Would place:</b>\n"
                              f"  BUY {qty} qty ({num} lots)\n"
                              f"  Entry: {signal.get('entry', '?')}\n"
                              f"  SL: {signal.get('sl', '?')}\n"
                              f"  TP: {signal.get('tp1', '?')}\n"
                              f"  <i>No real order — paper mode</i>")
            return

        edit_message_text(chat_id, msg_id,
                          clean_text + f"\n\n⏳ <b>Placing order... {num} lots</b>")

        # Execute
        result = execute_trade(signal, num)

        if result.get("success"):
            status_lines = [
                f"\n\n✅ <b>ORDER PLACED</b>",
                f"  Symbol: {result['tradingsymbol']}",
                f"  Qty: {result['qty']} ({num} lots)",
                f"  Option LTP: {result['opt_ltp']:.2f}",
                f"  Order ID: {result.get('order_id', 'N/A')}",
            ]
            if result.get("gtt_id"):
                status_lines += [
                    f"\n✅ <b>GTT OCO SET</b>",
                    f"  SL Trigger: {result['opt_sl']:.2f}",
                    f"  Target Trigger: {result['opt_target']:.2f}",
                    f"  GTT ID: {result['gtt_id']}",
                ]
            elif result.get("gtt_error"):
                status_lines += [
                    f"\n⚠️ <b>GTT FAILED</b>: {result['gtt_error']}",
                    f"  ⚠️ Place SL/Target manually!",
                ]
            edit_message_text(chat_id, msg_id,
                              clean_text + "\n".join(status_lines))
        else:
            edit_message_text(chat_id, msg_id,
                              clean_text + f"\n\n❌ <b>ORDER FAILED</b>\n{result.get('error', 'Unknown')}")
        return


# ═══════════════════════════════════════════════════════════════
# POLLING LOOP — listens for button clicks
# ═══════════════════════════════════════════════════════════════

def _refresh_kite_token():
    """Re-resolve the access token from Redis/env/file and update the global kite instance."""
    global kite
    try:
        token = _resolve_access_token()
        if token:
            kite.set_access_token(token)
            logger.info("Kite token refreshed for trade executor")
    except Exception as e:
        logger.warning(f"Kite token refresh failed: {e}")


def poll_updates():
    """Long-poll Telegram for callback_query updates from inline buttons."""
    offset = None
    _last_token_refresh = time.time()
    _TOKEN_REFRESH_INTERVAL = 300  # re-check token every 5 minutes
    logger.info("Trade Executor Bot started — polling for button clicks...")

    while True:
        try:
            now = time.time()
            if now - _last_token_refresh > _TOKEN_REFRESH_INTERVAL:
                _refresh_kite_token()
                _last_token_refresh = now
            params = {"timeout": 30, "allowed_updates": ["callback_query"]}
            if offset:
                params["offset"] = offset

            resp = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
            data = resp.json()

            if not data.get("ok"):
                logger.warning(f"getUpdates failed: {data}")
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                cq = update.get("callback_query")
                if cq:
                    try:
                        handle_callback(cq)
                    except Exception as e:
                        logger.error(f"Callback error: {e}\n{traceback.format_exc()}")

        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"Poll error: {e}")
            time.sleep(5)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 50)
    print("  TRADE EXECUTOR BOT")
    print("  Listening for Telegram button clicks...")
    print("=" * 50)
    poll_updates()
