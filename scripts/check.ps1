#!/usr/bin/env pwsh
# Pre-push / pre-commit gate: ruff lint + ruff format check + full pytest suite.
# Run from anywhere:  .\scripts\check.ps1
# Wired as a git pre-push hook via .githooks/pre-push (see AGENTS.md "Checks").

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$py = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "venv python not found at $py -- create the project venv first."
    exit 1
}

Write-Host "== ruff check ==" -ForegroundColor Cyan
& $py -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== ruff format --check ==" -ForegroundColor Cyan
& $py -m ruff format --check .
if ($LASTEXITCODE -ne 0) {
    Write-Host "Run '.venv\Scripts\python.exe -m ruff format .' to fix, then re-commit." -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Write-Host "== pytest ==" -ForegroundColor Cyan
& $py -m pytest
exit $LASTEXITCODE
