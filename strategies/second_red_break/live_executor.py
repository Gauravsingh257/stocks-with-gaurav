"""
Second Red Break — Full-Auto Live Executor.

Called by the engine when SRB scanner detects a breakdown signal.
Places BUY MARKET for ATM PUT + GTT OCO (SL + Target) on Kite.
No human approval required — fully automated.

KEY DESIGN: Entry/SL/Target are derived from the OPTION CHART, not delta mapping.
- 2nd red candle on INDEX = 2nd green candle on PUT OPTION
- SL = low of the 2nd green candle on the option chart (natural support)
- Entry = option LTP at breakdown moment
- Target = Entry + 3 × (Entry − SL), giving 1:3 RR on the option itself

NIFTY only. 1 lot per trade. NRML product.
"""

from __future__ import annotations

import logging
from datetime import date as dt_date, datetime, timedelta
from typing import Optional

from .utils import INDEX_CONFIG, get_atm_strike

logger = logging.getLogger("srb_executor")

# ── Config ─────────────────────────────────────────────────────────
NUM_LOTS = 1           # Conservative: 1 lot
PRODUCT = "NRML"       # Overnight-capable (options)
TICK_SIZE = 0.05
MIN_OPTION_RISK = 5.0  # Minimum SL distance on option (₹5) — avoid micro-stops
DELTA_APPROX = 0.50    # Fallback only — used when option chart method fails


def execute_srb_trade(
    signal: dict,
    kite,
    paper_mode: bool = False,
) -> dict:
    """
    Full-auto execution of a Second Red Break PUT trade.

    Uses the OPTION CHART to derive SL/target (2nd green candle low = SL).
    Falls back to delta-mapping if option chart data unavailable.

    Args:
        signal:     SRB signal dict from live_scanner
        kite:       Active KiteConnect instance (from engine)
        paper_mode: If True, skip real orders (log only)

    Returns:
        dict with keys: success, order_id, gtt_id, tradingsymbol, qty,
                        opt_ltp, opt_sl, opt_target, strike, sl_method, error
    """
    instrument = signal.get("srb_instrument", "NIFTY")
    cfg = INDEX_CONFIG.get(instrument)
    if not cfg:
        return {"success": False, "error": f"Unknown instrument: {instrument}"}

    spot = signal["entry"]
    index_sl = signal["sl"]
    index_target = signal["target"]
    step = cfg["step"]
    lot_size = cfg["lot_size"]
    qty = NUM_LOTS * lot_size

    # ── 1. ATM PUT strike selection ────────────────────────────────
    strike = get_atm_strike(spot, step)
    opt_type = "PE"

    logger.info(
        "SRB execute: %s %sPE spot=%.1f entry=%.1f SL=%.1f TGT=%.1f",
        instrument, strike, spot, signal["entry"], index_sl, index_target,
    )

    # ── 2. Find option tradingsymbol ───────────────────────────────
    try:
        from trade_executor_bot import find_option_tradingsymbol
        inst_info = find_option_tradingsymbol(instrument, float(strike), opt_type)
    except ImportError:
        inst_info = _find_option_fallback(kite, instrument, strike, opt_type)

    if not inst_info:
        return {"success": False, "error": f"No option: {instrument} {strike} PE"}

    tsym = inst_info["tradingsymbol"]
    actual_lot = inst_info.get("lot_size", lot_size)
    qty = NUM_LOTS * actual_lot
    opt_token = inst_info.get("instrument_token")

    result = {
        "tradingsymbol": tsym,
        "qty": qty,
        "strike": strike,
        "opt_type": opt_type,
        "instrument": instrument,
    }

    # ── 3. Fetch option LTP ────────────────────────────────────────
    nfo_sym = f"NFO:{tsym}"
    try:
        ltp_data = kite.ltp([nfo_sym])
        opt_ltp = ltp_data[nfo_sym]["last_price"]
        result["opt_ltp"] = opt_ltp
    except Exception as e:
        result["success"] = False
        result["error"] = f"Option LTP fetch failed: {e}"
        logger.error("SRB LTP fetch failed for %s: %s", tsym, e)
        return result

    # ── 4. Derive SL/Target from OPTION CHART ──────────────────────
    # Fetch 5-min candles of the PUT option itself.
    # The 2nd green candle on the option = 2nd red candle on the index.
    # SL = low of the 2nd green candle (natural support).
    # Target = Entry + 3 × risk (3R on the option chart).
    opt_sl, opt_target, sl_method = _derive_levels_from_option_chart(
        kite, nfo_sym, opt_token, opt_ltp, index_sl, spot, index_target,
    )

    # Round to tick
    opt_sl = round(round(opt_sl / TICK_SIZE) * TICK_SIZE, 2)
    opt_target = round(round(opt_target / TICK_SIZE) * TICK_SIZE, 2)

    if opt_sl <= 0:
        opt_sl = TICK_SIZE

    result["opt_sl"] = opt_sl
    result["opt_target"] = opt_target
    result["sl_method"] = sl_method

    logger.info(
        "SRB option: %s LTP=%.2f | opt_SL=%.2f opt_TGT=%.2f | method=%s | qty=%d",
        tsym, opt_ltp, opt_sl, opt_target, sl_method, qty,
    )

    # ── Paper mode: log and return ─────────────────────────────────
    if paper_mode:
        result["success"] = True
        result["order_id"] = "PAPER"
        result["gtt_id"] = "PAPER"
        logger.info("[PAPER] SRB trade logged: %s BUY %d @ %.2f", tsym, qty, opt_ltp)
        return result

    # ── 5. Place BUY MARKET order ──────────────────────────────────
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
        result["success"] = True
        logger.info("SRB BUY order placed: %s | %s qty=%d", order_id, tsym, qty)
    except Exception as e:
        result["success"] = False
        result["error"] = f"BUY order failed: {e}"
        logger.error("SRB BUY order FAILED: %s — %s", tsym, e)
        return result

    # ── 6. Place GTT OCO (SL + Target) ────────────────────────────
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
                },
            ],
        )
        result["gtt_id"] = gtt_id
        logger.info("SRB GTT OCO placed: %s | SL=%.2f TGT=%.2f", gtt_id, opt_sl, opt_target)
    except Exception as e:
        result["gtt_error"] = str(e)
        logger.error("SRB GTT OCO FAILED: %s — %s (order %s still active)", tsym, e, order_id)

    return result


# ────────────────────────────────────────────────────────────────────
# OPTION CHART SL/TARGET DERIVATION
# ────────────────────────────────────────────────────────────────────

def _derive_levels_from_option_chart(
    kite,
    nfo_sym: str,
    opt_token: int | None,
    opt_ltp: float,
    index_sl: float,
    index_entry: float,
    index_target: float,
) -> tuple[float, float, str]:
    """
    Derive SL and target from the PUT option's own 5-min chart.

    Logic:
    - On the index chart, 2nd red candle forms → index breaks down.
    - On the PUT option, this corresponds to 2nd green candle → option breaks UP.
    - SL = low of the 2nd green candle on the option chart (natural support).
    - Entry = current option LTP.
    - Target = Entry + 3 × (Entry − SL) → 3R on the option.

    Falls back to delta-mapping if option candles can't be fetched or
    fewer than 2 green candles found today.

    Returns:
        (opt_sl, opt_target, method_label)
    """
    try:
        opt_candles = _fetch_option_candles(kite, nfo_sym, opt_token)
        if opt_candles:
            sl_from_chart = _find_2nd_green_candle_low(opt_candles)
            if sl_from_chart is not None and sl_from_chart < opt_ltp:
                risk = opt_ltp - sl_from_chart
                if risk >= MIN_OPTION_RISK:
                    target = opt_ltp + 3.0 * risk
                    logger.info(
                        "SRB option-chart SL: 2nd green low=%.2f, risk=%.2f, "
                        "entry=%.2f, target=%.2f (3R)",
                        sl_from_chart, risk, opt_ltp, target,
                    )
                    return sl_from_chart, target, "OPTION_CHART"
                else:
                    logger.info(
                        "SRB option-chart risk too small (%.2f < %.2f), "
                        "falling back to delta",
                        risk, MIN_OPTION_RISK,
                    )
    except Exception as e:
        logger.warning("SRB option-chart derivation failed: %s — falling back to delta", e)

    # ── Fallback: delta approximation ──────────────────────────────
    index_risk = index_sl - index_entry
    index_reward = index_entry - index_target
    opt_sl = max(opt_ltp - index_risk * DELTA_APPROX, TICK_SIZE)
    opt_target = opt_ltp + index_reward * DELTA_APPROX
    return opt_sl, opt_target, "DELTA_FALLBACK"


def _fetch_option_candles(kite, nfo_sym: str, opt_token: int | None) -> list[dict]:
    """
    Fetch today's 5-min candles for the PUT option.
    Uses instrument_token if available, otherwise resolves via LTP.
    """
    # Resolve token if not provided
    if not opt_token:
        try:
            ltp_result = kite.ltp([nfo_sym])
            opt_token = list(ltp_result.values())[0].get("instrument_token")
        except Exception:
            pass

    if not opt_token:
        logger.warning("Cannot fetch option candles: no instrument_token for %s", nfo_sym)
        return []

    try:
        now = datetime.now()
        from_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)
        data = kite.historical_data(
            opt_token,
            from_date=from_dt,
            to_date=now,
            interval="5minute",
        )
        if data:
            logger.info("Fetched %d option candles for %s", len(data), nfo_sym)
        return data or []
    except Exception as e:
        logger.warning("Option candle fetch failed for %s: %s", nfo_sym, e)
        return []


def _find_2nd_green_candle_low(candles: list[dict]) -> float | None:
    """
    Find the LOW of the 2nd green candle in the option's intraday candles.

    A green candle = close > open (option price rising as index falls).
    The 2nd green candle's low is a natural support level on the option chart.

    Returns the low price, or None if fewer than 2 green candles found.
    """
    green_count = 0
    second_green_low = None

    for c in candles:
        if c["close"] > c["open"]:  # Green candle
            green_count += 1
            if green_count == 2:
                second_green_low = c["low"]
                break

    if second_green_low is not None:
        logger.info(
            "Found 2nd green candle on option: low=%.2f (candle %d of %d)",
            second_green_low, green_count, len(candles),
        )
    else:
        logger.info(
            "Only %d green candle(s) found in %d option candles",
            green_count, len(candles),
        )

    return second_green_low


# ────────────────────────────────────────────────────────────────────
# GTT MODIFICATION (STRATEGY 6: FULL TRAIL FROM 3R, 1.5R GAP)
# ────────────────────────────────────────────────────────────────────

SRB_TRAIL_GAP_R = 1.5   # Trail gap: SL sits 1.5R behind current peak
SRB_FAR_TARGET = 50000  # Absurdly high target to keep GTT OCO alive without auto-exit


def modify_srb_gtt(
    kite,
    trade: dict,
    new_trail_sl: float | None = None,
) -> dict:
    """
    Update GTT with a new trailing SL.  Called continuously as peak R grows.

    Strategy 6: After 3R, trail full position.  SL = opt_entry + (peak_R - 1.5) * opt_risk.
    Target is set very far so GTT never auto-exits on the target leg.

    Args:
        kite:         Active KiteConnect instance
        trade:        ACTIVE_TRADES dict for the SRB trade
        new_trail_sl: Pre-computed trailing SL price on the option

    Returns:
        dict with keys: success, new_gtt_id, new_sl, error
    """
    old_gtt_id = trade.get("srb_gtt_id")
    tsym = trade.get("option", "")
    qty = trade.get("srb_qty", 0)
    opt_entry = trade.get("srb_opt_entry", 0)

    if new_trail_sl is None:
        # Fallback: lock minimum 1.5R profit (first trail activation at 3R)
        opt_risk = trade.get("srb_opt_risk", 0)
        new_trail_sl = opt_entry + SRB_TRAIL_GAP_R * opt_risk

    new_sl = round(round(new_trail_sl / TICK_SIZE) * TICK_SIZE, 2)
    far_target = round(round(SRB_FAR_TARGET / TICK_SIZE) * TICK_SIZE, 2)

    result = {"success": False, "new_sl": new_sl}

    # ── 1. Cancel old GTT ──────────────────────────────────────────
    if old_gtt_id:
        try:
            kite.delete_gtt(old_gtt_id)
            logger.info("SRB GTT cancelled: %s", old_gtt_id)
        except Exception as e:
            logger.warning("SRB GTT cancel failed (may already be triggered): %s — %s", old_gtt_id, e)

    # ── 2. Get current option LTP for new GTT ─────────────────────
    nfo_sym = f"NFO:{tsym}"
    try:
        ltp_data = kite.ltp([nfo_sym])
        current_ltp = ltp_data[nfo_sym]["last_price"]
    except Exception as e:
        result["error"] = f"LTP fetch failed: {e}"
        logger.error("SRB trail LTP fetch failed: %s", e)
        return result

    # ── 3. Place new GTT OCO with trailed SL + far target ─────────
    try:
        new_gtt_id = kite.place_gtt(
            trigger_type=kite.GTT_TYPE_OCO,
            tradingsymbol=tsym,
            exchange=kite.EXCHANGE_NFO,
            trigger_values=[new_sl, far_target],
            last_price=current_ltp,
            orders=[
                {
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity": qty,
                    "price": new_sl,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "product": PRODUCT,
                },
                {
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity": qty,
                    "price": far_target,
                    "order_type": kite.ORDER_TYPE_LIMIT,
                    "product": PRODUCT,
                },
            ],
        )
        result["success"] = True
        result["new_gtt_id"] = new_gtt_id
        logger.info(
            "SRB GTT trailed: old=%s → new=%s | SL %.2f → %.2f (far TGT %.0f)",
            old_gtt_id, new_gtt_id, trade.get("srb_opt_sl", 0), new_sl, far_target,
        )
    except Exception as e:
        result["error"] = f"New GTT placement failed: {e}"
        logger.error("SRB trail GTT FAILED: %s — %s", tsym, e)

    return result


# ────────────────────────────────────────────────────────────────────
# FALLBACK INSTRUMENT LOOKUP
# ────────────────────────────────────────────────────────────────────

def _find_option_fallback(kite, underlying: str, strike: float, opt_type: str) -> dict | None:
    """
    Fallback option lookup if trade_executor_bot is not importable.
    Downloads NFO instruments from Kite and finds matching option.
    """
    try:
        instruments = kite.instruments("NFO")
    except Exception as e:
        logger.error("Failed to fetch NFO instruments: %s", e)
        return None

    today = dt_date.today()
    matches = [
        i for i in instruments
        if i.get("name") == underlying
        and i.get("instrument_type") == opt_type
        and i.get("strike") == strike
        and i.get("segment") == "NFO-OPT"
        and i.get("expiry") >= today
    ]
    if not matches:
        return None

    matches.sort(key=lambda x: x["expiry"])
    return matches[0]
