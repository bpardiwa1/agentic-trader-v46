<#
.SYNOPSIS
    Agentic Trader FX v4 – Trade Monitor Runner
.DESCRIPTION
    Runs the FX v4 Trade Monitor microservice (Uvicorn FastAPI).
    Automatically restarts if it stops or crashes.
#>

# ================================
# 🧠 Configuration
# ================================
$python = ".\.venv\Scripts\python.exe"
$uvicorn = ".\.venv\Scripts\uvicorn.exe"
$module = "fx_v4.trade.trade_monitor:app"
$hostip   = "127.0.0.1"
$port   = 8011
$interval = 10      # restart delay (seconds)
$logDir = "logs"
$logFile = "$logDir\TradeMonitor_v4_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss")

# ================================
# 🧾 Setup
# ================================
Write-Host ""
Write-Host "=============================================================="
Write-Host "  Agentic Trader FX v4 - Trade Monitor Watchdog"
Write-Host "=============================================================="
Write-Host ("Start Time : {0}" -f (Get-Date))
Write-Host ("Log File   : {0}" -f $logFile)
Write-Host ("Listening  : http://{0}:{1}/status" -f $hostip, $port)
Write-Host "--------------------------------------------------------------"
Write-Host ""

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# ================================
# ♻️  Main Restart Loop
# ================================
while ($true) {
    Write-Host ""
    Write-Host ("Launching Trade Monitor on port {0}..." -f $port)
    Write-Host "--------------------------------------------------------------"

    try {
        & $uvicorn $module --host $hostip --port $port --log-level info *>> $logFile
        $exitCode = $LASTEXITCODE
        Write-Host ("Trade Monitor exited (code {0}) - restarting in {1}s..." -f $exitCode, $interval)
    }
    catch {
        Write-Host ("Error: {0}" -f $_.Exception.Message)
    }

    Start-Sleep -Seconds $interval
}
