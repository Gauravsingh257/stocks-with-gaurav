"""
Second Red Break Put Strategy — Core Logic.

Pure strategy logic with no broker dependency. Usable in both backtest and live mode.

Logic:
1. Find the 2nd RED candle of the day (close < open).
2. Wait for a subsequent candle that CLOSES below the LOW of the 2nd red candle.
3. On breakdown → entry signal for PUT.
4. SL = HIGH of the 2nd red candle.  Target = entry - 3 × (SL - entry)  [1:3 RR].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from .utils import Candle, TradeRecord, DaySummary, INDEX_CONFIG


@dataclass
class DayState:
    """Mutable state for a single trading day on one instrument."""
    instrument: str = ""
    trade_date: date | None = None
    red_count: int = 0
    second_red: Candle | None = None
    breakdown_candle: Candle | None = None
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target: float = 0.0
    in_trade: bool = False
    trade_done: bool = False  # one trade per instrument per day
    trade_record: TradeRecord | None = None
    # ── Filter config ──────────────────────────────────────────────
    max_entry_hour: int = 23       # skip breakdown if candle hour >= this (23 = off)
    max_sl_pts: float = 99999.0   # skip if SL distance > this (99999 = off)
    use_partial_exit: bool = False  # partial exit at 1.5R, trail rest to 3R
    use_full_trail: bool = True     # Strategy 6: full trail from 3R, 1.5R gap
    # ── Partial / trail tracking ───────────────────────────────────
    partial_target: float = 0.0
    partial_hit: bool = False
    trail_active: bool = False     # trailing mode activated (3R reached)
    trail_peak_r: float = 0.0     # highest R seen since entry
    trail_sl: float = 0.0         # current trailing SL price
    TRAIL_GAP_R: float = 1.5      # gap between peak and trailing SL in R


def reset_day(
    instrument: str,
    trade_date: date,
    max_entry_hour: int = 23,
    max_sl_pts: float = 99999.0,
    use_partial_exit: bool = False,
    use_full_trail: bool = True,
) -> DayState:
    """Fresh state for a new trading day."""
    return DayState(
        instrument=instrument,
        trade_date=trade_date,
        max_entry_hour=max_entry_hour,
        max_sl_pts=max_sl_pts,
        use_partial_exit=use_partial_exit,
        use_full_trail=use_full_trail,
    )


def process_candle(state: DayState, candle: Candle) -> Optional[str]:
    """
    Feed one candle into the strategy state machine.

    Returns:
        - "ENTRY"   when a new put trade should be opened
        - "SL_HIT"  when stop-loss is hit on this candle
        - "TARGET"  when target is reached on this candle
        - None      otherwise
    """
    # Already traded today → nothing to do
    if state.trade_done and not state.in_trade:
        return None

    # ── Phase 1: Identify 2nd red candle ──────────────────────────
    if state.second_red is None:
        if candle.is_red:
            state.red_count += 1
            if state.red_count == 2:
                state.second_red = candle
        return None

    # ── Phase 2: Wait for breakdown ───────────────────────────────
    if not state.in_trade and not state.trade_done:
        if candle.close < state.second_red.low:
            # --- Filter 1: Time of day ---
            if candle.date.hour >= state.max_entry_hour:
                state.trade_done = True  # skip rest of day
                return None

            # Compute risk to check SL distance filter
            entry_price = candle.close
            sl_price = state.second_red.high
            risk = sl_price - entry_price

            # --- Filter 2: SL distance ---
            if risk > state.max_sl_pts:
                state.trade_done = True  # skip this setup
                return None

            # Breakdown confirmed on candle close
            state.breakdown_candle = candle
            state.entry_price = entry_price
            state.stop_loss = sl_price
            state.target = state.entry_price - (3 * risk)  # 1:3 RR (price goes down for profit)
            if state.use_partial_exit:
                state.partial_target = state.entry_price - (1.5 * risk)  # 1.5R level
            state.in_trade = True

            state.trade_record = TradeRecord(
                trade_date=str(state.trade_date),
                instrument=state.instrument,
                second_red_time=str(state.second_red.date),
                second_red_low=state.second_red.low,
                second_red_high=state.second_red.high,
                breakdown_time=str(candle.date),
                entry_price=state.entry_price,
                stop_loss=state.stop_loss,
                target=state.target,
                exit_price=0.0,
                exit_time="",
                outcome="OPEN",
                risk_points=risk,
            )
            return "ENTRY"
        return None

    # ── Phase 3: Manage open trade (SL / Target check) ────────────
    if state.in_trade:
        rec = state.trade_record
        assert rec is not None
        risk = rec.risk_points

        # --- Track peak R (how far price moved in our favor) ---
        current_r = (state.entry_price - candle.low) / risk if risk > 0 else 0
        if current_r > state.trail_peak_r:
            state.trail_peak_r = current_r

        # --- Strategy 6: Full trail from 3R (1.5R gap) ---
        if state.use_full_trail and state.trail_peak_r >= 3.0:
            if not state.trail_active:
                state.trail_active = True
            # Trail SL = entry - (peak_R - 1.5) * risk
            # As peak grows, SL ratchets down (in favor of the short trade)
            new_trail_sl = state.entry_price - (state.trail_peak_r - state.TRAIL_GAP_R) * risk
            # Only tighten SL, never loosen it
            if state.trail_sl == 0.0 or new_trail_sl < state.trail_sl:
                state.trail_sl = new_trail_sl
            state.stop_loss = state.trail_sl

        # --- Partial exit at 1.5R (lock half, move SL to breakeven) ---
        if state.use_partial_exit and not state.partial_hit and state.partial_target > 0:
            if candle.low <= state.partial_target:
                state.partial_hit = True
                if not state.trail_active:
                    state.stop_loss = state.entry_price  # move SL to breakeven

        # Check SL (high of candle breaches SL)
        if candle.high >= state.stop_loss:
            rec.exit_price = state.stop_loss
            rec.exit_time = str(candle.date)
            if state.trail_active:
                # Trailed out — exited at trail SL with guaranteed profit
                rec.pnl_points = state.entry_price - rec.exit_price
                rec.outcome = "WIN"
            elif state.partial_hit:
                # Half locked at 1.5R, half exits at BE → net positive 0.75R
                rec.pnl_points = 0.5 * (1.5 * rec.risk_points) + 0.5 * 0.0
                rec.outcome = "WIN"
            else:
                rec.pnl_points = rec.entry_price - rec.exit_price  # negative for loss
                rec.outcome = "LOSS"
            rec.rr_achieved = rec.pnl_points / rec.risk_points if rec.risk_points else 0
            state.in_trade = False
            state.trade_done = True
            return "SL_HIT"

        # Check Target — only exit at 3R if trailing is NOT enabled
        if not state.use_full_trail:
            if candle.low <= state.target:
                rec.exit_price = state.target
                rec.exit_time = str(candle.date)
                rec.outcome = "WIN"
                if state.partial_hit:
                    rec.pnl_points = 0.5 * (1.5 * rec.risk_points) + 0.5 * (3.0 * rec.risk_points)
                else:
                    rec.pnl_points = rec.entry_price - rec.exit_price  # 3R points
                rec.rr_achieved = rec.pnl_points / rec.risk_points if rec.risk_points else 0
                state.in_trade = False
                state.trade_done = True
                return "TARGET"

    return None


def close_open_trade_eod(state: DayState, last_candle: Candle) -> None:
    """Force-close any open trade at end of day at last candle close."""
    if state.in_trade and state.trade_record:
        rec = state.trade_record
        eod_price = last_candle.close
        raw_pnl = rec.entry_price - eod_price
        rec.exit_price = eod_price
        rec.exit_time = str(last_candle.date)
        rec.outcome = "EOD_EXIT"
        if state.trail_active:
            # Trailing was active — use actual EOD PnL (guaranteed > 1.5R)
            rec.pnl_points = raw_pnl
        elif state.partial_hit:
            # Half locked at 1.5R, half exits at EOD price
            rec.pnl_points = 0.5 * (1.5 * rec.risk_points) + 0.5 * raw_pnl
        else:
            rec.pnl_points = raw_pnl
        rec.rr_achieved = rec.pnl_points / rec.risk_points if rec.risk_points else 0
        state.in_trade = False
        state.trade_done = True


def run_day(
    instrument: str,
    trade_date: date,
    candles: List[Candle],
    max_entry_hour: int = 23,
    max_sl_pts: float = 99999.0,
    use_partial_exit: bool = False,
    use_full_trail: bool = True,
) -> "tuple[DaySummary, Optional[TradeRecord]]":
    """
    Run strategy on a single day's 5-min candles for one instrument.
    Returns DaySummary + attaches TradeRecord to state if traded.
    """
    state = reset_day(instrument, trade_date, max_entry_hour, max_sl_pts, use_partial_exit, use_full_trail)

    for candle in candles:
        signal = process_candle(state, candle)
        if signal in ("SL_HIT", "TARGET"):
            break  # trade closed

    # End-of-day: close open trades
    if state.in_trade and candles:
        close_open_trade_eod(state, candles[-1])

    traded = state.trade_record is not None
    return DaySummary(
        date=str(trade_date),
        instrument=instrument,
        traded=traded,
        outcome=state.trade_record.outcome if traded else "NO_TRADE",
        pnl_points=state.trade_record.pnl_points if traded else 0.0,
        rr_achieved=state.trade_record.rr_achieved if traded else 0.0,
    ), state.trade_record
