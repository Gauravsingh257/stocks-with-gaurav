<#
.SYNOPSIS
    ONE-CLICK morning startup for SMC Trading System.

    Launches everything in the correct order:
      1. Zerodha Login (token refresh)
      2. SMC Engine (live trading)
      3. Dashboard Backend (FastAPI :8000)
      4. Dashboard Frontend (Next.js :3000)
      5. Cloudflare Tunnel (optional, for mobile access)

.USAGE
    Right-click > "Run with PowerShell"
      or
    powershell -ExecutionPolicy Bypass -File start_morning.ps1

    With tunnel:
    powershell -ExecutionPolicy Bypass -File start_morning.ps1 -Tunnel
#>

param(
    [switch]$Tunnel,
    [switch]$SkipLogin
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Prefer venv python, then system python
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
$pyExe = if (Test-Path $venvPy) { $venvPy } else { "python" }

# -- Helpers --
function Kill-Port {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $p = $c.OwningProcess
        if ($p -and $p -ne 0 -and $p -ne 4) {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            Write-Host "  [cleanup] Killed PID $p on port $Port" -ForegroundColor Yellow
        }
    }
}

function Start-Window {
    param([string]$Title, [string]$Cmd)
    Start-Process powershell -ArgumentList "-NoExit","-Command",
        "& { `$host.UI.RawUI.WindowTitle='$Title'; $Cmd }" -WindowStyle Normal
}

# -- Banner --
Clear-Host
Write-Host ""
Write-Host "  ========================================================" -ForegroundColor Cyan
Write-Host "         SMC TRADING SYSTEM -- MORNING START               " -ForegroundColor Cyan
Write-Host "  ========================================================" -ForegroundColor Cyan
Write-Host ""

# ==================================================================
# STEP 1: Zerodha Login (token refresh)
# ==================================================================
if (-not $SkipLogin) {
    Write-Host "  [1/5] ZERODHA LOGIN -- Refresh access token" -ForegroundColor White
    Write-Host "        Browser will open. Login and paste the request_token." -ForegroundColor Gray
    Write-Host ""

    Set-Location $Root
    & $pyExe zerodha_login.py

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "  [!] Login may have failed. Continue anyway? (Y/N)" -ForegroundColor Red
        $ans = Read-Host
        if ($ans -ne "Y" -and $ans -ne "y") { exit 1 }
    }

    # Verify token
    Write-Host ""
    Write-Host "  [>] Verifying token..." -ForegroundColor Gray
    $pyScript = @"
from kiteconnect import KiteConnect
from kite_credentials import API_KEY
t = open('access_token.txt').read().strip()
k = KiteConnect(api_key=API_KEY); k.set_access_token(t)
try:
    m = k.margins(); print('OK|{:.2f}'.format(m['equity']['net']))
except:
    print('FAIL')
"@
    $verify = & $pyExe -c $pyScript 2>$null

    if ($verify -like "OK|*") {
        $bal = $verify.Split("|")[1]
        Write-Host "  [OK] Token VALID -- Balance: Rs $bal" -ForegroundColor Green
    }
    else {
        Write-Host "  [X] Token invalid! Re-run login." -ForegroundColor Red
        Read-Host "  Press Enter to exit"
        exit 1
    }
}
else {
    Write-Host "  [1/5] SKIPPED -- Using existing token" -ForegroundColor Yellow
}

Write-Host ""
Start-Sleep -Seconds 1

# ==================================================================
# STEP 2: Kill stale processes
# ==================================================================
Write-Host "  [2/5] Clearing stale processes on ports 8000, 3000..." -ForegroundColor White
Kill-Port 8000
Kill-Port 3000
Start-Sleep -Milliseconds 500
Write-Host "  [OK] Ports cleared" -ForegroundColor Green
Write-Host ""

# ==================================================================
# STEP 3: Start SMC Engine (live trading)
# ==================================================================
Write-Host "  [3/5] Starting SMC Trading Engine..." -ForegroundColor White
$engineCmd = "Set-Location '$Root'; & '$pyExe' smc_mtf_engine_v4.py"
Start-Window -Title "SMC Engine" -Cmd $engineCmd
Start-Sleep -Seconds 3
Write-Host "  [OK] Engine launched (separate window)" -ForegroundColor Green
Write-Host ""

# ==================================================================
# STEP 4: Start Dashboard (Backend + Frontend)
# ==================================================================
Write-Host "  [4/5] Starting Dashboard Backend (port 8000)..." -ForegroundColor White
$backendCmd = "Set-Location '$Root'; & '$pyExe' -m uvicorn dashboard.backend.main:app --port 8000 --log-level info"
Start-Window -Title "SMC Backend 8000" -Cmd $backendCmd
Start-Sleep -Seconds 3

Write-Host "  [4/5] Starting Dashboard Frontend (port 3000)..." -ForegroundColor White
$frontendCmd = "Set-Location '$Root\dashboard\frontend'; npm run dev"
Start-Window -Title "SMC Frontend 3000" -Cmd $frontendCmd
Start-Sleep -Seconds 2
Write-Host "  [OK] Dashboard launched" -ForegroundColor Green
Write-Host ""

# ==================================================================
# STEP 5: Cloudflare Tunnel (optional)
# ==================================================================
if ($Tunnel) {
    Write-Host "  [5/5] Starting Cloudflare Tunnel..." -ForegroundColor White
    Start-Sleep -Seconds 5
    if (Test-Path "$Root\setup_tunnel.ps1") {
        & "$Root\setup_tunnel.ps1"
    }
    else {
        Write-Host "  [!] setup_tunnel.ps1 not found, skipping" -ForegroundColor Yellow
    }
}
else {
    Write-Host "  [5/5] Tunnel skipped (use -Tunnel flag to enable)" -ForegroundColor Gray
}

# ==================================================================
# SUMMARY
# ==================================================================
Write-Host ""
Write-Host "  ========================================================" -ForegroundColor Green
Write-Host "              ALL SYSTEMS LAUNCHED                         " -ForegroundColor Green
Write-Host "  ========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard  -->  http://localhost:3000" -ForegroundColor Cyan
Write-Host "  Backend    -->  http://localhost:8000" -ForegroundColor Cyan
Write-Host "  API Docs   -->  http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "  4 windows are now running:" -ForegroundColor Gray
Write-Host "    - SMC Engine      (live trading + data feed)" -ForegroundColor Gray
Write-Host "    - SMC Backend     (FastAPI + AI agents + scheduler)" -ForegroundColor Gray
Write-Host "    - SMC Frontend    (Next.js dashboard)" -ForegroundColor Gray
if ($Tunnel) {
    Write-Host "    - Cloudflare      (mobile tunnel)" -ForegroundColor Gray
}
Write-Host ""
Write-Host "  To stop everything: close the 3-4 PowerShell windows." -ForegroundColor Yellow
Write-Host "  Or run: Get-Process python,node | Stop-Process -Force" -ForegroundColor Yellow
Write-Host ""
Read-Host "  Press Enter to close this launcher"
