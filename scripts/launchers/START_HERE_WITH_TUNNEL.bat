@echo off
title SMC Morning Start + Tunnel
echo.
echo   Starting SMC Trading System with Mobile Access...
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0start_morning.ps1" -Tunnel
pause
