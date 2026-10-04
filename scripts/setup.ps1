# Run with: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -m venv .venv
        } else {
            & python -m venv .venv
        }
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.13+ with venv, then rerun setup.' }
    }
    & ./.venv/Scripts/python.exe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.13+ required. Rename the old .venv, install Python 3.13+, rerun setup.' }
    & ./.venv/Scripts/python.exe -m pip install .
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed. Check network/package index access and rerun setup.' }
    if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
    & ./.venv/Scripts/python.exe scripts/doctor.py --offline
    $doctorExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $doctorExit
