<#
.SYNOPSIS
    F4.10: Auto-restart wrapper for SMC Trading Engine.
    
.DESCRIPTION
    Runs the trading engine and auto-restarts on crash with exponential backoff.
    Designed to be scheduled via Windows Task Scheduler for market hours.
    
    Task Scheduler setup:
    1. Open Task Scheduler → Create Task
    2. Trigger: Daily at 09:00 AM (Mon-Fri)
    3. Action: Start Program → powershell.exe
       Arguments: -ExecutionPolicy Bypass -File "C:\Users\g6666\Trading Algo\run_engine_service.ps1"
    4. Settings: Stop task if runs longer than 10 hours
    5. Conditions: Start only if on AC power (optional)
    
.NOTES
    Max restarts: 5 per session
    Backoff: 30s → 60s → 120s → 240s → 480s
    Logs to: engine_service.log
#>

param(
    [switch]$Tunnel,      # also start Cloudflare tunnel and show the URL
    [switch]$Dashboard    # also start the FastAPI backend + Next.js frontend
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ── Optional: Dashboard (backend + frontend) ──────────────────────────────────
if ($Dashboard -or $Tunnel) {
    Write-Host ""
    Write-Host "[dashboard] Launching backend (port 8000) + frontend (port 3000)..." -ForegroundColor Cyan
    & "$ScriptDir\run_dashboard.ps1"
    Start-Sleep -Seconds 6
}

# ── Optional: Cloudflare tunnel ───────────────────────────────────────────────
if ($Tunnel) {
    Write-Host ""
    Write-Host "[tunnel] Starting Cloudflare tunnel..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass",
        "-File", "$ScriptDir\setup_tunnel.ps1"
    ) -WindowStyle Normal
    Write-Host "[tunnel] Tunnel window opened — URL will appear there in ~10s." -ForegroundColor Yellow
    Write-Host ""
}

# Configuration
$PythonPath = "python"
$EngineScript = "smc_mtf_engine_v4.py"
$MaxRestarts = 5
$BaseBackoffSeconds = 30
$LogFile = "engine_service.log"
$MarketCloseHour = 16  # Stop restarting after 4 PM

function Write-ServiceLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "[$timestamp] $Message"
    Write-Host $entry
    Add-Content -Path $LogFile -Value $entry
}

Write-ServiceLog "=== ENGINE SERVICE STARTED ==="
Write-ServiceLog "Working directory: $ScriptDir"
Write-ServiceLog "Max restarts: $MaxRestarts"

$restartCount = 0

while ($restartCount -lt $MaxRestarts) {
    # Check if we're past market close — no point restarting
    $currentHour = (Get-Date).Hour
    if ($currentHour -ge $MarketCloseHour) {
        Write-ServiceLog "Past market close ($MarketCloseHour:00). Stopping service."
        break
    }
    
    Write-ServiceLog "Starting engine (attempt $($restartCount + 1)/$MaxRestarts)..."
    
    try {
        $process = Start-Process -FilePath $PythonPath -ArgumentList $EngineScript `
            -NoNewWindow -PassThru -Wait
        
        $exitCode = $process.ExitCode
        Write-ServiceLog "Engine exited with code: $exitCode"
        
        if ($exitCode -eq 0) {
            Write-ServiceLog "Clean shutdown detected. Not restarting."
            break
        }
        
        # Exit code 1 with emergency halt — don't restart
        if ($exitCode -eq 1) {
            Write-ServiceLog "Emergency halt (exit code 1). NOT restarting."
            break
        }
    }
    catch {
        Write-ServiceLog "ERROR: $($_.Exception.Message)"
    }
    
    $restartCount++
    
    if ($restartCount -ge $MaxRestarts) {
        Write-ServiceLog "MAX RESTARTS ($MaxRestarts) REACHED. Giving up."
        break
    }
    
    # Exponential backoff
    $backoff = $BaseBackoffSeconds * [math]::Pow(2, $restartCount - 1)
    Write-ServiceLog "Waiting ${backoff}s before restart..."
    Start-Sleep -Seconds $backoff
}

Write-ServiceLog "=== ENGINE SERVICE STOPPED ==="
