# Print the Railway WEB service URL so you can set BACKEND_URL.
# Usage: .\scripts\get_railway_web_url.ps1
# Requires: railway link (link to your project first), and web service selected or use -s web.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
Set-Location $rootDir

Write-Host ""
Write-Host "Finding Railway web service URL..." -ForegroundColor Cyan
Write-Host ""

$railway = Get-Command railway -ErrorAction SilentlyContinue
if (-not $railway) {
    Write-Host "Railway CLI not installed. Get the URL manually:" -ForegroundColor Yellow
    Write-Host "  1. Open https://railway.app/dashboard" -ForegroundColor White
    Write-Host "  2. Open your project" -ForegroundColor White
    Write-Host "  3. Click the 'web' service" -ForegroundColor White
    Write-Host "  4. Go to Settings -> Networking (or Domains)" -ForegroundColor White
    Write-Host "  5. Copy the generated URL (e.g. https://web-production-xxxxx.up.railway.app)" -ForegroundColor White
    Write-Host ""
    Write-Host "Then set in .go_live_config: BACKEND_URL=<that URL>" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

try {
    # railway domain lists domains; need to be linked and have web service
    $configPath = Join-Path $rootDir ".go_live_config"
    $webService = "web"
    if (Test-Path $configPath) {
        Get-Content $configPath | ForEach-Object {
            if ($_ -match '^\s*WEB_SERVICE=(.*)$') {
                $webService = $matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    $out = railway status -s $webService 2>&1
    $domainOut = railway domain -s $webService 2>&1
    if ($LASTEXITCODE -eq 0 -and $domainOut) {
        $url = $domainOut.Trim()
        if (-not $url.StartsWith("http")) { $url = "https://" + $url }
        Write-Host "Web service URL: $url" -ForegroundColor Green
        Write-Host ""
        Write-Host "Set in .go_live_config: BACKEND_URL=$url" -ForegroundColor Gray
    } else {
        Write-Host "Could not get URL from CLI. Get it manually:" -ForegroundColor Yellow
        Write-Host "  Railway -> your project -> 'web' service -> Settings -> Networking/Domains" -ForegroundColor White
        Write-Host "  Copy the URL and set BACKEND_URL=<url> in .go_live_config" -ForegroundColor Gray
    }
} catch {
    Write-Host "Railway CLI error. Get the URL manually:" -ForegroundColor Yellow
    Write-Host "  Railway -> your project -> 'web' service -> Settings -> Networking/Domains" -ForegroundColor White
    Write-Host "  Copy the URL (e.g. https://web-production-xxxxx.up.railway.app)" -ForegroundColor White
    Write-Host "  Set in .go_live_config: BACKEND_URL=<that URL>" -ForegroundColor Gray
}
Write-Host ""
