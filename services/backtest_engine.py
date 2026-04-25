from __future__ import annotations

import asyncio
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from services.universe_manager import load_nse_universe
from services.validation_engine import Horizon, _fetch_frames, run_validation_scan


@dataclass(slots=True)
class BacktestTrade:
    symbol: str
    scan_date: str
    entry_date: str
    exit_date: str
    entry: float
    stop_loss: float
    target: float
    exit_price: float
    exit_reason: str
    return_pct: float
    gross_return_pct: float
    transaction_cost_pct: float
    slippage_pct: float
    confidence: float
    setup: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def _date_label(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _normalize_frame(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    frame = df.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    if "date" not in frame.columns:
        if isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.reset_index().rename(columns={frame.reset_index().columns[0]: "date"})
        else:
            return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_convert(None)
    frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(frame.columns)):
        return None
    for col in ("open", "high", "low", "close"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    return frame if not frame.empty else None


def _scan_dates(frames: dict[str, pd.DataFrame | None], start_date: str, end_date: str) -> list[str]:
    dates: set[str] = set()
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    for frame in frames.values():
        norm = _normalize_frame(frame)
        if norm is None:
            continue
        mask = (norm["date"] >= start) & (norm["date"] <= end)
        dates.update(d.date().isoformat() for d in norm.loc[mask, "date"])
    return sorted(dates)


def _next_row_after(df: pd.DataFrame, scan_date: str) -> tuple[int, pd.Series] | None:
    scan_ts = pd.Timestamp(scan_date)
    rows = df.index[df["date"] > scan_ts].tolist()
    if not rows:
        return None
    idx = int(rows[0])
    return idx, df.loc[idx]


def _simulate_long_trade(
    symbol: str,
    frame: pd.DataFrame | None,
    scan_date: str,
    stop_loss: float,
    target: float,
    confidence: float,
    setup: str | None,
    hold_days: int,
    transaction_cost_pct: float,
    slippage_pct: float,
) -> BacktestTrade | None:
    df = _normalize_frame(frame)
    if df is None:
        return None
    start = _next_row_after(df, scan_date)
    if start is None:
        return None
    start_idx, entry_row = start
    raw_entry = float(entry_row["open"])
    if raw_entry <= 0:
        return None
    entry = raw_entry * (1.0 + slippage_pct / 100.0)

    raw_exit_price = float(df.loc[min(start_idx + hold_days - 1, len(df) - 1), "close"])
    exit_date = pd.Timestamp(df.loc[min(start_idx + hold_days - 1, len(df) - 1), "date"]).date().isoformat()
    exit_reason = "TIME_EXIT"

    end_idx = min(start_idx + hold_days, len(df))
    for idx in range(start_idx, end_idx):
        row = df.loc[idx]
        row_date = pd.Timestamp(row["date"]).date().isoformat()
        low = float(row["low"])
        high = float(row["high"])
        # Conservative same-day ordering: if both are touched, count stop first.
        if low <= stop_loss:
            raw_exit_price = float(stop_loss)
            exit_date = row_date
            exit_reason = "STOP_LOSS"
            break
        if high >= target:
            raw_exit_price = float(target)
            exit_date = row_date
            exit_reason = "TARGET_HIT"
            break
    exit_price = raw_exit_price * (1.0 - slippage_pct / 100.0)
    gross_return_pct = (raw_exit_price - raw_entry) / raw_entry * 100.0
    net_return_pct = (exit_price - entry) / entry * 100.0 - (2.0 * transaction_cost_pct)

    return BacktestTrade(
        symbol=symbol,
        scan_date=scan_date,
        entry_date=pd.Timestamp(entry_row["date"]).date().isoformat(),
        exit_date=exit_date,
        entry=round(entry, 2),
        stop_loss=round(float(stop_loss), 2),
        target=round(float(target), 2),
        exit_price=round(exit_price, 2),
        exit_reason=exit_reason,
        return_pct=round(net_return_pct, 2),
        gross_return_pct=round(gross_return_pct, 2),
        transaction_cost_pct=round(float(transaction_cost_pct), 4),
        slippage_pct=round(float(slippage_pct), 4),
        confidence=round(float(confidence), 2),
        setup=setup,
    )


def _max_drawdown(returns_pct: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns_pct:
        equity *= 1.0 + (ret / 100.0)
        peak = max(peak, equity)
        dd = (equity - peak) / peak * 100.0
        max_dd = min(max_dd, dd)
    return round(abs(max_dd), 2)


def _sharpe(returns_pct: list[float]) -> float:
    if len(returns_pct) < 2:
        return 0.0
    avg = sum(returns_pct) / len(returns_pct)
    var = sum((r - avg) ** 2 for r in returns_pct) / (len(returns_pct) - 1)
    stdev = math.sqrt(var)
    if stdev == 0:
        return 0.0
    return round((avg / stdev) * math.sqrt(252), 2)


def _walk_forward(trades: list[BacktestTrade]) -> list[dict[str, Any]]:
    buckets: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        window = str(trade.entry_date)[:7]
        buckets.setdefault(window, []).append(trade)
    output: list[dict[str, Any]] = []
    for window in sorted(buckets):
        rows = buckets[window]
        returns = [row.return_pct for row in rows]
        wins = [row for row in rows if row.return_pct > 0]
        output.append(
            {
                "window": window,
                "trades": len(rows),
                "win_rate": round(len(wins) / len(rows) * 100.0, 2) if rows else 0.0,
                "avg_return": round(sum(returns) / len(rows), 2) if rows else 0.0,
                "max_drawdown": _max_drawdown(returns),
            }
        )
    return output


async def run_backtest(
    start_date: str,
    end_date: str,
    *,
    horizon: Horizon = "SWING",
    top_n: int = 5,
    target_universe: int = 500,
    hold_days: int = 20,
    source: str = "yfinance",
    scan_step_days: int = 1,
    log_scans: bool = False,
    transaction_cost_pct: float = 0.10,
    slippage_pct: float = 0.05,
) -> dict[str, Any]:
    """Historical validation/backtest using the same 3-layer scan engine.

    Each scan uses OHLC data sliced to that date, selects only all-layer-pass
    names, enters at next day's open, then exits on target, stop, or time.
    """
    universe = load_nse_universe(target_universe)
    symbols = universe.symbols
    frames = await _fetch_frames(symbols, source, days=900, as_of=None)
    dates = _scan_dates(frames, start_date, end_date)
    if scan_step_days > 1:
        dates = dates[::scan_step_days]

    trades: list[BacktestTrade] = []
    daily_funnels: list[dict] = []
    coverage: dict | None = None

    for scan_date in dates:
        result = await run_validation_scan(
            horizon=horizon,
            top_k=top_n,
            target_universe=target_universe,
            symbols=symbols,
            source=source,
            as_of=scan_date,
            log_scan=log_scans,
            historical_frames=frames,
        )
        coverage = result.coverage.to_dict()
        daily_funnels.append({"date": scan_date, **result.funnel.to_dict()})
        for record in result.selected[:top_n]:
            if not record.targets or record.stop_loss is None:
                continue
            trade = _simulate_long_trade(
                symbol=record.symbol,
                frame=frames.get(record.symbol),
                scan_date=scan_date,
                stop_loss=float(record.stop_loss),
                target=float(record.targets[-1]),
                confidence=record.confidence_score,
                setup=record.setup,
                hold_days=hold_days,
                transaction_cost_pct=transaction_cost_pct,
                slippage_pct=slippage_pct,
            )
            if trade is not None:
                trades.append(trade)

    returns = [trade.return_pct for trade in trades]
    gross_returns = [trade.gross_return_pct for trade in trades]
    wins = [trade for trade in trades if trade.return_pct > 0]
    total = len(trades)
    metrics = {
        "total_trades": total,
        "win_rate": round(len(wins) / total * 100.0, 2) if total else 0.0,
        "avg_return": round(sum(returns) / total, 2) if total else 0.0,
        "avg_gross_return": round(sum(gross_returns) / total, 2) if total else 0.0,
        "max_drawdown": _max_drawdown(returns),
        "sharpe_ratio": _sharpe(returns),
    }
    return {
        "start_date": start_date,
        "end_date": end_date,
        "horizon": horizon,
        "top_n": top_n,
        "hold_days": hold_days,
        "scan_step_days": scan_step_days,
        "cost_assumptions": {
            "transaction_cost_pct_per_side": transaction_cost_pct,
            "slippage_pct_per_side": slippage_pct,
            "entry_model": "next_candle_open_plus_slippage",
            "exit_model": "target_stop_or_time_minus_slippage",
        },
        "coverage": coverage or {},
        "funnel_by_day": daily_funnels,
        "walk_forward": _walk_forward(trades),
        "metrics": metrics,
        "trades": [trade.to_dict() for trade in trades],
        "data_notes": [
            "OHLC is sliced to each scan date to avoid price lookahead.",
            "Fundamental and sentiment snapshots use currently available project data providers unless historical providers are configured.",
        ],
    }


def run_backtest_sync(start_date: str, end_date: str, **kwargs) -> dict[str, Any]:
    return asyncio.run(run_backtest(start_date, end_date, **kwargs))
