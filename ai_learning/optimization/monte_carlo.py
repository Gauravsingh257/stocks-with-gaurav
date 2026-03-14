"""
Monte Carlo & Walk-Forward Analysis
=====================================
Statistical validation of strategy robustness.
"""

import logging
import numpy as np
from typing import List, Dict, Any, Tuple

from ai_learning.config import (
    MONTE_CARLO_SIMULATIONS, MONTE_CARLO_CONFIDENCE,
    WALK_FORWARD_WINDOWS, WALK_FORWARD_TRAIN_PCT,
)
from ai_learning.optimization.backtester import Backtester, BacktestResult

log = logging.getLogger("ai_learning.monte_carlo")


class MonteCarloAnalyzer:
    """
    Monte Carlo simulation to assess strategy robustness.

    Randomly resamples trade sequences to estimate:
    - Distribution of possible returns
    - Probability of ruin
    - Confidence intervals for key metrics
    """

    def __init__(self, n_simulations: int = MONTE_CARLO_SIMULATIONS):
        self.n_simulations = n_simulations

    def analyze(self, backtest: BacktestResult) -> Dict[str, Any]:
        """
        Run Monte Carlo analysis on backtest results.

        Args:
            backtest: BacktestResult from backtester

        Returns:
            dict with MC metrics
        """
        if not backtest.trades or len(backtest.trades) < 10:
            log.warning("Insufficient trades for Monte Carlo analysis")
            return {"error": "insufficient_trades"}

        trade_returns = [t.pnl_r for t in backtest.trades]
        n_trades = len(trade_returns)

        # Run simulations
        final_equities = []
        max_drawdowns = []
        win_rates = []
        max_consec_losses_all = []

        rng = np.random.default_rng(42)

        for _ in range(self.n_simulations):
            # Resample with replacement
            sample = rng.choice(trade_returns, size=n_trades, replace=True)

            # Compute equity curve
            equity = np.cumsum(sample)
            final_equities.append(float(equity[-1]))

            # Drawdown
            peak = np.maximum.accumulate(equity)
            dd = peak - equity
            max_drawdowns.append(float(dd.max()))

            # Win rate
            wr = np.sum(sample > 0) / len(sample)
            win_rates.append(float(wr))

            # Consecutive losses
            max_cl = 0
            cl = 0
            for r in sample:
                if r < 0:
                    cl += 1
                    max_cl = max(max_cl, cl)
                else:
                    cl = 0
            max_consec_losses_all.append(max_cl)

        final_eq = np.array(final_equities)
        max_dd = np.array(max_drawdowns)

        # Confidence intervals
        alpha = 1 - MONTE_CARLO_CONFIDENCE
        ci_low = alpha / 2 * 100
        ci_high = (1 - alpha / 2) * 100

        result = {
            "n_simulations": self.n_simulations,
            "n_original_trades": n_trades,

            # Return distribution
            "median_return_r": round(float(np.median(final_eq)), 2),
            "mean_return_r": round(float(np.mean(final_eq)), 2),
            f"p{int(ci_low)}_return_r": round(float(np.percentile(final_eq, ci_low)), 2),
            f"p{int(ci_high)}_return_r": round(float(np.percentile(final_eq, ci_high)), 2),
            "p5_return_r": round(float(np.percentile(final_eq, 5)), 2),
            "p95_return_r": round(float(np.percentile(final_eq, 95)), 2),

            # Drawdown distribution
            "median_max_dd_r": round(float(np.median(max_dd)), 2),
            "p95_max_dd_r": round(float(np.percentile(max_dd, 95)), 2),

            # Probability of ruin (ending below 0)
            "ruin_probability": round(float(np.mean(final_eq < 0)), 4),

            # Probability of positive outcome
            "profit_probability": round(float(np.mean(final_eq > 0)), 4),

            # Win rate distribution
            "median_win_rate": round(float(np.median(win_rates)), 4),
            "p5_win_rate": round(float(np.percentile(win_rates, 5)), 4),

            # Max consecutive losses
            "median_max_consec_losses": int(np.median(max_consec_losses_all)),
            "p95_max_consec_losses": int(np.percentile(max_consec_losses_all, 95)),
        }

        log.info(
            f"Monte Carlo: median return={result['median_return_r']}R, "
            f"ruin prob={result['ruin_probability']:.2%}, "
            f"p5 DD={result['p95_max_dd_r']}R"
        )

        return result


class WalkForwardAnalyzer:
    """
    Walk-forward analysis for out-of-sample validation.

    Splits data into rolling train/test windows and evaluates
    whether the strategy generalizes to unseen data.
    """

    def __init__(
        self,
        n_windows: int = WALK_FORWARD_WINDOWS,
        train_pct: float = WALK_FORWARD_TRAIN_PCT,
    ):
        self.n_windows = n_windows
        self.train_pct = train_pct

    def analyze(
        self,
        candles: List[dict],
        signal_func,
        backtester: Backtester,
        strategy_name: str = "Strategy",
        params: Dict = None,
    ) -> Dict[str, Any]:
        """
        Run walk-forward analysis.

        Args:
            candles: Full OHLC data
            signal_func: Strategy detection function
            backtester: Backtester instance
            strategy_name: Strategy name
            params: Strategy parameters

        Returns:
            dict with walk-forward metrics
        """
        params = params or {}
        n = len(candles)
        window_size = n // self.n_windows

        if window_size < 100:
            log.warning("Insufficient data for walk-forward analysis")
            return {"error": "insufficient_data"}

        train_size = int(window_size * self.train_pct)
        test_size = window_size - train_size

        in_sample_results = []
        out_of_sample_results = []

        for w in range(self.n_windows):
            start = w * window_size
            train_end = start + train_size
            test_end = min(start + window_size, n)

            train_candles = candles[start:train_end]
            test_candles = candles[train_end:test_end]

            if len(train_candles) < 50 or len(test_candles) < 20:
                continue

            # In-sample backtest
            is_result = backtester.run(
                train_candles, signal_func, f"{strategy_name}_IS_W{w}", params
            )
            in_sample_results.append(is_result)

            # Out-of-sample backtest
            oos_result = backtester.run(
                test_candles, signal_func, f"{strategy_name}_OOS_W{w}", params
            )
            out_of_sample_results.append(oos_result)

        if not out_of_sample_results:
            return {"error": "no_valid_windows"}

        # Aggregate metrics
        is_wrs = [r.win_rate for r in in_sample_results if r.total_trades > 0]
        oos_wrs = [r.win_rate for r in out_of_sample_results if r.total_trades > 0]
        is_pfs = [r.profit_factor for r in in_sample_results
                  if r.total_trades > 0 and r.profit_factor < float('inf')]
        oos_pfs = [r.profit_factor for r in out_of_sample_results
                   if r.total_trades > 0 and r.profit_factor < float('inf')]
        oos_expectations = [r.expectancy_r for r in out_of_sample_results
                            if r.total_trades > 0]

        # Consistency ratio: % of OOS windows that are profitable
        oos_profitable = sum(1 for r in out_of_sample_results
                             if r.total_pnl_r > 0)
        consistency = oos_profitable / len(out_of_sample_results) if out_of_sample_results else 0

        # Degradation: how much does OOS differ from IS
        avg_is_wr = np.mean(is_wrs) if is_wrs else 0
        avg_oos_wr = np.mean(oos_wrs) if oos_wrs else 0
        degradation = (avg_is_wr - avg_oos_wr) / avg_is_wr if avg_is_wr > 0 else 0

        result = {
            "n_windows": self.n_windows,
            "train_pct": self.train_pct,
            "valid_windows": len(out_of_sample_results),

            # In-sample
            "avg_is_win_rate": round(float(np.mean(is_wrs)), 4) if is_wrs else 0,
            "avg_is_profit_factor": round(float(np.mean(is_pfs)), 3) if is_pfs else 0,

            # Out-of-sample
            "avg_oos_win_rate": round(float(np.mean(oos_wrs)), 4) if oos_wrs else 0,
            "avg_oos_profit_factor": round(float(np.mean(oos_pfs)), 3) if oos_pfs else 0,
            "avg_oos_expectancy": round(float(np.mean(oos_expectations)), 4) if oos_expectations else 0,

            # Robustness
            "consistency_ratio": round(consistency, 3),
            "performance_degradation": round(float(degradation), 4),

            # Per-window detail
            "window_details": [
                {
                    "window": w,
                    "is_trades": in_sample_results[w].total_trades,
                    "is_wr": round(in_sample_results[w].win_rate, 3),
                    "oos_trades": out_of_sample_results[w].total_trades,
                    "oos_wr": round(out_of_sample_results[w].win_rate, 3),
                    "oos_pnl_r": round(out_of_sample_results[w].total_pnl_r, 2),
                }
                for w in range(len(out_of_sample_results))
            ],
        }

        log.info(
            f"Walk-Forward: {result['valid_windows']} windows, "
            f"OOS WR={result['avg_oos_win_rate']:.1%}, "
            f"consistency={result['consistency_ratio']:.1%}, "
            f"degradation={result['performance_degradation']:.1%}"
        )

        return result
