# ============================================================
# Agentic Trader - XAU Telegram Watcher Service Runner
# Uses local .venv Python (no activation required)
# ============================================================

$projectRoot = "C:\Users\Bomi\AgenticAI\agentic-trader"
Set-Location $projectRoot

$python = "$projectRoot\.venv\Scripts\python.exe"
$watcherPath = "$projectRoot\xau_46\watchers\xau_telegram_watcher.py"

Write-Host "============================================="
Write-Host "XAU Telegram Watcher Service Starting"
Write-Host "Using Python: $python"
Write-Host "============================================="

while ($true) {
    try {
        Write-Host "Starting watcher at $(Get-Date)"
        & $python $watcherPath
        Write-Host "Watcher exited. Restarting in 5 seconds..."
    }
    catch {
        Write-Host "Watcher crashed: $_"
    }

    Start-Sleep -Seconds 5
}

