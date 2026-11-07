$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "Installing build tools..."
py -m pip install --upgrade pip
py -m pip install -r requirements.txt pyinstaller

Write-Host "Building the unified VBCCPAS Soft desktop application..."
py -m PyInstaller --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build" "$Root\desktop_app.spec"

Write-Host ""
Write-Host "Build complete."
Write-Host "Application: $Root\dist\VBCCPAS-Soft.exe"
Write-Host "Double-click the EXE to open the desktop interface."
