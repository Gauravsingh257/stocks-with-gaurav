@echo off
:: Login catch-up: runs on every Windows login.
:: Checks the current time and fires any missed pre/post market pipelines.
::
:: Rules:
::   Before 8:30 AM  → do nothing (scheduled task will fire at 8:30)
::   8:30 AM – 3:30 PM → run pre-market if not done today
::   3:30 PM – 11:59 PM → run pre-market if not done, then post-market if not done
::
:: Each bat is idempotent (flag-file guard), so calling them is always safe.

cd /d "C:\Users\g6666\Trading Algo"

:: Build today's date
for /f "tokens=2 delims==" %%G in ('wmic os get localdatetime /value ^| find "="') do set DT=%%G
set TODAY=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%

:: Get current hour (24h) and minute — strip leading zeros for comparison
set HH=%DT:~8,2%
set MM=%DT:~10,2%

:: Remove leading zero for numeric comparison
if "%HH:~0,1%"=="0" set HH=%HH:~1%
if "%MM:~0,1%"=="0" set MM=%MM:~1%
if "%HH%"=="" set HH=0
if "%MM%"=="" set MM=0

:: Calculate total minutes since midnight
set /a NOW_MIN=%HH% * 60 + %MM%

:: Thresholds (in minutes from midnight)
set /a PRE_THRESHOLD=8 * 60 + 30
set /a POST_THRESHOLD=15 * 60 + 30

echo [%date% %time%] Login catch-up: now=%HH%:%MM% (%NOW_MIN% min) >> logs\auto_content.log

:: Before 8:30 AM — too early, scheduled tasks will handle it
if %NOW_MIN% LSS %PRE_THRESHOLD% (
    echo [%date% %time%] Before 8:30 AM — nothing to catch up. >> logs\auto_content.log
    exit /b 0
)

:: 8:30+ → run pre-market if missing
if not exist "logs\pre_market_done_%TODAY%.flag" (
    echo [%date% %time%] Pre-market not posted yet — catching up now... >> logs\auto_content.log
    call "CLICK ONCE to START\AUTO_PRE_MARKET.bat"
) else (
    echo [%date% %time%] Pre-market already done today. >> logs\auto_content.log
)

:: 3:30 PM+ → also run post-market if missing
if %NOW_MIN% GEQ %POST_THRESHOLD% (
    if not exist "logs\post_market_done_%TODAY%.flag" (
        echo [%date% %time%] Post-market not posted yet — catching up now... >> logs\auto_content.log
        call "CLICK ONCE to START\AUTO_POST_MARKET.bat"
    ) else (
        echo [%date% %time%] Post-market already done today. >> logs\auto_content.log
    )
)

echo [%date% %time%] Login catch-up complete. >> logs\auto_content.log
