# HomeMind Central Controller Startup Script
param(
    [ValidateSet("simulated", "real")]
    [string]$Mode = "simulated",
    [string]$Host = "0.0.0.0",
    [int]$Port = 5000,
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

# Activate conda environment
Write-Host ""
Write-Host "Activating conda environment..." -ForegroundColor Cyan
try {
    conda activate homemind
    $ActiveEnv = "homemind"
} catch {
    conda activate used_pytorch
    $ActiveEnv = "used_pytorch"
}

# Switch to script directory
Set-Location $PSScriptRoot

$DisplayHost = if ($Host -eq "0.0.0.0") { "localhost" } else { $Host }
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
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# Start server
if ($Debug) {
    python run_web.py --mode $Mode --host $Host --port $Port --debug
} else {
    python run_web.py --mode $Mode --host $Host --port $Port
}
