<#
Agentic Trader FX v4 - Resilient Runner
---------------------------------------
• Activates .venv
• Runs fx_v4.fx_main in loop mode
• Auto-restarts on crash or exit (with 10-second delay)
• Logs to timestamped file in /logs
Usage:
    PS> ./run_fx_v4.ps1
#>

# --- Setup paths ---
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $projectRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"
$logs = Join-Path $projectRoot "logs"

if (-not (Test-Path $logs)) {
    New-Item -ItemType Directory -Path $logs | Out-Null
}

# --- Settings ---
$interval = 60             # seconds between trade loop cycles
$restartDelay = 10         # seconds before restart if crash
$loglevel = "INFO"         # INFO or DEBUG
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logs "FX_v4_$timestamp.log"

Write-Host "Starting Agentic Trader FX v4 Watchdog" -ForegroundColor Cyan
Write-Host "Loop Interval: $interval s | Restart Delay: $restartDelay s" -ForegroundColor Gray
Write-Host "Logging to: $logFile"
Write-Host "--------------------------------------------------------------"

# --- Watchdog loop ---
while ($true) {
    try {
        $startTime = Get-Date
        Write-Host "`n Launching new FX v4 session ($($startTime.ToString('HH:mm:ss')))..." -ForegroundColor Yellow

# --- Load environment variables from fx_v4.env ---
    $envFile = Join-Path $projectRoot "fx_v4\app\fx_v46.env"
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
         if ($_ -match "^\s*#") { return }             # skip comments
            if ($_ -match "^\s*$") { return }             # skip empty lines
                $parts = $_ -split "=", 2
            if ($parts.Length -eq 2) {
                $key = $parts[0].Trim()
                $val = $parts[1].Trim()
                [System.Environment]::SetEnvironmentVariable($key, $val)
            }
     }
    Write-Host "Environment loaded from $envFile"
    } else {
        Write-Host "No fx_v4.env found at $envFile"
    }
    #  python.exe -m fx_v4.fx_main --loop --interval $interval --loglevel $loglevel *>&1 | Tee-Object -FilePath $logFile -Append            Tee-Object -FilePath $logFile -Append

        python.exe -m fx_v4.fx_main --loop --interval 60   

        $LASTEXITCODE = 0


        $exitCode = $LASTEXITCODE
        $endTime = Get-Date
        Write-Host "Agent exited (code=$exitCode) at $($endTime.ToString('HH:mm:ss'))" -ForegroundColor Red
        Write-Host "Restarting in $restartDelay seconds..." -ForegroundColor DarkYellow
        Start-Sleep -Seconds $restartDelay
    }
    catch {
        Write-Host "Unexpected PowerShell exception: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Restarting in $restartDelay seconds..." -ForegroundColor DarkYellow
        Start-Sleep -Seconds $restartDelay
    }
}
