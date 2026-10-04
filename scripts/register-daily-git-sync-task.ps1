#Requires -Version 5.1
<#
.SYNOPSIS
  Register (or update) a Windows Scheduled Task to run daily-git-sync.ps1 once per day.

.PARAMETER Time
  Daily start time in HH:mm (24h), local timezone. Default: 18:00.

.PARAMETER TaskName
  Scheduled task name. Default: LewisClark_youtube_daily_git_sync

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\register-daily-git-sync-task.ps1 -Time "09:00"

  Run elevated if you use a different user account for the task.
#>
param(
    [string] $Time = "18:00",
    [string] $TaskName = "LewisClark_youtube_daily_git_sync"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scriptPath = Join-Path $PSScriptRoot "daily-git-sync.ps1"
$logPath = Join-Path $RepoRoot "logs\git-auto-sync.log"

if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$pwsh = $PSHOME + "\powershell.exe"
if (-not (Test-Path -LiteralPath $pwsh)) {
    $pwsh = "powershell.exe"
}

$argList = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -RepoRoot `"$RepoRoot`" -LogPath `"$logPath`""
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $argList -WorkingDirectory $RepoRoot

# Daily at $Time (local). With StartWhenAvailable, a missed run starts when the machine is next idle enough.
$trigger = New-ScheduledTaskTrigger -Daily -At $Time

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered task '$TaskName' daily at $Time (local)."
Write-Host "Script: $scriptPath"
Write-Host "Log:    $logPath"
