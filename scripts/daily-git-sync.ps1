#Requires -Version 5.1
<#
.SYNOPSIS
  If the repo has any changes (respecting .gitignore), stage all, commit, and push.

.DESCRIPTION
  Intended for a daily scheduled task. Skips quietly when there is nothing to commit.
  Aborts if a merge or rebase is in progress.

  Git push uses your normal credentials (e.g. SSH to CodeCommit). Scheduled tasks run
  with a minimal environment: ensure SSH works non-interactively (ssh-agent + key loaded
  at logon, or a deploy key / credential helper configured for this user).

.PARAMETER RepoRoot
  Repository root. Default: parent of the directory containing this script.

.PARAMETER LogPath
  Optional append-only log file (directory is created if missing).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\daily-git-sync.ps1
#>
param(
    [string] $RepoRoot = "",
    [string] $LogPath = ""
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string] $Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    if ($LogPath) {
        $dir = Split-Path -Parent $LogPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    }
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

Set-Location -LiteralPath $RepoRoot

$gitDir = Join-Path $RepoRoot ".git"
if (-not (Test-Path -LiteralPath $gitDir)) {
    Write-Log "ERROR: Not a git repository: $RepoRoot"
    exit 1
}

$null = git rev-parse --is-inside-work-tree 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: git rev-parse failed."
    exit 1
}

if (Test-Path (Join-Path $gitDir "MERGE_HEAD")) {
    Write-Log "SKIP: merge in progress."
    exit 0
}
if ((Test-Path (Join-Path $gitDir "rebase-merge")) -or (Test-Path (Join-Path $gitDir "rebase-apply"))) {
    Write-Log "SKIP: rebase in progress."
    exit 0
}

$porcelain = git status --porcelain 2>$null
if (-not $porcelain) {
    Write-Log "OK: working tree clean; nothing to do."
    exit 0
}

git add -A
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: git add -A failed."
    exit 1
}

git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Log "OK: nothing staged after add (likely only ignored paths); skip commit."
    exit 0
}

$msg = "chore: auto-sync {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm")
git commit -m $msg
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: git commit failed."
    exit 1
}

git push
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: git push failed."
    exit 1
}

Write-Log "OK: committed and pushed."
exit 0
