# Quick backend health check. Uses BACKEND_URL from .go_live_config.
# Usage: .\scripts\check_backend_health.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
Set-Location $rootDir

$configPath = Join-Path $rootDir ".go_live_config"
if (Test-Path $configPath) {
    Get-Content $configPath | ForEach-Object {
        if ($_ -match '^\s*BACKEND_URL=(.*)$') {
            $env:BACKEND_URL = $matches[1].Trim().Trim('"').Trim("'")
        }
    }
}

$base = $env:BACKEND_URL
if (-not $base) {
    Write-Host "BACKEND_URL not set. Add it to .go_live_config or set env." -ForegroundColor Red
    exit 1
}

$base = $base.TrimEnd('/')
Write-Host ""
Write-Host "Checking backend: $base" -ForegroundColor Cyan
Write-Host ""

# /health
try {
    $r = Invoke-RestMethod -Uri "$base/health" -Method Get -TimeoutSec 10
    Write-Host "[OK] /health -> $($r.status)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] /health -> $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "       Backend may be down or wrong URL." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  To find the correct URL:" -ForegroundColor Cyan
    Write-Host "    1. Open https://railway.app/dashboard -> your project" -ForegroundColor White
    Write-Host "    2. Click the 'web' service" -ForegroundColor White
    Write-Host "    3. Settings -> Networking (or Domains) -> copy the URL" -ForegroundColor White
    Write-Host "    4. Put it in .go_live_config as BACKEND_URL=https://that-url" -ForegroundColor White
    Write-Host "  Or run: .\scripts\get_railway_web_url.ps1" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# /health/kite
try {
    $k = Invoke-RestMethod -Uri "$base/health/kite" -Method Get -TimeoutSec 10
    $keySet = $k.kite_api_key_set
    $tokenSet = $k.kite_access_token_set
    $ready = $k.kite_ready
    if ($ready) {
        Write-Host "[OK] /health/kite -> Kite ready (API key + token set)" -ForegroundColor Green
    } else {
        Write-Host "[WARN] /health/kite -> Kite not ready (key=$keySet, token=$tokenSet)" -ForegroundColor Yellow
        if ($k.hint) { Write-Host "       $($k.hint)" -ForegroundColor Gray }
    }
} catch {
    Write-Host "[WARN] /health/kite -> $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "If /health is OK, backend is up. If Kite is ready, Charts should work." -ForegroundColor Gray
Write-Host ""
