# Agentic Trader - Run Reporting Every 30 Minutes
# - Writes PnL CSVs under .\report\
# - Writes log analysis CSVs under .\report\log_analysis\
# - Intended to be invoked by Windows Task Scheduler

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
# If this script lives in app\reporting\, go up two levels to agentic-trader root
$projectRoot = (Resolve-Path (Join-Path $projectRoot "..\..")).Path

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Python not found: $python" }

Set-Location $projectRoot

$reportDir = Join-Path $projectRoot "report"
$analysisDir = Join-Path $reportDir "log_analysis"

New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
New-Item -ItemType Directory -Force -Path $analysisDir | Out-Null

$logFile = Join-Path $analysisDir "run_reporting_every_30m.log"

"[$(Get-Date -Format s)] Starting reporting run" | Out-File -FilePath $logFile -Append -Encoding utf8

# 1) PnL: last 30 days
& $python -m app.reporting.daily_pnl_reporter --window-days 30 2>&1 |
    Out-File -FilePath $logFile -Append -Encoding utf8

# 2) Logs: rebuild daily behaviour CSVs (force outdir + include rotated logs)
& $python -m app.reporting.log_analyzer_v46 `
    --outdir "report\log_analysis" `
    --paths "logs\fx_v4.6\*.log*" `
    --tag "fx_v46" `
    --include-today 2>&1 |
    Out-File -FilePath $logFile -Append -Encoding utf8

& $python -m app.reporting.log_analyzer_v46 `
    --outdir "report\log_analysis" `
    --paths "logs\xau_v4.6\*.log*" `
    --tag "xau_v46" `
    --include-today 2>&1 |
    Out-File -FilePath $logFile -Append -Encoding utf8

& $python -m app.reporting.log_analyzer_v46 `
    --outdir "report\log_analysis" `
    --paths "logs\idx_v4.6\*.log*" `
    --tag "idx_v46" `
    --include-today `
    --fill-missing-days 2>&1 |
    Out-File -FilePath $logFile -Append -Encoding utf8

"[$(Get-Date -Format s)] Reporting run complete" | Out-File -FilePath $logFile -Append -Encoding utf8
# ------------------------------------------------------------
# Loss concentration & attribution analysis (IDX / FX / XAU)
# ------------------------------------------------------------
Write-Host "[$(Get-Date -Format s)] Running loss attribution analysis..."

$analysisJobs = @(
    @{ tag = "idx_v46"; group = "IDX" },
    @{ tag = "fx_v46";  group = "FX"  },
    @{ tag = "xau_v46"; group = "XAU" }
)

foreach ($job in $analysisJobs) {
    Write-Host "[$(Get-Date -Format s)] [analysis] tag=$($job.tag) group=$($job.group)"
    
    $paths = @()
    if ($job.tag -eq "idx_v46") { $paths = @("logs\idx_v4.6\*.log*") }
    elseif ($job.tag -eq "fx_v46") { $paths = @("logs\fx_v4.6\*.log*") }
    elseif ($job.tag -eq "xau_v46") { $paths = @("logs\xau_v4.6\*.log*") }
    & $python `
        -m app.analysis.analysis_loss_attribution `
        --tag $job.tag `
        --group $job.group `
        --outdir report/log_analysis `
        --paths $paths `
        2>&1 | Tee-Object -FilePath $logFile -Append
}

Write-Host "[$(Get-Date -Format s)] Loss attribution analysis complete."
