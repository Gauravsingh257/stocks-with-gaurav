@echo off
setlocal
title Content Engine — All diagrams
cd /d "%~dp0..\.."
echo.
echo [%DATE% %TIME%] Project: %CD%
echo Generating all 12 diagram templates (may take a minute^)...
echo Output: content_engine\generated_posts
echo.

where py >nul 2>&1 && (
  py -3 -m content_engine.preview_local diagrams
) || (
  python -m content_engine.preview_local diagrams
)

if errorlevel 1 (
  echo.
  echo ERROR: Python failed.
)
echo.
pause
