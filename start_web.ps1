# HomeMind Central Controller Startup Script
param(
    [ValidateSet("simulated", "real")]
    [string]$Mode = "simulated",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 5000,
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

# Activate conda environment
Write-Host ""
Write-Host "Activating conda environment..." -ForegroundColor Cyan

conda activate used_pytorch
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to activate conda environment: used_pytorch" -ForegroundColor Red
    exit 1
}

$ActiveEnv = "used_pytorch"
Write-Host "Activated conda environment: $ActiveEnv" -ForegroundColor Green


# Switch to script directory
Set-Location $PSScriptRoot

$DisplayHost = if ($HostAddress -eq "0.0.0.0") { "localhost" } else { $HostAddress }
$BaseUrl = "http://${DisplayHost}:$Port"
$DebugStatus = if ($Debug) { "ON" } else { "OFF" }

# Display environment info
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "   HomeMind Central Controller" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Active Conda Env: $ActiveEnv" -ForegroundColor Cyan
Write-Host "  Mode:             $Mode" -ForegroundColor Cyan
Write-Host "  Debug:            $DebugStatus" -ForegroundColor Cyan
Write-Host "  Current Python:"
python --version
Write-Host ""
Write-Host "  Access URLs:" -ForegroundColor Green
Write-Host "    - Entry Page:    $BaseUrl/" -ForegroundColor White
Write-Host "    - Control Panel: $BaseUrl/web/client/index.html" -ForegroundColor White
Write-Host "    - API Status:    $BaseUrl/api/status" -ForegroundColor White
Write-Host ""
Write-Host "  Runtime:" -ForegroundColor Green
Write-Host "    - Mode:          $Mode" -ForegroundColor White
Write-Host "    - Host:          $HostAddress" -ForegroundColor White
Write-Host "    - Port:          $Port" -ForegroundColor White
Write-Host ""
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# Start server
$argsList = @(
    "main.py",
    "--host", $HostAddress,
    "--port", "$Port",
    "--mode", $Mode
)

if ($Debug) {
    $argsList += "--debug"
}

python @argsList
