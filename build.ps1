# Builds the standalone Windows application into dist\SchoolPagingSystem\
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
.\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed - build aborted." }
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean school_paging_system.spec
Compress-Archive -Path "dist\SchoolPagingSystem\*" -DestinationPath "dist\SchoolPagingSystem-win64.zip" -Force
Write-Host "Done: dist\SchoolPagingSystem\SchoolPagingSystem.exe"
