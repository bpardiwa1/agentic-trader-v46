<#
.SYNOPSIS
    Agentic Trader FX v4 – Environment Verification Runner
.DESCRIPTION
    Runs the Python-based sanity checker and prints a green "ALL GOOD"
    banner if everything passes or a red "FIX ENV" banner otherwise.
#>

$python = ".\.venv\Scripts\python.exe"
$script = "fx_v4\tools\verify_env.py"

Write-Host ""
Write-Host "=============================================================="
Write-Host "  Agentic Trader FX v4 - Environment Verification"
Write-Host "=============================================================="
Write-Host ("Start Time : {0}" -f (Get-Date))
Write-Host "--------------------------------------------------------------"
Write-Host ""

try {
    # Run the Python verification script
    & $python $script
    $exitCode = $LASTEXITCODE

    Write-Host ""
    Write-Host "--------------------------------------------------------------"
    if ($exitCode -eq 0) {
        Write-Host "ALL GOOD TO GO - Environment looks clean!" -ForegroundColor Green
    } else {
        Write-Host "FIX ENV - Some settings need attention!" -ForegroundColor Red
    }
    Write-Host "--------------------------------------------------------------"
}
catch {
    Write-Host ("Error: {0}" -f $_.Exception.Message) -ForegroundColor Red
}
