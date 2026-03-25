<#
.SYNOPSIS
    Phase 6: SMC Dashboard master launcher.
    Kills any stale processes on ports 8000/3000, then opens two new
    PowerShell windows -- one for the FastAPI backend, one for Next.js.

.USAGE
    Right-click → "Run with PowerShell"
      -- or --
    powershell -ExecutionPolicy Bypass -File run_dashboard.ps1

    To also start the Cloudflare tunnel for mobile access:
    powershell -ExecutionPolicy Bypass -File run_dashboard.ps1 -Tunnel
#>

param(
    [switch]$Tunnel,      # pass -Tunnel to also launch the Cloudflare tunnel
    [switch]$Engine       # pass -Engine to also start the trading engine
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Helpers ──────────────────────────────────────────────────────────────────

function Kill-Port {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $p = $c.OwningProcess
        if ($p -and $p -ne 0) {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            Write-Host "  [cleanup] Killed PID $p on port $Port" -ForegroundColor Yellow
        }
    }
}

function Start-Window {
    param([string]$Title, [string]$Command)
    $procArgs = @("-NoExit", "-Command",
        "`$host.UI.RawUI.WindowTitle='$Title'; $Command")
    Start-Process powershell -ArgumentList $procArgs -WindowStyle Normal
}

# ── Kill stale processes ──────────────────────────────────────────────────────

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  SMC Dashboard Launcher" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[1/3] Clearing ports 8000 and 3000..." -ForegroundColor White
Kill-Port 8000
Kill-Port 3000
Start-Sleep -Milliseconds 800

# ── Backend ──────────────────────────────────────────────────────────────────

Write-Host "[2/3] Starting FastAPI backend on port 8000..." -ForegroundColor White
$pyExe  = "C:/Users/g6666/AppData/Local/Programs/Python/Python311/python.exe"
$backendCmd = "Set-Location '$Root'; $pyExe -m uvicorn dashboard.backend.main:app --port 8000 --log-level info"
Start-Window -Title "SMC Backend :8000" -Command $backendCmd
Start-Sleep -Seconds 2

# ── Frontend ─────────────────────────────────────────────────────────────────

Write-Host "[3/3] Starting Next.js frontend on port 3000..." -ForegroundColor White
$frontendCmd = "Set-Location '$Root\dashboard\frontend'; npm run dev"
Start-Window -Title "SMC Frontend :3000" -Command $frontendCmd

# ── Engine (optional) ────────────────────────────────────────────────────────

if ($Engine) {
    Write-Host "[opt] Starting SMC Engine..." -ForegroundColor White
    $engineCmd = "Set-Location '$Root'; $pyExe smc_mtf_engine_v4.py"
    Start-Window -Title "SMC Engine" -Command $engineCmd
}

# ── Summary ──────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  Dashboard is starting up" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Frontend  →  http://localhost:3000" -ForegroundColor Cyan
Write-Host "  Backend   →  http://localhost:8000" -ForegroundColor Cyan
Write-Host "  API Docs  →  http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "  WebSocket →  ws://localhost:8000/ws" -ForegroundColor Cyan
Write-Host ""
Write-Host "  All /api/* calls are proxied through Next.js." -ForegroundColor Gray
Write-Host "  For mobile access via Cloudflare tunnel:" -ForegroundColor Gray
Write-Host "    powershell -ExecutionPolicy Bypass -File setup_tunnel.ps1" -ForegroundColor Gray
Write-Host "    -- or run:  .\run_dashboard.ps1 -Tunnel" -ForegroundColor Gray
Write-Host ""

# ── Tunnel (optional) ────────────────────────────────────────────────────────

if ($Tunnel) {
    Write-Host "[tunnel] Waiting 5s for services to start..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    & "$Root\setup_tunnel.ps1"
}
