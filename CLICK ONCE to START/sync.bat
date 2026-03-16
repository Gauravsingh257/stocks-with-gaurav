@echo off
REM One-click sync: pull latest, commit your changes, push to GitHub
cd /d "%~dp0\.."

echo.
echo === Syncing to GitHub ===
echo.

REM 1. Stage and commit your changes (so pull can run)
git add -A
git status --short
set /p MSG="Commit message (or press Enter for 'Update'): "
if "%MSG%"=="" set MSG=Update
git commit -m "%MSG%"
if %errorlevel% neq 0 (
    echo Nothing to commit - that's OK.
)

REM 2. Get latest from GitHub, then put your commit on top
echo Pulling latest...
git pull --rebase origin main
if %errorlevel% neq 0 (
    echo Pull had conflicts. Fix them, then run sync again.
    pause
    exit /b 1
)

REM 3. Push
git push origin main
if %errorlevel% neq 0 (
    echo Push failed. Check your connection.
    pause
    exit /b 1
)

echo.
echo === Done. Vercel and Railway will auto-deploy. ===
echo.
set /p SYNC="Also sync trades to cloud? (y/n): "
if /i "%SYNC%"=="y" powershell -ExecutionPolicy Bypass -File "%~dp0..\sync_trades_to_cloud.ps1"
echo.
pause
