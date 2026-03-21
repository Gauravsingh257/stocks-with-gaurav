@echo off
setlocal
title Content Engine — News images
cd /d "%~dp0..\.."
echo.
echo [%DATE% %TIME%] Project: %CD%
echo Generating news + headline previews...
echo Output: content_engine\generated_posts
echo.

where py >nul 2>&1 && (
  py -3 -m content_engine.preview_local news
) || (
  python -m content_engine.preview_local news
)

if errorlevel 1 (
  echo.
  echo ERROR: Python failed. Install Python 3.11+ or use: py -3 -m content_engine.preview_local news
)
echo.
pause
