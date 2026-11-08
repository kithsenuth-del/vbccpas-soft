param(
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$TargetExe,
    [Parameter(Mandatory=$true)][string]$PackageDirectory,
    [int]$UpdaterProcessId = 0
)

$ErrorActionPreference = 'Stop'
$changed = New-Object System.Collections.ArrayList
$backupDirectory = $null
$stageRoot = $null

function Show-UpdateError([string]$Message) {
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $Message, 'VBCCPAS Soft Updater',
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Error
        ) | Out-Null
    } catch { }
}

function Wait-ForProcessExit([int]$Id, [string]$Description) {
    if ($Id -le 0 -or $Id -eq $PID) { return }
    try {
        Wait-Process -Id $Id -Timeout 180 -ErrorAction Stop
    } catch {
        if (Get-Process -Id $Id -ErrorAction SilentlyContinue) {
            throw ($Description + ' did not close in time. No application files were replaced.')
        }
    }
}

try {
    $TargetExe = [IO.Path]::GetFullPath($TargetExe)
    $PackageDirectory = [IO.Path]::GetFullPath($PackageDirectory)
    if ([IO.Path]::GetFileName($TargetExe) -ne 'VBCCPAS-Soft.exe') {
        throw 'The selected application target is not VBCCPAS-Soft.exe.'
    }
    if (-not (Test-Path -LiteralPath $TargetExe -PathType Leaf)) {
        throw 'The installed VBCCPAS application could not be found.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $PackageDirectory 'VBCCPAS-Soft.exe') -PathType Leaf)) {
        throw 'The downloaded package does not contain VBCCPAS-Soft.exe.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $PackageDirectory 'updater.ps1') -PathType Leaf)) {
        throw 'The downloaded package is missing its installation helper.'
    }

    $targetDirectory = [IO.Path]::GetDirectoryName($TargetExe)
    Wait-ForProcessExit $ProcessId 'VBCCPAS Soft'
    Wait-ForProcessExit $UpdaterProcessId 'VBCCPAS Updater'

    # Keep rollback files outside the installation folder and never include user data.
    $backupRoot = Join-Path $env:LOCALAPPDATA 'VBCCPAS\UpdateBackups'
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $backupDirectory = Join-Path $backupRoot ([Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null

    $files = @(Get-ChildItem -LiteralPath $PackageDirectory -File -Recurse)
    if ($files.Count -eq 0) { throw 'The update package contains no application files.' }

    foreach ($file in $files) {
        $relative = $file.FullName.Substring($PackageDirectory.TrimEnd('\').Length).TrimStart('\')
        $parts = $relative -split '[\\/]'
        if ($parts -contains '..' -or $parts -contains '.' -or $parts -contains 'society.db') {
            throw ('Unsafe or protected path in update package: ' + $relative)
        }
        $protected = @('backups','exports','logs','user-data','user_data','.vbccpas-data')
        foreach ($part in $parts) {
            if ($protected -contains $part.ToLowerInvariant()) {
                throw ('The update package attempts to replace user data: ' + $relative)
            }
        }

        $destination = [IO.Path]::GetFullPath((Join-Path $targetDirectory $relative))
        $rootPrefix = $targetDirectory.TrimEnd('\') + [IO.Path]::DirectorySeparatorChar
        if (-not $destination.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw ('The update package contains a path outside the application folder: ' + $relative)
        }
        $backupFile = Join-Path $backupDirectory $relative
        $existed = Test-Path -LiteralPath $destination -PathType Leaf
        if ($existed) {
            $backupParent = Split-Path -Parent $backupFile
            New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
            Copy-Item -LiteralPath $destination -Destination $backupFile -Force
        }
        [void]$changed.Add(@{ Destination = $destination; Backup = $backupFile; Existed = $existed })

        $destinationParent = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
        $temporary = $destination + '.vbccpas-new-' + [Guid]::NewGuid().ToString('N')
        Copy-Item -LiteralPath $file.FullName -Destination $temporary
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }

    # Keep the last successful rollback set under LocalAppData for manual recovery.
    Start-Process -FilePath $TargetExe | Out-Null
    $stageRoot = Split-Path -Parent $PackageDirectory
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
} catch {
    $rollbackErrors = @()
    for ($index = $changed.Count - 1; $index -ge 0; $index--) {
        $item = $changed[$index]
        try {
            if ($item.Existed -and (Test-Path -LiteralPath $item.Backup -PathType Leaf)) {
                $parent = Split-Path -Parent $item.Destination
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
                Copy-Item -LiteralPath $item.Backup -Destination $item.Destination -Force
            } elseif (-not $item.Existed -and (Test-Path -LiteralPath $item.Destination)) {
                Remove-Item -LiteralPath $item.Destination -Force
            }
        } catch {
            $rollbackErrors += ($item.Destination + ': ' + $_.Exception.Message)
        }
    }
    $message = 'The complete update failed. The updater attempted to restore the previous application files.'
    if ($rollbackErrors.Count -gt 0) {
        $message += [Environment]::NewLine + 'Some rollback operations need attention:' +
                    [Environment]::NewLine + ($rollbackErrors -join [Environment]::NewLine)
    } elseif ($backupDirectory) {
        $message += [Environment]::NewLine + 'Rollback copy: ' + $backupDirectory
    }
    $message += [Environment]::NewLine + [Environment]::NewLine + $_.Exception.Message
    Show-UpdateError $message
    if ($stageRoot -and (Test-Path -LiteralPath $stageRoot)) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
