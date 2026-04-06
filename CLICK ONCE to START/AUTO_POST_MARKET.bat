@echo off
:: Automated post-market content: generate + send to Telegram
:: Runs via Task Scheduler at 4:20 PM daily OR on login (catch-up)
:: Idempotent: flag file prevents duplicate posts for the same day.
cd /d "C:\Users\g6666\Trading Algo"
set PYTHONPATH=C:\Users\g6666\Trading Algo\Content Creation

:: Build today's date as YYYY-MM-DD
for /f "tokens=2 delims==" %%G in ('wmic os get localdatetime /value ^| find "="') do set DT=%%G
set TODAY=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%
set FLAG=logs\post_market_done_%TODAY%.flag

:: Skip if already posted today
if exist "%FLAG%" (
    echo [%date% %time%] Post-market already posted today ^(%TODAY%^). Skipping.
    exit /b 0
)

echo [%date% %time%] Starting post-market content pipeline...
.\.venv\Scripts\python.exe scripts\auto_content_post.py post >> logs\auto_content.log 2>&1

if %ERRORLEVEL%==0 (
    echo done > "%FLAG%"
    echo [%date% %time%] Post-market pipeline SUCCESS — flag written.
) else (
    echo [%date% %time%] Post-market pipeline FAILED (exit code %ERRORLEVEL%).
)
