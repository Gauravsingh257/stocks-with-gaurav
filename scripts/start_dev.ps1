param(
    [switch]$NoFrontend,
    [switch]$WithTunnel
)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Trading Algo Dev Environment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = "python"
    Write-Host "[WARN] No .venv found, using system Python" -ForegroundColor Yellow
} else {
    Write-Host "[OK] Using venv Python" -ForegroundColor Green
}

# Kill stale processes on ports
$conns8000 = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
foreach ($c in $conns8000) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
$conns3000 = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue
foreach ($c in $conns3000) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

# Start FastAPI backend
Write-Host "`n[Starting] FastAPI backend on port 8000..." -ForegroundColor Yellow
$env:PYTHONPATH = $ProjectRoot
$backendArgs = "-NoExit -Command `"Set-Location '$ProjectRoot'; & '$venvPython' -m uvicorn dashboard.backend.main:app --reload --port 8000 --host 0.0.0.0`""
Start-Process powershell -ArgumentList $backendArgs -WindowStyle Normal
Write-Host "[OK] Backend started" -ForegroundColor Green

# Start Next.js frontend
if (-not $NoFrontend) {
    $frontendDir = Join-Path $ProjectRoot "dashboard\frontend"
    if (Test-Path (Join-Path $frontendDir "package.json")) {
        Write-Host "`n[Starting] Next.js frontend on port 3000..." -ForegroundColor Yellow
        $frontendArgs = "-NoExit -Command `"Set-Location '$frontendDir'; npm run dev`""
        Start-Process powershell -ArgumentList $frontendArgs -WindowStyle Normal
        Write-Host "[OK] Frontend started" -ForegroundColor Green
    }
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Backend:  http://localhost:8000" -ForegroundColor White
Write-Host "  API Docs: http://localhost:8000/docs" -ForegroundColor White
if (-not $NoFrontend) {
    Write-Host "  Frontend: http://localhost:3000" -ForegroundColor White
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`nClose the spawned PowerShell windows to stop services.`n" -ForegroundColor Gray
