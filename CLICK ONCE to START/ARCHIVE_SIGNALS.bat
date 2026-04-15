@echo off
title Archive Signals to GitHub
cd /d "%~dp0\.."

echo.
echo  =============================================
echo   ARCHIVE SIGNALS TO GITHUB
echo  =============================================
echo   Pulls all signals from the past 30 days
echo   from Railway backend and saves them to
echo   signal_history\signals_YYYY.csv
echo   Then commits + pushes to GitHub.
echo  =============================================
echo.

REM Run archiver
echo  [1/3] Fetching signals from Railway...
echo.
"%~dp0\..\\.venv\Scripts\python.exe" scripts\archive_signals.py --days 30

echo.
echo  [2/3] Committing to git...
git add signal_history\
git status --short
git diff --cached --stat

REM Only commit if there's something new
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "archive: signal history update %date:~6,4%-%date:~3,2%-%date:~0,2%"
    echo.
    echo  [3/3] Pushing to GitHub...
    git push origin main
    if errorlevel 1 (
        echo  [ERROR] Push failed. Try running sync.bat manually.
        pause
        exit /b 1
    )
    echo.
    echo  Done! Signal history committed and pushed.
) else (
    echo.
    echo  Nothing new to commit - signal_history is already up to date.
)

echo.
pause
