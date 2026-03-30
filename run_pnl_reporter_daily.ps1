param(
    [string]$Env = "app\env\core.env",
    [string]$Date = "",
    [int]$DaysBack = 1
)

Write-Host "Running Agentic Trader Daily PnL Reporter"

# Project & venv
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $projectRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Warning "No venv python found, falling back to 'python' on PATH"
    $python = "python"
}

# Optional: point to your merged env (same style as run_agent.ps1)
if (Test-Path $Env) {
    $env:DOTENV_FILE = $Env
}

# Logging
$logDir = Join-Path $projectRoot "logs\pnl"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logFile = Join-Path $logDir ("pnl_runner_" + (Get-Date -Format "yyyyMMdd") + ".log")

# Build args
$argsList = @("-m", "app.reporting.daily_pnl_reporter")

if ($Date) {
    $argsList += @("--date", $Date)
} else {
    $argsList += @("--days-back", $DaysBack)
}

Write-Host "Command:" $python ($argsList -join " ")

& $python @argsList 2>&1 | Out-String | Tee-Object -FilePath $logFile -Append
