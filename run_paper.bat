@echo off
REM =========================================
REM  Paper Trading Mode Launcher
REM  Phase 6: Runs the engine with PAPER_MODE=1
REM  No real orders — all signals logged to CSV
REM =========================================
set PAPER_MODE=1
set BACKTEST_MODE=0
echo.
echo ================================================
echo   PAPER TRADING MODE
echo   Signals: paper_trade_log.csv
echo   Outcomes: paper_trade_outcomes.csv
echo   Telegram alerts prefixed with [PAPER]
echo ================================================
echo.
python smc_mtf_engine_v4.py
pause
