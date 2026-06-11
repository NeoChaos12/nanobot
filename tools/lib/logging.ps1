# Shared rotating-log helper, dot-sourced by tools/*.ps1 scripts.
#
# Rotation policy: -Path (e.g. ".../watchdog.log") is treated as a base name.
# Each calendar day's entries go into "<name>-yyyy-MM-dd<ext>", so a single
# rotation only ever drops one day's worth of history -- not the whole file
# at once. On every write, daily files older than -MaxDays (default 30) are
# deleted. As a safety net, if a single day's file somehow exceeds -MaxSizeMB
# (default 5), it's split off with a time-suffixed name so logging can continue.
#
# DEBUG-level checkpoint messages are written by default (useful during
# development) and can be silenced by setting the environment variable
# NANOBOT_LOG_LEVEL=INFO at the task/session level.

function Write-Log {
    param(
        [Parameter(Mandatory=$true)] [string]$Path,
        [Parameter(Mandatory=$true)] [string]$Message,
        [ValidateSet("DEBUG","INFO","ERROR")] [string]$Level = "INFO",
        [int]$MaxSizeMB = 5,
        [int]$MaxDays = 30
    )

    if ($Level -eq "DEBUG" -and $env:NANOBOT_LOG_LEVEL -eq "INFO") {
        return
    }

    $dir   = Split-Path -Parent $Path
    $name  = [IO.Path]::GetFileNameWithoutExtension($Path)
    $ext   = [IO.Path]::GetExtension($Path)
    $today = Get-Date -Format "yyyy-MM-dd"
    $dailyPath = Join-Path $dir "$name-$today$ext"

    if (Test-Path $dailyPath) {
        $item = Get-Item $dailyPath
        if ($item.Length -ge ($MaxSizeMB * 1MB)) {
            $stamp = Get-Date -Format "HHmmss"
            Move-Item -Path $dailyPath -Destination (Join-Path $dir "$name-$today-$stamp$ext") -Force
        }
    }

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $dailyPath -Value "$timestamp [$Level] $Message"

    $cutoff = (Get-Date).AddDays(-$MaxDays)
    Get-ChildItem -Path $dir -Filter "$name-*$ext" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
