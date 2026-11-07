$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
Write-Host "Installing build tools..."
py -m pip install --upgrade pip
py -m pip install -r requirements.txt pyinstaller
Write-Host "Building the unified VBCCPAS Soft desktop application..."
py -m PyInstaller --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build" "$Root\desktop_app.spec"
$Dist = Join-Path $Root "dist"
Copy-Item -LiteralPath (Join-Path $Root "Create-Desktop-Shortcut.ps1") -Destination $Dist -Force
Copy-Item -LiteralPath (Join-Path $Root "Setup-VBCCPAS-Shortcut.bat") -Destination $Dist -Force
Write-Host "Creating a desktop shortcut on this computer..."
& (Join-Path $Dist "Create-Desktop-Shortcut.ps1") -TargetExe (Join-Path $Dist "VBCCPAS-Soft.exe")
Write-Host ""
Write-Host "Build complete."
Write-Host "Application: $Dist\VBCCPAS-Soft.exe"
Write-Host "Desktop shortcut: VBCCPAS Soft"
Write-Host "For another computer, distribute the complete dist folder and run Setup-VBCCPAS-Shortcut.bat once."
