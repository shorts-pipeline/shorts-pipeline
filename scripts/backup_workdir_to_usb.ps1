#Requires -Version 5.1
<#
.SYNOPSIS
  Copy this repository’s working tree to a USB drive under a dated snapshot folder.

.DESCRIPTION
  Uses robocopy (resume-friendly, multi-threaded). Writes BACKUP_MANIFEST.txt and
  RESTORE.txt into the snapshot so later backups and recovery are self-explanatory.

  Default destination layout:
    <UsbRoot>\<SnapshotsDirName>\<yyyy-MM-dd_HHmmss>\   (repo files here)

.PARAMETER RepoRoot
  Repository root. Default: parent of the directory containing this script.

.PARAMETER UsbRoot
  Root path of the flash drive (must exist), e.g. D:\

.PARAMETER SnapshotsDirName
  Folder created on the USB holding timestamped snapshots.

.PARAMETER SkipVenv
  Exclude .venv (saves space; recreate with project docs / python -m venv).

.PARAMETER DryRun
  Robocopy list-only (/L); no files copied.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup_workdir_to_usb.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup_workdir_to_usb.ps1 -UsbRoot "E:\" -SkipVenv
#>
param(
    [string] $RepoRoot = "",
    [string] $UsbRoot = "D:\",
    [string] $SnapshotsDirName = "lewisclark_youtube_usb_backups",
    [switch] $SkipVenv,
    [switch] $DryRun
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string] $Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

$usbRootNorm = ($UsbRoot + "").Trim()
if (-not $usbRootNorm) {
    Write-Log "ERROR: UsbRoot is empty."
    exit 1
}
# Join-Path "D:" "x" -> "D:x" (wrong). Force "D:\" for drive-letter roots.
if ($usbRootNorm -match '^[A-Za-z]:$') {
    $usbRootNorm = $usbRootNorm + "\"
}
if (-not (Test-Path -LiteralPath $usbRootNorm)) {
    Write-Log "ERROR: USB path not found: $usbRootNorm"
    exit 1
}

$driveLetter = $null
if ($usbRootNorm -match '^([A-Za-z]):') {
    $driveLetter = $Matches[1].ToUpperInvariant()
}

if ($driveLetter) {
    $psDrive = Get-PSDrive -Name $driveLetter -ErrorAction SilentlyContinue
    if (-not $psDrive) {
        Write-Log "ERROR: No PowerShell drive for ${driveLetter}: (is the USB mounted?)"
        exit 1
    }
    $freeGiB = [math]::Round($psDrive.Free / 1GB, 2)
    Write-Log "Destination drive ${driveLetter}: free ~ $freeGiB GiB"
    if ($psDrive.Free -lt 5.5GB) {
        Write-Log "WARNING: Less than ~5.5 GiB free; full tree may not fit. Consider -SkipVenv."
    }
}

$snapRoot = Join-Path $usbRootNorm $SnapshotsDirName
if (-not (Test-Path -LiteralPath $snapRoot)) {
    New-Item -ItemType Directory -Path $snapRoot -Force | Out-Null
    Write-Log "Created $snapRoot"
}

$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$destRoot = Join-Path $snapRoot $stamp
New-Item -ItemType Directory -Path $destRoot -Force | Out-Null

$logPath = Join-Path $destRoot "robocopy.log"
$robocopyArgs = @(
    "`"$RepoRoot`"",
    "`"$destRoot`"",
    "/E",
    "/COPY:DAT",
    "/DCOPY:T",
    "/R:2",
    "/W:5",
    "/MT:8",
    "/LOG:`"$logPath`"",
    "/NP"
)
if ($SkipVenv) {
    $robocopyArgs += "/XD"
    $robocopyArgs += ".venv"
}
if ($DryRun) {
    $robocopyArgs += "/L"
    Write-Log "Dry run (robocopy /L); listing only."
}

Write-Log "Source: $RepoRoot"
Write-Log "Destination: $destRoot"

$robocopyCmd = "robocopy.exe " + ($robocopyArgs -join " ")
cmd.exe /c $robocopyCmd
$rc = $LASTEXITCODE

# robocopy: 0-7 = success-ish for our purposes; 8+ = failure
if ($rc -ge 8) {
    Write-Log "ERROR: robocopy failed (exit $rc). See $logPath"
    exit $rc
}

$gitHead = ""
$gitDirty = ""
Push-Location -LiteralPath $RepoRoot
try {
    $null = git rev-parse --is-inside-work-tree 2>$null
    if ($LASTEXITCODE -eq 0) {
        $gitHead = (git rev-parse HEAD 2>$null | Out-String).Trim()
        $porcelain = git status --porcelain 2>$null
        if ($porcelain) {
            $gitDirty = "yes (see git status on source machine)"
        } else {
            $gitDirty = "no"
        }
    } else {
        $gitHead = "(not a git repo or git missing)"
        $gitDirty = "unknown"
    }
} finally {
    Pop-Location
}

$manifestPath = Join-Path $destRoot "BACKUP_MANIFEST.txt"
$manifest = @"
Lewis & Clark YouTube — USB working-directory snapshot
--------------------------------------------------------
Created (local):     $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Created (UTC):       $((Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss"))
Hostname:            $env:COMPUTERNAME
User:                $env:USERNAME
Source (repo root):  $RepoRoot
Git HEAD:            $gitHead
Working tree dirty:  $gitDirty
SkipVenv:            $SkipVenv
DryRun:              $DryRun
Robocopy exit code:  $rc
Robocopy log:        $logPath

Next backup
-----------
Keep running this script; each run adds a new folder under:
  $(Join-Path $usbRootNorm $SnapshotsDirName)\
Older snapshots can be deleted manually from the USB when space is tight.

Push to remote git separately; this copy is for large / ignored trees and quick recovery.
"@
Set-Content -LiteralPath $manifestPath -Value $manifest.TrimEnd() -Encoding utf8

$restorePath = Join-Path $destRoot "RESTORE.txt"
$restore = @"
Restore on a new PC (short)
---------------------------
1. Copy this entire folder to a path of your choice (e.g. Documents\py\lewisclark_youtube).
2. Install Python 3.x and Git; clone or use this folder as the repo root (if .git was copied, it is still a repo).
3. Create .venv and install deps per project docs (AGENTS.md / docs).
4. Copy machine-local files if you have separate backups: state\run_daily_state.json, pipeline_ui\options.json, latest_*.url (see AGENTS.md).
5. Restore secrets: .env, token.json, client_secrets*.json — never commit these; keep encrypted backups off-machine.

This USB snapshot may omit .venv if the backup was run with -SkipVenv.
"@
Set-Content -LiteralPath $restorePath -Value $restore.TrimEnd() -Encoding utf8

Write-Log "Wrote $manifestPath"
Write-Log "Wrote $restorePath"
Write-Log "Done. robocopy exit $rc (0-7 treated as OK)."
exit 0
