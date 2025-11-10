# ============================================================
# Agentic Trader FX v4 — ACMI Dashboard Runner
# ============================================================

Write-Host "Starting Agentic Trader FX v4 ACMI Dashboard" -ForegroundColor Cyan
Write-Host "------------------------------------------------------------"

$python = "$((Get-Command python).Source)"
$logDir = "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

$timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$logFile = "$logDir\ACMI_Dashboard_v4_$timestamp.log"
$port = 8010

Write-Host "Log File : $logFile"
Write-Host "Listening : http://127.0.0.1:$port/acmi/dashboard"
Write-Host "------------------------------------------------------------`n"

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "& {
        try {
            $env:PYTHONUNBUFFERED='1'
            & $python -m uvicorn fx_v4.acmi.acmi_interface:app --host 127.0.0.1 --port $port --reload 2>&1 | Tee-Object -FilePath '$logFile'
        } catch {
            Write-Host 'ACMI Dashboard exited unexpectedly.' -ForegroundColor Red
        }
    }"
)
