# Print BACKEND_URL and REDIS_URL for the audit script.
# These are NOT in the repo — they come from Railway and optionally .go_live_config.
#
# Usage: .\scripts\show_audit_env.ps1
# Then copy the export lines (or run the audit with these env vars set).

$rootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$configPath = Join-Path $rootDir ".go_live_config"

Write-Host ""
Write-Host "Audit script env (BACKEND_URL, REDIS_URL)" -ForegroundColor Cyan
Write-Host ""

$backendUrl = $env:BACKEND_URL
$redisUrl = $env:REDIS_URL

if (Test-Path $configPath) {
    Get-Content $configPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -match '^\s*BACKEND_URL=(.*)$') {
            $backendUrl = $matches[1].Trim().Trim('"').Trim("'")
        }
        if ($line -match '^\s*REDIS_URL=(.*)$') {
            $redisUrl = $matches[1].Trim().Trim('"').Trim("'")
        }
    }
}

if ($backendUrl) {
    Write-Host "BACKEND_URL (from env or .go_live_config):" -ForegroundColor Green
    Write-Host "  $backendUrl"
    Write-Host ""
    Write-Host "PowerShell (run before audit):" -ForegroundColor Yellow
    Write-Host '  $env:BACKEND_URL = "' + $backendUrl + '"'
} else {
    Write-Host "BACKEND_URL not set." -ForegroundColor Red
    Write-Host "  Get it: Railway -> your project -> Dashboard API (web) service -> Settings -> Networking -> Generate Domain / copy URL"
    Write-Host "  Example: https://trading-algo-production.up.railway.app"
    Write-Host ""
    Write-Host "  Then set: .go_live_config line BACKEND_URL=<that URL>  OR  export BACKEND_URL=<that URL>"
    Write-Host ""
}

if ($redisUrl) {
    Write-Host "REDIS_URL (from env or .go_live_config):" -ForegroundColor Green
    Write-Host "  $redisUrl"
    Write-Host ""
    Write-Host "PowerShell:" -ForegroundColor Yellow
    Write-Host '  $env:REDIS_URL = "' + $redisUrl + '"'
} else {
    Write-Host "REDIS_URL not set." -ForegroundColor Red
    Write-Host "  Get it: Railway -> your project -> Redis service (or the service that has Redis) -> Connect -> copy REDIS_URL / Connection URL"
    Write-Host "  Example: redis://default:xxxxx@containers-us-west-xxx.railway.app:port"
    Write-Host ""
    Write-Host "  Then set: Railway Variables on Dashboard + Engine services already have it; for local audit copy it to .go_live_config as REDIS_URL=<url>  OR  export REDIS_URL=<url>"
    Write-Host ""
}

Write-Host ""
Write-Host "Run audit:" -ForegroundColor Cyan
if ($backendUrl) {
    Write-Host "  python scripts/audit_cloud_deployment.py"
    if ($redisUrl) {
        Write-Host "  # or: python scripts/audit_cloud_deployment.py --backend $backendUrl --redis <REDIS_URL>"
    } else {
        Write-Host "  python scripts/audit_cloud_deployment.py --backend $backendUrl"
    }
} else {
    Write-Host "  export BACKEND_URL=\"https://YOUR-RAILWAY-URL\""
    Write-Host "  export REDIS_URL=\"redis://YOUR-REDIS\""
    Write-Host "  python scripts/audit_cloud_deployment.py"
}
Write-Host ""
