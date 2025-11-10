# ============================================================
# Agentic Trader – XAU v4.6 Watchdog
# ------------------------------------------------------------
#  - Launches XAUUSD bot with safe auto-restart loop
#  - Logs each session to /logs/XAU_v4_<timestamp>.log
#  - Auto-restarts if script crashes
# ============================================================

param(
    [int]$interval = 60,
    [string]$symbols = "XAUUSD-ECNc",
    [string]$loglevel = "INFO"
)

# --- Paths ---
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = "$base\.venv\Scripts\python.exe"
$logDir = "$base\logs"
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# --- Timestamped log file ---
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = "$logDir\XAU_v4_$timestamp.log"
Write-Host "Starting Agentic Trader XAU v4.6 Watchdog" -ForegroundColor Cyan
Write-Host "Loop Interval: $interval s | Restart Delay: 10 s" -ForegroundColor Gray
Write-Host "Logging to: $logFile"
Write-Host "--------------------------------------------------------------`n"

# --- Continuous watchdog loop ---
while ($true) {
    try {
        $ts = Get-Date -Format "HH:mm:ss"
        Write-Host "`n Launching new XAU v4.6 session ($ts)..."
        & $venvPython -m xau_v46.xau_main_v46 `
            --symbols $symbols `
            --interval $interval `
            --loop `
            --loglevel $loglevel  

        Write-Host "`n [Watchdog] Session ended or crashed. Restarting in 10s..." -ForegroundColor Yellow
        Start-Sleep -Seconds 10
    }
    catch {
        Write-Host "`n [Watchdog] Error: $_" -ForegroundColor Red
        Start-Sleep -Seconds 10
    }
}
