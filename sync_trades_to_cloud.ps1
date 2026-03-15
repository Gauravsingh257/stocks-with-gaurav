# Sync trade_ledger_2026.csv to production (Railway)
# Run this to push your local trades to stockswithgaurav.com
# Usage: .\sync_trades_to_cloud.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$csvPath = Join-Path $scriptDir "trade_ledger_2026.csv"

# Load BACKEND_URL from .go_live_config if not set
if (-not $env:BACKEND_URL) {
    $configPath = Join-Path $scriptDir ".go_live_config"
    if (Test-Path $configPath) {
        Get-Content $configPath | ForEach-Object {
            if ($_ -match '^\s*BACKEND_URL=(.*)$') {
                $env:BACKEND_URL = $matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
}
$apiUrl = if ($env:BACKEND_URL) { $env:BACKEND_URL.Trim().TrimEnd('/') } else { "" }
if (-not $apiUrl) {
    Write-Host "ERROR: Set BACKEND_URL first. Example: BACKEND_URL=https://YOUR-RAILWAY-URL.up.railway.app in .go_live_config"
    exit 1
}
if (-not $apiUrl.StartsWith("http")) { $apiUrl = "https://" + $apiUrl }
$syncUrl = "$apiUrl/api/journal/sync"

if (-not (Test-Path $csvPath)) {
    Write-Host "ERROR: trade_ledger_2026.csv not found at $csvPath"
    exit 1
}

$rows = Import-Csv $csvPath -Encoding UTF8
$trades = @()
foreach ($r in $rows) {
    $trades += @{
        date       = $r.date
        symbol     = $r.symbol
        direction  = $r.direction
        setup      = $r.setup
        entry      = if ($r.entry) { [double]$r.entry } else { $null }
        exit_price = if ($r.exit_price) { [double]$r.exit_price } else { $null }
        result     = $r.result
        pnl_r      = if ($r.pnl_r) { [double]$r.pnl_r } else { $null }
    }
}

$body = $trades | ConvertTo-Json -Compress
$headers = @{
    "Content-Type" = "application/json"
}
if ($env:TRADES_SYNC_KEY) {
    $headers["X-Sync-Key"] = $env:TRADES_SYNC_KEY
}

Write-Host "Syncing $($trades.Count) trades to $syncUrl ..."
try {
    $resp = Invoke-RestMethod -Uri $syncUrl -Method Post -Body $body -Headers $headers
    Write-Host "OK: Synced $($resp.synced) trades."
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    if ($_.Exception.Message -match "502") {
        Write-Host ""
        Write-Host "502 = Backend is down or not ready. Fix the Railway WEB service:" -ForegroundColor Yellow
        Write-Host "  1. Railway -> web -> Settings -> Build -> Dockerfile Path = Dockerfile" -ForegroundColor White
        Write-Host "  2. Redeploy and wait for 'Deployment successful'" -ForegroundColor White
        Write-Host "  3. Run this sync again" -ForegroundColor White
    }
    exit 1
}
