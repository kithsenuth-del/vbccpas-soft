param(
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$TargetExe,
    [Parameter(Mandatory=$true)][string]$NewExe
)

$ErrorActionPreference = 'Stop'

function Show-UpdateError([string]$Message) {
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $Message,
            'VBCCPAS Soft updater',
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Error
        ) | Out-Null
    } catch {
        # Keep updater failure handling silent if Windows UI components are unavailable.
    }
}

try {
    if ([IO.Path]::GetFileName($TargetExe) -ne 'VBCCPAS-Soft.exe') {
        throw 'Unexpected application target.'
    }
    if (-not (Test-Path -LiteralPath $TargetExe -PathType Leaf)) {
        throw 'The installed application could not be found.'
    }
    if (-not (Test-Path -LiteralPath $NewExe -PathType Leaf)) {
        throw 'The staged update executable is missing.'
    }

    # Avoid a UAC prompt for normal user-writable installations. Elevate only
    # when the install directory does not allow the updater to stage a file.
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $targetDirectory = [IO.Path]::GetDirectoryName($TargetExe)
    $canWriteDirectory = $false
    if (-not $isAdministrator) {
        $permissionProbe = Join-Path $targetDirectory ('.vbccpas-write-test-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        try {
            $stream = [IO.File]::Open($permissionProbe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $stream.Dispose()
            Remove-Item -LiteralPath $permissionProbe -Force
            $canWriteDirectory = $true
        } catch {
            if (Test-Path -LiteralPath $permissionProbe) {
                Remove-Item -LiteralPath $permissionProbe -Force -ErrorAction SilentlyContinue
            }
        }
    }
    if (-not $isAdministrator -and -not $canWriteDirectory) {
        $arguments = @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', ('"' + $PSCommandPath + '"'),
            '-ProcessId', $ProcessId,
            '-TargetExe', ('"' + $TargetExe + '"'),
            '-NewExe', ('"' + $NewExe + '"')
        )
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments | Out-Null
        exit 0
    }

    # The desktop app closes immediately after starting this updater.
    try {
        Wait-Process -Id $ProcessId -Timeout 120 -ErrorAction Stop
    } catch {
        if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
            throw 'VBCCPAS Soft did not close in time. The existing version was kept.'
        }
    }

    $targetDirectory = [IO.Path]::GetDirectoryName($TargetExe)
    $replacement = Join-Path $targetDirectory ('.VBCCPAS-Soft-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $backup = Join-Path $targetDirectory ('VBCCPAS-Soft-previous-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.exe')

    # Copy to the install volume first, then replace atomically. Keep a rollback copy.
    Copy-Item -LiteralPath $NewExe -Destination $replacement
    if (-not (Test-Path -LiteralPath $replacement -PathType Leaf)) {
        throw 'The replacement executable could not be staged beside the installed application.'
    }
    [IO.File]::Replace($replacement, $TargetExe, $backup, $true)

    try {
        Start-Process -FilePath $TargetExe | Out-Null
    } catch {
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            Copy-Item -LiteralPath $backup -Destination $TargetExe -Force
        }
        throw ('The new version could not be started. The previous version was restored. ' + $_.Exception.Message)
    }
} catch {
    Show-UpdateError (
        'The update could not be completed. Your existing application and society database were not intentionally changed.' +
        [Environment]::NewLine + [Environment]::NewLine + $_.Exception.Message
    )
} finally {
    if ($replacement -and (Test-Path -LiteralPath $replacement)) {
        Remove-Item -LiteralPath $replacement -Force -ErrorAction SilentlyContinue
    }
}
