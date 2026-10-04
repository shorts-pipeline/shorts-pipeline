#Requires -Version 5.1
<#
.SYNOPSIS
  Start the Lewis & Clark Pipeline UI (local web server for run-daily).

.EXAMPLE
  .\scripts\Start-LewisClarkUI.ps1

.EXAMPLE
  .\scripts\Start-LewisClarkUI.ps1 -NoReload
#>
param(
    [switch] $NoReload
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing venv Python: $Python`nCreate the venv first (see AGENTS.md)."
}

$Options = Join-Path $RepoRoot "pipeline_ui\options.json"
$Example = Join-Path $RepoRoot "pipeline_ui\options.json.example"
if (-not (Test-Path -LiteralPath $Options)) {
    if (Test-Path -LiteralPath $Example) {
        Copy-Item -LiteralPath $Example -Destination $Options
        Write-Host "Created pipeline_ui\options.json from options.json.example"
    } else {
        Write-Warning "Missing pipeline_ui\options.json - copy from options.json.example if the UI form is empty."
    }
}

$Server = Join-Path $RepoRoot "pipeline_ui\server.py"
if (-not (Test-Path -LiteralPath $Server)) {
    throw "Missing server: $Server"
}

$Port = if ($env:PIPELINE_UI_PORT) { $env:PIPELINE_UI_PORT } else { "8765" }
$args = @($Server)
if (-not $NoReload) {
    $args += "--reload"
}

Write-Host "Pipeline UI - open http://127.0.0.1:${Port}/ (Ctrl+C to stop)"
& $Python @args
