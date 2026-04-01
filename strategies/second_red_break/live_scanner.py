"""
Second Red Break — Live Scanner for Engine Integration.

Called every 5-minute cycle by the main engine (smc_mtf_engine_v4.py).
Maintains per-day strategy state and emits signals when breakdown is detected.

V3 Config (NIFTY only):
  - max_entry_hour = 10  (skip breakdowns after 10:00 AM)
  - max_sl_pts = 70      (skip if SL distance > 70 pts)
  - use_partial_exit = False  (full 3R target, no partial)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from .strategy import DayState, close_open_trade_eod, process_candle, reset_day
from .utils import Candle, INDEX_CONFIG

logger = logging.getLogger("srb_scanner")

# ── V3 Strategy Config ─────────────────────────────────────────
SRB_MAX_ENTRY_HOUR = 10       # Breakdowns only before 10:00 AM
SRB_MAX_SL_PTS_NIFTY = 70.0  # Max SL distance for NIFTY
SRB_INSTRUMENTS = ["NIFTY"]   # NIFTY only for live


class SRBScanner:
    """
    Stateful scanner — one instance lives in the engine for the entire session.
    Resets automatically at the start of each trading day.
    """

    def __init__(self):
        self._states: dict[str, DayState] = {}
        self._today: date | None = None
        self._last_candle_time: dict[str, datetime] = {}
        # Track if we already emitted a signal today per instrument
        self._signal_emitted: dict[str, bool] = {}

    def _ensure_daily_reset(self) -> None:
        """Reset state if the date has changed."""
        today = date.today()
        if self._today != today:
            self._today = today
            self._states.clear()
            self._last_candle_time.clear()
            self._signal_emitted.clear()
            for inst in SRB_INSTRUMENTS:
                max_sl = SRB_MAX_SL_PTS_NIFTY if inst == "NIFTY" else 99999.0
                self._states[inst] = reset_day(
                    instrument=inst,
                    trade_date=today,
                    max_entry_hour=SRB_MAX_ENTRY_HOUR,
                    max_sl_pts=max_sl,
                    use_partial_exit=False,
                )
                self._signal_emitted[inst] = False
            logger.info("SRB scanner reset for %s | instruments=%s", today, SRB_INSTRUMENTS)

    def scan(self, candles_5m: list[dict], instrument: str = "NIFTY") -> Optional[dict]:
        """
        Feed the latest 5-minute candles and check for breakdown signal.

        Args:
            candles_5m: List of OHLCV dicts from engine's fetch_ohlc()
                        e.g. [{"date": datetime, "open": ..., "high": ..., ...}, ...]
            instrument: "NIFTY" (only NIFTY supported in V3)

        Returns:
            Signal dict (engine-compatible) if ENTRY detected, else None.
            Signal dict keys: symbol, direction, setup, entry, sl, target, rr,
                              grade, smc_score, ai_score, risk_mult, analysis,
                              srb_second_red_time, srb_second_red_low, srb_second_red_high
        """
        if instrument not in SRB_INSTRUMENTS:
            return None

        self._ensure_daily_reset()
        state = self._states.get(instrument)
        if state is None:
            return None

        # Already traded/signaled today
        if self._signal_emitted.get(instrument, False):
            return None
        if state.trade_done and not state.in_trade:
            return None

        if not candles_5m:
            return None

        # Filter to today's candles only
        today = self._today
        today_candles = []
        for c in candles_5m:
            c_date = c.get("date")
            if c_date and hasattr(c_date, "date") and c_date.date() == today:
                today_candles.append(c)

        if not today_candles:
            return None

        # Find the last candle we haven't processed yet
        last_processed = self._last_candle_time.get(instrument)

        for raw in today_candles:
            c_dt = raw["date"]
            if last_processed and c_dt <= last_processed:
                continue

            candle = Candle(
                date=c_dt,
                open=raw["open"],
                high=raw["high"],
                low=raw["low"],
                close=raw["close"],
                volume=raw.get("volume", 0),
            )

            # Track state before processing for milestone detection
            _prev_second_red = state.second_red
            _prev_trade_done = state.trade_done

            signal = process_candle(state, candle)
            self._last_candle_time[instrument] = c_dt

            # Log milestone: 2nd red candle identified
            if state.second_red is not None and _prev_second_red is None:
                logger.info(
                    "SRB 2ND RED found %s | time=%s low=%.1f high=%.1f | SL_range=%.1f (max=%s)",
                    instrument, state.second_red.date,
                    state.second_red.low, state.second_red.high,
                    state.second_red.high - state.second_red.low,
                    SRB_MAX_SL_PTS_NIFTY if instrument == "NIFTY" else "N/A",
                )

            # Log milestone: trade_done set without entry (rejected)
            if state.trade_done and not _prev_trade_done and signal != "ENTRY":
                _reject_reason = "time_cutoff" if candle.date.hour >= state.max_entry_hour else "sl_too_wide"
                logger.info(
                    "SRB REJECTED %s | reason=%s | candle=%s close=%.1f | 2nd_red_low=%.1f 2nd_red_high=%.1f",
                    instrument, _reject_reason, candle.date, candle.close,
                    state.second_red.low if state.second_red else 0,
                    state.second_red.high if state.second_red else 0,
                )

            if signal == "ENTRY":
                self._signal_emitted[instrument] = True
                cfg = INDEX_CONFIG[instrument]
                risk = state.stop_loss - state.entry_price
                rr = 3.0  # Fixed 1:3 RR

                logger.info(
                    "SRB ENTRY %s | 2nd-red=%s low=%.1f | breakdown=%s close=%.1f | "
                    "SL=%.1f TGT=%.1f risk=%.1f",
                    instrument, state.second_red.date, state.second_red.low,
                    candle.date, candle.close,
                    state.stop_loss, state.target, risk,
                )

                return {
                    "symbol": cfg["exchange_symbol"],  # "NSE:NIFTY 50"
                    "direction": "SHORT",               # PUT = bearish
                    "setup": "SECOND-RED-BREAK",
                    "entry": state.entry_price,
                    "sl": state.stop_loss,
                    "target": state.target,
                    "rr": rr,
                    "grade": "A",
                    "smc_score": 7,       # Fixed — strategy-validated
                    "ai_score": 70,       # Fixed
                    "risk_mult": 1.0,
                    "analysis": (
                        f"Second Red Break: 2nd red candle at "
                        f"{state.second_red.date.strftime('%H:%M') if hasattr(state.second_red.date, 'strftime') else state.second_red.date}, "
                        f"breakdown below {state.second_red.low:.1f}. "
                        f"Entry {state.entry_price:.1f}, SL {state.stop_loss:.1f} "
                        f"(risk {risk:.1f} pts), Target {state.target:.1f} (3R)."
                    ),
                    # Extra metadata for executor
                    "srb_instrument": instrument,
                    "srb_second_red_time": str(state.second_red.date),
                    "srb_second_red_low": state.second_red.low,
                    "srb_second_red_high": state.second_red.high,
                }

        return None

    def get_state_summary(self) -> dict:
        """Return current scanner state for diagnostics."""
        self._ensure_daily_reset()
        summary = {}
        for inst in SRB_INSTRUMENTS:
            st = self._states.get(inst)
            if st:
                summary[inst] = {
                    "red_count": st.red_count,
                    "second_red_found": st.second_red is not None,
                    "second_red_low": st.second_red.low if st.second_red else None,
                    "in_trade": st.in_trade,
                    "trade_done": st.trade_done,
                    "signal_emitted": self._signal_emitted.get(inst, False),
                }
        return summary


# Module-level singleton for engine import
_scanner: SRBScanner | None = None


def get_scanner() -> SRBScanner:
    """Get or create the singleton SRB scanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = SRBScanner()
    return _scanner


def scan_second_red_break(candles_5m: list[dict], instrument: str = "NIFTY") -> Optional[dict]:
    """
    Convenience function for engine integration.

    Usage in smc_mtf_engine_v4.py:
        from strategies.second_red_break.live_scanner import scan_second_red_break
        sig = scan_second_red_break(nifty_5m_candles, "NIFTY")
        if sig:
            # Execute trade via live_executor
    """
    return get_scanner().scan(candles_5m, instrument)
