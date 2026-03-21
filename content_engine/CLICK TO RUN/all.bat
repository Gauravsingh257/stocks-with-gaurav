@echo off
setlocal
title Content Engine — Generate ALL previews
cd /d "%~dp0..\.."
echo.
echo [%DATE% %TIME%] Project: %CD%
echo This runs: strategy + news + tips + all diagrams
echo It may take several minutes. Output: content_engine\generated_posts
echo.

where py >nul 2>&1 && (
  py -3 -m content_engine.preview_local all
) || (
  python -m content_engine.preview_local all
)

if errorlevel 1 (
  echo.
  echo ERROR: Python failed.
)
echo.
pause
