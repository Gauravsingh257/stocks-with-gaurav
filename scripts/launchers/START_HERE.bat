@echo off
title SMC Morning Start
echo.
echo   Starting SMC Trading System...
echo   This will: Login + Engine + Dashboard
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0start_morning.ps1"
pause
