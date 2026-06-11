# Watchdog: if NanobotAgent is not Running, start it.
# Run every 1 minute by the NanobotWatchdog scheduled task -- this is the EXCLUSIVE
# restart mechanism. NanobotAgent's own RestartOnFailure does not fire reliably
# under RunLevel=Highest + LogonType=InteractiveToken -- this is a Windows Task
# Scheduler limitation (the engine-driven relaunch path for InteractiveToken tasks
# fails silently before any loggable event), not a code bug.

. "$PSScriptRoot\lib\logging.ps1"

$logPath = Join-Path $PSScriptRoot "logs\watchdog.log"
$taskName = "NanobotAgent"

Write-Log -Path $logPath -Level DEBUG -Message "watchdog: poll start"

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if (-not $task) {
    Write-Log -Path $logPath -Level ERROR -Message "task '$taskName' not found"
    exit 1
}

Write-Log -Path $logPath -Level DEBUG -Message "watchdog: task state=$($task.State)"

if ($task.State -ne "Running") {
    Write-Log -Path $logPath -Level INFO -Message "state=$($task.State) -- starting"
    Start-ScheduledTask -TaskName $taskName
} else {
    Write-Log -Path $logPath -Level INFO -Message "state=Running -- ok"
}
