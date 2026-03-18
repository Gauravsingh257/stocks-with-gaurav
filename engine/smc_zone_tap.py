"""
SMC Zone-Tap Monitor — Independent SMC structure alerts for NIFTY / BANKNIFTY.

Scans 5m charts every 5 minutes.  When price taps an Order Block or FVG zone
and shows a rejection candle pattern, fires an IMMEDIATE alert — no dependency
on OI Short Covering or any other module.

This typically fires 20-40 minutes EARLIER than OI-based detection because it
reads price structure directly instead of waiting for OI data to confirm.

Integration:
    Called from smc_mtf_engine_v4.py outer loop at  minute % 5 == 1.
    Uses the engine's fetch_ohlc() for cached candle data.

Example trigger (March 5, 2026 — NIFTY):
    14:20 candle:  O=24531  H=24600  L=24530  C=24591  ← Strong bounce from
    Bullish OB (24534-24566).  Alert fires at 14:26 with LONG entry suggestion.
    OI SC didn't fire until 14:53 — and on the wrong index (BANKNIFTY).
"""

import logging
from datetime import datetime, timedelta, time

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

import smc_detectors as smc

logger = logging.getLogger("smc_zone_tap")

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
SCORE_THRESHOLD = 6          # minimum score to fire alert (raised from 5 to cut marginal signals)
COOLDOWN_MINUTES = 45        # same-zone re-alert cooldown (raised from 30)
MARKET_OPEN = time(9, 20)    # skip opening auction volatility
MARKET_CLOSE = time(15, 16)
MAX_ZONE_ATR_MULT = 2.5      # reject zones wider than 2.5× ATR (gap zones, etc.)

UNDERLYING_MAP = {
    "NSE:NIFTY 50":   "NIFTY",
    "NSE:NIFTY BANK": "BANKNIFTY",
}

# ═══════════════════════════════════════════════════════════════
# STATE  (module-level, reset daily)
# ═══════════════════════════════════════════════════════════════
# Cooldowns tracked per (underlying, zone_key) so that different zones firing
# within the same underlying don't clobber each other's cooldown timers.
# zone_key = f"{direction}_{round(zone_midpoint)}" — tolerates tiny float drift.
_state = {}  # {underlying: {last_candle_ts, zone_cooldowns: {zone_key: datetime}}}


def _zone_key(direction, zone):
    """Stable string key for a zone — rounded midpoint avoids float drift."""
    mid = round((zone[0] + zone[1]) / 2)
    return f"{direction}_{mid}"


def _get_state(underlying):
    if underlying not in _state:
        _state[underlying] = {
            "last_candle_ts": None,
            "zone_cooldowns": {},   # zone_key -> last_alert_time
        }
    return _state[underlying]


def reset_state():
    """Reset all state — call at start of each trading day."""
    _state.clear()


# ═══════════════════════════════════════════════════════════════
# REJECTION CANDLE DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_rejection_candle(candle, prev_candle, direction):
    """
    Check if *candle* shows a rejection pattern suitable for a zone-tap entry.

    Args:
        candle:      dict with open/high/low/close
        prev_candle: dict with open/high/low/close  (for engulfing check)
        direction:   "LONG" or "SHORT"

    Returns:
        (is_rejection: bool, pattern_name: str, strength: "STRONG"|"MODERATE"|"")
    """
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    body = abs(c - o)
    total = h - l
    if total == 0:
        return False, "", ""

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    is_bull = c > o
    is_bear = c < o

    if direction == "LONG":
        # ── 1. Hammer ──
        # Long lower wick, close in upper portion
        if lower_wick > 2 * body and is_bull and (h - c) < 0.4 * total:
            strength = "STRONG" if lower_wick > 3 * body else "MODERATE"
            return True, "Hammer", strength

        # ── 2. Strong Bullish Bounce ──
        # Big body (>50% of range), close near high
        if is_bull and body > 0.5 * total and (h - c) < 0.15 * total:
            return True, "Strong Bullish Bounce", "STRONG"

        # ── 3. Bullish Engulfing ──
        if prev_candle and is_bull:
            po, pc = prev_candle["open"], prev_candle["close"]
            if pc < po and c > po and o <= pc:  # prev bearish, current engulfs
                return True, "Bullish Engulfing", "STRONG"

        # ── 4. Moderate Bullish ──
        # Decent body pointing up, close above open
        if is_bull and body > 0.4 * total:
            return True, "Bullish Bounce", "MODERATE"

    elif direction == "SHORT":
        # ── 1. Shooting Star ──
        if upper_wick > 2 * body and is_bear and (c - l) < 0.4 * total:
            strength = "STRONG" if upper_wick > 3 * body else "MODERATE"
            return True, "Shooting Star", strength

        # ── 2. Strong Bearish Rejection ──
        if is_bear and body > 0.5 * total and (c - l) < 0.15 * total:
            return True, "Strong Bearish Rejection", "STRONG"

        # ── 3. Bearish Engulfing ──
        if prev_candle and is_bear:
            po, pc = prev_candle["open"], prev_candle["close"]
            if pc > po and c < po and o >= pc:
                return True, "Bearish Engulfing", "STRONG"

        # ── 4. Moderate Bearish ──
        if is_bear and body > 0.4 * total:
            return True, "Bearish Rejection", "MODERATE"

    return False, "", ""


# ═══════════════════════════════════════════════════════════════
# ZONE TAP DETECTION
# ═══════════════════════════════════════════════════════════════

def _zone_touched(candle, zone, direction, atr=0):
    """
    Check if a candle's wick / body entered a zone.

    For LONG (bullish zone — demand):  candle low should reach into the zone.
    For SHORT (bearish zone — supply): candle high should reach into the zone.

    Allows liquidity sweeps: price can wick *through* the zone (e.g. below a
    bullish OB) as long as the close recovers back above the zone and the
    sweep distance doesn't exceed max(ATR, zone_range).
    """
    if not zone:
        return False

    zone_low, zone_high = zone
    zone_range = zone_high - zone_low
    buf = zone_range * 0.3                             # "near zone" buffer
    sweep_buf = max(atr, zone_range) if atr > 0 else zone_range  # sweep allowance

    if direction == "LONG":
        wick_reached = candle["low"] <= zone_high + buf       # wick touched zone
        not_too_far  = candle["low"] >= zone_low - sweep_buf  # sweep not excessive
        close_held   = candle["close"] >= zone_low            # close inside/above zone
        return wick_reached and not_too_far and close_held

    if direction == "SHORT":
        wick_reached = candle["high"] >= zone_low - buf
        not_too_far  = candle["high"] <= zone_high + sweep_buf
        close_held   = candle["close"] <= zone_high
        return wick_reached and not_too_far and close_held

    return False


# ═══════════════════════════════════════════════════════════════
# MAIN SCANNER
# ═══════════════════════════════════════════════════════════════

def scan_zone_taps(symbol, candles, spot, *, now_override=None):
    """
    Scan the latest completed 5m candle for OB / FVG zone taps with rejection.

    Args:
        symbol:       e.g. "NSE:NIFTY 50"
        candles:      list of 5m candle dicts (from fetch_ohlc)
        spot:         current spot price (float)
        now_override: datetime for replay/backtest (uses real time if None)

    Returns:
        list of signal dicts  (usually 0 or 1 per call)
    """
    underlying = UNDERLYING_MAP.get(symbol, symbol)
    now = now_override or datetime.now(_IST)

    # ── time window ──
    t_now = now.time()
    if not (MARKET_OPEN <= t_now <= MARKET_CLOSE):
        return []

    if not candles or len(candles) < 30:
        return []

    # ── find the last COMPLETED candle ──
    # Kite's API may return the currently-forming candle.  We need the
    # last one whose 5-minute window has fully elapsed.
    tap_idx = len(candles) - 1
    for i in range(len(candles) - 1, max(len(candles) - 4, -1), -1):
        c_ts = candles[i].get("date")
        if c_ts and hasattr(c_ts, "replace"):
            c_ts = c_ts.replace(tzinfo=None)
        if c_ts and c_ts + timedelta(minutes=5) <= now + timedelta(seconds=30):
            tap_idx = i
            break
    else:
        # Fallback: use candles[-2] (safe default)
        tap_idx = len(candles) - 2
        if tap_idx < 0:
            return []

    tap_candle = candles[tap_idx]
    prev_candle = candles[tap_idx - 1] if tap_idx > 0 else None

    # ── de-duplicate: skip if we already processed this candle ──
    st = _get_state(underlying)
    candle_ts = tap_candle.get("date")
    if candle_ts and hasattr(candle_ts, "replace"):
        candle_ts = candle_ts.replace(tzinfo=None)
    if st["last_candle_ts"] and candle_ts == st["last_candle_ts"]:
        return []
    st["last_candle_ts"] = candle_ts
    # clean up expired cooldowns to avoid memory growth over a session
    now_check = now_override or datetime.now(_IST)
    st["zone_cooldowns"] = {
        k: v for k, v in st["zone_cooldowns"].items()
        if (now_check - v).total_seconds() < COOLDOWN_MINUTES * 60
    }

    # ── detect structure using candles up to (and including) the tap candle ──
    analysis_candles = candles[:tap_idx + 1]
    # Filter to today's candles only for bias — multi-day data causes
    # detect_htf_bias to see yesterday's swing structure, returning wrong bias
    today_candles = [c for c in analysis_candles
                     if c["date"].date() == now.date()]
    if len(today_candles) >= 10:
        bias = smc.get_ltf_structure_bias(today_candles)
        # Normalize BULLISH/BEARISH → LONG/SHORT
        if bias == "BULLISH": bias = "LONG"
        elif bias == "BEARISH": bias = "SHORT"
    else:
        bias = smc.detect_htf_bias(analysis_candles)
    ob_long = smc.detect_order_block(analysis_candles, "LONG")
    ob_short = smc.detect_order_block(analysis_candles, "SHORT")
    fvg_long = smc.detect_fvg(analysis_candles, "LONG")
    fvg_short = smc.detect_fvg(analysis_candles, "SHORT")
    atr = smc.calculate_atr(analysis_candles)

    if atr <= 0:
        return []

    signals = []

    import threading
    def _start_1m_scan(zone_type, zone, direction):
        zone_key = _zone_key(direction, zone)
        if st["active_1m_scans"].get(zone_key):
            return
        st["active_1m_scans"][zone_key] = True
        def _scan():
            import time as _time
            from engine import fetch_ohlc
            print(f"[ZONE TAP] Starting 1m scan for {underlying} {zone_type} {direction} {zone}")
            for _ in range(20):  # scan for up to 20 minutes
                candles_1m = fetch_ohlc(symbol, "1minute", 30)
                if not candles_1m or len(candles_1m) < 5:
                    _time.sleep(60)
                    continue
                for c in candles_1m[-5:]:
                    # Check if candle tapped the zone
                    if _zone_touched(c, zone, direction):
                        # Fire alert with entry = candle close
                        sig = {
                            "signal_type": "SMC_ZONE_TAP",
                            "underlying": underlying,
                            "symbol": symbol,
                            "direction": direction,
                            "zone_type": zone_type,
                            "zone": zone,
                            "timeframe": "1m",
                            "entry": round(c["close"], 1),
                            "candle_close": round(c["close"], 1),
                            "sl": None,  # will be filled by main engine
                            "tp1": None,
                            "tp2": None,
                            "spot": round(c["close"], 1),
                            "atr": None,
                            "pattern": "1m tap",
                            "strength": "",
                            "bias": "",
                            "score": 0,
                            "reasons": ["1m tap after 5m zone detected"],
                            "candle_time": c.get("date"),
                            "timestamp": datetime.now(),
                        }
                        # Only fire once per zone per direction
                        if not st["zone_cooldowns"].get(zone_key):
                            from smc_mtf_engine_v4 import handle_zone_tap_signal
                            handle_zone_tap_signal(sig)
                            st["zone_cooldowns"][zone_key] = datetime.now()
                        st["active_1m_scans"][zone_key] = False
                        return
                _time.sleep(60)
            st["active_1m_scans"][zone_key] = False
        threading.Thread(target=_scan, daemon=True).start()

    # ── Check LONG setups (bullish zones) ──
    for zone_type, zone in [("Bullish OB", ob_long), ("Bullish FVG", fvg_long)]:
        sig = _evaluate_tap(
            underlying, symbol, zone_type, zone, "LONG",
            tap_candle, prev_candle, candles, bias, spot, atr, now, st,
            ob_long=ob_long, ob_short=ob_short,
            fvg_long=fvg_long, fvg_short=fvg_short,
        )
        if sig:
            signals.append(sig)
            _start_1m_scan(zone_type, zone, "LONG")
            break  # one signal per direction per scan

    # ── Check SHORT setups (bearish zones) — only if no LONG signal ──
    if not signals:
        for zone_type, zone in [("Bearish OB", ob_short), ("Bearish FVG", fvg_short)]:
            sig = _evaluate_tap(
                underlying, symbol, zone_type, zone, "SHORT",
                tap_candle, prev_candle, candles, bias, spot, atr, now, st,
                ob_long=ob_long, ob_short=ob_short,
                fvg_long=fvg_long, fvg_short=fvg_short,
            )
            if sig:
                signals.append(sig)
                _start_1m_scan(zone_type, zone, "SHORT")
                break

    return signals


def _evaluate_tap(underlying, symbol, zone_type, zone, direction,
                  tap_candle, prev_candle, candles, bias, spot, atr, now, st,
                  *, ob_long, ob_short, fvg_long, fvg_short):
    """
    Evaluate whether *tap_candle* qualifies as a zone-tap entry.
    Returns signal dict or None.
    """
    if not zone:
        return None

    # ── 1. Did candle touch the zone? ──
    if not _zone_touched(tap_candle, zone, direction, atr=atr):
        return None

    # ── 1b. Zone width sanity check — reject overnight gaps / absurdly wide zones ──
    zone_width = zone[1] - zone[0]
    if atr > 0 and zone_width > MAX_ZONE_ATR_MULT * atr:
        logger.debug(
            f"Zone rejected (too wide): {underlying} {zone_type} "
            f"width={zone_width:.0f} > {MAX_ZONE_ATR_MULT}×ATR={MAX_ZONE_ATR_MULT * atr:.0f}"
        )
        return None

    # ── 2. Rejection candle? ──
    is_rej, pattern, strength = detect_rejection_candle(
        tap_candle, prev_candle, direction
    )
    if not is_rej:
        return None

    # ── 3. Cooldown: per-zone, per-direction tracking ──
    zk = _zone_key(direction, zone)
    last_fire = st["zone_cooldowns"].get(zk)
    if last_fire:
        elapsed = (now - last_fire).total_seconds()
        if elapsed < COOLDOWN_MINUTES * 60:
            logger.debug(
                f"Cooldown active: {underlying} {direction} {zone_type} "
                f"({elapsed/60:.0f}/{COOLDOWN_MINUTES} min)"
            )
            return None

    # ── 4. Score the setup ──
    score = 0
    reasons = []

    # Zone tap
    is_ob = "OB" in zone_type
    zone_pts = 3 if is_ob else 2
    score += zone_pts
    reasons.append(f"Zone tap: {zone_type} ({zone[0]:.1f} – {zone[1]:.1f})")

    # Rejection quality
    rej_pts = 2 if strength == "STRONG" else 1
    score += rej_pts
    reasons.append(f"{'Strong' if strength == 'STRONG' else 'Moderate'} rejection: {pattern}")

    # Bias alignment
    # Note: counter-trend penalty is REDUCED for strong OB rejections because
    # OB + strong rejection IS how reversals start — the bias changes AFTER
    # the bounce confirms.
    aligned_bias = "LONG" if direction == "LONG" else "SHORT"
    counter_bias = "SHORT" if direction == "LONG" else "LONG"
    if bias == aligned_bias:
        score += 2
        reasons.append(f"Bias: {bias} ✓ (aligned)")
    elif bias == counter_bias:
        if is_ob and strength == "STRONG":
            # Strong OB rejection = potential reversal, don't penalize
            reasons.append(f"Bias: {bias} ↔ (counter, but strong OB reversal)")
        else:
            score -= 1
            reasons.append(f"Bias: {bias} ✗ (counter-trend)")
    else:
        reasons.append("Bias: UNCLEAR")

    # Multi-candle zone hold: if 2+ of the last 3 candles BEFORE the tap
    # candle wicked into or held near the zone, confidence is higher.
    hold_count = 0
    check_candles = candles[max(0, len(candles) - 5): -1]  # 3-4 candles before tap
    for cc in check_candles:
        if _zone_touched(cc, zone, direction, atr=atr):
            hold_count += 1
    if hold_count >= 2:
        score += 1
        reasons.append(f"Zone hold: {hold_count} candles tested zone ✓")

    # Discount / Premium zone
    try:
        if direction == "LONG" and smc.is_discount_zone(candles, spot):
            score += 1
            reasons.append("Discount zone ✓")
        elif direction == "SHORT" and smc.is_premium_zone(candles, spot):
            score += 1
            reasons.append("Premium zone ✓")
    except Exception:
        pass

    # ── 5. Threshold check ──
    if score < SCORE_THRESHOLD:
        logger.debug(
            f"Zone tap below threshold: {underlying} {direction} "
            f"score={score}/{SCORE_THRESHOLD} — {'; '.join(reasons)}"
        )
        return None

    # ── 6. Build signal ──
    # Use live spot price as entry (not stale candle close — alert fires 1-5
    # minutes after candle closed, so spot is the actual tradeable price).
    entry = spot
    sl, tp1, tp2 = _calc_levels(direction, zone, entry, atr)

    sig = {
        "signal_type": "SMC_ZONE_TAP",
        "underlying": underlying,
        "symbol": symbol,
        "direction": direction,
        "zone_type": zone_type,
        "zone": zone,
        "timeframe": "5m",
        "entry": round(entry, 1),
        "candle_close": round(tap_candle["close"], 1),  # reference: candle close price
        "sl": round(sl, 1),
        "tp1": round(tp1, 1),
        "tp2": round(tp2, 1),
        "spot": round(spot, 1),
        "atr": round(atr, 1),
        "pattern": pattern,
        "strength": strength,
        "bias": bias,
        "score": score,
        "reasons": reasons,
        "candle_time": tap_candle.get("date"),
        "timestamp": now,
        "structure": {
            "ob_long": ob_long,
            "ob_short": ob_short,
            "fvg_long": fvg_long,
            "fvg_short": fvg_short,
        },
    }

    # update per-zone cooldown
    st["zone_cooldowns"][_zone_key(direction, zone)] = now

    logger.info(
        f"🎯 ZONE TAP: {underlying} {direction} @ {zone_type} "
        f"({zone[0]:.1f}–{zone[1]:.1f}) | {pattern} | score {score} | "
        f"Entry {entry:.1f}  SL {sl:.1f}  TP1 {tp1:.1f}  TP2 {tp2:.1f}"
    )

    return sig


# ═══════════════════════════════════════════════════════════════
# SL / TP CALCULATION
# ═══════════════════════════════════════════════════════════════

def _calc_levels(direction, zone, entry, atr):
    """
    Entry ≈ candle close.
    SL   = just beyond the zone (zone_low − 0.3×ATR for LONG).
    TP1  = 1.5 × risk.
    TP2  = 2.5 × risk.

    Minimum risk = 0.5×ATR to avoid noise-level SL on tight zones.
    """
    buf = 0.3 * atr
    min_risk = 0.5 * atr

    if direction == "LONG":
        sl = zone[0] - buf
        risk = entry - sl
        if risk < min_risk:
            sl = entry - min_risk
            risk = min_risk
        tp1 = entry + 1.5 * risk
        tp2 = entry + 2.5 * risk
    else:  # SHORT
        sl = zone[1] + buf
        risk = sl - entry
        if risk < min_risk:
            sl = entry + min_risk
            risk = min_risk
        tp1 = entry - 1.5 * risk
        tp2 = entry - 2.5 * risk

    return sl, tp1, tp2


# ═══════════════════════════════════════════════════════════════
# TELEGRAM FORMATTER
# ═══════════════════════════════════════════════════════════════

def format_zone_tap_alert(sig):
    """
    Build the Telegram message (HTML parse mode).
    """
    d = sig
    direction = d["direction"]
    arrow = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    tf = d["timeframe"]

    # ── Zone lines ──
    zone_lines = []
    st = d["structure"]
    tapped_zone = d["zone"]

    for label, key in [("📗 Bullish OB", "ob_long"),
                       ("📕 Bearish OB", "ob_short"),
                       ("📗 Bullish FVG", "fvg_long"),
                       ("📕 Bearish FVG", "fvg_short")]:
        z = st.get(key)
        if z:
            tag = " ← TAPPED" if z == tapped_zone else ""
            zone_lines.append(f"  {label} ({tf}): {z[0]:.1f} – {z[1]:.1f}{tag}")

    zones_text = "\n".join(zone_lines) if zone_lines else "  —"

    # ── Reasons ──
    reasons_text = "\n".join(f"  • {r}" for r in d["reasons"])

    # ── Candle time ──
    ct = d.get("candle_time")
    if ct and hasattr(ct, "strftime"):
        ct_str = ct.strftime("%H:%M")
    else:
        ct_str = str(ct) if ct else "?"

    msg = (
        f"🎯 <b>SMC ZONE TAP — {arrow}</b>\n"
        f"{'═' * 30}\n\n"
        f"<b>{d['underlying']}</b>  |  {tf} chart\n"
        f"📊 Spot: {d['spot']:.0f}  |  ATR: {d['atr']:.0f}\n\n"
        f"<b>Zone:</b> {d['zone_type']} ({tf})\n"
        f"  {d['zone'][0]:.1f} – {d['zone'][1]:.1f}\n"
        f"<b>Pattern:</b> {d['pattern']} ({d['strength']})\n"
        f"<b>Bias:</b> {d['bias'] or 'UNCLEAR'}\n"
        f"<b>Score:</b> {d['score']}/{SCORE_THRESHOLD + 3}\n\n"
        f"📝 <b>ACTION</b>\n"
        f"  Entry:  {d['entry']:.0f}  (live spot)\n"
        f"  Candle: {d['candle_close']:.0f}  (closed {ct_str})\n"
        f"  SL:     {d['sl']:.0f}\n"
        f"  TP1:    {d['tp1']:.0f}  (1.5 RR)\n"
        f"  TP2:    {d['tp2']:.0f}  (2.5 RR)\n\n"
        f"<b>Active Zones ({tf}):</b>\n"
        f"{zones_text}\n\n"
        f"<b>Why:</b>\n"
        f"{reasons_text}\n\n"
        f"<i>Candle: {ct_str}  |  Open {tf} chart to verify.</i>\n"
        f"Time: {d['timestamp'].strftime('%H:%M:%S')}"
    )
    return msg
