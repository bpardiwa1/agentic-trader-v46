# ============================================================
# NAS100 Scalper v1 — Runner (PowerShell) + venv activation
# ============================================================

$ErrorActionPreference = "Stop"

# --- Paths ---------------------------------------------------
$RootDir = "C:\Users\Bomi\AgenticAI\agentic-trader"
Set-Location $RootDir

# --- Activate venv (recommended) -----------------------------
$VenvActivate = Join-Path $RootDir ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
  try {
    . $VenvActivate
    Write-Host "[RUN] Activated venv: $VenvActivate"
  } catch {
    Write-Host "[RUN] WARNING: Failed to activate venv: $($_.Exception.Message)"
  }
} else {
  Write-Host "[RUN] WARNING: venv activate script not found at: $VenvActivate"
  Write-Host "[RUN] Continuing without venv activation..."
}

# --- Optional: point agent at env file -----------------------
# If you prefer explicit env file path (agent also defaults correctly):
$env:NAS100_SCALP_ENV = "nas100_scalp_v1\app\nas100_scalp_v1.env"

# --- Run loop ------------------------------------------------
while ($true) {
  $ts = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
  Write-Host "[$ts] Starting NAS100 Scalper v1..."

  try {
    python -m nas100_scalp_v1.nas100_agent_v1
  } catch {
    Write-Host "[RUN] Agent crashed: $($_.Exception.Message)"
  }

  Start-Sleep -Seconds 3
}