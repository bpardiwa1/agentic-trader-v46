# ============================================================
# Agentic Trader IDX v4.6 — PowerShell Runner (with Server Mode)
# ------------------------------------------------------------
# Usage:
#   ./run_idx_v46.ps1 [-Loop] [-Interval 20] [-LogConsole] [-Server]
#
# Modes:
#   -Loop        → Run IDX agent continuously
#   -Interval N  → Seconds between cycles (default: 20)
#   -LogConsole  → Stream logs to console instead of file
#   -Server      → Start FastAPI Uvicorn dashboard (app.main)
# ============================================================

param(
    [switch]$Loop = $false,
    [int]$Interval = 60,
    [switch]$LogConsole = $false,
    [switch]$Server = $false
)

# -------------------------------
# Setup Paths
# -------------------------------
$RootPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $RootPath

$EnvFile = Join-Path $RootPath "idx_v46\app\idx_v46.env"
$LogDir  = Join-Path $RootPath "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $LogDir "IDX_${Timestamp}.log"

# -------------------------------
# Activate Virtual Environment
# -------------------------------
if (Test-Path ".venv\Scripts\Activate.ps1") {
    Write-Host "[INIT] Activating virtual environment..."
    . ".venv\Scripts\Activate.ps1"
}
else {
    Write-Host "[WARN] No virtual environment found (.venv). Using system Python..."
}

# -------------------------------
# Verify Environment File
# -------------------------------
if (-not (Test-Path $EnvFile)) {
    Write-Host "[ERROR] IDX environment file not found at: $EnvFile"
    exit 1
}

Write-Host "[INFO] Using environment file: $EnvFile"
$env:DOTENV_FILE = $EnvFile

# -------------------------------
# Select Mode
# -------------------------------
if ($Server) {
    Write-Host "[MODE] Server mode → launching FastAPI dashboard..."
    $Port = 9010
    Write-Host "[INFO] Starting Uvicorn on http://127.0.0.1:$Port"

    # Clear any agent args
    $Arg = @()

    # Use plain python call for reliability
    python -m uvicorn idx_v46.idx_main_v46:app --host 127.0.0.1 --port $Port
    exit 0
}

# -------------------------------
# Prepare Python arguments (Agent)
# -------------------------------
$Arg = @("--interval", "$Interval")
if ($Loop) { $Arg += "--loop" }
$ArgList = @("-m", "idx_v46.idx_main_v46") + $Arg

# -------------------------------
# Run IDX Agent
# -------------------------------
Write-Host "[INFO] Launching Agentic Trader IDX v4.6"
Write-Host "------------------------------------------------------------"

if ($LogConsole) {
    # Interactive console output
    Write-host "[RUNNING]: $ArgList"
    & python.exe $ArgList
}
else {
    # Background mode (log file)
    Start-Process -NoNewWindow -FilePath "python.exe" `
        -ArgumentList $ArgList `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError $LogFile `
        -PassThru | Out-Null

    Write-Host "[RUNNING] Log file: $LogFile"
    Write-Host "[TIP] To tail logs: Get-Content -Path '$LogFile' -Wait"
}
