# util/telegram_test.ps1
$ErrorActionPreference = "Stop"

# ------------------------------------------------------------
# Load .env file into PowerShell environment
# ------------------------------------------------------------
$envFile = Join-Path $PSScriptRoot "telegram.env"

Write-Host "Using env file at: $envFile"

if (-not (Test-Path $envFile)) {
    throw "telegram.env not found at $envFile"
}

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $key, $value = $line -split "=", 2
        if ($key -and $value) {
            [System.Environment]::SetEnvironmentVariable(
                $key.Trim(),
                $value.Trim(),
                "Process"
            )
        }
    }
}

# ------------------------------------------------------------
# Read Telegram vars
# ------------------------------------------------------------
$token  = $env:TELEGRAM_BOT_TOKEN
$chatId = $env:TELEGRAM_CHAT_ID

Write-Host "Using Telegram BOT TOKEN: $token and CHAT ID: $chatId"

if ([string]::IsNullOrWhiteSpace($token))  { throw "Missing TELEGRAM_BOT_TOKEN" }
if ([string]::IsNullOrWhiteSpace($chatId)) { throw "Missing TELEGRAM_CHAT_ID" }

# ------------------------------------------------------------
# Send test message
# ------------------------------------------------------------
$uri  = "https://api.telegram.org/bot$token/sendMessage"
$body = @{
    chat_id = $chatId
    text    = "Agentic Trader Telegram test successful"
}

Invoke-RestMethod -Method Post -Uri $uri -Body $body | Out-Host
Write-Host "Telegram test message sent successfully."

