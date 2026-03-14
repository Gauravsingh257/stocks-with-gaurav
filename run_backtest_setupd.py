"""
run_backtest_setupd.py
=====================================================================
Dedicated backtest for the UPGRADED Setup-D pipeline
(8-phase: BOS stage, liquidity sweep bypass, 4h expiry, v2 scorer)

Instruments : NSE:NIFTY 50  +  NSE:NIFTY BANK
Period       : 2026-01-01 → 2026-03-04 (today)
Logic        : mirrors the live detect_setup_d() + smc_confluence_score_setup_d()
               implemented in smc_mtf_engine_v4.py (8-phase upgrade)

Run:
    python run_backtest_setupd.py
=====================================================================
"""

import os
import sys
import csv
import logging
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, time, timedelta
from typing import Optional, List, Dict, Tuple

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Kite ──────────────────────────────────────────────────────────────────
from kite_credentials import API_KEY
from kiteconnect import KiteConnect

token_path = os.path.join(os.path.dirname(__file__), "access_token.txt")
with open(token_path) as f:
    ACCESS_TOKEN = f.read().strip()

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)
try:
    ltp = kite.ltp("NSE:NIFTY 50")
    print(f"[OK] Kite connected — NIFTY LTP: {ltp['NSE:NIFTY 50']['last_price']}")
except Exception as e:
    print(f"[FAIL] Kite: {e}")
    sys.exit(1)

# ─── Detectors ─────────────────────────────────────────────────────────────
import smc_detectors as smc

# ─── Local copies of engine helpers ────────────────────────────────────────
try:
    from engine.liquidity_engine import detect_liquidity_sweep as _ext_sweep
    HAS_LIQUIDITY_ENGINE = True
except ImportError:
    HAS_LIQUIDITY_ENGINE = False


def _calculate_atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    recent = trs[-period:]
    return sum(recent) / len(recent) if recent else 0.0


def _liquidity_sweep_detected(candles: list, lookback: int = 15) -> bool:
    """Mirrors smc_detectors.liquidity_sweep_detected used in Phase 4 bypass."""
    try:
        return bool(smc.liquidity_sweep_detected(candles, lookback=lookback))
    except Exception:
        pass
    # Fallback: use engine/liquidity_engine if available
    if HAS_LIQUIDITY_ENGINE:
        try:
            return _ext_sweep(candles, lookback=lookback) is not None
        except Exception:
            pass
    return False


def _is_discount_zone(htf: list, price: float) -> bool:
    try:
        return bool(smc.is_discount_zone(htf, price))
    except Exception:
        return False


def _is_premium_zone(htf: list, price: float) -> bool:
    try:
        return bool(smc.is_premium_zone(htf, price))
    except Exception:
        return False


def _volume_expansion(candles: list) -> bool:
    if len(candles) < 21:
        return False
    vols = [c.get("volume", 0) for c in candles[-21:]]
    avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
    return vols[-1] > avg * 1.2 if avg > 0 else False


# ─── Phase 6: v2 confluence scorer (mirrored from engine) ─────────────────
def confluence_score_setup_d(signal: dict, ltf: list, htf: list) -> Tuple[int, dict]:
    """
    Phase 6 Setup-D scorer:
      sweep(2), choch(1), bos(2), ob(2), fvg(1), zone(1), volume(1) = max 10
    """
    score = 0
    bd: Dict[str, int] = {}

    direction = signal.get("direction", "")
    entry = signal.get("entry", 0)

    # 1. Sweep
    if signal.get("sweep_detected"):
        score += 2; bd["sweep"] = 2
    elif _liquidity_sweep_detected(ltf):
        score += 1; bd["sweep"] = 1
    else:
        bd["sweep"] = 0

    # 2. CHoCH (always 1 for Setup-D — it is the entry gate)
    score += 1; bd["choch"] = 1

    # 3. BOS
    if signal.get("bos_confirmed"):
        score += 2; bd["bos"] = 2
    else:
        bd["bos"] = 0

    # 4. OB
    if signal.get("ob"):
        score += 2; bd["ob"] = 2
    else:
        bd["ob"] = 0

    # 5. FVG
    if signal.get("fvg"):
        score += 1; bd["fvg"] = 1
    else:
        bd["fvg"] = 0

    # 6. Zone
    if direction == "LONG" and _is_discount_zone(htf, entry):
        score += 1; bd["zone"] = 1
    elif direction == "SHORT" and _is_premium_zone(htf, entry):
        score += 1; bd["zone"] = 1
    else:
        bd["zone"] = 0

    # 7. Volume
    if _volume_expansion(ltf):
        score += 1; bd["volume"] = 1
    else:
        bd["volume"] = 0

    return score, bd


# ─── Setup-D state machine (Phase 2: BOS_WAIT stage) ─────────────────────
ATR_BUFFER_MULT = 0.1   # grid search optimal
RR = 2.0
EXPIRY_SECS = 14400     # 4 hours for index
MIN_SCORE = 6

def run_setupd_backtest(
    symbol: str,
    candles_5m: list,
    candles_1h: list,
) -> List[dict]:
    """
    Stateful candle-by-candle Setup-D simulation.

    State stages: BOS_WAIT → WAIT → TAPPED → SIGNAL
    """
    trades: List[dict] = []
    state: Optional[dict] = None
    trade_id = 0

    # Open trades list: {entry, sl, target, direction, entry_time, ...}
    open_trade: Optional[dict] = None

    # Resample 5m → 1h on the fly
    htf_window: List[dict] = list(candles_1h)  # pre-fetched 1h

    # Daily caps
    daily_counts: Dict[str, int] = defaultdict(int)
    daily_pnl: Dict[str, float] = defaultdict(float)

    for i in range(50, len(candles_5m)):
        candle = candles_5m[i]
        ts = _strip_tz(candle["date"])
        ts_str = ts.isoformat()
        day = ts_str[:10]
        ctime = ts.time()

        # ── Skip non-trading hours ──────────────────────────────────────
        if not (time(9, 20) <= ctime <= time(15, 0)):
            continue

        # ── Daily circuit breaker ───────────────────────────────────────
        if daily_pnl[day] <= -3.0:
            continue

        # ── EOD close of open trade ─────────────────────────────────────
        if ctime >= time(15, 0) and open_trade:
            _close_open(open_trade, candle["close"], ts_str, "EOD", trades, daily_pnl)
            open_trade = None
            state = None
            continue

        ltf = candles_5m[max(0, i - 200): i + 1]

        # Build HTF window from 1h candles up to this timestamp
        htf = [c for c in candles_1h if _strip_tz(c["date"]) <= ts]
        htf = htf[-100:]

        # ── Manage open trade (SL/TP) ──────────────────────────────────
        if open_trade:
            h, l = candle["high"], candle["low"]
            hit = False
            if open_trade["direction"] == "LONG":
                if l <= open_trade["sl"]:
                    _close_open(open_trade, open_trade["sl"], ts_str, "SL", trades, daily_pnl)
                    hit = True
                elif h >= open_trade["target"]:
                    _close_open(open_trade, open_trade["target"], ts_str, "TP", trades, daily_pnl)
                    hit = True
            else:
                if h >= open_trade["sl"]:
                    _close_open(open_trade, open_trade["sl"], ts_str, "SL", trades, daily_pnl)
                    hit = True
                elif l <= open_trade["target"]:
                    _close_open(open_trade, open_trade["target"], ts_str, "TP", trades, daily_pnl)
                    hit = True
            if hit:
                open_trade = None
                # Keep state for potential next setup (different from live; state already cleared at signal)

        # One trade per day per symbol cap
        if daily_counts[day] >= 1:
            continue

        # ─────────────── STATE MACHINE ────────────────────────────────
        if state is None:
            # ─ Step 1: Detect CHoCH ────────────────────────────────────
            # On significant gap days, yesterday's swing highs/lows pollute the
            # 30-bar lookback window making CHoCH undetectable. Use same-day bars
            # with the dedicated opening-gap CHoCH detector.
            _today_bars = [c for c in ltf if _strip_tz(c["date"]).date() == ts.date()]
            _prev_bars   = [c for c in ltf if _strip_tz(c["date"]).date() < ts.date()]
            _is_gap_day  = False
            _today_start_in_ltf = len(ltf) - len(_today_bars)  # offset for idx remapping

            if _today_bars and _prev_bars:
                _prev_close = _prev_bars[-1]["close"]
                _today_open = _today_bars[0]["open"]
                _gap_pct    = abs(_today_open - _prev_close) / _prev_close * 100
                _is_gap_day = _gap_pct > 0.3  # >0.3% gap triggers dedicated scan

            if _is_gap_day and len(_today_bars) >= 10:
                choch_raw = smc.detect_choch_opening_gap(_today_bars)
                if choch_raw:
                    # remap idx from _today_bars coordinates → ltf coordinates
                    choch = (choch_raw[0], _today_start_in_ltf + choch_raw[1])
                else:
                    choch = None
            elif _is_gap_day:
                # Not enough same-day bars yet — skip to avoid cross-day CHoCH noise
                continue
            else:
                choch = smc.detect_choch_setup_d(ltf)
            if not choch:
                continue

            direction, idx = choch

            # Phase 4: liquidity sweep → bypass HTF filter
            # On gap days the gap itself IS a liquidity sweep (macro stop-hunt of prior lows/highs)
            sweep = _liquidity_sweep_detected(ltf[:idx + 1], lookback=15)
            if _is_gap_day and not sweep:
                sweep = True  # gap = sweep; override so HTF counter-trend filter is bypassed
            htf_bias = smc.detect_htf_bias(htf) if htf else None
            if htf_bias and direction != htf_bias and not sweep:
                continue  # counter-trend with no sweep → skip

            # Displacement check — skipped for gap days (the gap itself IS the displacement)
            if not _is_gap_day:
                if idx < 10:
                    continue
                avg_range = sum(c["high"] - c["low"] for c in ltf[max(0, idx - 10):idx]) / max(1, min(10, idx))
                disp = ltf[idx]
                if avg_range > 0 and (disp["high"] - disp["low"]) < avg_range * 1.2:
                    continue

            state = {
                "bias": direction,
                "stage": "BOS_WAIT",
                "sweep_detected": sweep,
                "choch_time": ts,
                "time": ts,
                "choch_level": ltf[idx]["close"],  # used for gap-day BOS
                "is_gap_day": _is_gap_day,
                "ob": None,
                "fvg": None,
                "bos_confirmed": False,
            }
            continue

        # ─ Expiry check (4h) ───────────────────────────────────────────
        if (ts - state["time"]).total_seconds() > EXPIRY_SECS:
            state = None
            continue

        bias = state["bias"]

        # ─ Step 2: BOS confirmation ─────────────────────────────────────
        if state["stage"] == "BOS_WAIT":
            lookback_bars = ltf[-7:-1]
            if len(lookback_bars) < 5:
                continue
            recent_high = max(c["high"] for c in lookback_bars)
            recent_low  = min(c["low"]  for c in lookback_bars)

            bos = False
            # Gap-day BOS: use CHoCH close level (structural break already happened)
            # instead of the noisy max-of-6-bars check which fires too late
            if state.get("is_gap_day") and state.get("choch_level", 0) > 0:
                cl = state["choch_level"]
                if bias == "LONG"  and candle["close"] > cl * 1.001:
                    bos = True
                elif bias == "SHORT" and candle["close"] < cl * 0.999:
                    bos = True

            if not bos:
                if bias == "LONG"  and candle["close"] > recent_high:
                    bos = True
                elif bias == "SHORT" and candle["close"] < recent_low:
                    bos = True

            if bos:
                ob = smc.detect_order_block(ltf, bias)
                fvg = smc.detect_fvg(ltf, bias)
                if ob and fvg:
                    state["ob"] = ob
                    state["fvg"] = fvg
                    state["stage"] = "WAIT"
                    state["bos_confirmed"] = True
            continue

        # Guard: need ob + fvg before WAIT/TAPPED
        if not state.get("ob") or not state.get("fvg"):
            state = None
            continue

        z_low, z_high = state["ob"]
        f_low, f_high = state["fvg"]

        # ─ Step 3: Wait for FVG tap ──────────────────────────────────────
        if state["stage"] == "WAIT":
            if (bias == "LONG" and candle["low"] <= f_high) or \
               (bias == "SHORT" and candle["high"] >= f_low):
                state["stage"] = "TAPPED"
            continue

        # ─ Step 4: Reaction / entry ──────────────────────────────────────
        if state["stage"] == "TAPPED":
            atr = _calculate_atr(ltf)
            if atr <= 0:
                state = None
                continue

            entry = (f_low + f_high) / 2
            buffer = atr * ATR_BUFFER_MULT

            if bias == "LONG":
                if candle["close"] <= z_high:
                    continue
                sl = z_low - buffer
                target = entry + RR * (entry - sl)
            elif bias == "SHORT":
                if candle["close"] >= z_low:
                    continue
                sl = z_high + buffer
                target = entry - RR * (sl - entry)
            else:
                state = None
                continue

            if sl == entry:
                state = None
                continue

            rr_actual = abs(target - entry) / abs(entry - sl)
            if rr_actual < 2.0:
                state = None
                continue

            # Build signal dict for scoring
            sig = {
                "symbol": symbol,
                "direction": bias,
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "target": round(target, 2),
                "rr": round(rr_actual, 2),
                "ob": state["ob"],
                "fvg": state["fvg"],
                "sweep_detected": state["sweep_detected"],
                "bos_confirmed": state["bos_confirmed"],
                "choch_time": state["choch_time"],
            }

            score, breakdown = confluence_score_setup_d(sig, ltf, htf)
            state = None  # Clear state regardless of score

            if score < MIN_SCORE:
                logger.debug(f"  BLOCKED score={score} {symbol} {bias} @ {ts_str[:16]}")
                continue

            # Skip if already have open trade
            if open_trade:
                continue

            # Daily cap
            if daily_counts[day] >= 1:
                continue

            trade_id += 1
            daily_counts[day] += 1

            open_trade = {
                "trade_id": trade_id,
                "symbol": symbol,
                "direction": bias,
                "entry": sig["entry"],
                "sl": sig["sl"],
                "target": sig["target"],
                "rr": sig["rr"],
                "smc_score": score,
                "smc_breakdown": breakdown,
                "entry_time": ts_str,
                "sweep_detected": sig["sweep_detected"],
                "bos_confirmed": sig["bos_confirmed"],
                "choch_time": str(sig["choch_time"])[:16] if sig["choch_time"] else None,
                # filled at close:
                "exit_price": None,
                "exit_time": None,
                "exit_reason": None,
                "r_multiple": None,
            }
            logger.info(f"  → ENTRY #{trade_id} {symbol} {bias} @ {sig['entry']} "
                        f"SL={sig['sl']} TP={sig['target']} "
                        f"Score={score} [{', '.join(k+':'+str(v) for k, v in breakdown.items() if v)}]")

    # Force-close anything still open
    if open_trade and candles_5m:
        last = candles_5m[-1]
        ts_str = str(last["date"])[:19]
        _close_open(open_trade, last["close"], ts_str, "EOD", trades, daily_pnl)
        open_trade = None

    return trades


def _close_open(trade: dict, exit_price: float, exit_time: str,
                reason: str, trades: list, daily_pnl: dict):
    trade["exit_price"] = round(exit_price, 2)
    trade["exit_time"] = exit_time
    trade["exit_reason"] = reason

    risk = abs(trade["entry"] - trade["sl"])
    if risk > 0:
        pnl_pts = (exit_price - trade["entry"]) if trade["direction"] == "LONG" \
            else (trade["entry"] - exit_price)
        trade["r_multiple"] = round(pnl_pts / risk, 4)
    else:
        trade["r_multiple"] = 0.0

    day = exit_time[:10]
    daily_pnl[day] += trade["r_multiple"]
    trades.append(deepcopy(trade))


# ─── Metrics ────────────────────────────────────────────────────────────────
def calc_metrics(trades: list) -> dict:
    if not trades:
        return {"total_trades": 0}

    rs = [t["r_multiple"] for t in trades if t["r_multiple"] is not None]
    winners = [r for r in rs if r > 0]
    losers = [r for r in rs if r <= 0]

    total = len(rs)
    n_win = len(winners)
    n_loss = len(losers)
    win_rate = n_win / total if total else 0.0

    gross_profit = sum(winners) if winners else 0.0
    gross_loss = abs(sum(losers)) if losers else 0.001
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0
    total_r = sum(rs)
    expectancy = total_r / total if total else 0.0

    # Max drawdown
    peak, cumul, max_dd = 0.0, 0.0, 0.0
    for r in rs:
        cumul += r
        peak = max(peak, cumul)
        max_dd = max(max_dd, peak - cumul)

    # Max consecutive losses
    max_cl, cur_cl = 0, 0
    for r in rs:
        if r <= 0:
            cur_cl += 1; max_cl = max(max_cl, cur_cl)
        else:
            cur_cl = 0

    # Sharpe (daily R)
    daily: Dict[str, float] = defaultdict(float)
    for t in trades:
        if t["r_multiple"] is None:
            continue
        day = (t["exit_time"] or t["entry_time"])[:10]
        daily[day] += t["r_multiple"]
    dreturns = list(daily.values())
    if len(dreturns) > 1:
        import statistics
        mu = statistics.mean(dreturns)
        sigma = statistics.stdev(dreturns)
        sharpe = (mu / sigma) * (252 ** 0.5) if sigma > 0 else 0.0
    else:
        sharpe = 0.0

    avg_win = sum(winners) / n_win if n_win else 0.0
    avg_los = sum(losers) / n_loss if n_loss else 0.0

    # Score distribution
    score_buckets: Dict[int, Dict] = defaultdict(lambda: {"n": 0, "wins": 0, "r": 0.0})
    for t in trades:
        s = t.get("smc_score", 0)
        score_buckets[s]["n"] += 1
        score_buckets[s]["r"] += t.get("r_multiple") or 0.0
        if (t.get("r_multiple") or 0) > 0:
            score_buckets[s]["wins"] += 1

    # Breakdown by direction
    longs = [t for t in trades if t.get("direction") == "LONG"]
    shorts = [t for t in trades if t.get("direction") == "SHORT"]

    dir_stats = {}
    for label, subset in [("LONG", longs), ("SHORT", shorts)]:
        if subset:
            sub_r = [t["r_multiple"] for t in subset if t["r_multiple"] is not None]
            dir_stats[label] = {
                "trades": len(subset),
                "win_rate": len([r for r in sub_r if r > 0]) / len(sub_r) if sub_r else 0,
                "total_r": sum(sub_r),
                "expectancy": sum(sub_r) / len(sub_r) if sub_r else 0,
            }

    # Monthly breakdown
    monthly: Dict[str, Dict] = defaultdict(lambda: {"n": 0, "wins": 0, "r": 0.0})
    for t in trades:
        mo = (t["exit_time"] or t["entry_time"])[:7]
        monthly[mo]["n"] += 1
        monthly[mo]["r"] += t.get("r_multiple") or 0.0
        if (t.get("r_multiple") or 0) > 0:
            monthly[mo]["wins"] += 1

    return {
        "total_trades": total,
        "winners": n_win,
        "losers": n_loss,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy_r": expectancy,
        "total_r": total_r,
        "avg_winner_r": avg_win,
        "avg_loser_r": avg_los,
        "max_drawdown_r": max_dd,
        "max_consecutive_losses": max_cl,
        "sharpe_ratio": sharpe,
        "score_buckets": dict(score_buckets),
        "direction_stats": dir_stats,
        "monthly": dict(monthly),
    }


# ─── Report printer ─────────────────────────────────────────────────────────
def print_report(trades: list, metrics: dict, title: str = "SETUP-D BACKTEST"):
    W = 72
    print("\n" + "=" * W)
    print(f"  {title}")
    print("=" * W)

    if not trades:
        print("  No trades generated.")
        print("=" * W)
        return

    m = metrics
    total = m["total_trades"]
    print(f"\n  Trades      : {total}  ({m['winners']}W / {m['losers']}L)")
    print(f"  Win Rate    : {m['win_rate']*100:.1f}%")
    print(f"  Profit Fac  : {m['profit_factor']:.3f}")
    print(f"  Expectancy  : {m['expectancy_r']:+.4f} R/trade")
    print(f"  Total R     : {m['total_r']:+.2f} R")
    print(f"  Avg Winner  : +{m['avg_winner_r']:.3f} R")
    print(f"  Avg Loser   : {m['avg_loser_r']:.3f} R")
    print(f"  Max DD      : {m['max_drawdown_r']:.2f} R")
    print(f"  Max Cons. L : {m['max_consecutive_losses']}")
    print(f"  Sharpe      : {m['sharpe_ratio']:.2f}")

    # Direction
    print(f"\n  {'─'*68}")
    print(f"  DIRECTION BREAKDOWN")
    print(f"  {'─'*68}")
    for label, ds in m.get("direction_stats", {}).items():
        print(f"  {label:6s}: {ds['trades']:2d}T  WR={ds['win_rate']*100:.1f}%  "
              f"Total={ds['total_r']:+.2f}R  E={ds['expectancy']:+.4f}R")

    # Monthly
    print(f"\n  {'─'*68}")
    print(f"  MONTHLY BREAKDOWN")
    print(f"  {'─'*68}")
    for mo, ms in sorted(m.get("monthly", {}).items()):
        wr = ms["wins"] / ms["n"] * 100 if ms["n"] else 0
        print(f"  {mo}   Trades={ms['n']:2d}  WR={wr:.0f}%  Total={ms['r']:+.2f}R  "
              f"E={ms['r']/ms['n']:+.4f}R")

    # Score distribution
    print(f"\n  {'─'*68}")
    print(f"  SMC SCORE DISTRIBUTION  (threshold: {MIN_SCORE})")
    print(f"  {'─'*68}")
    for sc, sv in sorted(m.get("score_buckets", {}).items()):
        wr = sv["wins"] / sv["n"] * 100 if sv["n"] else 0
        print(f"  Score {sc}/10: {sv['n']:2d}T  WR={wr:.0f}%  Total={sv['r']:+.2f}R  "
              f"E={sv['r']/sv['n']:+.4f}R")

    # Trade list
    print(f"\n  {'─'*68}")
    print(f"  TRADE LOG  (all {total} trades)")
    print(f"  {'─'*68}")
    print(f"  {'#':>3} {'Date':>12} {'Sym':>12} {'Dir':>5} {'Entry':>10} "
          f"{'Exit':>10} {'R':>7} {'Reason':>5} {'Scr':>4} "
          f"{'Swp':>4} {'BOS':>4}")
    print(f"  {'-'*3} {'-'*12} {'-'*12} {'-'*5} {'-'*10} "
          f"{'-'*10} {'-'*7} {'-'*5} {'-'*4} {'-'*4} {'-'*4}")
    for t in trades:
        sym_s = str(t["symbol"]).replace("NSE:", "")[:12]
        entry_date = str(t.get("entry_time", ""))[:10]
        r_str = f"{t['r_multiple']:+.2f}" if t["r_multiple"] is not None else "  ---"
        swp = "Yes" if t.get("sweep_detected") else "No"
        bos = "Yes" if t.get("bos_confirmed") else "No"
        print(f"  {t['trade_id']:>3} {entry_date:>12} {sym_s:>12} "
              f"{t.get('direction','')[:5]:>5} "
              f"{t.get('entry', 0):>10.1f} "
              f"{(t.get('exit_price') or 0):>10.1f} "
              f"{r_str:>7} "
              f"{str(t.get('exit_reason',''))[:5]:>5} "
              f"{t.get('smc_score', 0):>4} "
              f"{swp:>4} {bos:>4}")

    print("=" * W)


# ─── Data fetcher ────────────────────────────────────────────────────────────
def _strip_tz(dt) -> datetime:
    """Ensure datetime is naive (strip timezone if present)."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _normalise_candle(c: dict) -> dict:
    """Return candle with a naive datetime in the 'date' field."""
    c2 = dict(c)
    c2["date"] = _strip_tz(c2["date"])
    return c2


def _fetch(symbol: str, interval: str, start: datetime, end: datetime) -> list:
    import time as _time
    token_map: Dict[str, int] = {}

    try:
        inst = kite.ltp(symbol)
        token = list(inst.values())[0]["instrument_token"]
        token_map[symbol] = token
    except Exception as e:
        logger.error(f"LTP failed for {symbol}: {e}")
        return []

    all_data = []
    chunk_start = start
    # Kite historical_data limit: ~60 days per call for 5m
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=59), end)
        for attempt in range(3):
            try:
                _time.sleep(0.35)
                data = kite.historical_data(
                    token_map[symbol], chunk_start, chunk_end, interval
                )
                all_data.extend(data)
                break
            except Exception as e:
                if attempt < 2:
                    _time.sleep(2)
                else:
                    logger.error(f"Fetch failed {symbol} {interval}: {e}")
        chunk_start = chunk_end + timedelta(minutes=1)

    # Normalise all candles to naive datetime + filter to range
    result = [
        _normalise_candle(c) for c in all_data
        if start <= _strip_tz(c["date"]) <= end
    ]
    logger.info(f"  {symbol} {interval}: {len(result)} candles "
                f"({result[0]['date'] if result else '?'} → {result[-1]['date'] if result else '?'})")
    return result


# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    SYMBOLS = ["NSE:NIFTY 50", "NSE:NIFTY BANK"]
    START = datetime(2026, 1, 1)
    END = datetime(2026, 3, 4, 15, 30)

    print("\n" + "=" * 72)
    print("  SETUP-D BACKTEST  |  8-Phase Pipeline  |  Jan 1 – Mar 4, 2026")
    print("=" * 72)
    print(f"\n  Instruments : {', '.join(SYMBOLS)}")
    print(f"  Period      : {START.date()} → {END.date()}")
    print(f"  Config      : BOS_WAIT stage | 4h expiry | Sweep HTF bypass")
    print(f"              : v2 scorer (max 10) | min_score={MIN_SCORE} | RR={RR}")
    print(f"\n  Fetching data...\n")

    symbol_data = {}
    for sym in SYMBOLS:
        print(f"  [{sym}] fetching 5m + 1h...")
        c5 = _fetch(sym, "5minute", START - timedelta(days=3), END)
        c1h = _fetch(sym, "60minute", START - timedelta(days=10), END)
        if not c5:
            print(f"  [WARN] No 5m data for {sym} — skipping")
            continue
        symbol_data[sym] = {"5m": c5, "1h": c1h}

    if not symbol_data:
        print("[ERROR] No data fetched.")
        sys.exit(1)

    all_trades = []
    for sym, data in symbol_data.items():
        print(f"\n  Running Setup-D simulation on {sym}...")
        trades = run_setupd_backtest(sym, data["5m"], data["1h"])
        print(f"  → {sym}: {len(trades)} trades generated")
        all_trades.extend(trades)

    # Sort by entry time
    all_trades.sort(key=lambda t: t.get("entry_time") or "")

    # ── Combined report ──────────────────────────────────────────────────
    combined_metrics = calc_metrics(all_trades)
    print_report(all_trades, combined_metrics,
                 title="SETUP-D  |  COMBINED (NIFTY + BANKNIFTY)  |  Jan–Mar 4, 2026")

    # ── Per-symbol report ────────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("  PER-SYMBOL BREAKDOWN")
    print("=" * 72)
    for sym in SYMBOLS:
        sym_trades = [t for t in all_trades if t["symbol"] == sym]
        if not sym_trades:
            print(f"\n  {sym}: No trades")
            continue
        m = calc_metrics(sym_trades)
        sname = sym.replace("NSE:", "")
        print(f"\n  {sname}")
        print(f"    Trades={m['total_trades']}  WR={m['win_rate']*100:.1f}%  "
              f"PF={m['profit_factor']:.3f}  E={m['expectancy_r']:+.4f}R  "
              f"Total={m['total_r']:+.2f}R  DD={m['max_drawdown_r']:.2f}R  "
              f"Sharpe={m['sharpe_ratio']:.2f}")

    # ── Sweep vs no-sweep split ──────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("  PHASE 4 VALIDATION: SWEEP vs NO-SWEEP trades")
    print("=" * 72)
    sweep_trades = [t for t in all_trades if t.get("sweep_detected")]
    nosweep_trades = [t for t in all_trades if not t.get("sweep_detected")]

    for label, subset in [("WITH liquidity sweep", sweep_trades),
                           ("WITHOUT liquidity sweep", nosweep_trades)]:
        if subset:
            ms = calc_metrics(subset)
            print(f"\n  {label}:")
            print(f"    Trades={ms['total_trades']}  WR={ms['win_rate']*100:.1f}%  "
                  f"PF={ms['profit_factor']:.3f}  E={ms['expectancy_r']:+.4f}R  "
                  f"Total={ms['total_r']:+.2f}R")
        else:
            print(f"\n  {label}: No trades")

    # ── BOS confirmed split ──────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("  PHASE 2 VALIDATION: BOS CONFIRMED vs NOT")
    print("=" * 72)
    bos_yes = [t for t in all_trades if t.get("bos_confirmed")]
    bos_no = [t for t in all_trades if not t.get("bos_confirmed")]

    for label, subset in [("BOS confirmed", bos_yes), ("BOS NOT confirmed", bos_no)]:
        if subset:
            ms = calc_metrics(subset)
            print(f"\n  {label}:")
            print(f"    Trades={ms['total_trades']}  WR={ms['win_rate']*100:.1f}%  "
                  f"PF={ms['profit_factor']:.3f}  E={ms['expectancy_r']:+.4f}R  "
                  f"Total={ms['total_r']:+.2f}R")
        else:
            print(f"\n  {label}: No trades")

    # ── Export CSV ───────────────────────────────────────────────────────
    csv_path = os.path.join(os.path.dirname(__file__), "backtest_setupd_jan_mar2026.csv")
    if all_trades:
        fieldnames = ["trade_id", "symbol", "direction", "entry_time", "exit_time",
                      "entry", "exit_price", "sl", "target", "rr", "r_multiple",
                      "exit_reason", "smc_score", "sweep_detected", "bos_confirmed", "choch_time",
                      "smc_breakdown"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for t in all_trades:
                row = dict(t)
                if isinstance(row.get("smc_breakdown"), dict):
                    row["smc_breakdown"] = str(row["smc_breakdown"])
                w.writerow(row)
        print(f"\n\n  [EXPORT] {len(all_trades)} trades → {csv_path}")

    print(f"\n  [DONE]\n")


if __name__ == "__main__":
    main()
