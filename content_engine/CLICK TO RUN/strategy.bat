@echo off
setlocal EnableDelayedExpansion
title Content Engine — Strategy images
cd /d "%~dp0..\.."
echo.
echo [%DATE% %TIME%] Project: %CD%
echo Generating strategy previews (real chart, diagram, cheatsheet, text card^)...
echo Output: content_engine\generated_posts
echo.

where py >nul 2>&1 && (
  py -3 -m content_engine.preview_local strategy
) || (
  python -m content_engine.preview_local strategy
)

if errorlevel 1 (
  echo.
  echo ERROR: Python failed. Install Python 3.11+ or run from a terminal: py -3 -m content_engine.preview_local strategy
)
echo.
pause
