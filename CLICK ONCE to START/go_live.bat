@echo off
REM One command: Zerodha login, update Railway token, push, sync trades
REM Just run this and paste the request_token when prompted.
cd /d "%~dp0.."
powershell -ExecutionPolicy Bypass -File "%~dp0..\go_live.ps1"
pause
