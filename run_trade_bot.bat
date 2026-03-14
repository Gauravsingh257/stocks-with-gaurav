@echo off
title Trade Executor Bot - Telegram Button Handler
echo =============================================
echo   TRADE EXECUTOR BOT
echo   Handles [Trade Live] / [Observe] buttons
echo   from Telegram signal alerts
echo =============================================
echo.

cd /d "%~dp0"
python trade_executor_bot.py

echo.
echo Bot stopped. Press any key to exit.
pause >nul
