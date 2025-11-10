# Create the directory tree
$root = "fx_v4"
New-Item -ItemType Directory -Force -Path "$root","$root\app","$root\util","$root\acmi","$root\guardrails" | Out-Null
New-Item -ItemType File -Force -Path "$root\__init__.py","$root\app\__init__.py","$root\util\__init__.py","$root\acmi\__init__.py","$root\guardrails\__init__.py" | Out-Null

# Write installer
@'
MetaTrader5>=5.0
numpy>=1.26
pandas>=2.2
python-dotenv>=1.0
requests>=2.32
'@ | Out-File "$root\requirements.txt" -Encoding utf8

@'
# Agentic Trader FX v4 Environment Setup
Write-Host "🚀 Setting up Agentic Trader FX v4 Environment..." -ForegroundColor Cyan
if (Test-Path ".venv") { Write-Host "⚠️  Existing virtual environment found." -ForegroundColor Yellow } else { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install --no-cache-dir -r fx_v4/requirements.txt
if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }
Write-Host "`n✅ Setup complete! Run: python -m fx_v4.fx_main --loop --interval 15"
'@ | Out-File "$root\install_env.ps1" -Encoding utf8

# Minimal placeholder files
"print('fx_main placeholder')" | Out-File "$root\fx_main.py"
"print('fx_agent placeholder')" | Out-File "$root\fx_agent.py"
"print('fx_executor placeholder')" | Out-File "$root\fx_executor.py"
"print('fx_decider placeholder')" | Out-File "$root\fx_decider.py"
"print('fx_features placeholder')" | Out-File "$root\fx_features.py"
"print('fx_env placeholder')" | Out-File "$root\app\fx_env.py"
"AGENT_SYMBOLS=EURUSD-ECNc" | Out-File "$root\app\fx_config_sample.env"
"print('utils placeholder')" | Out-File "$root\util\fx_utils.py"
"print('indicators placeholder')" | Out-File "$root\util\fx_indicators.py"
"print('mt5 bars placeholder')" | Out-File "$root\util\fx_mt5_bars.py"
"print('acmi placeholder')" | Out-File "$root\acmi\acmi_interface.py"
"print('guardrails placeholder')" | Out-File "$root\guardrails\fx_guardrails.py"

# Zip it up
Compress-Archive -Path "$root" -DestinationPath "fx_v4.zip" -Force
Write-Host "✅ fx_v4.zip created in $(Get-Location)"
