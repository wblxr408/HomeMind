# HomeMind Central Controller Startup Script
param(
    [ValidateSet("simulated", "real")]
    [string]$Mode = "simulated",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 5000,
    [switch]$DebugMode
)

$ErrorActionPreference = "Stop"

# Activate conda environment
Write-Host ""
Write-Host "Activating conda used_pytorch environment..." -ForegroundColor Cyan
conda activate used_pytorch

# Switch to script directory
Set-Location $PSScriptRoot

# Display environment info
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "   HomeMind Central Controller" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Current Python:"
python --version
Write-Host ""
Write-Host "  Access URLs:" -ForegroundColor Green
Write-Host "    - Control Panel: http://$HostAddress`:$Port" -ForegroundColor White
Write-Host "    - API Status:    http://$HostAddress`:$Port/api/status" -ForegroundColor White
Write-Host ""
Write-Host "  Runtime:" -ForegroundColor Green
Write-Host "    - Mode:          $Mode" -ForegroundColor White
Write-Host "    - Host:          $HostAddress (local-only by default)" -ForegroundColor White
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

if ($DebugMode) {
    $argsList += "--debug"
}

python @argsList
