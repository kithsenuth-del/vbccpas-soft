$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PyInstaller = "py"

Write-Host "Building desktop EXE..."
& $PyInstaller -m PyInstaller --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build" "$Root\desktop_app.spec"

Write-Host "Building web EXE..."
& $PyInstaller -m PyInstaller --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build" "$Root\web_server.spec"

Write-Host "Build complete. Output is in $Root\dist"
