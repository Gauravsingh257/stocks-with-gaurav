"""
Backtester — Event-driven backtesting engine for AI-generated strategies.
==========================================================================
Simulates strategy execution on historical candle data with realistic
fills, slippage, and trade management.
"""

import logging
import numpy as np
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("ai_learning.backtester")


@dataclass
class BacktestTrade:
    """A single backtested trade."""
    trade_id: int
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    target: float
    entry_bar: int
    exit_bar: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    pnl_points: float = 0.0
    pnl_r: float = 0.0
    result: str = ""         # WIN, LOSS, BE
    bars_held: int = 0
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    strategy_name: str = ""
    score: float = 0.0


@dataclass
class BacktestResult:
    """Complete backtest result."""
    strategy_name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    total_pnl_r: float = 0.0
    max_drawdown_r: float = 0.0
    max_consecutive_losses: int = 0
    avg_bars_held: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "avg_win_r": round(self.avg_win_r, 3),
            "avg_loss_r": round(self.avg_loss_r, 3),
            "expectancy_r": round(self.expectancy_r, 4),
            "profit_factor": round(self.profit_factor, 3),
            "total_pnl_r": round(self.total_pnl_r, 2),
            "max_drawdown_r": round(self.max_drawdown_r, 3),
            "max_consecutive_losses": self.max_consecutive_losses,
            "avg_bars_held": round(self.avg_bars_held, 1),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "calmar_ratio": round(self.calmar_ratio, 3),
            "params": self.params,
        }

    def summary(self) -> str:
        return (
            f"═══ {self.strategy_name} Backtest ═══\n"
            f"Trades: {self.total_trades}  |  WR: {self.win_rate:.1%}  |  "
            f"PF: {self.profit_factor:.2f}\n"
            f"Expectancy: {self.expectancy_r:.3f}R  |  "
            f"Total PnL: {self.total_pnl_r:.1f}R\n"
            f"Max DD: {self.max_drawdown_r:.2f}R  |  "
            f"Sharpe: {self.sharpe_ratio:.2f}\n"
            f"Avg Win: {self.avg_win_r:.2f}R  |  "
            f"Avg Loss: {self.avg_loss_r:.2f}R\n"
            f"Max Consec Losses: {self.max_consecutive_losses}  |  "
            f"Avg Hold: {self.avg_bars_held:.0f} bars"
        )


class Backtester:
    """
    Event-driven backtester for strategy evaluation.

    Usage:
        bt = Backtester()
        result = bt.run(
            candles=candles_5m,
            signal_func=detect_my_strategy,
            params={"sl_atr_mult": 1.5, "tp_rr_ratio": 2.5}
        )
        print(result.summary())
    """

    def __init__(
        self,
        slippage_pct: float = 0.01,    # 0.01% slippage
        commission_pct: float = 0.0,    # commission per trade
        max_trades_per_day: int = 5,
        cooldown_bars: int = 6,         # bars between trades (30 min on 5m)
    ):
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct
        self.max_trades_per_day = max_trades_per_day
        self.cooldown_bars = cooldown_bars

    def run(
        self,
        candles: List[dict],
        signal_func: Callable,
        strategy_name: str = "Strategy",
        params: Optional[Dict[str, Any]] = None,
        htf_candles: Optional[List[dict]] = None,
        min_lookback: int = 50,
    ) -> BacktestResult:
        """
        Run a backtest.

        Args:
            candles: OHLC candle data (5m or desired TF)
            signal_func: Function(candles_slice, htf_candles, **params) -> signal_dict or None
            strategy_name: Name for reporting
            params: Strategy parameters
            htf_candles: Higher timeframe context candles
            min_lookback: Minimum bars before first signal check

        Returns:
            BacktestResult with full metrics
        """
        params = params or {}
        trades: List[BacktestTrade] = []
        trade_counter = 0
        active_trade: Optional[BacktestTrade] = None
        last_trade_bar = -self.cooldown_bars
        equity = [0.0]

        for i in range(min_lookback, len(candles)):
            bar = candles[i]

            # ─── Manage active trade ─────────────────────────────
            if active_trade:
                exit_price, exit_reason = self._check_exit(
                    active_trade, bar, i, params
                )
                if exit_price is not None:
                    self._close_trade(active_trade, exit_price, exit_reason, i)
                    trades.append(active_trade)
                    equity.append(equity[-1] + active_trade.pnl_r)
                    last_trade_bar = i
                    active_trade = None
                else:
                    # Update max favorable/adverse
                    if active_trade.direction == "LONG":
                        active_trade.max_favorable = max(
                            active_trade.max_favorable,
                            bar["high"] - active_trade.entry_price
                        )
                        active_trade.max_adverse = max(
                            active_trade.max_adverse,
                            active_trade.entry_price - bar["low"]
                        )
                    else:
                        active_trade.max_favorable = max(
                            active_trade.max_favorable,
                            active_trade.entry_price - bar["low"]
                        )
                        active_trade.max_adverse = max(
                            active_trade.max_adverse,
                            bar["high"] - active_trade.entry_price
                        )
                continue

            # ─── Check for new signal ────────────────────────────
            if i - last_trade_bar < self.cooldown_bars:
                continue

            candle_slice = candles[max(0, i - 200):i + 1]
            try:
                signal = signal_func(candle_slice, htf_candles, **params)
            except Exception as e:
                log.debug(f"Signal func error at bar {i}: {e}")
                continue

            if not signal:
                continue

            # ─── Open trade ──────────────────────────────────────
            trade_counter += 1
            entry = signal.get("entry", bar["close"])
            sl = signal.get("sl", signal.get("stop_loss"))
            target = signal.get("target", signal.get("tp1"))
            direction = signal.get("direction", "LONG")

            if not sl or not target:
                continue

            # Apply slippage
            slip = entry * self.slippage_pct / 100
            if direction == "LONG":
                entry += slip
            else:
                entry -= slip

            active_trade = BacktestTrade(
                trade_id=trade_counter,
                symbol=signal.get("symbol", ""),
                direction=direction,
                entry_price=entry,
                stop_loss=sl,
                target=target,
                entry_bar=i,
                strategy_name=strategy_name,
                score=signal.get("score", 0),
            )

        # Close any remaining trade at last bar
        if active_trade:
            last_bar = candles[-1]
            self._close_trade(active_trade, last_bar["close"], "END_OF_DATA", len(candles) - 1)
            trades.append(active_trade)
            equity.append(equity[-1] + active_trade.pnl_r)

        return self._compute_metrics(trades, equity, strategy_name, params)

    def _check_exit(
        self,
        trade: BacktestTrade,
        bar: dict,
        bar_idx: int,
        params: dict,
    ) -> Tuple[Optional[float], str]:
        """Check if trade should exit on this bar."""
        if trade.direction == "LONG":
            # Stop loss hit
            if bar["low"] <= trade.stop_loss:
                return trade.stop_loss, "STOP_LOSS"
            # Target hit
            if bar["high"] >= trade.target:
                return trade.target, "TARGET"
        else:
            if bar["high"] >= trade.stop_loss:
                return trade.stop_loss, "STOP_LOSS"
            if bar["low"] <= trade.target:
                return trade.target, "TARGET"

        # Time-based exit (max 60 bars = 5 hours on 5m)
        max_bars = params.get("max_hold_bars", 60)
        if bar_idx - trade.entry_bar >= max_bars:
            return bar["close"], "TIME_EXIT"

        return None, ""

    def _close_trade(
        self,
        trade: BacktestTrade,
        exit_price: float,
        reason: str,
        exit_bar: int,
    ):
        """Close a trade and compute P&L."""
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.exit_bar = exit_bar
        trade.bars_held = exit_bar - trade.entry_bar

        risk = abs(trade.entry_price - trade.stop_loss)
        if risk <= 0:
            risk = 1

        if trade.direction == "LONG":
            trade.pnl_points = exit_price - trade.entry_price
        else:
            trade.pnl_points = trade.entry_price - exit_price

        trade.pnl_r = round(trade.pnl_points / risk, 3)

        if trade.pnl_r > 0.1:
            trade.result = "WIN"
        elif trade.pnl_r < -0.1:
            trade.result = "LOSS"
        else:
            trade.result = "BE"

    def _compute_metrics(
        self,
        trades: List[BacktestTrade],
        equity: List[float],
        strategy_name: str,
        params: dict,
    ) -> BacktestResult:
        """Compute comprehensive backtest metrics."""
        result = BacktestResult(strategy_name=strategy_name, params=params)

        if not trades:
            return result

        result.total_trades = len(trades)
        result.wins = sum(1 for t in trades if t.result == "WIN")
        result.losses = sum(1 for t in trades if t.result == "LOSS")
        result.breakeven = sum(1 for t in trades if t.result == "BE")
        result.win_rate = result.wins / result.total_trades if result.total_trades > 0 else 0
        result.trades = trades
        result.equity_curve = equity

        # Average win/loss in R
        win_rs = [t.pnl_r for t in trades if t.result == "WIN"]
        loss_rs = [abs(t.pnl_r) for t in trades if t.result == "LOSS"]
        result.avg_win_r = float(np.mean(win_rs)) if win_rs else 0
        result.avg_loss_r = float(np.mean(loss_rs)) if loss_rs else 1

        # Expectancy
        result.expectancy_r = (
            result.win_rate * result.avg_win_r -
            (1 - result.win_rate) * result.avg_loss_r
        )

        # Profit factor
        gross_profit = sum(t.pnl_r for t in trades if t.pnl_r > 0)
        gross_loss = abs(sum(t.pnl_r for t in trades if t.pnl_r < 0))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Total PnL
        result.total_pnl_r = sum(t.pnl_r for t in trades)

        # Drawdown
        peak = 0.0
        max_dd = 0.0
        for eq in equity:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_r = max_dd

        # Max consecutive losses
        max_consec = 0
        current_consec = 0
        for t in trades:
            if t.result == "LOSS":
                current_consec += 1
                max_consec = max(max_consec, current_consec)
            else:
                current_consec = 0
        result.max_consecutive_losses = max_consec

        # Average bars held
        result.avg_bars_held = float(np.mean([t.bars_held for t in trades]))

        # Sharpe ratio (per-trade returns)
        returns = np.array([t.pnl_r for t in trades])
        if len(returns) > 1 and returns.std() > 0:
            result.sharpe_ratio = float(returns.mean() / returns.std() * np.sqrt(252))
        else:
            result.sharpe_ratio = 0.0

        # Sortino ratio
        downside = returns[returns < 0]
        if len(downside) > 1 and downside.std() > 0:
            result.sortino_ratio = float(returns.mean() / downside.std() * np.sqrt(252))
        else:
            result.sortino_ratio = result.sharpe_ratio

        # Calmar ratio
        if max_dd > 0:
            result.calmar_ratio = float(result.total_pnl_r / max_dd)
        else:
            result.calmar_ratio = float('inf') if result.total_pnl_r > 0 else 0

        return result
