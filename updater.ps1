param(
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$TargetExe,
    [Parameter(Mandatory=$true)][string]$NewExe
)
$ErrorActionPreference = 'Stop'
try {
    if ([IO.Path]::GetFileName($TargetExe) -ne 'VBCCPAS-Soft.exe') { throw 'Unexpected application target.' }
    if (-not (Test-Path -LiteralPath $NewExe -PathType Leaf)) { throw 'The staged update executable is missing.' }
    try { Wait-Process -Id $ProcessId -Timeout 90 -ErrorAction Stop } catch {
        if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) { throw 'VBCCPAS Soft did not close in time. The existing version was kept.' }
    }
    Copy-Item -LiteralPath $NewExe -Destination $TargetExe -Force
    Start-Process -FilePath $TargetExe
} catch {
    Add-Type -AssemblyName PresentationFramework
    $message = 'The update could not be completed. Your existing database was not changed.' + [Environment]::NewLine + [Environment]::NewLine + $_.Exception.Message
    [System.Windows.MessageBox]::Show($message, 'VBCCPAS Soft updater', 'OK', 'Error') | Out-Null
}