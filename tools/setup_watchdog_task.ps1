# Registers "NanobotWatchdog": a Task Scheduler task that runs every 1 minute
# (PT1M is Task Scheduler's documented minimum RepetitionInterval -- PT30S is
# rejected with "value ... incorrectly formatted or out of range").
# This is the EXCLUSIVE restart mechanism for NanobotAgent -- RestartOnFailure
# does not fire reliably under RunLevel=Highest + LogonType=InteractiveToken (a
# Windows Task Scheduler limitation), so it's kept only for parity/no harm. If
# the bot task isn't Running, this starts it via Start-ScheduledTask.
#
# Run as Administrator (one-time, after setup_task_scheduler.ps1):
#   powershell -ExecutionPolicy Bypass -File "C:\path\to\nanobot\tools\setup_watchdog_task.ps1"

$taskName  = "NanobotWatchdog"
$scriptPath = Join-Path $PSScriptRoot "watchdog.ps1"

$runHidden = "$PSScriptRoot\run_hidden.vbs"

# Launched via run_hidden.vbs (WshShell.Run with window style 0) instead of
# "powershell -WindowStyle Hidden" directly -- the latter still flashes a
# console window briefly on an interactive desktop.
$action  = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "//B //Nologo `"$runHidden`" powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -Force | Out-Null

Start-ScheduledTask -TaskName $taskName

Write-Host "NanobotWatchdog registered and started. It will check every 1 minute and"
Write-Host "start NanobotAgent if it's not Running. Log: tools\logs\watchdog-<date>.log"
