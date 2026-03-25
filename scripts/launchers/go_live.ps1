# go_live.ps1 — One command: login, update Railway token, push code, sync trades
# Usage: .\go_live.ps1
# One-time setup: see GO_LIVE_README.md

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host ""
Write-Host ("=" * 40) -ForegroundColor Cyan
Write-Host "  GO LIVE - Engine + Trades" -ForegroundColor Cyan
Write-Host ("=" * 40) -ForegroundColor Cyan
Write-Host ""

# Load config (BACKEND_URL, ENGINE_SERVICE)
$configPath = Join-Path $scriptDir ".go_live_config"
if (Test-Path $configPath) {
    Get-Content $configPath | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            $name = $matches[1].Trim()
            $val = $matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $val, "Process")
        }
    }
}
$backendUrl = $env:BACKEND_URL
$engineService = $env:ENGINE_SERVICE
$webService = $env:WEB_SERVICE
$apiKey = $env:KITE_API_KEY
if (-not $engineService) { $engineService = "engine" }
if (-not $webService) { $webService = "web" }

# Step 1: Zerodha login
Write-Host "[1/4] Zerodha Login" -ForegroundColor Yellow
Write-Host "      Browser will open. Log in, then paste the request_token from the URL." -ForegroundColor Gray
Write-Host ""
python zerodha_login.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Zerodha login failed." -ForegroundColor Red
    exit 1
}

$tokenPath = Join-Path $scriptDir "access_token.txt"
if (-not (Test-Path $tokenPath)) {
    Write-Host "ERROR: access_token.txt not created." -ForegroundColor Red
    exit 1
}
$token = (Get-Content $tokenPath -Raw).Trim()
if (-not $token) {
    Write-Host "ERROR: Token is empty." -ForegroundColor Red
    exit 1
}
Write-Host "      Token saved." -ForegroundColor Green
Write-Host ""

# Step 2: Update Railway (engine + web services)
Write-Host "[2/4] Updating KITE credentials on Railway (engine + web)..." -ForegroundColor Yellow
$railway = Get-Command railway -ErrorAction SilentlyContinue
if ($railway) {
    $engineOk = $false
    $webOk = $false
    # KITE_ACCESS_TOKEN (required for both)
    $token | railway variable set KITE_ACCESS_TOKEN --stdin -s $engineService
    if ($LASTEXITCODE -eq 0) { $engineOk = $true }
    $token | railway variable set KITE_ACCESS_TOKEN --stdin -s $webService
    if ($LASTEXITCODE -eq 0) { $webOk = $true }
    # KITE_API_KEY (required for charts; set on both if in config)
    if ($apiKey) {
        $apiKey | railway variable set KITE_API_KEY --stdin -s $engineService 2>$null
        $apiKey | railway variable set KITE_API_KEY --stdin -s $webService 2>$null
        Write-Host "      KITE_API_KEY + KITE_ACCESS_TOKEN updated on both services." -ForegroundColor Green
    } elseif ($engineOk -and $webOk) {
        Write-Host "      KITE_ACCESS_TOKEN updated on both. Add KITE_API_KEY to .go_live_config for charts." -ForegroundColor Yellow
    }
    if (-not $engineOk -or -not $webOk) {
        Write-Host "      Some updates failed. Paste token manually to Railway -> Variables." -ForegroundColor Yellow
        Set-Clipboard -Value $token
        Write-Host "      (Token copied to clipboard)" -ForegroundColor Gray
    }
} else {
    Write-Host "      Railway CLI not found. Install: npm i -g @railway/cli then run: railway login, railway link" -ForegroundColor Yellow
    Set-Clipboard -Value $token
    Write-Host "      Token copied to clipboard. Paste into Railway -> engine and web -> Variables." -ForegroundColor Gray
}
Write-Host ""

# Step 3: Push to GitHub
Write-Host "[3/4] Pushing to GitHub..." -ForegroundColor Yellow
git add -A
$status = git status --short
if ($status) {
    git commit -m "Update (go_live)"
    git push origin main
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      Pushed. Vercel + Railway will auto-deploy." -ForegroundColor Green
    } else {
        Write-Host "      Push failed. Check connection." -ForegroundColor Red
    }
} else {
    Write-Host "      Nothing to commit." -ForegroundColor Gray
}
Write-Host ""

# Step 4: Sync trades
Write-Host "[4/4] Syncing trades to cloud..." -ForegroundColor Yellow
if ($backendUrl) {
    $env:BACKEND_URL = $backendUrl
    & "$scriptDir\sync_trades_to_cloud.ps1"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      Trades synced." -ForegroundColor Green
    } else {
        Write-Host "      Sync failed. Check BACKEND_URL in .go_live_config" -ForegroundColor Yellow
    }
} else {
    Write-Host "      Skipped. Add BACKEND_URL to .go_live_config to enable trade sync." -ForegroundColor Gray
}
Write-Host ""
Write-Host ("=" * 40) -ForegroundColor Cyan
Write-Host "  Done." -ForegroundColor Green
Write-Host ("=" * 40) -ForegroundColor Cyan
Write-Host ""
