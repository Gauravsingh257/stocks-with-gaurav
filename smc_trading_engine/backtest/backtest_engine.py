"""
Backtest Engine
===============
Candle-by-candle simulation with no future leak.
Includes slippage, brokerage, RR tracking.
Exports results to CSV.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, time
import logging
import csv

from smc_trading_engine.smc.market_structure import calculate_atr
from smc_trading_engine.smc.bos_choch import detect_bias
from smc_trading_engine.smc.order_blocks import detect_order_blocks, is_price_in_ob
from smc_trading_engine.smc.fvg import detect_fvg, is_price_in_fvg
from smc_trading_engine.smc.liquidity import detect_all_liquidity
from smc_trading_engine.strategy.entry_model import (
    evaluate_entry, has_confirmation_candle, is_in_session,
    has_volume_expansion, is_mid_range_entry, compute_confidence
)
from smc_trading_engine.strategy.risk_management import RiskManager, RiskParams

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    target: float
    rr: float
    entry_time: datetime = None
    exit_time: datetime = None
    exit_price: float = 0.0
    pnl: float = 0.0
    result: str = ""  # "WIN", "LOSS", "BREAKEVEN"
    confidence: float = 0.0
    slippage_cost: float = 0.0
    brokerage_cost: float = 0.0
    bars_held: int = 0


@dataclass
class BacktestResult:
    trades: List[BacktestTrade] = field(default_factory=list)
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_rr: float = 0.0
    max_drawdown: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0


class BacktestEngine:
    """Candle-by-candle backtesting engine. No future leak."""

    def __init__(self, risk_params: RiskParams = None,
                 htf_interval: str = "15minute", ltf_interval: str = "5minute"):
        self.risk_mgr = RiskManager(risk_params or RiskParams())
        self.htf_interval = htf_interval
        self.ltf_interval = ltf_interval
        self.trades: List[BacktestTrade] = []
        self.active_trade: Optional[BacktestTrade] = None
        self.equity_curve: List[float] = []

    def run(self, symbol: str, htf_df: pd.DataFrame,
            ltf_df: pd.DataFrame, progress_cb=None) -> BacktestResult:
        """
        Run backtest candle-by-candle on LTF data.
        HTF is used for bias detection (uses only data available up to current bar).
        """
        if len(ltf_df) < 50 or len(htf_df) < 30:
            logger.warning("Insufficient data for backtest")
            return BacktestResult()

        equity = self.risk_mgr.params.account_size
        peak_equity = equity
        max_dd = 0.0
        self.equity_curve = [equity]
        self.trades = []
        min_bars = 30  # warmup

        for i in range(min_bars, len(ltf_df)):
            # Slice data up to current bar (no future leak)
            ltf_slice = ltf_df.iloc[:i + 1].copy()
            current = ltf_df.iloc[i]
            current_price = float(current['close'])
            bar_time = ltf_df.index[i] if isinstance(ltf_df.index, pd.DatetimeIndex) else None

            # ── Manage active trade ──
            if self.active_trade is not None:
                self.active_trade.bars_held += 1
                hit = self._check_exit(current, self.active_trade)
                if hit:
                    net_pnl = self.active_trade.pnl - self.active_trade.brokerage_cost
                    equity += net_pnl
                    self.trades.append(self.active_trade)
                    self.risk_mgr.record_trade_result(net_pnl)
                    self.active_trade = None
                self.equity_curve.append(equity)

                # Track drawdown
                peak_equity = max(peak_equity, equity)
                dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
                max_dd = max(max_dd, dd)
                continue

            # ── Look for new entry ──
            # Get HTF slice up to current time
            if bar_time and isinstance(htf_df.index, pd.DatetimeIndex):
                htf_slice = htf_df[htf_df.index <= bar_time].copy()
            else:
                ratio = max(1, len(htf_df) * i // len(ltf_df))
                htf_slice = htf_df.iloc[:ratio].copy()

            if len(htf_slice) < 20:
                self.equity_curve.append(equity)
                continue

            # Session filter
            if bar_time:
                ct = bar_time.time()
                if not is_in_session(ct):
                    self.equity_curve.append(equity)
                    continue

            # Use entry model
            setup, rejection = evaluate_entry(
                symbol, htf_slice, ltf_slice,
                risk_mgr=self.risk_mgr,
                current_time=bar_time.time() if bar_time else None
            )

            if setup:
                entry_with_slip = self.risk_mgr.apply_slippage(
                    setup.entry, setup.direction, True)
                trade = BacktestTrade(
                    symbol=symbol, direction=setup.direction,
                    entry_price=entry_with_slip,
                    stop_loss=setup.stop_loss, target=setup.target,
                    rr=setup.rr, entry_time=bar_time,
                    confidence=setup.confidence_score,
                    slippage_cost=abs(entry_with_slip - setup.entry),
                    brokerage_cost=self.risk_mgr.total_costs()
                )
                self.active_trade = trade

            self.equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
            max_dd = max(max_dd, dd)

            if progress_cb and i % 100 == 0:
                progress_cb(i, len(ltf_df))

        return self._compile_results(max_dd)

    def _check_exit(self, candle, trade: BacktestTrade) -> bool:
        """Check if SL or TP was hit on current candle."""
        if trade.direction == "LONG":
            if candle['low'] <= trade.stop_loss:
                trade.exit_price = self.risk_mgr.apply_slippage(
                    trade.stop_loss, "LONG", False)
                trade.pnl = trade.exit_price - trade.entry_price
                trade.result = "LOSS"
                trade.exit_time = candle.name if hasattr(candle, 'name') else None
                return True
            if candle['high'] >= trade.target:
                trade.exit_price = trade.target
                trade.pnl = trade.exit_price - trade.entry_price
                trade.result = "WIN"
                trade.exit_time = candle.name if hasattr(candle, 'name') else None
                return True
        else:  # SHORT
            if candle['high'] >= trade.stop_loss:
                trade.exit_price = self.risk_mgr.apply_slippage(
                    trade.stop_loss, "SHORT", False)
                trade.pnl = trade.entry_price - trade.exit_price
                trade.result = "LOSS"
                trade.exit_time = candle.name if hasattr(candle, 'name') else None
                return True
            if candle['low'] <= trade.target:
                trade.exit_price = trade.target
                trade.pnl = trade.entry_price - trade.exit_price
                trade.result = "WIN"
                trade.exit_time = candle.name if hasattr(candle, 'name') else None
                return True
        return False

    def _compile_results(self, max_dd: float) -> BacktestResult:
        r = BacktestResult(trades=self.trades, max_drawdown=round(max_dd * 100, 2))
        if not self.trades:
            return r
        r.win_count = sum(1 for t in self.trades if t.result == "WIN")
        r.loss_count = sum(1 for t in self.trades if t.result == "LOSS")
        total = r.win_count + r.loss_count
        r.win_rate = round(r.win_count / total * 100, 2) if total else 0
        r.total_pnl = round(sum(t.pnl - t.brokerage_cost for t in self.trades), 2)
        rrs = [t.rr for t in self.trades if t.rr > 0]
        r.avg_rr = round(np.mean(rrs), 2) if rrs else 0
        # Expectancy
        wins = [t.pnl for t in self.trades if t.result == "WIN"]
        losses = [abs(t.pnl) for t in self.trades if t.result == "LOSS"]
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 1
        wr = r.win_rate / 100
        r.expectancy = round(wr * avg_win - (1 - wr) * avg_loss, 2)
        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 1
        r.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0
        # Sharpe (simplified daily)
        pnls = [t.pnl for t in self.trades]
        if len(pnls) > 1:
            r.sharpe_ratio = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252), 2)
        return r

    def export_csv(self, path: str):
        """Export trade results to CSV."""
        if not self.trades:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "direction", "entry_price", "exit_price",
                         "stop_loss", "target", "rr", "pnl", "result",
                         "confidence", "entry_time", "exit_time", "bars_held"])
            for t in self.trades:
                w.writerow([t.symbol, t.direction, t.entry_price, t.exit_price,
                            t.stop_loss, t.target, t.rr, round(t.pnl, 2),
                            t.result, t.confidence, t.entry_time, t.exit_time,
                            t.bars_held])
