# Agentic Trader – Report Refresh Scheduler (30m) - XML-based (most compatible)

$ErrorActionPreference = "Stop"

$taskName = "AgenticTrader_ReportRefresh_30m"

# Script to run (your correct location)
$script = "C:\Users\Bomi\AgenticAI\agentic-trader\app\reporting\run_reporting_every_30m.ps1"
if (-not (Test-Path $script)) {
    throw "Reporting script not found: $script"
}

# Build command
$cmd = "powershell.exe"
$args = "-NoProfile -ExecutionPolicy Bypass -File `"$script`""

# Start time: 1 minute from now (local)
$start = (Get-Date).AddMinutes(1).ToString("s")

# Task XML (runs every 30 minutes indefinitely)
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Agentic Trader: refresh PnL + log analysis CSVs every 30 minutes</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>$start</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT30M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$cmd</Command>
      <Arguments>$args</Arguments>
      <WorkingDirectory>C:\Users\Bomi\AgenticAI\agentic-trader</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Remove existing task if present
try {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
} catch {}

# Register from XML (no repetition property issues)
Register-ScheduledTask -TaskName $taskName -Xml $xml | Out-Null

Write-Host "✅ Scheduled Task created: $taskName"
Write-Host "   Runs every 30 minutes:"
Write-Host "   $args"
