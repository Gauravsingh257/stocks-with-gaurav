# SMC Morning Login - PowerShell helper
# Prompts for URL/token paste, POSTs to backend, then runs health check.
# Also saves token locally (access_token.txt + .env) for local backtests.
param([string]$Backend = "https://web-production-2781a.up.railway.app")

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Save-TokenLocally {
    param([string]$Token)
    if (-not $Token) { return }
    # 1. access_token.txt
    $tokenFile = Join-Path $projectRoot "access_token.txt"
    Set-Content -Path $tokenFile -Value $Token -NoNewline
    Write-Host "    -> Saved to access_token.txt" -ForegroundColor DarkGray
    # 2. .env (update KITE_ACCESS_TOKEN line)
    $envFile = Join-Path $projectRoot ".env"
    if (Test-Path $envFile) {
        $lines = Get-Content $envFile
        $found = $false
        $newLines = @()
        foreach ($line in $lines) {
            if ($line -match "^KITE_ACCESS_TOKEN=") {
                $newLines += "KITE_ACCESS_TOKEN=$Token"
                $found = $true
            } else {
                $newLines += $line
            }
        }
        if (-not $found) { $newLines += "KITE_ACCESS_TOKEN=$Token" }
        $newLines | Set-Content $envFile
        Write-Host "    -> Updated .env KITE_ACCESS_TOKEN" -ForegroundColor DarkGray
    }
}

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
        # Save returned access_token locally for backtests / local scripts
        if ($r.access_token) {
            Write-Host "  Saving token locally..." -ForegroundColor Cyan
            Save-TokenLocally -Token $r.access_token
        }
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

# Always try to sync token from backend (covers auto-redirect case)
if (-not $submitted) {
    Write-Host ""
    Write-Host "  Syncing token from Railway backend..."
    try {
        $tr = Invoke-RestMethod -Uri "$Backend/api/kite/current-token" -Method GET -TimeoutSec 10
        if ($tr.access_token) {
            Save-TokenLocally -Token $tr.access_token
            Write-Host "  Token synced from backend." -ForegroundColor Green
        }
    } catch {
        Write-Host "  Could not sync token from backend (may not be logged in yet)." -ForegroundColor Yellow
    }
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
