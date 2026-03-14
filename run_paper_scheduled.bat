@echo off
REM =========================================
REM  Paper Trading Engine — Task Scheduler
REM  Auto-starts at 9:10 AM (after manual login)
REM  Logs output to engine_scheduler.log
REM  Auto-stops at 3:35 PM via engine's own logic
REM =========================================

cd /d "C:\Users\g6666\Trading Algo"

REM Paper mode
set PAPER_MODE=1
set BACKTEST_MODE=0

REM Check if access token exists and is from today
python -c "import os;from datetime import date;f='access_token.txt';ok=os.path.exists(f) and date.fromtimestamp(os.path.getmtime(f))==date.today();print('TOKEN_OK' if ok else 'TOKEN_STALE')" > _token_check.tmp 2>&1
set /p TOKEN_STATUS=<_token_check.tmp
del _token_check.tmp

if "%TOKEN_STATUS%" NEQ "TOKEN_OK" (
    echo [%date% %time%] WARNING: Access token is stale or missing. Please run run_login.bat first. >> engine_scheduler.log
    exit /b 1
)

echo [%date% %time%] Starting Paper Trading Engine via Task Scheduler >> engine_scheduler.log
python smc_mtf_engine_v4.py >> engine_scheduler.log 2>&1

echo [%date% %time%] Engine stopped >> engine_scheduler.log
