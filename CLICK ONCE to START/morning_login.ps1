# SMC Morning Login - PowerShell helper
# Prompts for URL/token paste, POSTs to backend, then runs health check.
param([string]$Backend = "https://web-production-2781a.up.railway.app")

Write-Host ""
Write-Host "  If you see 'connected' in browser — press Enter to verify."
Write-Host "  If redirect failed — paste the full URL or request_token below:"
Write-Host ""
$input = Read-Host "  Paste URL or token (or Enter to skip)"

$submitted = $false
if ($input -and ($input = $input.Trim())) {
    # Extract request_token if user pasted full URL
    $token = $input
    if ($input -match 'request_token=([^&\s]+)') {
        $token = $Matches[1].Trim()
    }
    Write-Host ""
    Write-Host "  Submitting token..." -ForegroundColor Cyan
    try {
        $escaped = [uri]::EscapeDataString($token)
        $r = Invoke-RestMethod -Uri "$Backend/api/kite/callback?request_token=$escaped" -Method GET
        Write-Host "  " $r.message -ForegroundColor Green
        $submitted = $true
    } catch {
        Write-Host "  Error:" $_.Exception.Message -ForegroundColor Red
    }
}

if (-not $submitted) {
    Write-Host ""
    Write-Host "  Waiting 45 seconds for auto redirect..."
    Start-Sleep -Seconds 45
} else {
    Start-Sleep -Seconds 5
}

Write-Host ""
Write-Host "  Checking system health..."
Write-Host ""
try {
    $r = Invoke-WebRequest -Uri "$Backend/api/system/health" -TimeoutSec 15 -UseBasicParsing
    $j = $r.Content | ConvertFrom-Json
    Write-Host "  Kite Connected:" $j.kite_connected
    Write-Host "  Token Present:" $j.token_present
    Write-Host "  Token Source:" $j.token_source
    Write-Host "  Engine Live:" $j.engine_live
    Write-Host "  Backend Version:" $j.backend_version
    if ($j.kite_disconnect_reason) { Write-Host "  Reason:" $j.kite_disconnect_reason }
    if ($j.kite_hint) { Write-Host ""; Write-Host "  Hint:" $j.kite_hint }
} catch {
    Write-Host "  Could not reach backend. Check Railway." -ForegroundColor Red
}
