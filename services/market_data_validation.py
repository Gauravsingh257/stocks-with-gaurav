"""Canonical validation boundary for externally-sourced market data.

Providers occasionally publish malformed bars. On 2026-08-21 Yahoo/yfinance
retro-corrupted the daily bar for effectively every NSE equity: OHLC came back
as ``NaN`` while volume stayed intact. Python does not object — ``float('nan')``
is a perfectly valid float — so the bad value travelled all the way into
portfolio processing. SQLite has no NaN at all: binding one to a REAL column
stores NULL, which is how a bad Yahoo candle surfaced as

    IntegrityError: NOT NULL constraint failed: momentum_positions.profit_loss_pct

and took down the whole Momentum cycle, not just the offending symbol.

Everything crossing from a provider into engine or portfolio logic should pass
through here, so that one upstream glitch can neither crash a cycle nor silently
poison a calculation. Rejection is always logged with symbol + timeframe +
reason — a missing bar is a data-quality event, never something to hide.

Note on semantics: invalid bars are *dropped*, never repaired or back-filled.
The series stays chronological and keeps its timeframe; it simply ends at the
last bar the provider actually delivered. Callers that need a price then see the
previous valid close, which is the correct reading for a daily-candle system —
as opposed to a fabricated value.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from typing import Any

log = logging.getLogger("services.market_data_validation")

#: OHLC keys used by this codebase's candle dicts.
OHLC_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")

#: OHLC column names as yfinance returns them in a DataFrame.
FRAME_OHLC_COLUMNS: tuple[str, ...] = ("Open", "High", "Low", "Close")

#: How many offending dates to name in a rejection log line.
_SAMPLE = 3


def is_finite_number(value: Any) -> bool:
    """True only for a real, finite number.

    Rejects None, NaN, +/-inf, bools and anything non-numeric. This is the single
    predicate the rest of the codebase should use — ``if price`` is not
    equivalent, because ``bool(float('nan'))`` is True.
    """
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def finite_or_none(value: Any) -> float | None:
    """Coerce to float, or None when the value is not a finite number."""
    return float(value) if is_finite_number(value) else None


def all_finite(values: Iterable[Any]) -> bool:
    """True when every value is a finite number."""
    return all(is_finite_number(v) for v in values)


def sanitize_candles(
    candles: Sequence[dict] | None,
    *,
    symbol: str = "?",
    timeframe: str = "1d",
    required: Sequence[str] = OHLC_FIELDS,
    context: str = "",
) -> list[dict]:
    """Drop candles whose required OHLC fields are not finite numbers.

    Volume is deliberately *not* a rejection criterion — it never drives a price
    decision — but a non-finite volume is normalised to 0.0 so downstream
    arithmetic stays finite.

    Returns a new list; input candles are never mutated.
    """
    if not candles:
        return []

    kept: list[dict] = []
    dropped_dates: list[str] = []
    for candle in candles:
        if not isinstance(candle, dict):
            dropped_dates.append("<non-dict>")
            continue
        if not all_finite(candle.get(field) for field in required):
            dropped_dates.append(str(candle.get("date", "?")))
            continue
        if is_finite_number(candle.get("volume")):
            kept.append(candle)
        else:
            patched = dict(candle)
            patched["volume"] = 0.0
            kept.append(patched)

    if dropped_dates:
        where = f" [{context}]" if context else ""
        log.warning(
            "Rejected %d/%d non-finite %s candle(s) for %s%s — offending bar(s): %s%s",
            len(dropped_dates), len(candles), timeframe, symbol, where,
            ", ".join(dropped_dates[:_SAMPLE]),
            "..." if len(dropped_dates) > _SAMPLE else "",
        )
    return kept


def finite_ohlc_frame(
    df: Any,
    *,
    symbol: str = "?",
    timeframe: str = "1d",
    columns: Sequence[str] | None = None,
    context: str = "",
) -> Any:
    """Drop DataFrame rows whose OHLC columns hold non-finite values.

    For the pandas-native consumers that do their maths on the frame directly
    rather than converting to candle dicts first. Returns the frame unchanged
    (and logs) if it cannot be evaluated, so validation can never itself become
    a new failure mode.
    """
    if df is None:
        return df
    frame_columns = getattr(df, "columns", None)
    if frame_columns is None:
        return df
    cols = [c for c in (columns or FRAME_OHLC_COLUMNS) if c in frame_columns]
    if not cols:
        return df

    try:
        import numpy as np

        mask = np.isfinite(df[cols].astype("float64")).all(axis=1)
        dropped = int((~mask).sum())
        if not dropped:
            return df
        where = f" [{context}]" if context else ""
        bad_index = [str(i)[:10] for i in df.index[~mask][:_SAMPLE]]
        log.warning(
            "Rejected %d/%d non-finite %s row(s) for %s%s — offending bar(s): %s%s",
            dropped, len(df), timeframe, symbol, where, ", ".join(bad_index),
            "..." if dropped > _SAMPLE else "",
        )
        return df[mask]
    except Exception as exc:  # never let validation itself break a caller
        log.warning("finite_ohlc_frame could not validate %s (%s): %s",
                    symbol, timeframe, exc)
        return df


def finite_fields(
    fields: dict[str, Any],
    *,
    symbol: str = "?",
    context: str = "",
) -> dict[str, Any]:
    """Split off non-finite numeric values before a database write.

    Returns only the entries safe to persist. Non-numeric values (strings such as
    a status, or None where the column is nullable) are passed through untouched;
    only *numbers that are not finite* are removed, because SQLite silently turns
    NaN into NULL and a NOT NULL column then raises IntegrityError.
    """
    safe: dict[str, Any] = {}
    rejected: list[str] = []
    for key, value in fields.items():
        if isinstance(value, float) and not math.isfinite(value):
            rejected.append(key)
            continue
        safe[key] = value
    if rejected:
        where = f" [{context}]" if context else ""
        log.warning("Dropped non-finite field(s) for %s%s before DB write: %s",
                    symbol, where, ", ".join(sorted(rejected)))
    return safe


__all__ = [
    "OHLC_FIELDS",
    "FRAME_OHLC_COLUMNS",
    "is_finite_number",
    "finite_or_none",
    "all_finite",
    "sanitize_candles",
    "finite_ohlc_frame",
    "finite_fields",
]
