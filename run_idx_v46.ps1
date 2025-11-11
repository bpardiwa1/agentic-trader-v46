# ============================================================
# Agentic Trader — IDX v4.6 Runner (PowerShell)
# ============================================================
# This script launches the Indices agent (NAS100.s, UK100.s, HK50.s)
# in a persistent loop with environment variables loaded from app\idx_v46.env
# and logs captured under logs\IDX_YYYY-MM-DD_HH-MM-SS.log
# ============================================================

$ErrorActionPreference = "Stop"

# --- Directories --------------------------------------------------------
$RootDir = "C:\Users\Bomi\AgenticAI\agentic-trader"
$AppDir  = "$RootDir\idx_v46"
$EnvFile = "$AppDir\app\idx_v46.env"
$LogDir  = "$RootDir\logs"

# --- Ensure log directory exists ---------------------------------------
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# --- Timestamped log filename ------------------------------------------
$Timestamp = (Get-Date -Format "yyyy-MM-dd_HH-mm-ss")
$LogFile = "$LogDir\IDX_$Timestamp.log"
$LatestLink = "$LogDir\IDX.latest.log"

# --- Activate venv -----------------------------------------------------
$VenvPath = "$RootDir\.venv\Scripts\Activate.ps1"
if (Test-Path $VenvPath) {
    Write-Host "Activating Python venv..."
    & $VenvPath
} else {
    Write-Host "❌ Virtual environment not found at $VenvPath"
    exit 1
}

# --- Load environment file ---------------------------------------------
Write-Host "Loading environment variables from: $EnvFile"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*#') { return }
        if ($_ -match '^\s*$') { return }
        $pair = $_ -split '=', 2
        if ($pair.Length -eq 2) {
            [System.Environment]::SetEnvironmentVariable($pair[0].Trim(), $pair[1].Trim(), "Process")
        }
    }
} else {
    Write-Host "❌ Environment file not found: $EnvFile"
    exit 1
}

# --- Create latest symlink for convenience ------------------------------
# --- Ensure log file exists before linking ------------------------------
if (!(Test-Path $LogFile)) {
    New-Item -ItemType File -Path $LogFile | Out-Null
}

# --- Create latest symlink for convenience ------------------------------
if (Test-Path $LatestLink) {
    Remove-Item $LatestLink -Force
}
try {
    New-Item -ItemType SymbolicLink -Path $LatestLink -Target $LogFile | Out-Null
} catch {
    Write-Warning "Could not create symbolic link for latest log: $($_.Exception.Message)"
}


# --- Run agent ----------------------------------------------------------
Write-Host "============================================================"
Write-Host "Launching IDX v4.6 Agent..."
Write-Host "Symbols: $env:AGENT_SYMBOLS"
Write-Host "Timeframe: $env:IDX_TIMEFRAME"
Write-Host "Logging to: $LogFile"
Write-Host "============================================================"

$PythonExe = "$RootDir\.venv\Scripts\python.exe"
$MainScript = "$AppDir\idx_main_v46.py"

if (!(Test-Path $PythonExe)) {
    Write-Host "❌ Python executable not found at $PythonExe"
    exit 1
}

if (!(Test-Path $MainScript)) {
    Write-Host "❌ IDX main script not found at $MainScript"
    exit 1
}

# --- Launch main module with loop + info level logging -----------------
# & $PythonExe -m idx_v46.idx_main_v46 --loop --loglevel INFO *>&1 | Tee-Object -FilePath $LogFile
& $PythonExe -m idx_v46.idx_main_v46 --loop --loglevel INFO 
Write-Host "============================================================"
Write-Host "IDX v4.6 Agent stopped or exited."
Write-Host "============================================================"
