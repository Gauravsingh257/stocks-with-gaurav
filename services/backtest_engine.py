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
    sector: str | None = None

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
        sector=_safe_sector(symbol),
    )


def _safe_sector(symbol: str) -> str | None:
    """Pure symbol→sector lookup (read-only). 'Others' is a mixed catch-all,
    not a real concentrated sector, so we return None there to exempt it
    from the sector concentration cap rather than fake a bucket."""
    try:
        from engine.swing import get_sector

        s = get_sector(symbol)
        return None if not s or s == "Others" else s
    except Exception:
        return None


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


@dataclass(slots=True)
class PortfolioConfig:
    """F-Risk: realistic capital-constrained portfolio model. Replaces the
    naive 'whole account in every trade, compounded sequentially' drawdown
    (which produced the inflated 40-71% numbers — an artifact of an
    unrealistic model, not a real risk measure)."""
    risk_per_trade_pct: float = 1.0      # % of equity lost if the stop is hit
    max_position_pct: float = 20.0       # hard cap on a single position weight
    max_concurrent_positions: int = 8    # capital is finite
    max_per_sector: int = 3              # sector concentration cap (Others exempt)
    starting_equity: float = 1.0


def _simulate_portfolio(trades: list[BacktestTrade], cfg: PortfolioConfig) -> dict[str, Any]:
    """Replay the trade list as a real risk-sized, capacity-constrained
    portfolio. Account equity moves by (position_weight x trade_return), and
    drawdown is measured on the ACCOUNT curve — the number a real trader
    would actually experience."""
    ordered = sorted(trades, key=lambda t: (t.entry_date, t.scan_date, t.symbol))
    equity = cfg.starting_equity
    peak = equity
    max_dd = 0.0
    open_pos: list[dict[str, Any]] = []  # {exit_date, weight, ret_pct, sector}
    curve: list[dict[str, Any]] = []
    taken = 0
    skipped_capacity = 0
    skipped_capital = 0
    concurrent_samples: list[int] = []

    def _close_due(as_of: str) -> None:
        nonlocal equity, peak, max_dd, open_pos
        still: list[dict[str, Any]] = []
        for p in open_pos:
            if p["exit_date"] <= as_of:
                equity *= 1.0 + (p["weight"] * p["ret_pct"] / 100.0)
                peak = max(peak, equity)
                dd = (equity - peak) / peak * 100.0
                max_dd = min(max_dd, dd)
                curve.append({"date": p["exit_date"], "equity": round(equity, 5)})
            else:
                still.append(p)
        open_pos = still

    for t in ordered:
        _close_due(t.entry_date)
        risk_dist_pct = abs(t.entry - t.stop_loss) / t.entry * 100.0 if t.entry > 0 else 0.0
        if risk_dist_pct <= 0:
            continue
        weight = min(cfg.max_position_pct, cfg.risk_per_trade_pct / risk_dist_pct * 100.0) / 100.0
        committed = sum(p["weight"] for p in open_pos)
        sector_n = sum(1 for p in open_pos if p["sector"] and p["sector"] == t.sector)
        concurrent_samples.append(len(open_pos))

        if len(open_pos) >= cfg.max_concurrent_positions:
            skipped_capacity += 1
            continue
        if t.sector and sector_n >= cfg.max_per_sector:
            skipped_capacity += 1
            continue
        if committed + weight > 1.0:
            skipped_capital += 1
            continue

        open_pos.append({
            "exit_date": t.exit_date,
            "weight": weight,
            "ret_pct": t.return_pct,
            "sector": t.sector,
        })
        taken += 1

    # close any still-open at the end (exit-date order)
    for p in sorted(open_pos, key=lambda x: x["exit_date"]):
        equity *= 1.0 + (p["weight"] * p["ret_pct"] / 100.0)
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity - peak) / peak * 100.0)
        curve.append({"date": p["exit_date"], "equity": round(equity, 5)})

    total_ret = (equity / cfg.starting_equity - 1.0) * 100.0
    avg_conc = (sum(concurrent_samples) / len(concurrent_samples)) if concurrent_samples else 0.0
    return {
        "model": "risk_sized_capacity_constrained_portfolio",
        "config": asdict(cfg),
        "positions_taken": taken,
        "skipped_capacity": skipped_capacity,
        "skipped_capital": skipped_capital,
        "avg_concurrent_positions": round(avg_conc, 2),
        "account_total_return_pct": round(total_ret, 2),
        "account_max_drawdown_pct": round(abs(max_dd), 2),
        "final_equity_multiple": round(equity / cfg.starting_equity, 4),
        "equity_curve_points": len(curve),
    }


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
    universe_quality_filter: bool = False,
    quality_min_tier: str = "Good",
    portfolio_cfg: PortfolioConfig | None = None,
    gated_only: bool = False,
) -> dict[str, Any]:
    """Historical validation/backtest using the same 3-layer scan engine.

    Each scan uses OHLC data sliced to that date, selects only all-layer-pass
    names, enters at next day's open, then exits on target, stop, or time.

    When ``universe_quality_filter`` is set, the symbol set is pre-filtered
    by the Phase F2 Quality Universe Engine using ONLY OHLC up to
    ``start_date`` (point-in-time, no look-ahead). This isolates the effect
    of selection quality vs the unfiltered baseline.
    """
    portfolio_cfg = portfolio_cfg or PortfolioConfig()
    universe = load_nse_universe(target_universe)
    symbols = universe.symbols
    frames = await _fetch_frames(symbols, source, days=900, as_of=None)

    quality_filter_stats: dict[str, Any] | None = None
    if universe_quality_filter:
        from services.universe_quality import filter_symbols_by_quality

        symbols, quality_filter_stats = filter_symbols_by_quality(
            symbols, frames, cutoff=start_date, min_tier=quality_min_tier
        )
        # keep only kept symbols' frames for the scan loop
        frames = {s: frames.get(s) for s in symbols}

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
            disable_fallback_levels=gated_only,
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
        # NOTE: this max_drawdown is the legacy NAIVE model (whole account in
        # every trade, sequential compounding) — kept for continuity with the
        # F8/F3 baseline numbers. The realistic figure is in portfolio_metrics.
        "max_drawdown": _max_drawdown(returns),
        "sharpe_ratio": _sharpe(returns),
    }
    portfolio_metrics = _simulate_portfolio(trades, portfolio_cfg) if trades else None
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
        "universe_quality_filter": universe_quality_filter,
        "quality_filter_stats": quality_filter_stats,
        "gated_only": gated_only,
        "funnel_by_day": daily_funnels,
        "walk_forward": _walk_forward(trades),
        "metrics": metrics,
        "portfolio_metrics": portfolio_metrics,
        "trades": [trade.to_dict() for trade in trades],
        "data_notes": [
            "OHLC is sliced to each scan date to avoid price lookahead.",
            "Fundamental and sentiment snapshots use currently available project data providers unless historical providers are configured.",
        ],
    }


def run_backtest_sync(start_date: str, end_date: str, **kwargs) -> dict[str, Any]:
    return asyncio.run(run_backtest(start_date, end_date, **kwargs))


# ─────────────────────────────────────────────────────────────────────────
# PHASE G2-5 — planned-execution state-machine backtest (shadow / research)
# Parallel path; run_backtest above is untouched. Same metrics + portfolio
# model so results are DIRECTLY comparable to docs/ALPHA_BASELINE.md.
# ─────────────────────────────────────────────────────────────────────────

def _simulate_planned_entry_trade(
    symbol: str, daily: list[dict], fire, hold_days: int,
    transaction_cost_pct: float, slippage_pct: float,
) -> "BacktestTrade | None":
    """Enter at the PLANNED zone price (fire.entry, a LIMIT) starting the bar
    AFTER the fire bar, then exit on stop/target/time. Stop counted first on
    same-bar (conservative — identical convention to _simulate_long_trade).
    This tests planned-zone execution, NOT next-open chasing."""
    n = len(daily)
    start = fire.entry_idx + 1
    if start >= n:
        return None
    entry = float(fire.entry)
    sl = float(fire.stop_loss)
    target = float(fire.target)
    if entry <= 0 or sl >= entry:
        return None
    eff_entry = entry * (1.0 + slippage_pct / 100.0)
    end_idx = min(start + hold_days, n)
    raw_exit = float(daily[min(start + hold_days - 1, n - 1)]["close"])
    exit_date = str(daily[min(start + hold_days - 1, n - 1)].get("date"))[:10]
    exit_reason = "TIME_EXIT"
    for k in range(start, end_idx):
        c = daily[k]
        if float(c["low"]) <= sl:
            raw_exit, exit_reason = sl, "STOP_LOSS"
            exit_date = str(c.get("date"))[:10]
            break
        if float(c["high"]) >= target:
            raw_exit, exit_reason = target, "TARGET_HIT"
            exit_date = str(c.get("date"))[:10]
            break
    eff_exit = raw_exit * (1.0 - slippage_pct / 100.0)
    net = (eff_exit - eff_entry) / eff_entry * 100.0 - 2.0 * transaction_cost_pct
    gross = (raw_exit - entry) / entry * 100.0
    return BacktestTrade(
        symbol=symbol, scan_date=str(daily[fire.entry_idx].get("date"))[:10],
        entry_date=str(daily[start].get("date"))[:10], exit_date=exit_date,
        entry=round(eff_entry, 2), stop_loss=round(sl, 2), target=round(target, 2),
        exit_price=round(eff_exit, 2), exit_reason=exit_reason,
        return_pct=round(net, 2), gross_return_pct=round(gross, 2),
        transaction_cost_pct=round(transaction_cost_pct, 4),
        slippage_pct=round(slippage_pct, 4), confidence=0.0,
        setup="SMC_STATE_MACHINE_LONG", sector=_safe_sector(symbol),
    )


async def run_state_machine_backtest(
    start_date: str,
    end_date: str,
    *,
    target_universe: int = 300,
    hold_days: int = 15,
    source: str = "yfinance",
    transaction_cost_pct: float = 0.10,
    slippage_pct: float = 0.05,
    quality_min_tier: str = "Good",
    portfolio_cfg: PortfolioConfig | None = None,
) -> dict[str, Any]:
    """G2-5: planned-execution state machine over the F2-filtered equity
    universe, point-in-time. Records shadow lifecycle events. Read-only."""
    from datetime import date as _date

    from services.research_levels import daily_candles_to_weekly, df_to_candles
    from services.universe_quality import filter_symbols_by_quality
    from services.validation_engine import _fetch_frames, _slice_to_date
    from services.state_machine_sim import simulate_state_machine_entries

    portfolio_cfg = portfolio_cfg or PortfolioConfig()
    universe = load_nse_universe(target_universe)
    frames = await _fetch_frames(universe.symbols, source, days=900, as_of=None)
    symbols, qstats = filter_symbols_by_quality(
        universe.symbols, frames, cutoff=start_date, min_tier=quality_min_tier
    )

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    trades: list[BacktestTrade] = []
    armed = fired = 0

    for sym in symbols:
        df = _slice_to_date(frames.get(sym), end_date)
        daily = df_to_candles(df)
        if len(daily) < 70:
            continue
        weekly = daily_candles_to_weekly(daily)
        try:
            fire_list = simulate_state_machine_entries(daily, weekly, expiry_bars=15)
        except Exception as exc:
            log.debug("state-machine sim failed %s: %s", sym, exc)
            continue
        for fr in fire_list:
            fdt = pd.Timestamp(str(daily[fr.entry_idx].get("date"))[:10])
            if not (start_ts <= fdt <= end_ts):
                continue
            armed += 1
            tr = _simulate_planned_entry_trade(
                sym, daily, fr, hold_days, transaction_cost_pct, slippage_pct
            )
            if tr is not None:
                fired += 1
                trades.append(tr)
                try:
                    from dashboard.backend.lifecycle_ledger import record_lifecycle_event
                    record_lifecycle_event(
                        sym, "ENTRY_ACTIVE", horizon="SWING",
                        source="g2_5_state_machine_shadow", prev_state="ARMED",
                        planned_entry=tr.entry, stop_loss=tr.stop_loss,
                        target_1=tr.target, setup=tr.setup,
                        details={"backtest": True, "fire_date": tr.scan_date},
                    )
                except Exception:
                    pass

    returns = [t.return_pct for t in trades]
    wins = [t for t in trades if t.return_pct > 0]
    total = len(trades)
    metrics = {
        "total_trades": total,
        "win_rate": round(len(wins) / total * 100.0, 2) if total else 0.0,
        "avg_return": round(sum(returns) / total, 2) if total else 0.0,
        "max_drawdown": _max_drawdown(returns),
        "sharpe_ratio": _sharpe(returns),
    }
    portfolio_metrics = _simulate_portfolio(trades, portfolio_cfg) if trades else None
    return {
        "engine_mode": "state_machine",
        "start_date": start_date, "end_date": end_date, "horizon": "SWING",
        "hold_days": hold_days,
        "quality_filter_stats": qstats,
        "state_machine": {"armed": armed, "fired": fired},
        "walk_forward": _walk_forward(trades),
        "metrics": metrics,
        "portfolio_metrics": portfolio_metrics,
        "trades": [t.to_dict() for t in trades],
        "data_notes": [
            "Planned-execution: entry at the FVG-mid LIMIT, bar AFTER fire.",
            "Faithful bar-clocked reproduction of detect_setup_a (see "
            "services/state_machine_sim.py docstring). Point-in-time, no look-ahead.",
            "Shadow only — zero production behaviour change.",
        ],
    }
