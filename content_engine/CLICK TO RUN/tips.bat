@echo off
setlocal
title Content Engine — Quick tips
cd /d "%~dp0..\.."
echo.
echo [%DATE% %TIME%] Project: %CD%
echo Generating quick-tips cards...
echo Output: content_engine\generated_posts
echo.

where py >nul 2>&1 && (
  py -3 -m content_engine.preview_local tips
) || (
  python -m content_engine.preview_local tips
)

if errorlevel 1 (
  echo.
  echo ERROR: Python failed.
)
echo.
pause
