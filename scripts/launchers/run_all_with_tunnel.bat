@echo off
title SMC Dashboard + Cloudflare Tunnel
echo.
echo  Starting SMC Dashboard with Cloudflare Tunnel (mobile access)
echo  Frontend  : http://localhost:3000
echo  Backend   : http://localhost:8000
echo  Tunnel URL will appear in this window after services start.
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0run_dashboard.ps1" -Tunnel
pause
