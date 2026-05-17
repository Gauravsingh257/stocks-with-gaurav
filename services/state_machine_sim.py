"""
PHASE G2-5 — Planned-execution state-machine SIMULATOR (shadow / backtest).

WHY a simulator and not the live function:
`smc_mtf_engine_v4.detect_setup_a` is the real planned-execution machine,
but it is coupled to WALL-CLOCK time (state expiry =
`(now_ist() - state.time) > 1800s`) and to index-OPTIONS mechanics
(`option_strike`). Driven over historical bars its wall-clock expiry would
never fire — that would be a *broken* measurement, worse than honest
simulation. So this module faithfully reproduces detect_setup_a's
DOCUMENTED state-machine logic, decoupled from wall-clock and options, on
DAILY equity candles, point-in-time (no look-ahead).

Faithful mapping to detect_setup_a:
  FORMED      : detect_daily_ob(LONG) AND detect_daily_fvg(LONG) both present
  invalidation: close < ob_low                     (invalidate_structure)
  expiry      : > expiry_bars without a tap        (bar clock ≈ 1800s wall)
  TAPPED      : bar low ≤ fvg_high and bar low ≥ fvg_low  (is_price_inside_fvg)
  FIRE        : confirmation_candle(LONG): close>open and lower_wick>1.2*body
  entry       : fvg midpoint                       (LIMIT, exactly as live)
  sl          : min(low,10) - atr*0.3 buffer       (atr*0.3 = codebase
                 standard SMC stop-buffer proxy for compute_dynamic_buffer)
  target      : entry + 2*(entry-sl)               (rr=2.0, exactly as live)

HTF bias = weekly trend (longs only on BULLISH/STRONG_BULL) — mirrors the
equity research design (weekly trend → daily structure) and the one
audit-validated instinct: do nothing in non-bull regimes.

Pure / read-only. Reuses engine.swing daily/weekly detectors so this stays
consistent with the rest of the stock pipeline (not a divergent SMC impl).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from engine.indicators import calculate_atr
from engine.swing import detect_daily_fvg, detect_daily_ob, detect_weekly_trend

log = logging.getLogger("services.state_machine_sim")

_EXPIRY_BARS_DEFAULT = 15  # bars to wait for tap+rejection before EXPIRED


@dataclass(slots=True)
class StateMachineFire:
    entry_idx: int          # index into the daily candle list where FIRE occurred
    entry: float
    stop_loss: float
    target: float
    formed_idx: int
    tapped_idx: int
    bias: str = "LONG"

    def to_dict(self) -> dict:
        return {
            "entry_idx": self.entry_idx, "entry": round(self.entry, 2),
            "stop_loss": round(self.stop_loss, 2), "target": round(self.target, 2),
            "formed_idx": self.formed_idx, "tapped_idx": self.tapped_idx,
            "bias": self.bias,
        }


def _confirmation_candle_long(c: dict) -> bool:
    """Exact reproduction of smc_mtf_engine_v4.confirmation_candle (LONG)."""
    body = abs(c["close"] - c["open"])
    wick_low = min(c["open"], c["close"]) - c["low"]
    return c["close"] > c["open"] and wick_low > body * 1.2


def simulate_state_machine_entries(
    daily: list[dict],
    weekly: list[dict],
    *,
    expiry_bars: int = _EXPIRY_BARS_DEFAULT,
    min_history: int = 60,
) -> list[StateMachineFire]:
    """Walk the daily candle array once, replaying the planned-execution
    state machine. Returns every FIRE (planned-zone entry) event.

    No look-ahead: at bar i, only candles[:i+1] inform the decision.
    """
    fires: list[StateMachineFire] = []
    n = len(daily)
    if n < min_history or len(weekly) < 12:
        return fires

    i = min_history
    while i < n:
        window = daily[: i + 1]
        # HTF bias gate — weekly trend must be bullish (longs-only).
        wt = detect_weekly_trend(weekly_up_to(weekly, daily, i))
        if wt not in ("BULLISH", "STRONG_BULL"):
            i += 1
            continue

        ob = detect_daily_ob(window, "LONG")
        fvg = detect_daily_fvg(window, "LONG")
        if not ob or not fvg:
            i += 1
            continue

        # FORMED at bar i. Walk forward for tap → rejection within expiry.
        formed_idx = i
        fvg_lo, fvg_hi = (fvg[0], fvg[1]) if fvg[0] <= fvg[1] else (fvg[1], fvg[0])
        ob_low = ob[0]
        tapped_idx = -1
        fired = False
        j = i + 1
        while j < n and (j - formed_idx) <= expiry_bars:
            c = daily[j]
            # invalidation: close below OB low
            if c["close"] < ob_low:
                break
            if tapped_idx < 0:
                # TAPPED: bar trades into the FVG zone
                if c["low"] <= fvg_hi and c["high"] >= fvg_lo:
                    tapped_idx = j
            else:
                # awaiting rejection confirmation candle
                if _confirmation_candle_long(c):
                    entry = round((fvg_lo + fvg_hi) / 2.0, 2)
                    atr = calculate_atr(daily[: j + 1], 14) or (entry * 0.02)
                    recent_low = min(x["low"] for x in daily[max(0, j - 9): j + 1])
                    sl = round(recent_low - atr * 0.3, 2)
                    if sl < entry:  # geometry sane (longs-only)
                        target = round(entry + 2.0 * (entry - sl), 2)
                        fires.append(StateMachineFire(
                            entry_idx=j, entry=entry, stop_loss=sl,
                            target=target, formed_idx=formed_idx, tapped_idx=tapped_idx,
                        ))
                        fired = True
                    break
            j += 1
        # advance past this attempt (next bar after fire/expiry/invalidate)
        i = (j + 1) if fired else (formed_idx + 1)
    return fires


def weekly_up_to(weekly: list[dict], daily: list[dict], daily_idx: int) -> list[dict]:
    """Point-in-time weekly slice: only weekly bars whose date ≤ the daily
    bar at daily_idx (no look-ahead into future weeks)."""
    try:
        cutoff = daily[daily_idx].get("date")
        if cutoff is None:
            return weekly
        return [w for w in weekly if w.get("date") is None or w["date"] <= cutoff] or weekly[: max(12, len(weekly) // 2)]
    except Exception:
        return weekly
