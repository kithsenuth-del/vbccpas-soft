param(
    [int]$ProcessId = 0,
    [Parameter(Mandatory=$true)][string]$TargetExe,
    [string]$PackageDirectory = "",
    [int]$UpdaterProcessId = 0,
    [switch]$RepairMode,
    [switch]$RecoverOnly,
    [switch]$RestartAfterRecovery,
    [switch]$CheckPermissionsOnly,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$changed = New-Object System.Collections.ArrayList
$backupDirectory = $null
$stageRoot = $null
$started = $null
$recoveryCompleted = $false
$logPath = Join-Path $env:LOCALAPPDATA "VBCCPAS\update.log"
$journalPath = Join-Path $env:LOCALAPPDATA "VBCCPAS\update-recovery.json"
$recoveryScriptPath = Join-Path $env:LOCALAPPDATA "VBCCPAS\Recovery\updater-recovery.ps1"
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

function Wait-ForProcessExit([int]$Id, [string]$Description, [string]$ExpectedProcessName = '') {
    if ($Id -le 0 -or $Id -eq $PID) { return }
    $process = Get-Process -Id $Id -ErrorAction SilentlyContinue
    if (-not $process) { return }
    if ($ExpectedProcessName -and
        -not [string]::Equals($process.ProcessName, $ExpectedProcessName, [StringComparison]::OrdinalIgnoreCase)) {
        return
    }
    try {
        Wait-Process -Id $Id -Timeout 180 -ErrorAction Stop
    } catch {
        if (Get-Process -Id $Id -ErrorAction SilentlyContinue) {
            throw ($Description + ' did not close in time. No application files were replaced.')
        }
    }
}

function Test-CanWriteDirectory([string]$Directory) {
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { return $false }
    $probe = Join-Path $Directory ('.vbccpas-write-test-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $stream = [IO.File]::Open($probe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $stream.Dispose()
        Remove-Item -LiteralPath $probe -Force
        return $true
    } catch {
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        return $false
    }
}

function Get-TargetApplicationProcesses([string]$ExpectedPath) {
    $expected = [IO.Path]::GetFullPath($ExpectedPath)
    try {
        $items = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='VBCCPAS-Soft.exe'" -ErrorAction Stop)
    } catch {
        throw ('Could not safely inspect running VBCCPAS Soft processes: ' + $_.Exception.Message)
    }
    $foundProcesses = @()
    foreach ($item in $items) {
        if ([string]::IsNullOrWhiteSpace([string]$item.ExecutablePath)) {
            throw ('A running VBCCPAS Soft process (PID ' + $item.ProcessId + ') could not be path-verified. Close it manually and retry.')
        }
        $actual = [IO.Path]::GetFullPath([string]$item.ExecutablePath)
        if ([string]::Equals($actual, $expected, [StringComparison]::OrdinalIgnoreCase)) {
            $process = Get-Process -Id ([int]$item.ProcessId) -ErrorAction SilentlyContinue
            if ($process) { $foundProcesses += $process }
        }
    }
    return @($foundProcesses)
}

function Stop-TargetApplication([string]$ExpectedPath) {
    # The updater must never overwrite a running executable. Ask every matching
    # instance to close normally, then wait. A non-responsive app is left alone.
    $deadline = (Get-Date).AddSeconds(90)
    do {
        $running = @(Get-TargetApplicationProcesses $ExpectedPath)
        if ($running.Count -eq 0) { return }
        foreach ($process in $running) {
            try { [void]$process.CloseMainWindow() } catch { }
        }
        foreach ($process in $running) {
            try { [void]$process.WaitForExit(5000) } catch { }
        }
        if ((Get-Date) -ge $deadline) {
            $remaining = @(Get-TargetApplicationProcesses $ExpectedPath)
            if ($remaining.Count -gt 0) {
                throw 'VBCCPAS Soft is still running and did not close after a normal close request. Save your work, close the application manually, and run the updater again. No application files were replaced.'
            }
        }
        if (@(Get-TargetApplicationProcesses $ExpectedPath).Count -gt 0) {
            Start-Sleep -Seconds 1
        }
    } while ((Get-Date) -lt $deadline)
}

function Wait-ForApplicationHealth($StartedProcess, [string]$ExpectedPath) {
    $deadline = (Get-Date).AddSeconds(45)
    do {
        $StartedProcess.Refresh()
        if ($StartedProcess.HasExited) {
            throw ('The updated application exited during startup (exit code ' + $StartedProcess.ExitCode + ').')
        }
        $window = $StartedProcess.MainWindowHandle
        if ($window -ne [IntPtr]::Zero -and $StartedProcess.Responding) {
            $actualPath = $null
            try {
                $item = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId=" + $StartedProcess.Id) -ErrorAction Stop
                $actualPath = [IO.Path]::GetFullPath([string]$item.ExecutablePath)
            } catch {
                throw 'The updated application process could not be verified after restart.'
            }
            if (-not [string]::Equals($actualPath, $ExpectedPath, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'The restarted application is not running from the selected installation folder.'
            }
            return
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    throw 'The updated application did not create a responsive main window within 45 seconds.'
}

function Show-UpdateSuccess([string]$Message) {
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $Message, 'VBCCPAS Soft Updater',
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Information
        ) | Out-Null
    } catch { }
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
        if ($part -match '[<>:"|?*]' -or $part.EndsWith(' ') -or $part.EndsWith('.')) {
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
function Save-RecoveryJournal($Journal) {
    $temporaryJournal = $journalPath + '.tmp'
    $json = $Journal | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($temporaryJournal, $json, (New-Object System.Text.UTF8Encoding($false)))
    if (Test-Path -LiteralPath $journalPath -PathType Leaf) {
        [IO.File]::Replace($temporaryJournal, $journalPath, $null)
    } else {
        Move-Item -LiteralPath $temporaryJournal -Destination $journalPath -Force
    }
}

function Restore-RecoveryJournal([string]$ExpectedTarget) {
    if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf)) { return $false }
    $saved = Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not [string]::Equals([IO.Path]::GetFullPath([string]$saved.target), $ExpectedTarget, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The recovery journal does not match this application installation. Manual review is required.'
    }
    if ($saved.state -eq 'verified') {
        Remove-Item -LiteralPath $journalPath -Force
        if (Test-Path -LiteralPath $logPath) {
            Add-Content -LiteralPath $logPath -Value ("[{0}] Verified update journal was safely finalised." -f (Get-Date -Format o))
        }
        return $false
    }
    if ($saved.state -ne 'installing') {
        throw 'The recovery journal has an unknown state. Manual review is required.'
    }
    $root = [IO.Path]::GetDirectoryName($ExpectedTarget)
    $entries = @($saved.entries)
    for ($index = $entries.Count - 1; $index -ge 0; $index--) {
        $entry = $entries[$index]
        $destination = Get-AppDestination $root ([string]$entry.relative)
        if ($entry.temporary) {
            $temporaryPath = [IO.Path]::GetFullPath([string]$entry.temporary)
            $destinationParent = [IO.Path]::GetDirectoryName($destination).TrimEnd('\') + [IO.Path]::DirectorySeparatorChar
            if ($temporaryPath.StartsWith($destinationParent, [StringComparison]::OrdinalIgnoreCase) -and
                (Test-Path -LiteralPath $temporaryPath -PathType Leaf)) {
                Remove-Item -LiteralPath $temporaryPath -Force
            }
        }
        if ([bool]$entry.existed) {
            $backup = [IO.Path]::GetFullPath([string]$entry.backup)
            $backupRoot = [IO.Path]::GetFullPath([string]$saved.backup).TrimEnd('\') + [IO.Path]::DirectorySeparatorChar
            if (-not $backup.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase) -or
                -not (Test-Path -LiteralPath $backup -PathType Leaf)) {
                throw ('A required recovery copy is missing: ' + $entry.relative)
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $backup -Destination $destination -Force
        } elseif (Test-Path -LiteralPath $destination -PathType Leaf) {
            Remove-Item -LiteralPath $destination -Force
        }
        Get-ChildItem -LiteralPath (Split-Path -Parent $destination) -Filter ((Split-Path -Leaf $destination) + '.vbccpas-new-*') -File -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $journalPath -Force
    if (Test-Path -LiteralPath $logPath) {
        Add-Content -LiteralPath $logPath -Value ("[{0}] Interrupted installation was rolled back from its recovery journal." -f (Get-Date -Format o))
    }
    return $true
}

try {
    $TargetExe = [IO.Path]::GetFullPath($TargetExe)
    $targetDirectory = [IO.Path]::GetDirectoryName($TargetExe)
    if ([IO.Path]::GetFileName($TargetExe) -ne 'VBCCPAS-Soft.exe') {
        throw 'The selected application target is not VBCCPAS-Soft.exe.'
    }
    if ($CheckPermissionsOnly) {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        $isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        $canWriteTarget = Test-CanWriteDirectory $targetDirectory
        [pscustomobject]@{
            target_directory = $targetDirectory
            is_administrator = $isAdministrator
            can_write_target = $canWriteTarget
            requires_elevation = (-not $isAdministrator -and -not $canWriteTarget)
        } | ConvertTo-Json -Compress
        if (-not $canWriteTarget) { exit 2 }
        exit 0
    }
    if ($RecoverOnly) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
        if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf)) { exit 0 }
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        $isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        $canWriteTarget = Test-CanWriteDirectory $targetDirectory
        if (-not $isAdministrator -and -not $canWriteTarget) {
            $arguments = @(
                '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', ('"' + $PSCommandPath + '"'),
                '-TargetExe', ('"' + $TargetExe + '"'),
                '-RecoverOnly', '-Quiet'
            )
            if ($RestartAfterRecovery) { $arguments += '-RestartAfterRecovery' }
            $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList $arguments
            if ($elevated.ExitCode -ne 0) {
                throw ('Elevated recovery failed with exit code ' + $elevated.ExitCode + '.')
            }
            exit 0
        }
        Stop-TargetApplication $TargetExe
        [void](Restore-RecoveryJournal $TargetExe)
        $recoveryCompleted = $true
        if ($RestartAfterRecovery -and (Test-Path -LiteralPath $TargetExe -PathType Leaf)) {
            $recoveredProcess = Start-Process -FilePath $TargetExe -PassThru
            Wait-ForApplicationHealth $recoveredProcess $TargetExe
            Add-Content -LiteralPath $logPath -Value ("[{0}] Recovered application restarted and its responsive window verified (PID {1})." -f (Get-Date -Format o), $recoveredProcess.Id)
        }
        exit 0
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
    Add-Content -LiteralPath $logPath -Value ("[{0}] Standalone updater started. Target: {1}" -f (Get-Date -Format o), $TargetExe)
    $PackageDirectory = [IO.Path]::GetFullPath($PackageDirectory)
    if (-not (Test-Path -LiteralPath $TargetExe -PathType Leaf) -and -not $RepairMode) {
        throw 'The installed VBCCPAS application could not be found. Run VBCCPAS-Updater.exe --repair to restore it.'
    }
    if (-not (Test-Path -LiteralPath $targetDirectory -PathType Container)) { throw 'The installation folder does not exist.' }
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
        $newRelativeFiles -notcontains 'updater.ps1' -or
        $newRelativeFiles -notcontains 'Recover-VBCCPAS.bat') {
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

    # Request elevation only for protected installation folders.
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $canWriteDirectory = Test-CanWriteDirectory $targetDirectory
    if (-not $isAdministrator -and -not $canWriteDirectory) {
        $arguments = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', ('"' + $PSCommandPath + '"'),
            '-ProcessId', $ProcessId,
            '-UpdaterProcessId', $UpdaterProcessId,
            '-TargetExe', ('"' + $TargetExe + '"'),
            '-PackageDirectory', ('"' + $PackageDirectory + '"')
        )
        if ($RepairMode) { $arguments += '-RepairMode' }
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments | Out-Null
        exit 0
    }

    Add-Content -LiteralPath $logPath -Value ("[{0}] Waiting for the installed application to close normally." -f (Get-Date -Format o))
    Stop-TargetApplication $TargetExe
    Wait-ForProcessExit $UpdaterProcessId 'VBCCPAS Updater' 'VBCCPAS-Updater'
    Add-Content -LiteralPath $logPath -Value ("[{0}] Confirmed no matching application process remains." -f (Get-Date -Format o))

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

    New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path -Parent $recoveryScriptPath) -Force | Out-Null
    $recoveryTemp = $recoveryScriptPath + '.tmp-' + [Guid]::NewGuid().ToString('N')
    Copy-Item -LiteralPath $PSCommandPath -Destination $recoveryTemp -Force
    Move-Item -LiteralPath $recoveryTemp -Destination $recoveryScriptPath -Force
    Add-Content -LiteralPath $logPath -Value ("[{0}] Update started. Target: {1}; Repair: {2}" -f (Get-Date -Format o), $TargetExe, [bool]$RepairMode)
    $backupRoot = Join-Path $env:LOCALAPPDATA 'VBCCPAS\UpdateBackups'
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $backupDirectory = Join-Path $backupRoot ([Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null

    $journal = [ordered]@{ state='installing'; target=$TargetExe; backup=$backupDirectory; started=(Get-Date -Format o); entries=@() }
    Save-RecoveryJournal $journal
    Add-Content -LiteralPath $logPath -Value ("[{0}] Recovery journal created: {1}" -f (Get-Date -Format o), $backupDirectory)
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
                $journal.entries += [pscustomobject]@{ relative=[string]$relative; destination=$destination; backup=$backupFile; existed=$true }
                Save-RecoveryJournal $journal
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
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        $temporary = $destination + '.vbccpas-new-' + [Guid]::NewGuid().ToString('N')
        $journal.entries += [pscustomobject]@{ relative=$relative; destination=$destination; backup=$backupFile; existed=$existed; temporary=$temporary }
        Save-RecoveryJournal $journal
        Copy-Item -LiteralPath $file.FullName -Destination $temporary
        Move-Item -LiteralPath $temporary -Destination $destination -Force
        $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        $installedHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
        if ($sourceHash -ne $installedHash) {
            throw ('Installed file verification failed: ' + $relative)
        }
    }

    # Verify the replacement can launch before declaring success.
    Add-Content -LiteralPath $logPath -Value ("[{0}] Files replaced; checking application startup." -f (Get-Date -Format o))
    $started = Start-Process -FilePath $TargetExe -PassThru
    Wait-ForApplicationHealth $started $TargetExe
    $journal.state = 'verified'
    $journal.verified = Get-Date -Format o
    Save-RecoveryJournal $journal
    Add-Content -LiteralPath $logPath -Value ("[{0}] Update completed successfully. Responsive application window verified (PID {1}). Backup retained at {2}" -f (Get-Date -Format o), $started.Id, $backupDirectory)
    $stageRoot = Split-Path -Parent $PackageDirectory
    if (Test-Path -LiteralPath $journalPath) { Remove-Item -LiteralPath $journalPath -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not $Quiet) {
        Show-UpdateSuccess ('VBCCPAS Soft was updated successfully and restarted.' + [Environment]::NewLine + 'Your society database and personal data were preserved.' + [Environment]::NewLine + 'Rollback copy: ' + $backupDirectory)
    }
 } catch {
    $failureMessage = $_.Exception.Message
    if ($started -and (Get-Process -Id $started.Id -ErrorAction SilentlyContinue)) {
        try { Stop-Process -Id $started.Id -Force -ErrorAction Stop } catch { }
    }
    $rollbackErrors = @()
    try {
        if (-not $RecoverOnly -and (Test-Path -LiteralPath $journalPath -PathType Leaf)) {
            [void](Restore-RecoveryJournal $TargetExe)
        }
    } catch {
        $rollbackErrors += ('Recovery journal: ' + $_.Exception.Message)
    }
    if ($logPath) {
        try {
            New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
            Add-Content -LiteralPath $logPath -Value ("[{0}] Update failed: {1}" -f (Get-Date -Format o), $failureMessage)
        } catch { }
    }
    if ($RecoverOnly -and $recoveryCompleted) {
        $message = 'The previous application files were restored, but the recovered application did not pass its restart check.'
    } elseif ($RecoverOnly) {
        $message = 'Interrupted-update recovery could not be completed. The recovery journal has been retained for another attempt.'
    } else {
        $message = 'The complete update failed. The updater attempted to restore the previous application files.'
    }
    if ($rollbackErrors.Count -gt 0) {
        $message += [Environment]::NewLine + 'Some rollback operations need attention:' +
                    [Environment]::NewLine + ($rollbackErrors -join [Environment]::NewLine)
    } elseif ($backupDirectory) {
        $message += [Environment]::NewLine + 'Rollback completed successfully. Recovery copy: ' + $backupDirectory
    } else {
        $message += [Environment]::NewLine + 'No installation files were changed.'
    }

    $message += [Environment]::NewLine + 'Update log: ' + $logPath
    $message += [Environment]::NewLine + [Environment]::NewLine + $failureMessage
    if (-not $Quiet) { Show-UpdateError $message }
    if ($stageRoot -and (Test-Path -LiteralPath $stageRoot)) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 1
}
