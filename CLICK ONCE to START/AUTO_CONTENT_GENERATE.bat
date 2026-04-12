@echo off
title SMC Auto Content Generator
echo.
echo  =============================================
echo   AUTO CONTENT GENERATOR
echo  =============================================
echo   Watches for new closed trades and generates
echo   Instagram content + TradingView screenshots.
echo.
echo   Keep this running while TradingView Desktop
echo   is open. It auto-generates after each trade.
echo  =============================================
echo.

cd /d "%~dp0.."

:: Activate venv
call .venv\Scripts\activate.bat

:: Generate content from last trade (run once now)
echo [%date% %time%] Running initial content generation...
python scripts\trade_to_content.py --last 1

echo.
echo  Content generation complete.
echo  Output: content_output\
echo.

:: Sync rejections to dashboard
echo [%date% %time%] Syncing rejection log to dashboard...
python -c "from services.dashboard_sync import _get_config; import requests, json, os; url,key = _get_config(); f='signal_rejections_today.json'; data=json.load(open(f)) if os.path.exists(f) else []; resp=requests.post(f'{url}/api/journal/rejections/sync', headers={'X-Sync-Key': key, 'Content-Type':'application/json'}, timeout=10) if url and data else None; print(f'Synced: {resp.status_code if resp else \"skipped\"}')" 2>nul
echo.

echo  =============================================
echo   Done! Check content_output\ for ready posts
echo  =============================================
pause
