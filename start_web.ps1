# HomeMind Central Controller Startup Script
param(
    [ValidateSet("simulated", "real")]
    [string]$Mode = "simulated",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 5000,
    [ValidateSet("mock", "qwen25-1.5b-q4", "qwen25-3b-q4")]
    [string]$LlmProfile = "qwen25-1.5b-q4",
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

$ProfileConfig = @{
    "mock" = @{
        Backend = "mock"
        DisplayName = "Mock"
        ModelPath = ""
        Context = 0
        Threads = 0
    }
    "qwen25-1.5b-q4" = @{
        Backend = "llama_cpp"
        DisplayName = "Qwen2.5-1.5B-Instruct-Q4_K_M"
        ModelPath = Join-Path $PSScriptRoot "models\Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
        Context = 2048
        Threads = 4
    }
    "qwen25-3b-q4" = @{
        Backend = "llama_cpp"
        DisplayName = "Qwen2.5-3B-Instruct-Q4_K_M"
        ModelPath = Join-Path $PSScriptRoot "models\Qwen2.5-3B-Instruct-Q4_K_M.gguf"
        Context = 2048
        Threads = 4
    }
}

$SelectedProfile = $ProfileConfig[$LlmProfile]
if (-not $SelectedProfile) {
    Write-Host "Unknown LLM profile: $LlmProfile" -ForegroundColor Red
    exit 1
}

if ($SelectedProfile.Backend -eq "llama_cpp" -and -not (Test-Path $SelectedProfile.ModelPath)) {
    Write-Host "Configured GGUF model not found: $($SelectedProfile.ModelPath)" -ForegroundColor Yellow
    Write-Host "Falling back to mock backend for this run." -ForegroundColor Yellow
    $SelectedProfile = $ProfileConfig["mock"]
    $LlmProfile = "mock"
}

$env:EDGE_LLM_PROFILE = switch ($LlmProfile) {
    "qwen25-3b-q4" { "qwen25_3b_q4" }
    default { "qwen25_1_5b_q4" }
}
$env:LLM_BACKEND = $SelectedProfile.Backend
if ($SelectedProfile.ModelPath) {
    $env:LLM_MODEL_PATH = $SelectedProfile.ModelPath
    $env:LLAMA_N_CTX = "$($SelectedProfile.Context)"
    $env:LLAMA_N_THREADS = "$($SelectedProfile.Threads)"
    $env:LLAMA_N_GPU_LAYERS = "0"
}

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
Write-Host "    - LLM Backend:   $($SelectedProfile.Backend)" -ForegroundColor White
Write-Host "    - LLM Profile:   $($SelectedProfile.DisplayName)" -ForegroundColor White
if ($SelectedProfile.ModelPath) {
    Write-Host "    - Model Path:    $($SelectedProfile.ModelPath)" -ForegroundColor White
    Write-Host "    - Context:       $($SelectedProfile.Context)" -ForegroundColor White
    Write-Host "    - Threads:       $($SelectedProfile.Threads)" -ForegroundColor White
}
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
