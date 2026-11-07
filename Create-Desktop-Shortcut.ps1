param(
    [string]$TargetExe = (Join-Path $PSScriptRoot "VBCCPAS-Soft.exe"),
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "VBCCPAS Soft.lnk")
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $TargetExe -PathType Leaf)) {
    throw "VBCCPAS-Soft.exe was not found beside this script."
}
$TargetExe = (Resolve-Path -LiteralPath $TargetExe).Path
$WorkingDirectory = Split-Path -Parent $TargetExe
$ShortcutPath = [System.IO.Path]::GetFullPath($ShortcutPath)
$ShortcutDirectory = Split-Path -Parent $ShortcutPath
if (-not (Test-Path -LiteralPath $ShortcutDirectory)) {
    New-Item -ItemType Directory -Path $ShortcutDirectory -Force | Out-Null
}
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetExe
$Shortcut.WorkingDirectory = $WorkingDirectory
$Shortcut.Description = "VBCCPAS Photographic Arts Society"
$Shortcut.IconLocation = "$TargetExe,0"
$Shortcut.WindowStyle = 1
$Shortcut.Save()
if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) {
    throw "Windows did not create the desktop shortcut."
}
Write-Host "Desktop shortcut created: $ShortcutPath"
