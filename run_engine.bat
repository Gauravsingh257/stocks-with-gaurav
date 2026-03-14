@echo off
title Trading Engine - SMC MTF
cd /d "%~dp0"
.venv\Scripts\python.exe smc_mtf_engine.py
pause
