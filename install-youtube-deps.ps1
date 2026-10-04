# Install YouTube upload dependencies into the project venv.
# Run from project root: .\install-youtube-deps.ps1
# Then activate and use as usual: .\.venv\Scripts\Activate.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

# Use python -m pip so we don't rely on pip.exe (which can point to wrong path if project was renamed)
$pythonExe = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Error "Virtual environment not found. Create it first: python -m venv .venv"
    exit 1
}

Write-Host "Using: $pythonExe"
Write-Host "pip: $(& $pythonExe -m pip --version)"
Write-Host "Installing YouTube dependencies into .venv..."
$reqPath = Join-Path $root "requirements-youtube.txt"
$out = & $pythonExe -m pip install -r $reqPath 2>&1
$exitCode = $LASTEXITCODE
$out | ForEach-Object { Write-Host $_ }
if ($exitCode -ne 0) {
    Write-Host "pip install failed with exit code $exitCode"
    Write-Host "If you see 'Unable to create process' or a wrong path, recreate the venv: Remove-Item -Recurse -Force .venv; python -m venv .venv"
    exit $exitCode
}

Write-Host ""
Write-Host "Done. To activate the venv and use pip/python as usual:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "Then run login-only:"
Write-Host "  python youtube_upload.py --login-only"
