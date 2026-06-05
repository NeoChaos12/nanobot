# Stops the running bot, re-registers the Task Scheduler task, and starts it.
# Run as Administrator when deploying code changes or adjusting task settings.
#
# Usage: Right-click -> "Run with PowerShell" (as Administrator)
#        OR from an elevated PowerShell prompt:
#        powershell -ExecutionPolicy Bypass -File "C:\path\to\nanobot\tools\setup_task_scheduler.ps1"
#
# Restart-on-failure:
#   -RestartInterval and -RestartCount below make Task Scheduler relaunch the bot
#   automatically whenever it exits with a non-zero code. The /restart Telegram command
#   triggers this intentionally (listener.py calls sys.exit(1) after a clean shutdown).

# Replace these paths with your actual installation paths
$pythonExe = "C:\path\to\nanobot\windows\.venv\Scripts\pythonw.exe"
$scriptArg = "src\listener.py"
$workingDir = "C:\path\to\nanobot\windows"
$taskName   = "NanobotAgent"

# Stop and remove old WinSW service if still present
$winsw = "C:\path\to\nanobot\tools\WinSW.exe"
if (Test-Path $winsw) {
    Write-Host "Stopping and uninstalling WinSW service (if running)..."
    & $winsw stop    $taskName 2>$null
    & $winsw uninstall $taskName 2>$null
}

# Stop any currently running instance before re-registering
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq "Running") {
    Write-Host "Stopping running instance of $taskName..."
    Stop-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 3
}

# Build task components
$action   = New-ScheduledTaskAction -Execute $pythonExe -Argument $scriptArg -WorkingDirectory $workingDir
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 999 `
    -StartWhenAvailable

# Register (overwrites if already exists)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force

Write-Host "Task registered. Starting now..."
Start-ScheduledTask -TaskName $taskName

Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "Task state: $state"

if ($state -eq "Running") {
    Write-Host "SUCCESS - bot is running. Send a message on Telegram to verify."
} else {
    Write-Host "WARNING - task is not in Running state. Check Task Scheduler for details."
}
