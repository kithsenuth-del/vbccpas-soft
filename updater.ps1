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
$manifestName = 'vbccpas-managed-files.json'
$protectedParts = @('society.db','backups','exports','logs','user-data','user_data','.vbccpas-data')

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

function Assert-SafeRelativePath([string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or [IO.Path]::IsPathRooted($RelativePath)) {
        throw 'The package manifest contains an absolute or empty path.'
    }
    if ($RelativePath.Contains('\') -or $RelativePath.Contains(':')) {
        throw ('The package manifest contains an unsafe path: ' + $RelativePath)
    }
    $parts = $RelativePath -split '/'
    foreach ($part in $parts) {
        if ($part -in @('', '.', '..') -or $protectedParts -contains $part.ToLowerInvariant()) {
            throw ('The package manifest contains a protected or unsafe path: ' + $RelativePath)
        }
        if ($part -match '[<>:"|?*]$' -or $part.EndsWith(' ') -or $part.EndsWith('.')) {
            throw ('The package manifest contains a Windows-invalid path: ' + $RelativePath)
        }
    }
}

function Get-AppDestination([string]$Root, [string]$RelativePath) {
    Assert-SafeRelativePath $RelativePath
    $nativeRelative = $RelativePath.Replace('/', [IO.Path]::DirectorySeparatorChar)
    $destination = [IO.Path]::GetFullPath((Join-Path $Root $nativeRelative))
    $rootPrefix = $Root.TrimEnd('\') + [IO.Path]::DirectorySeparatorChar
    if (-not $destination.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw ('The package path escapes the installation folder: ' + $RelativePath)
    }
    return $destination
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
    if (-not (Test-Path -LiteralPath (Join-Path $PackageDirectory 'VBCCPAS-Updater.exe') -PathType Leaf)) {
        throw 'The downloaded package does not contain the standalone updater.'
    }

    $manifestPath = Join-Path $PackageDirectory $manifestName
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'The downloaded package does not contain its managed-file manifest.'
    }
    $newManifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($newManifest.schema_version -ne 1 -or $null -eq $newManifest.files) {
        throw 'The update package manifest has an unsupported format.'
    }
    $newRelativeFiles = @($newManifest.files)
    if ($newRelativeFiles.Count -eq 0 -or
        $newRelativeFiles.Count -ne (@($newRelativeFiles | Select-Object -Unique)).Count) {
        throw 'The update package manifest is empty or contains duplicate file paths.'
    }
    foreach ($relative in $newRelativeFiles) { Assert-SafeRelativePath ([string]$relative) }
    if ($newRelativeFiles -notcontains 'VBCCPAS-Soft.exe' -or
        $newRelativeFiles -notcontains 'VBCCPAS-Updater.exe' -or
        $newRelativeFiles -notcontains 'updater.ps1') {
        throw 'The update package manifest omits a required application component.'
    }

    $packageFiles = @(Get-ChildItem -LiteralPath $PackageDirectory -File -Recurse |
        Where-Object { $_.Name -ne $manifestName })
    $actualRelativeFiles = @($packageFiles | ForEach-Object {
        $_.FullName.Substring($PackageDirectory.TrimEnd('\').Length).TrimStart('\').Replace('\', '/')
    })
    $difference = @(Compare-Object ($newRelativeFiles | Sort-Object) ($actualRelativeFiles | Sort-Object))
    if ($difference.Count -gt 0) {
        throw 'The package manifest does not match the files in the downloaded package.'
    }

    $targetDirectory = [IO.Path]::GetDirectoryName($TargetExe)

    # Request elevation only for protected installation folders.
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $canWriteDirectory = $false
    if (-not $isAdministrator) {
        $probe = Join-Path $targetDirectory ('.vbccpas-write-test-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        try {
            $stream = [IO.File]::Open($probe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $stream.Dispose()
            Remove-Item -LiteralPath $probe -Force
            $canWriteDirectory = $true
        } catch {
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $isAdministrator -and -not $canWriteDirectory) {
        $arguments = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', ('"' + $PSCommandPath + '"'),
            '-ProcessId', $ProcessId,
            '-UpdaterProcessId', $UpdaterProcessId,
            '-TargetExe', ('"' + $TargetExe + '"'),
            '-PackageDirectory', ('"' + $PackageDirectory + '"')
        )
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments | Out-Null
        exit 0
    }

    Wait-ForProcessExit $ProcessId 'VBCCPAS Soft'
    Wait-ForProcessExit $UpdaterProcessId 'VBCCPAS Updater'

    # Parse the previous manifest before changing anything. It allows obsolete
    # application files to be removed on later updates without touching user files.
    $oldManifestPath = Join-Path $targetDirectory $manifestName
    $oldRelativeFiles = @()
    if (Test-Path -LiteralPath $oldManifestPath -PathType Leaf) {
        $oldManifest = Get-Content -LiteralPath $oldManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($oldManifest.schema_version -ne 1 -or $null -eq $oldManifest.files) {
            throw 'The installed application manifest is invalid. No files were changed.'
        }
        $oldRelativeFiles = @($oldManifest.files)
        if ($oldRelativeFiles.Count -ne (@($oldRelativeFiles | Select-Object -Unique)).Count) {
            throw 'The installed application manifest contains duplicate paths. No files were changed.'
        }
        foreach ($relative in $oldRelativeFiles) { Assert-SafeRelativePath ([string]$relative) }
    }

    $backupRoot = Join-Path $env:LOCALAPPDATA 'VBCCPAS\UpdateBackups'
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $backupDirectory = Join-Path $backupRoot ([Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null

    $newManaged = @{}
    foreach ($relative in $newRelativeFiles) { $newManaged[[string]$relative] = $true }
    $newManaged[$manifestName] = $true

    # Remove only obsolete files that a previous VBCCPAS manifest explicitly owned.
    foreach ($relative in $oldRelativeFiles) {
        if (-not $newManaged.ContainsKey([string]$relative)) {
            $destination = Get-AppDestination $targetDirectory ([string]$relative)
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                $backupFile = Join-Path $backupDirectory ([string]$relative)
                New-Item -ItemType Directory -Path (Split-Path -Parent $backupFile) -Force | Out-Null
                Copy-Item -LiteralPath $destination -Destination $backupFile -Force
                [void]$changed.Add(@{ Destination = $destination; Backup = $backupFile; Existed = $true })
                Remove-Item -LiteralPath $destination -Force
            }
        }
    }

    # Copy every file from the new package, including the manifest itself.
    $filesToInstall = @($packageFiles) + @(Get-Item -LiteralPath $manifestPath)
    foreach ($file in $filesToInstall) {
        $relative = $file.FullName.Substring($PackageDirectory.TrimEnd('\').Length).TrimStart('\').Replace('\', '/')
        $destination = Get-AppDestination $targetDirectory $relative
        $backupFile = Join-Path $backupDirectory $relative
        $existed = Test-Path -LiteralPath $destination -PathType Leaf
        if ($existed) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $backupFile) -Force | Out-Null
            Copy-Item -LiteralPath $destination -Destination $backupFile -Force
        }
        [void]$changed.Add(@{ Destination = $destination; Backup = $backupFile; Existed = $existed })
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        $temporary = $destination + '.vbccpas-new-' + [Guid]::NewGuid().ToString('N')
        Copy-Item -LiteralPath $file.FullName -Destination $temporary
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }

    # Retain this backup set in LocalAppData for manual recovery if needed.
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
