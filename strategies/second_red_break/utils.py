"""
Utility helpers for the Second Red Break Put Strategy.

Provides:
- Index config (strike step, lot size)
- ATM / ITM strike computation
- Option tradingsymbol resolution
- Trade logging helpers
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

# ── Index configuration ────────────────────────────────────────────────
INDEX_CONFIG = {
    "NIFTY": {"step": 50, "lot_size": 65, "exchange_symbol": "NSE:NIFTY 50"},
    "BANKNIFTY": {"step": 100, "lot_size": 30, "exchange_symbol": "NSE:NIFTY BANK"},
}

PREMIUM_THRESHOLD = 150  # below this → ATM, else 1-strike ITM

STRATEGY_DIR = Path(__file__).parent
RESULTS_DIR = STRATEGY_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ── Data classes ───────────────────────────────────────────────────────
@dataclass
class Candle:
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def is_red(self) -> bool:
        return self.close < self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


@dataclass
class TradeRecord:
    trade_date: str
    instrument: str
    second_red_time: str
    second_red_low: float
    second_red_high: float
    breakdown_time: str
    entry_price: float
    stop_loss: float
    target: float
    exit_price: float
    exit_time: str
    outcome: str  # "WIN", "LOSS", "OPEN", "NO_TRADE"
    pnl_points: float = 0.0
    risk_points: float = 0.0
    rr_achieved: float = 0.0
    option_strike: float = 0.0
    option_type: str = "PE"
    option_premium_entry: float = 0.0
    option_premium_exit: float = 0.0
    option_pnl: float = 0.0
    lots: int = 1

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DaySummary:
    date: str
    instrument: str
    traded: bool
    outcome: str = ""
    pnl_points: float = 0.0
    rr_achieved: float = 0.0


# ── Strike helpers ─────────────────────────────────────────────────────
def get_atm_strike(spot: float, step: int) -> int:
    """Round spot to nearest strike step."""
    return int(round(spot / step) * step)


def get_itm_put_strike(spot: float, step: int) -> int:
    """One strike ITM for PUT = one step above ATM."""
    atm = get_atm_strike(spot, step)
    return atm + step


def select_put_strike(spot: float, step: int, premium_atm: float | None = None) -> int:
    """
    Choose ATM or 1-strike-ITM PUT based on premium threshold.
    If premium data unavailable, default to ATM.
    """
    if premium_atm is not None and premium_atm >= PREMIUM_THRESHOLD:
        return get_itm_put_strike(spot, step)
    return get_atm_strike(spot, step)


def build_option_symbol(underlying: str, strike: int, opt_type: str,
                        expiry: date) -> str:
    """
    Build NSE option tradingsymbol, e.g. NIFTY26MAR23800PE.
    Uses standard Zerodha format: NAME + YY + MMM + STRIKE + CE/PE.
    """
    yy = expiry.strftime("%y")
    mmm = expiry.strftime("%b").upper()
    # Weekly format for index options: NAME + YY + M + DD (single-char month)
    # But for simplicity, use the monthly-style which Kite also accepts
    # Actual resolution will be done via instrument lookup in live mode
    return f"{underlying}{yy}{mmm}{strike}{opt_type}"


# ── CSV / logging helpers ──────────────────────────────────────────────
def save_trades_csv(trades: List[TradeRecord], filename: str = "trades.csv") -> Path:
    """Write trade records to CSV in results directory."""
    filepath = RESULTS_DIR / filename
    if not trades:
        return filepath
    fieldnames = list(trades[0].as_dict().keys())
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(t.as_dict())
    return filepath


def save_summary_json(summary: dict, filename: str = "summary.json") -> Path:
    """Write summary report to JSON in results directory."""
    filepath = RESULTS_DIR / filename
    with open(filepath, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    return filepath


def print_summary_table(summary: dict) -> None:
    """Print a formatted console summary."""
    print("\n" + "=" * 60)
    print("  SECOND RED BREAK PUT STRATEGY — BACKTEST SUMMARY")
    print("=" * 60)
    for key, val in summary.items():
        if isinstance(val, dict):
            print(f"\n  [{key}]")
            for k2, v2 in val.items():
                print(f"    {k2:<25} : {v2}")
        else:
            print(f"  {key:<25} : {val}")
    print("=" * 60 + "\n")


# ── Data fetching wrappers ─────────────────────────────────────────────
def fetch_historical_5m(kite, instrument_token: int,
                        from_date: date, to_date: date) -> List[Candle]:
    """
    Fetch 5-minute OHLC via Kite historical_data API.
    Returns list of Candle objects sorted by time.
    Handles Kite API's max-60-day per request limit.
    """
    candles: List[Candle] = []
    current = from_date
    while current <= to_date:
        chunk_end = min(current + timedelta(days=59), to_date)
        data = kite.historical_data(
            instrument_token,
            from_date=current,
            to_date=chunk_end,
            interval="5minute",
        )
        for row in data:
            candles.append(Candle(
                date=row["date"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row.get("volume", 0),
            ))
        current = chunk_end + timedelta(days=1)
    return candles


def group_candles_by_day(candles: List[Candle]) -> dict[date, List[Candle]]:
    """Group candle list into {date: [candles]} dict."""
    days: dict[date, List[Candle]] = {}
    for c in candles:
        d = c.date.date() if isinstance(c.date, datetime) else c.date
        days.setdefault(d, []).append(c)
    # Sort each day's candles by time
    for d in days:
        days[d].sort(key=lambda x: x.date)
    return days


# ── Candle data cache (CSV) ────────────────────────────────────────────
DATA_DIR = STRATEGY_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def save_candles_csv(candles: List[Candle], instrument: str,
                     from_date: date, to_date: date) -> Path:
    """Cache candle data to CSV for offline re-use."""
    fname = f"{instrument.lower()}_5m_{from_date}_{to_date}.csv"
    fpath = DATA_DIR / fname
    with open(fpath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for c in candles:
            writer.writerow([c.date.isoformat(), c.open, c.high, c.low,
                             c.close, c.volume])
    print(f"  Saved {len(candles)} candles → {fpath}")
    return fpath


def load_candles_csv(instrument: str, from_date: date,
                     to_date: date) -> Optional[List[Candle]]:
    """Load cached candle CSV if it exists. Returns None if not found."""
    fname = f"{instrument.lower()}_5m_{from_date}_{to_date}.csv"
    fpath = DATA_DIR / fname
    if not fpath.exists():
        return None
    candles: List[Candle] = []
    with open(fpath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append(Candle(
                date=datetime.fromisoformat(row["date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(float(row.get("volume", 0))),
            ))
    candles.sort(key=lambda c: c.date)
    print(f"  Loaded {len(candles)} cached candles for {instrument} from {fpath.name}")
    return candles
