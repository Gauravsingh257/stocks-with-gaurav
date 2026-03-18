@echo off
REM sync.bat — One-click sync:
REM   1. Commit + push code to GitHub  (triggers Railway + Vercel auto-deploy)
REM   2. Optionally sync trades CSV to cloud
REM   3. Optionally verify backend health
REM
REM Run after editing any code or when you want to push trade data to cloud.

cd /d "%~dp0"

REM ── Load .go_live_config for BACKEND_URL ─────────────────────────────────────
set BACKEND_URL=
if exist ".go_live_config" (
    for /f "tokens=1,* delims==" %%A in ('type ".go_live_config" ^| findstr /v "^#"') do (
        if "%%A"=="BACKEND_URL" set BACKEND_URL=%%B
    )
)
if "%BACKEND_URL%"=="" (
    REM Fallback: read from .env
    for /f "tokens=1,* delims==" %%A in ('type ".env" ^| findstr /v "^#"') do (
        if "%%A"=="BACKEND_URL" set BACKEND_URL=%%B
    )
)

echo.
echo =========================================================
echo   TRADING ALGO — SYNC TO CLOUD
echo =========================================================
if not "%BACKEND_URL%"=="" (
    echo   Backend: %BACKEND_URL%
) else (
    echo   Backend: NOT SET  ^(set BACKEND_URL in .go_live_config^)
)
echo =========================================================
echo.

REM ── STEP 1: Git commit + push ────────────────────────────────────────────────
echo [1/3] Staging changes...
git add -A
git status --short

set /p MSG="Commit message (Enter = 'Update'): "
if "%MSG%"=="" set MSG=Update

git commit -m "%MSG%"
if %errorlevel% neq 0 (
    echo Nothing to commit — that's OK.
)

echo.
echo Pulling latest from GitHub (rebase)...
git pull --rebase origin main
if %errorlevel% neq 0 (
    echo.
    echo ❌ Pull/rebase had conflicts. Fix them manually then re-run.
    pause
    exit /b 1
)

echo Pushing to GitHub...
git push origin main
if %errorlevel% neq 0 (
    echo.
    echo ❌ Push failed. Check your internet connection or GitHub auth.
    pause
    exit /b 1
)

echo.
echo ✅ Code pushed. Railway and Vercel will auto-deploy in ~2 minutes.
echo.

REM ── STEP 2: Sync trades to cloud ─────────────────────────────────────────────
set /p SYNC_TRADES="[2/3] Sync trade_ledger_2026.csv to cloud? (y/n): "
if /i "%SYNC_TRADES%"=="y" (
    powershell -ExecutionPolicy Bypass -File "%~dp0sync_trades_to_cloud.ps1"
)

REM ── STEP 3: Health check ─────────────────────────────────────────────────────
set /p CHECK_HEALTH="[3/3] Check backend health? (y/n): "
if /i "%CHECK_HEALTH%"=="y" (
    if not "%BACKEND_URL%"=="" (
        echo.
        echo Checking %BACKEND_URL%/health ...
        powershell -Command "try { $r = Invoke-RestMethod '%BACKEND_URL%/health' -TimeoutSec 10; Write-Host ('Backend: ' + $r.status + ' (' + $r.service + ')') -ForegroundColor Green } catch { Write-Host ('Backend unreachable: ' + $_.Exception.Message) -ForegroundColor Red }"
        echo.
        echo Checking Kite token status...
        powershell -Command "try { $r = Invoke-RestMethod '%BACKEND_URL%/health/kite' -TimeoutSec 10; Write-Host ('Kite ready: ' + $r.kite_ready) -ForegroundColor Cyan; if ($r.hint) { Write-Host $r.hint -ForegroundColor Yellow } } catch { Write-Host ('Kite check failed: ' + $_.Exception.Message) -ForegroundColor Red }"
    ) else (
        echo ⚠️  BACKEND_URL not set — skipping health check.
        echo     Set it in .go_live_config: BACKEND_URL=https://YOUR-APP.up.railway.app
    )
)

echo.
echo =========================================================
echo   SYNC COMPLETE
echo =========================================================
echo.
pause
