@echo off
REM One-click sync: commit all changes and push to GitHub
REM Run this after editing any code in Trading Algo folder

cd /d "%~dp0"

echo.
echo === Syncing to GitHub ===
echo.

git add -A
git status --short

if %errorlevel% neq 0 (
    echo ERROR: Not a git repo or git failed.
    pause
    exit /b 1
)

set /p MSG="Commit message (or press Enter for 'Update'): "
if "%MSG%"=="" set MSG=Update

git commit -m "%MSG%"
if %errorlevel% neq 0 (
    echo Nothing to commit, or commit failed.
)

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
if /i "%SYNC%"=="y" powershell -ExecutionPolicy Bypass -File "%~dp0sync_trades_to_cloud.ps1"
echo.
pause
