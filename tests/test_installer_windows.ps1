param(
    [Parameter(Mandatory=$true)]
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$TempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$TestRoot = Join-Path $TempRoot ("VBCCPAS-Installer-Test-" + [Guid]::NewGuid().ToString("N"))
$InstallDir = Join-Path $TestRoot "Installed VBCCPAS Soft"

try {
    New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

    # Simulate an existing portable installation: the live database and
    # backups are already present before the installer touches this folder.
    $Database = Join-Path $InstallDir "society.db"
    [IO.File]::WriteAllText($Database, "VBCCPAS-USER-DATA-PRESERVATION-TEST")
    $BackupDir = Join-Path $InstallDir "backups"
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $Backup = Join-Path $BackupDir "preserve-this-backup.db"
    [IO.File]::WriteAllText($Backup, "VBCCPAS-BACKUP-PRESERVATION-TEST")
    $UserFile = Join-Path $InstallDir "society-notes.txt"
    [IO.File]::WriteAllText($UserFile, "VBCCPAS-USER-FILE-PRESERVATION-TEST")

    $StartMenuShortcut = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Microsoft\Windows\Start Menu\Programs\VBCCPAS Soft\VBCCPAS Soft.lnk"
    $DesktopShortcut = Join-Path ([Environment]::GetFolderPath("DesktopDirectory")) "VBCCPAS Soft.lnk"

    Write-Host "Installing VBCCPAS Soft over a simulated existing portable folder..."
    $InstallArgs = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS=desktopicon /DIR=`"$InstallDir`""
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

    # The installer must not overwrite pre-existing user data.
    if ((Get-Content -LiteralPath $Database -Raw) -ne "VBCCPAS-USER-DATA-PRESERVATION-TEST") {
        throw "The installer overwrote the existing society database."
    }
    if ((Get-Content -LiteralPath $Backup -Raw) -ne "VBCCPAS-BACKUP-PRESERVATION-TEST") {
        throw "The installer overwrote an existing user backup."
    }
    if ((Get-Content -LiteralPath $UserFile -Raw) -ne "VBCCPAS-USER-FILE-PRESERVATION-TEST") {
        throw "The installer overwrote an existing user-created file."
    }
    if (-not (Test-Path -LiteralPath $StartMenuShortcut -PathType Leaf)) {
        throw "The Start Menu shortcut was not created."
    }
    if (-not (Test-Path -LiteralPath $DesktopShortcut -PathType Leaf)) {
        throw "The requested desktop shortcut was not created."
    }

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
    if ((Get-Content -LiteralPath $Backup -Raw) -ne "VBCCPAS-BACKUP-PRESERVATION-TEST") {
        throw "The backup contents changed during uninstall."
    }
    if (-not (Test-Path -LiteralPath $UserFile -PathType Leaf) -or
        (Get-Content -LiteralPath $UserFile -Raw) -ne "VBCCPAS-USER-FILE-PRESERVATION-TEST") {
        throw "The uninstaller deleted or changed a user-created file."
    }
    if (Test-Path -LiteralPath $StartMenuShortcut) {
        throw "The uninstaller left the Start Menu shortcut behind."
    }
    if (Test-Path -LiteralPath $DesktopShortcut) {
        throw "The uninstaller left the desktop shortcut behind."
    }
    Write-Host "PASS: upgrade install, shortcuts, uninstaller, and preservation of existing society.db/backups."
}
finally {
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
