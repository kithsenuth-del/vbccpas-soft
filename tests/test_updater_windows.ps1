$ErrorActionPreference = 'Stop'
$scriptPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\updater.ps1'))
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('vbccpas-updater-e2e-' + [Guid]::NewGuid().ToString('N'))
$env:LOCALAPPDATA = Join-Path $tempRoot 'local'
$permissionDirectory = Join-Path $tempRoot 'permission-check'
$installDirectory = Join-Path $tempRoot 'installation'
$payloadDirectory = Join-Path $tempRoot 'stage\payload'
$targetExe = Join-Path $installDirectory 'VBCCPAS-Soft.exe'
$oldProcess = $null
$newProcess = $null
$originalAcl = $null

function New-TestApplication([string]$OutputPath, [string]$ClassName, [string]$WindowTitle) {
    $source = @"
using System;
using System.Windows.Forms;
public static class $ClassName {
    [STAThread]
    public static void Main() {
        Application.EnableVisualStyles();
        using (Form form = new Form()) {
            form.Text = "$WindowTitle";
            form.Width = 420;
            form.Height = 220;
            form.StartPosition = FormStartPosition.CenterScreen;
            Application.Run(form);
        }
    }
}
"@
    Add-Type -TypeDefinition $source -Language CSharp -OutputAssembly $OutputPath -OutputType WindowsApplication -ReferencedAssemblies @('System.Windows.Forms.dll', 'System.Drawing.dll')
    if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "Test application executable was not created: $OutputPath"
    }
}

function Invoke-PermissionProbe([string]$Directory, [string]$Executable) {
    $output = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath -TargetExe $Executable -CheckPermissionsOnly 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    $jsonLine = @($output -split [Environment]::NewLine | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    if ($jsonLine.Count -eq 0) {
        throw "Permission probe did not return its JSON result. Exit code: $exitCode. Output: $output"
    }
    return [pscustomobject]@{
        exit_code = $exitCode
        result = ($jsonLine[0] | ConvertFrom-Json)
        output = $output
    }
}

try {
    New-Item -ItemType Directory -Path $permissionDirectory, $installDirectory, $payloadDirectory -Force | Out-Null
    $permissionTarget = Join-Path $permissionDirectory 'VBCCPAS-Soft.exe'
    Set-Content -LiteralPath $permissionTarget -Value 'permission probe only' -Encoding ASCII

    # Writable user-owned folder: the same probe used by the updater must pass.
    $writable = Invoke-PermissionProbe $permissionDirectory $permissionTarget
    if ($writable.exit_code -ne 0 -or -not $writable.result.can_write_target) {
        throw "Writable-folder permission test failed: $($writable.output)"
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $expectedAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ([bool]$writable.result.is_administrator -ne [bool]$expectedAdmin) {
        throw 'Updater administrator-token detection does not match the current Windows token.'
    }

    # Explicitly deny write access to this disposable folder. This exercises the
    # standard-user/protected-folder branch without invoking an interactive UAC prompt.
    $originalAcl = Get-Acl -LiteralPath $permissionDirectory
    $denyRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $identity.User,
        [System.Security.AccessControl.FileSystemRights]::Write,
        ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit),
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Deny
    )
    $deniedAcl = Get-Acl -LiteralPath $permissionDirectory
    $deniedAcl.AddAccessRule($denyRule)
    Set-Acl -LiteralPath $permissionDirectory -AclObject $deniedAcl
    $denied = Invoke-PermissionProbe $permissionDirectory $permissionTarget
    if ($denied.exit_code -ne 2 -or $denied.result.can_write_target) {
        throw "Denied-folder permission test failed; updater did not detect the write restriction. Output: $($denied.output)"
    }
    $expectedElevation = -not [bool]$denied.result.is_administrator
    if ([bool]$denied.result.requires_elevation -ne $expectedElevation) {
        throw 'Updater elevation decision does not match the current token and denied-folder access.'
    }
    Set-Acl -LiteralPath $permissionDirectory -AclObject $originalAcl
    $originalAcl = $null

    # Create a real Windows GUI test executable so the updater can request a
    # normal WM_CLOSE, wait for process exit, replace it, and verify a restart.
    $oldExe = Join-Path $tempRoot 'old-app.exe'
    $newExe = Join-Path $payloadDirectory 'VBCCPAS-Soft.exe'
    New-TestApplication $oldExe 'VBCCPASOldUpdaterSimulation' 'VBCCPAS updater simulation - old'
    New-TestApplication $newExe 'VBCCPASNewUpdaterSimulation' 'VBCCPAS updater simulation - new'
    Copy-Item -LiteralPath $oldExe -Destination $targetExe -Force

    $databasePath = Join-Path $installDirectory 'society.db'
    $databaseContent = 'existing society records must remain byte-for-byte unchanged'
    [IO.File]::WriteAllText($databasePath, $databaseContent)
    $databaseHash = (Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash
    New-Item -ItemType Directory -Path (Join-Path $installDirectory 'backups'), (Join-Path $installDirectory 'user-data') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $installDirectory 'backups\keep.txt') -Value 'backup marker'
    Set-Content -LiteralPath (Join-Path $installDirectory 'user-data\keep.txt') -Value 'user data marker'
    Set-Content -LiteralPath (Join-Path $installDirectory 'obsolete-managed-file.txt') -Value 'old managed file'
    Set-Content -LiteralPath (Join-Path $installDirectory 'VBCCPAS-Updater.exe') -Value 'old updater'
    Set-Content -LiteralPath (Join-Path $installDirectory 'updater.ps1') -Value 'old installer'
    Set-Content -LiteralPath (Join-Path $installDirectory 'Recover-VBCCPAS.bat') -Value 'old recovery'
    $oldManifest = @{
        schema_version = 1
        files = @(
            'VBCCPAS-Soft.exe',
            'VBCCPAS-Updater.exe',
            'updater.ps1',
            'Recover-VBCCPAS.bat',
            'obsolete-managed-file.txt'
        )
    }
    $oldManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $installDirectory 'vbccpas-managed-files.json') -Encoding UTF8

    # Build a complete package using the actual updater/recovery scripts.
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\updater.ps1') -Destination (Join-Path $payloadDirectory 'updater.ps1')
    Set-Content -LiteralPath (Join-Path $payloadDirectory 'VBCCPAS-Updater.exe') -Value 'test placeholder; never executed by the install script'
    Set-Content -LiteralPath (Join-Path $payloadDirectory 'Recover-VBCCPAS.bat') -Value 'test recovery launcher'
    New-Item -ItemType Directory -Path (Join-Path $payloadDirectory 'assets') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $payloadDirectory 'assets\version.txt') -Value 'new-package'
    $newFiles = @(
        'VBCCPAS-Soft.exe',
        'VBCCPAS-Updater.exe',
        'updater.ps1',
        'Recover-VBCCPAS.bat',
        'assets/version.txt'
    )
    @{ schema_version = 1; files = $newFiles } |
        ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath (Join-Path $payloadDirectory 'vbccpas-managed-files.json') -Encoding UTF8

    # Package, copy (simulated download), hash-verify and extract the ZIP exactly
    # as the standalone updater does before handing it to updater.ps1.
    $downloadDirectory = Join-Path $tempRoot 'download'
    $verifiedPayload = Join-Path $downloadDirectory 'verified\payload'
    New-Item -ItemType Directory -Path $downloadDirectory, $verifiedPayload -Force | Out-Null
    $sourceZip = Join-Path $tempRoot 'source-package.zip'
    $downloadedZip = Join-Path $downloadDirectory 'VBCCPAS-Soft-Windows.zip'
    Compress-Archive -Path (Join-Path $payloadDirectory '*') -DestinationPath $sourceZip -Force
    $expectedPackageHash = (Get-FileHash -LiteralPath $sourceZip -Algorithm SHA256).Hash
    Copy-Item -LiteralPath $sourceZip -Destination $downloadedZip -Force
    $downloadedPackageHash = (Get-FileHash -LiteralPath $downloadedZip -Algorithm SHA256).Hash
    if ($downloadedPackageHash -ne $expectedPackageHash) {
        throw 'Downloaded Windows package failed SHA-256 integrity verification.'
    }
    Expand-Archive -LiteralPath $downloadedZip -DestinationPath $verifiedPayload -Force
    $verifiedManifest = Get-Content -LiteralPath (Join-Path $verifiedPayload 'vbccpas-managed-files.json') -Raw | ConvertFrom-Json
    if ($verifiedManifest.schema_version -ne 1 -or @($verifiedManifest.files).Count -ne $newFiles.Count) {
        throw 'Downloaded ZIP did not extract to the expected complete package.'
    }

    # Start the old application with no PID supplied. The updater must discover
    # it by its full executable path, request closure, and wait before replacing.
    $oldProcess = Start-Process -FilePath $targetExe -PassThru
    $windowDeadline = (Get-Date).AddSeconds(20)
    do {
        $oldProcess.Refresh()
        if ($oldProcess.HasExited) { throw 'The old test application exited before the updater was launched.' }
        if ($oldProcess.MainWindowHandle -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $windowDeadline)
    if ($oldProcess.MainWindowHandle -eq [IntPtr]::Zero) {
        throw 'The old test application did not create a main window.'
    }

    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath -TargetExe $targetExe -PackageDirectory $verifiedPayload -Quiet
    if ($LASTEXITCODE -ne 0) {
        throw "End-to-end update script failed with exit code $LASTEXITCODE."
    }

    $oldProcess.Refresh()
    if (-not $oldProcess.HasExited) {
        throw 'The old application process was not closed before replacement.'
    }
    $installedHash = (Get-FileHash -LiteralPath $targetExe -Algorithm SHA256).Hash
    $expectedHash = (Get-FileHash -LiteralPath $newExe -Algorithm SHA256).Hash
    if ($installedHash -ne $expectedHash) {
        throw 'The new application executable was not installed byte-for-byte.'
    }
    if (Test-Path -LiteralPath (Join-Path $installDirectory 'obsolete-managed-file.txt')) {
        throw 'An obsolete managed application file was not removed.'
    }
    if ((Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash -ne $databaseHash) {
        throw 'The society database changed during the update.'
    }
    if ((Get-Content -LiteralPath (Join-Path $installDirectory 'backups\keep.txt') -Raw).Trim() -ne 'backup marker') {
        throw 'The existing backups folder was changed during the update.'
    }
    if ((Get-Content -LiteralPath (Join-Path $installDirectory 'user-data\keep.txt') -Raw).Trim() -ne 'user data marker') {
        throw 'The existing user-data folder was changed during the update.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'VBCCPAS\update-recovery.json')) {
        throw 'A successful update left an unfinished recovery journal.'
    }

    $logPath = Join-Path $env:LOCALAPPDATA 'VBCCPAS\update.log'
    if (-not (Select-String -LiteralPath $logPath -Pattern 'Update completed successfully' -Quiet)) {
        throw 'The updater did not write its successful completion report.'
    }
    $newProcesses = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='VBCCPAS-Soft.exe'" |
        Where-Object { $_.ExecutablePath -and [string]::Equals([IO.Path]::GetFullPath($_.ExecutablePath), $targetExe, [StringComparison]::OrdinalIgnoreCase) })
    if ($newProcesses.Count -ne 1) {
        throw "Expected exactly one restarted application process; found $($newProcesses.Count)."
    }
    $newProcess = Get-Process -Id ([int]$newProcesses[0].ProcessId)
    $newProcess.Refresh()
    if ($newProcess.MainWindowHandle -eq [IntPtr]::Zero -or $newProcess.MainWindowTitle -ne 'VBCCPAS updater simulation - new') {
        throw 'The updated application did not restart with its new GUI.'
    }
    $newProcess.CloseMainWindow() | Out-Null
    [void]$newProcess.WaitForExit(10000)

    Write-Host 'Windows updater end-to-end simulation passed: running-app closure, verified replacement, restart, database/user-data preservation, and permission probes.'
} finally {
    if ($originalAcl) {
        try { Set-Acl -LiteralPath $permissionDirectory -AclObject $originalAcl } catch { }
    }
    if ($oldProcess) {
        try {
            $oldProcess.Refresh()
            if (-not $oldProcess.HasExited) { Stop-Process -Id $oldProcess.Id -Force -ErrorAction SilentlyContinue }
        } catch { }
    }
    if ($newProcess) {
        try {
            $newProcess.Refresh()
            if (-not $newProcess.HasExited) { Stop-Process -Id $newProcess.Id -Force -ErrorAction SilentlyContinue }
        } catch { }
    }
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
