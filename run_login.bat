@echo off
title ZERODHA KITE LOGIN
cd /d "%~dp0"

echo.
echo =========================================================
echo   ZERODHA KITE LOGIN — Token Generator
echo =========================================================
echo.

REM Activate venv if present
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

python zerodha_login.py
if %errorlevel% neq 0 (
    echo.
    echo ❌ Login script failed. See error above.
)
echo.
pause
