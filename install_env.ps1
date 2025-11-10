# Agentic Trader FX v4 Environment Setup
Write-Host "ðŸš€ Setting up Agentic Trader FX v4 Environment..." -ForegroundColor Cyan
if (Test-Path ".venv") { Write-Host "âš ï¸  Existing virtual environment found." -ForegroundColor Yellow } else { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install --no-cache-dir -r fx_v4/requirements.txt
if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }
Write-Host "`nâœ… Setup complete! Run: python -m fx_v4.fx_main --loop --interval 15"
