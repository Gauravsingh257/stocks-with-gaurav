@echo off
title SMC Dashboard Backend (port 8000)
cd /d "C:\Users\g6666\Trading Algo"
echo Starting SMC Dashboard Backend on http://localhost:8000 ...
echo Docs: http://localhost:8000/docs
echo WebSocket: ws://localhost:8000/ws
echo.
C:/Users/g6666/AppData/Local/Programs/Python/Python311/python.exe -m uvicorn dashboard.backend.main:app --port 8000 --reload
pause
