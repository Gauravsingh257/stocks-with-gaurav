# Sync trade_ledger_2026.csv to production (Railway)
# Run this to push your local trades to stockswithgaurav.com
# Usage: .\sync_trades_to_cloud.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$csvPath = Join-Path $scriptDir "trade_ledger_2026.csv"

# EDIT THIS: Your Railway backend URL (or set env BACKEND_URL)
$apiUrl = $env:BACKEND_URL
if (-not $apiUrl) {
    $apiUrl = "https://web-production-1eabc.up.railway.app"
}
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
    exit 1
}
