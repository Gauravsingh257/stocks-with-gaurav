@echo off
title SMC Dashboard Frontend (port 3000)
cd /d "C:\Users\g6666\Trading Algo\dashboard\frontend"
echo Starting SMC Dashboard Frontend on http://localhost:3000
echo Make sure the backend is running first (run_dashboard_backend.bat)
echo.
npm run dev
pause
