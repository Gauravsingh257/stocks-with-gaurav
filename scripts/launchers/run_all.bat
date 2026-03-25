@echo off
title SMC Dashboard — Launch
echo.
echo  Starting SMC Dashboard (Backend + Frontend)
echo  Frontend : http://localhost:3000
echo  Backend  : http://localhost:8000
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0run_dashboard.ps1"
