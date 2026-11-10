param(
    [Parameter(Mandatory=$true)]
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$TestRoot = Join-Path $env:RUNNER_TEMP ("VBCCPAS-Installer-Test-" + [Guid]::NewGuid().ToString("N"))
$InstallDir = Join-Path $TestRoot "Installed VBCCPAS Soft"

try {
    New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
    Write-Host "Installing VBCCPAS Soft into an isolated test directory..."
    $InstallArgs = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=`"$InstallDir`""
    $Install = Start-Process -FilePath $InstallerPath -ArgumentList $InstallArgs -Wait -PassThru
    if ($Install.ExitCode -ne 0) { throw "Installer exited with code $($Install.ExitCode)." }

    $AppExe = Join-Path $InstallDir "VBCCPAS-Soft.exe"
    $UpdaterExe = Join-Path $InstallDir "VBCCPAS-Updater.exe"
    $Uninstaller = Join-Path $InstallDir "unins000.exe"
    foreach ($Required in @($AppExe, $UpdaterExe, $Uninstaller, (Join-Path $InstallDir "updater.ps1"))) {
        if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
            throw "Installation is incomplete; missing $Required"
        }
    }

    # The application stores its SQLite database beside the executable.
    # Confirm uninstall removes application files but never society records.
    $Database = Join-Path $InstallDir "society.db"
    [IO.File]::WriteAllText($Database, "VBCCPAS-USER-DATA-PRESERVATION-TEST")
    $BackupDir = Join-Path $InstallDir "backups"
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $Backup = Join-Path $BackupDir "preserve-this-backup.db"
    [IO.File]::WriteAllText($Backup, "VBCCPAS-BACKUP-PRESERVATION-TEST")

    Write-Host "Running the generated uninstaller..."
    $Uninstall = Start-Process -FilePath $Uninstaller -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART" -Wait -PassThru
    if ($Uninstall.ExitCode -ne 0) { throw "Uninstaller exited with code $($Uninstall.ExitCode)." }

    if (Test-Path -LiteralPath $AppExe) {
        throw "Uninstaller left the application executable behind."
    }
    if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) {
        throw "Uninstaller deleted society.db. This is a data-loss failure."
    }
    if (-not (Test-Path -LiteralPath $Backup -PathType Leaf)) {
        throw "Uninstaller deleted a user backup. This is a data-loss failure."
    }
    if ((Get-Content -LiteralPath $Database -Raw) -ne "VBCCPAS-USER-DATA-PRESERVATION-TEST") {
        throw "The society database contents changed during uninstall."
    }
    Write-Host "PASS: install, uninstaller, and preservation of society.db/backups."
}
finally {
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
