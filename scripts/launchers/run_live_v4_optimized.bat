@echo off
echo ==================================================
echo SMC ENGINE V4 LAUNCHER (OPTIMIZED 2026-02-20)
echo ==================================================
echo.
echo [INFO] Environment Check:
if exist access_token.txt (
    echo [OK] Access Token Found
) else (
    echo [ERROR] Access Token MISSING! Run login...
    pause
    exit
)
echo.
echo [INFO] Features Active:
echo - Phase 1: Trailing Stops + Circuit Breaker
echo - Phase 2: Risk Controls + Expiry Override
echo - Phase 3: Intelligence (Vol/PCR/Profile)
echo - Phase 5: Optimized Hours (11:00-13:00 focus)
echo.
echo Starting Engine...
python smc_mtf_engine_v4.py
pause
