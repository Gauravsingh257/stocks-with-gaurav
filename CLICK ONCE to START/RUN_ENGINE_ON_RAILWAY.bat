@echo off
set "BACKEND=https://web-production-2781a.up.railway.app"
title SMC Engine — RAILWAY
echo.
echo  =============================================
echo   RUN ENGINE ON RAILWAY (cloud)
echo  =============================================
echo   Use this when you want the engine to run
echo   on Railway only — no local engine.
echo.
echo   Opens Zerodha login. After login, paste the
echo   redirect URL when prompted. Token is stored
echo   in Redis; Railway engine uses it in ~2 min.
echo  =============================================
echo.
echo  Opening Kite login...
echo.
start "" "%BACKEND%/api/kite/login"
echo.
echo  Complete Zerodha login, then paste the URL when asked.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0morning_login.ps1" -Backend "%BACKEND%"
echo.
echo  Done. Railway engine will use the new token shortly.
echo.
pause
