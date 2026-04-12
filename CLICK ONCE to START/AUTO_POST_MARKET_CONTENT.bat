@echo off
:: Post-market content + sync — runs at 16:15 IST via Task Scheduler
:: Generates content from today's trades and syncs rejection log

cd /d "%~dp0.."
call .venv\Scripts\activate.bat

echo [%date% %time%] Post-market content generation...

:: Generate Instagram content from all today's trades
python scripts\trade_to_content.py --last 5

:: Sync rejection log to dashboard
python -c "import requests, json, os; url=os.getenv('DASHBOARD_URL','https://web-production-2781a.up.railway.app').strip('/'); key=os.getenv('TRADES_SYNC_KEY',''); f='signal_rejections_today.json'; data=json.load(open(f)) if os.path.exists(f) else []; print(f'Rejections to sync: {len(data)}'); resp=requests.post(f'{url}/api/journal/rejections/sync', headers={'X-Sync-Key': key}, timeout=15) if data else None; print(f'Sync: {resp.status_code}' if resp else 'No data')" 2>nul

echo [%date% %time%] Done.
