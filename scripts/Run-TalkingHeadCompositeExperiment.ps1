# Quick talking-head background composite experiment (repo root).
# Usage:
#   .\scripts\Run-TalkingHeadCompositeExperiment.ps1 -DateId 18040529 -Segment 3 -Character lewis
#   .\scripts\Run-TalkingHeadCompositeExperiment.ps1 -DateId 18040529 -Segment 3 -Method ben

param(
    [Parameter(Mandatory = $true)][string]$DateId,
    [Parameter(Mandatory = $true)][int]$Segment,
    [string]$Character = "lewis",
    [ValidateSet("colorkey", "ben")]
    [string]$Method = "colorkey"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing venv Python: $py" }

$env:PYTHONUNBUFFERED = "1"
$argsList = @(
    "scripts\composite_talking_head_background.py",
    $DateId,
    "$Segment",
    "--method", $Method
)
if ($Character) { $argsList += @("--character", $Character) }

Write-Host "Running: $py $($argsList -join ' ')"
& $py @argsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exp = Join-Path $Root "movie-images\$DateId\experiments"
Write-Host ""
Write-Host "Outputs under: $exp"
Get-ChildItem $exp -Filter ("{0:D2}_*" -f $Segment) | Sort-Object LastWriteTime -Descending | Select-Object Name, Length, LastWriteTime
