<#
.SYNOPSIS
    Phase 6: Cloudflare quick-tunnel for mobile access to SMC Dashboard.

.DESCRIPTION
    Opens a public HTTPS URL (*.trycloudflare.com) that forwards to
    http://localhost:3000 (the Next.js frontend).

    Because Next.js is configured with /api/* rewrites (next.config.ts),
    all API calls from your phone are transparently proxied:
      phone --> https://xxx.trycloudflare.com/api/...
        --> Next.js rewrite --> http://localhost:8000/api/...

    No CORS configuration needed. Only port 3000 is exposed.

.NOTES
    - Quick tunnels are free, no login required.
    - URL changes every time you run this script.
    - For a permanent URL, set up a named tunnel:
        cloudflared tunnel login
        cloudflared tunnel create smc-dashboard
        cloudflared tunnel route dns smc-dashboard dashboard.yourdomain.com

.USAGE
    powershell -ExecutionPolicy Bypass -File setup_tunnel.ps1
    -- or via launcher:
    powershell -ExecutionPolicy Bypass -File run_dashboard.ps1 -Tunnel
#>

$ErrorActionPreference = "Continue"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$CloudflaredDir = "$env:LOCALAPPDATA\cloudflared"
$CloudflaredExe = "$CloudflaredDir\cloudflared.exe"

# --- Check / install cloudflared -----------------------------------------

$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cf) {
    $cf = Get-Item "$ScriptDir\cloudflared.exe" -ErrorAction SilentlyContinue
}
if (-not $cf) {
    $cf = Get-Command $CloudflaredExe -ErrorAction SilentlyContinue
}

if (-not $cf) {
    Write-Host ""
    Write-Host "[tunnel] cloudflared not found -- downloading latest release..." -ForegroundColor Yellow
    Write-Host "         (No account or login needed for quick tunnels)" -ForegroundColor Gray

    $downloadUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

    try {
        New-Item -ItemType Directory -Path $CloudflaredDir -Force | Out-Null
        Write-Host "[tunnel] Downloading from GitHub..." -ForegroundColor Gray
        Invoke-WebRequest -Uri $downloadUrl -OutFile $CloudflaredExe -UseBasicParsing
        Write-Host "[tunnel] Saved to $CloudflaredExe" -ForegroundColor Green
    } catch {
        Write-Host ""
        Write-Host "[tunnel] ERROR: Could not download cloudflared." -ForegroundColor Red
        Write-Host "         Manual install: https://developers.cloudflare.com/cloudflared/install/" -ForegroundColor Red
        Write-Host "         Then re-run this script." -ForegroundColor Red
        exit 1
    }

    $cfCmd = $CloudflaredExe
} else {
    $cfCmd = if ($cf.Source) { $cf.Source } elseif ($cf.FullName) { $cf.FullName } else { "$cf" }
    Write-Host "[tunnel] Using cloudflared: $cfCmd" -ForegroundColor Gray
}

# --- Launch tunnel -------------------------------------------------------

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Cloudflare Quick Tunnel" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Exposing: http://localhost:3000" -ForegroundColor White
Write-Host "  Scope:    Frontend + all /api/* (proxied by Next.js)" -ForegroundColor White
Write-Host ""
Write-Host "  Waiting for tunnel URL..." -ForegroundColor Yellow
Write-Host "  (This usually takes 5-10 seconds)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Press Ctrl+C to close the tunnel." -ForegroundColor Gray
Write-Host ""

# ── Run cloudflared, capture stderr to find URL ───────────────────────────

$urlFound = $false
$logFile  = "$ScriptDir\tunnel_url.txt"

# cloudflared writes trycloudflare URL to stderr — pipe 2>&1 to capture it
$process = Start-Process -FilePath $cfCmd `
    -ArgumentList "tunnel --url http://localhost:3000" `
    -NoNewWindow -PassThru `
    -RedirectStandardError "$ScriptDir\_tunnel_stderr.tmp"

# Poll the stderr temp file until we see the URL (up to 30 seconds)
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if (Test-Path "$ScriptDir\_tunnel_stderr.tmp") {
        $content = Get-Content "$ScriptDir\_tunnel_stderr.tmp" -Raw -ErrorAction SilentlyContinue
        if ($content -match 'https://[a-z0-9\-]+\.trycloudflare\.com') {
            $tunnelUrl = $Matches[0]
            $urlFound  = $true
            break
        }
    }
}

if ($urlFound) {
    # Save URL to file for easy reference
    $tunnelUrl | Set-Content $logFile -Force

    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║         YOUR DASHBOARD URL (copy to phone)               ║" -ForegroundColor Green
    Write-Host "║                                                          ║" -ForegroundColor Green
    Write-Host "║  $tunnelUrl" -ForegroundColor Cyan
    Write-Host "║                                                          ║" -ForegroundColor Green
    Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Saved to: $logFile" -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "[tunnel] WARNING: Could not detect URL within 30s." -ForegroundColor Yellow
    Write-Host "         Check the cloudflared output manually." -ForegroundColor Gray
    if (Test-Path "$ScriptDir\_tunnel_stderr.tmp") {
        Write-Host ""
        Write-Host "--- cloudflared output ---" -ForegroundColor Gray
        Get-Content "$ScriptDir\_tunnel_stderr.tmp" | Write-Host
    }
}

# Wait for cloudflared to finish (it runs until Ctrl+C)
if ($process -and -not $process.HasExited) {
    Write-Host "Tunnel is active. Press Ctrl+C to stop." -ForegroundColor Gray
    $process.WaitForExit()
}

# Cleanup temp file
Remove-Item "$ScriptDir\_tunnel_stderr.tmp" -Force -ErrorAction SilentlyContinue
