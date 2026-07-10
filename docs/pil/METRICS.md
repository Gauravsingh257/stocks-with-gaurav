# PIL Metric Definitions

All metrics are computed **per book** (Swing / Long-Term / Momentum) and for the
capital-weighted **Combined** book. Portfolio-level ratios come from the daily ₹
equity curve produced by the accounting layer; trade stats come from the
reconstructed closed-trade ₹ P&L.

## Balances (₹)
| Metric | Definition |
|--------|-----------|
| Portfolio Value | `cash + Σ (open qty × current_price)` |
| Invested Capital | Σ cost basis of open positions |
| Available Cash | book capital − deployed + realised proceeds |
| Open / Pending Positions | live positions / armed-awaiting-entry positions |
| Realised / Unrealised P&L | booked on exits / mark-to-market on open |

## Returns
| Metric | Definition |
|--------|-----------|
| Total Return | `(portfolio_value − initial_capital) / initial_capital` |
| Today's Return | last equity point vs the prior day |
| MTD / QTD / YTD | current NAV vs NAV on the last day before the period start |
| CAGR | `(V_end / V_start)^(1/years) − 1` over the equity curve |

## Risk (annualised, 252 trading days)
| Metric | Definition |
|--------|-----------|
| Volatility | `stdev(daily returns) × √252` |
| Max Drawdown | worst peak-to-trough of the equity curve (%) |
| Sharpe | `(annual return − rf) / annual vol` |
| Sortino | `(annual return − rf) / annual downside deviation` |
| Calmar | `CAGR / |Max Drawdown|` |
| Risk Score | 0 (calm)–100 (hot): blend of vol, drawdown depth, single-name concentration |

`rf` = `PIL_RISK_FREE_RATE` (default 6.5%).

## Trade quality (from closed trades)
| Metric | Definition |
|--------|-----------|
| Hit Rate | wins / total closed |
| Expectancy | mean ₹ P&L per trade (also % form) |
| Profit Factor | gross win ₹ / gross loss ₹ |
| Average Winner / Loser | mean ₹ P&L of winners / losers |
| Win/Loss Ratio | `|avg winner / avg loser|` |
| Average Hold Time | mean days held |
| Portfolio Turnover | Σ deployed notional / avg capital, annualised |

## Exposure & concentration (Part 2)
- Exposure buckets are expressed as **% of deployed capital** (Σ market value),
  so concentration is measured among what is actually held.
- **HHI** = Σ wᵢ² of single-name weights; **effective holdings** = 1/HHI.
- **Diversification score** (0–1) = `0.6·(1−HHI) + 0.4·min(effective/N, 1)`.
- **Portfolio beta** = market-value-weighted symbol betas.
- **Correlation matrix** = Pearson of engine daily returns on common dates.

## Health (Part 6)
Per-book 0–100 sub-scores (quality, risk, drawdown, momentum, concentration,
diversification, maturity; + liquidity & replacement-pressure for combined) →
weighted overall → **GREEN ≥ 70 · YELLOW ≥ 45 · RED < 45**.

## Scorecard quality (Part 3)
- **Engine Quality Score** — blend of hit-rate, profit-factor, expectancy, ranking quality.
- **Ranking Quality** — Pearson(entry conviction, realised P&L%); >0 means the
  engine ranked winners higher at entry.
- **Replacement Efficiency** — avg P&L% of trades exited to make room for a better
  idea (`null` if the engine doesn't record replacement exits).
- Missed-opportunity / avoided-loss are **geometry-based estimates** from expired
  armed ideas (target/entry, entry/stop) — no fabricated outcomes.
