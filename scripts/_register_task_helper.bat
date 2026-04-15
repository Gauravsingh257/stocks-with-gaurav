@echo off
schtasks /create /tn "SWG Archive Signals" /tr "powershell.exe -NonInteractive -ExecutionPolicy Bypass -File \"C:\Users\g6666\Trading Algo\scripts\archive_and_push.ps1\"" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 16:30 /f
if %errorlevel%==0 (
    echo Task "SWG Archive Signals" registered at 4:30 PM Mon-Fri
) else (
    echo Failed to register task
)
pause
