param(
    [ValidateSet("simulated", "real")]
    [string]$Mode = "simulated",
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 5000,
    [switch]$Debug,
    [switch]$SkipInstall,
    [string]$CondaEnv = "used_pytorch"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:CONDA_NO_PLUGINS = "true"

# Normalize console encoding so UTF-8 logs and Chinese text render consistently.
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
try {
    chcp 65001 > $null
} catch {
}

function Write-Banner {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "   HomeMind Central Controller" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host ""
}

function Invoke-CondaActivation {
    if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
        Write-Warning "conda command not found. Continuing with current Python environment."
        return
    }

    Write-Host ""
    Write-Host "Activating conda environment: $CondaEnv" -ForegroundColor Cyan

    $hookLines = @(conda shell.powershell hook 2>$null)
    $hook = ($hookLines -join [Environment]::NewLine).Trim()
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($hook)) {
        Invoke-Expression $hook
        conda activate $CondaEnv
    } else {
        Write-Warning "conda shell hook failed. Continuing with current Python environment."
    }
}

function Install-WebRequirements {
    if ($SkipInstall) {
        Write-Host "Skipping dependency installation." -ForegroundColor DarkYellow
        return
    }

    if (Test-Path ".\requirements-web.txt") {
        Write-Host "Installing/updating web dependencies from requirements-web.txt..." -ForegroundColor Cyan
        python -m pip install -q -r .\requirements-web.txt
    } else {
        Write-Warning "requirements-web.txt not found. Skipping dependency installation."
    }
}

Invoke-CondaActivation
Install-WebRequirements

if (-not $env:HOMEMIND_EMBEDDING_MODE) {
    $env:HOMEMIND_EMBEDDING_MODE = "local"
}

$debugEnabled = $Debug.IsPresent
$debugText = if ($debugEnabled) { "on" } else { "off" }
$launchArgs = @("--mode", $Mode, "--host", $BindHost, "--port", "$Port")
if ($debugEnabled) {
    $launchArgs += "--debug"
}

Write-Banner
Write-Host "  Current Python:"
python --version
Write-Host ""
Write-Host "  Launch Config:" -ForegroundColor Green
Write-Host "    - Mode:           $Mode" -ForegroundColor White
Write-Host "    - Host:           $BindHost" -ForegroundColor White
Write-Host "    - Port:           $Port" -ForegroundColor White
Write-Host "    - Debug:          $debugText" -ForegroundColor White
Write-Host "    - Embedding:      $env:HOMEMIND_EMBEDDING_MODE" -ForegroundColor White
Write-Host ""
Write-Host "  Access URLs:" -ForegroundColor Green
Write-Host "    - Control Panel:  http://localhost:$Port" -ForegroundColor White
Write-Host "    - API Status:     http://localhost:$Port/api/status" -ForegroundColor White
Write-Host "    - TAP Rules:      http://localhost:$Port/api/tap-rules" -ForegroundColor White
Write-Host "    - Floor Plans:    http://localhost:$Port/api/floor-plans" -ForegroundColor White
Write-Host ""
Write-Host "  Tip: set `$env:HOMEMIND_EMBEDDING_MODE='download'` before startup if you want to allow model download/loading." -ForegroundColor DarkGray
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

python .\run_web.py @launchArgs
