@echo off
title Pre-Market Post Generator
cd /d "C:\Users\g6666\Trading Algo"
set PYTHONPATH=C:\Users\g6666\Trading Algo\Content Creation

echo ============================================
echo   Pre-Market Carousel Generator
echo   StocksWithGaurav
echo ============================================
echo.

:: Get today's date in YYYY-MM-DD format
for /f "tokens=2 delims==" %%G in ('wmic os get localdatetime /value ^| find "="') do set DT=%%G
set TODAY=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%
set OUTDIR=Content Creation\output\%TODAY%

echo Generating pre-market carousel + copy kit...
echo.
.\.venv\Scripts\python.exe "Content Creation\main.py" --now pre --dry-run
echo.

if exist "%OUTDIR%\copy_pre_market.txt" (
    echo ============================================
    echo   Copy kit generated:
    echo   %OUTDIR%\copy_pre_market.txt
    echo ============================================
    echo.
    type "%OUTDIR%\copy_pre_market.txt"
    echo.
) else (
    echo [INFO] Copy kit not found - check logs for errors.
)

echo.
echo Opening output folder...
if exist "%OUTDIR%" explorer "%OUTDIR%"
echo.
pause
