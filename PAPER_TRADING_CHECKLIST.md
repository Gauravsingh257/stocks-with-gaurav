# Paper Trading Checklist
## Phase 6 — Validation Before Live

### Setup
- [ ] Run `zerodha_login.py` to refresh token
- [ ] Launch with `run_paper.bat` (sets `PAPER_MODE=1`)
- [ ] Verify Telegram alerts show `[PAPER]` prefix
- [ ] Confirm `paper_trade_log.csv` is being written

### Daily (10 trading days minimum)
- [ ] Engine starts before 9:15 AM
- [ ] Swing scan triggers at 9:30 AM
- [ ] Signals appear during 9:15-13:00 killzone
- [ ] SL/TP outcomes logged to `paper_trade_outcomes.csv`
- [ ] EOD paper summary received on Telegram at 4:00 PM

### Pass Criteria (from `engine/paper_mode.py`)
| Metric | Requirement |
|---|---|
| Trading Days | >= 10 |
| Total Trades | >= 20 |
| Win Rate | >= 45% |
| Profit Factor | >= 1.2 |
| Expectancy | >= +0.05R |
| Max Drawdown | >= -5.0R |

### Backtest Reference (C-only, optimized config)
| Metric | Backtest Value |
|---|---|
| Trades | 46 (6mo) |
| Win Rate | 54.4% |
| Profit Factor | 1.58 |
| Expectancy | +0.22R |
| Sharpe | 3.10 |

### Go-Live Gate
- [ ] 10+ days of paper data collected
- [ ] All pass criteria met
- [ ] No engine crashes during paper period
- [ ] No missed signals (compare scan count vs log count)
- [ ] Slippage estimate < 0.5R per trade average
- [ ] Sign-off before switching from `run_paper.bat` to `run_live_v4.bat`

### Config Snapshot (active during paper)
```
ENGINE_MODE = AGGRESSIVE
ACTIVE_STRATEGIES = A + C (B disabled, D disabled)
atr_buffer_mult = 0.15 (BANKNIFTY), 0.25 (NIFTY)
default_rr_c = 2.0
min_smc_score = 5 (for C-only mode in backtest)
```
