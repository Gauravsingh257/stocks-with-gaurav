@echo off
set "BACKEND=https://web-production-2781a.up.railway.app"
title SMC Morning Login — Update Kite Token
echo.
echo  =============================================
echo   SMC Trading System — Morning Kite Login
echo  =============================================
echo.
echo  This will open Zerodha login in your browser.
echo  After login, EITHER:
echo    - Auto: Zerodha redirects to our callback (token stored automatically)
echo    - Manual: Paste the full redirect URL or request_token when prompted
echo.
echo  Zerodha app redirect URL must be whitelisted:
echo  %BACKEND%/api/kite/callback
echo.
echo  Opening Kite login page...
echo.
start "" "%BACKEND%/api/kite/login"
echo.
echo  Browser opened. Complete Zerodha login.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0morning_login.ps1" -Backend "%BACKEND%"
echo.
echo  Done! Token is stored in Redis. Dashboard uses it immediately.
echo  Engine (Railway or local) will pick up the new token within 2 minutes — no restart needed.
echo  You can close this window.
echo.
pause
