@echo off
title SMC Morning Login — Update Kite Token
echo.
echo  =============================================
echo   SMC Trading System — Morning Kite Login
echo  =============================================
echo.
echo  This will open Zerodha login in your browser.
echo  After logging in, the token is automatically
echo  stored in Redis and picked up by both the
echo  web dashboard and the trading engine.
echo.
echo  Opening Kite login page...
echo.
start "" "https://web-production-2781a.up.railway.app/api/kite/login"
echo.
echo  Browser opened. Complete the Zerodha login.
echo  Once you see "connected" in the browser, you're done.
echo.
echo  Verifying token in 30 seconds...
timeout /t 30 /nobreak > nul
echo.
echo  Checking system health...
echo.
powershell -Command "try { $r = Invoke-WebRequest -Uri 'https://web-production-2781a.up.railway.app/api/system/health' -TimeoutSec 15 -UseBasicParsing; $j = $r.Content | ConvertFrom-Json; Write-Host '  Kite Connected:' $j.kite_connected; Write-Host '  Token Present:' $j.token_present; Write-Host '  Engine Live:' $j.engine_live; Write-Host '  Backend Version:' $j.backend_version } catch { Write-Host '  Could not reach backend. Check Railway.' }"
echo.
echo  Done! The system will use the new token automatically.
echo  You can close this window.
echo.
pause
