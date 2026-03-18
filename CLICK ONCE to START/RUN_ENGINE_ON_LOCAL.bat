@echo off
title SMC Engine — LOCAL
cd /d "%~dp0\.."

echo.
echo  =============================================
echo   RUN ENGINE ON LOCAL (this PC)
echo  =============================================
echo   Starts the trading engine on your computer.
echo   Do NOT use if you want the engine on Railway.
echo  =============================================
echo.

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python smc_mtf_engine_v4.py
pause
