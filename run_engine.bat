@echo off
title Trading Engine - SMC MTF V4
cd /d "%~dp0"

REM Activate venv
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

REM Load .env into process environment (Windows batch style)
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('type ".env" ^| findstr /v "^#" ^| findstr /v "^$"') do (
        set "%%A=%%B"
    )
    echo [ENV] Loaded .env
)

echo Starting SMC MTF Engine V4...
python smc_mtf_engine_v4.py
pause
