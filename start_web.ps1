# HomeMind Central Controller Startup Script
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
Write-Host "    - Control Panel: http://localhost:5000" -ForegroundColor White
Write-Host "    - API Status:    http://localhost:5000/api/status" -ForegroundColor White
Write-Host ""
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# Start server
python run_web.py
