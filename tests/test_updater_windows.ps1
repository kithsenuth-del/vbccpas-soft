$ErrorActionPreference = 'Stop'
$scriptPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\updater.ps1'))
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('VBCCPAS Updater E2E Test ' + [Guid]::NewGuid().ToString('N'))
$env:LOCALAPPDATA = Join-Path $tempRoot 'local'
$permissionDirectory = Join-Path $tempRoot 'permission-check'
$installDirectory = Join-Path $tempRoot 'installation'
$payloadDirectory = Join-Path $tempRoot 'stage\payload'
$targetExe = Join-Path $installDirectory 'VBCCPAS-Soft.exe'
$oldProcess = $null
$newProcess = $null
$interruptedProcess = $null
$recoveredProcess = $null
$originalAcl = $null
$testUserName = $null
$testUserSid = $null
$testUserCreated = $false
$standardUserRoot = $null

$script:CSharpCompiler = Join-Path $env:WINDIR 'Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe'
if (-not (Test-Path -LiteralPath $script:CSharpCompiler -PathType Leaf)) {
    $script:CSharpCompiler = Join-Path $env:WINDIR 'Microsoft.NET\\Framework\\v4.0.30319\\csc.exe'
}
if (-not (Test-Path -LiteralPath $script:CSharpCompiler -PathType Leaf)) {
    throw 'The .NET Framework C# compiler is required for the Windows GUI updater simulation.'
}

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
    $sourcePath = $OutputPath + '.cs'
    [IO.File]::WriteAllText($sourcePath, $source, [Text.Encoding]::UTF8)
    & $script:CSharpCompiler /nologo /target:winexe "/out:$OutputPath" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll $sourcePath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "C# compiler failed to create the GUI test executable: $OutputPath"
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

function Invoke-StandardUserProbe([PSCredential]$Credential, [string]$PermissionScript, [string]$Executable, [string]$OutputPath) {
    if (Test-Path -LiteralPath $OutputPath) { Remove-Item -LiteralPath $OutputPath -Force }
    $arguments = @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $PermissionScript + '"'),
        '-TargetExe', ('"' + $Executable + '"'),
        '-CheckPermissionsOnly'
    )
    $process = Start-Process -FilePath 'powershell.exe' -Credential $Credential -LoadUserProfile -ArgumentList $arguments -RedirectStandardOutput $OutputPath -Wait -PassThru
    $output = if (Test-Path -LiteralPath $OutputPath) { Get-Content -LiteralPath $OutputPath -Raw } else { '' }
    if ([string]::IsNullOrWhiteSpace($output)) {
        throw "Standard-user permission probe returned no result (exit $($process.ExitCode))."
    }
    return [pscustomobject]@{
        exit_code = $process.ExitCode
        result = ($output.Trim() | ConvertFrom-Json)
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

    # Exercise the permission decision under a genuine non-administrator token.
    # The account is disposable and removed in the finally block.
    if ($expectedAdmin -and (Get-Command New-LocalUser -ErrorAction SilentlyContinue)) {
        $testUserName = 'vbccpas' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
        $plainPassword = [Guid]::NewGuid().ToString('N') + 'Aa!9'
        $securePassword = ConvertTo-SecureString $plainPassword -AsPlainText -Force
        $testUser = New-LocalUser -Name $testUserName -Password $securePassword -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword -Description 'Temporary VBCCPAS updater permission test'
        $testUserCreated = $true
        $testUserSid = $testUser.SID
        Add-LocalGroupMember -Group 'Users' -Member $testUserName -ErrorAction SilentlyContinue

        # ProgramData is traversable by ordinary Windows users; the runner's
        # private profile Temp directory is intentionally not used for this test.
        $standardUserRoot = Join-Path $env:ProgramData ('VBCCPAS-Updater-StandardUser-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $standardUserRoot -Force | Out-Null
        $standardRootAcl = Get-Acl -LiteralPath $standardUserRoot
        $standardModifyRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $testUserSid,
            [System.Security.AccessControl.FileSystemRights]::Modify,
            ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit),
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $standardRootAcl.AddAccessRule($standardModifyRule)
        Set-Acl -LiteralPath $standardUserRoot -AclObject $standardRootAcl
        $standardUserDirectory = Join-Path $standardUserRoot 'installation'
        New-Item -ItemType Directory -Path $standardUserDirectory -Force | Out-Null
        $standardTarget = Join-Path $standardUserDirectory 'VBCCPAS-Soft.exe'
        $standardScript = Join-Path $standardUserDirectory 'updater.ps1'
        Set-Content -LiteralPath $standardTarget -Value 'standard-user permission probe' -Encoding ASCII
        Copy-Item -LiteralPath $scriptPath -Destination $standardScript -Force
        $credential = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\$testUserName", $securePassword)
        $standardWritable = Invoke-StandardUserProbe $credential $standardScript $standardTarget (Join-Path $standardUserDirectory 'writable-result.json')
        if ($standardWritable.exit_code -ne 0 -or $standardWritable.result.is_administrator -or
            -not $standardWritable.result.can_write_target -or $standardWritable.result.requires_elevation) {
            throw ("The standard-user writable-installation permission test failed. Exit={0}; Admin={1}; CanWrite={2}; RequiresElevation={3}" -f $standardWritable.exit_code, $standardWritable.result.is_administrator, $standardWritable.result.can_write_target, $standardWritable.result.requires_elevation)
        }

        $standardDenyRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $testUserSid,
            [System.Security.AccessControl.FileSystemRights]::Write,
            ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit),
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Deny
        )
        $standardAcl = Get-Acl -LiteralPath $standardUserDirectory
        $standardAcl.AddAccessRule($standardDenyRule)
        Set-Acl -LiteralPath $standardUserDirectory -AclObject $standardAcl
        $standardDenied = Invoke-StandardUserProbe $credential $standardScript $standardTarget (Join-Path $standardUserDirectory 'denied-result.json')
        if ($standardDenied.exit_code -ne 2 -or $standardDenied.result.is_administrator -or
            $standardDenied.result.can_write_target -or -not $standardDenied.result.requires_elevation) {
            throw 'The standard-user protected-installation elevation test failed.'
        }
        Write-Host 'Standard-user permission tests passed: writable install accepted; protected install correctly requires elevation.'
    } elseif (-not $expectedAdmin) {
        Write-Warning 'A true standard-user token test was skipped because this runner is not an administrator.'
    } else {
        throw 'New-LocalUser is unavailable; the standard-user permission test cannot run.'
    }

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
    $databaseSidecarNames = @('society.db-wal','society.db-shm','society.db-journal')
    $databaseSidecarHashes = @{}
    foreach ($sidecarName in $databaseSidecarNames) {
        $sidecarPath = Join-Path $installDirectory $sidecarName
        [IO.File]::WriteAllText($sidecarPath, ('preserve ' + $sidecarName))
        $databaseSidecarHashes[$sidecarName] = (Get-FileHash -LiteralPath $sidecarPath -Algorithm SHA256).Hash
    }
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

    $updateOutput = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath -TargetExe $targetExe -PackageDirectory $verifiedPayload -Quiet 2>&1 | Out-String
    $updateExitCode = $LASTEXITCODE
    if ($updateExitCode -ne 0) {
        $updateLogPath = Join-Path $env:LOCALAPPDATA 'VBCCPAS\update.log'
        $updateLog = if (Test-Path -LiteralPath $updateLogPath) { Get-Content -LiteralPath $updateLogPath -Raw } else { 'No updater log was created.' }
        throw ("End-to-end update script failed with exit code " + $updateExitCode + "." + [Environment]::NewLine + $updateOutput + [Environment]::NewLine + $updateLog)
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
    foreach ($sidecarName in $databaseSidecarNames) {
        $sidecarPath = Join-Path $installDirectory $sidecarName
        if (-not (Test-Path -LiteralPath $sidecarPath -PathType Leaf) -or
            (Get-FileHash -LiteralPath $sidecarPath -Algorithm SHA256).Hash -ne $databaseSidecarHashes[$sidecarName]) {
            throw ('The updater changed or removed protected SQLite sidecar: ' + $sidecarName)
        }
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
    if (-not (Test-Path -LiteralPath (Join-Path $downloadDirectory 'verified') -PathType Container)) {
        throw 'The updater removed a caller-owned package directory while cleaning temporary staging.'
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
    if (-not $newProcess.WaitForExit(10000)) {
        throw 'The newly installed test application did not close after its health check.'
    }

    # Force the replacement executable to exit immediately. The installer must
    # report failure and restore the previously working managed files.
    $failureStage = Join-Path $tempRoot 'failure-stage'
    $failurePayload = Join-Path $failureStage 'payload'
    New-Item -ItemType Directory -Path (Join-Path $failurePayload 'assets') -Force | Out-Null
    $failingSource = @"
using System;
public static class VBCCPASFailingUpdaterSimulation {
    public static void Main() { Environment.Exit(7); }
}
"@
    $failingExe = Join-Path $failurePayload 'VBCCPAS-Soft.exe'
    $failingSourcePath = Join-Path $failurePayload 'failing-app.cs'
    [IO.File]::WriteAllText($failingSourcePath, $failingSource, [Text.Encoding]::UTF8)
    & $script:CSharpCompiler /nologo /target:exe "/out:$failingExe" $failingSourcePath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $failingExe -PathType Leaf)) {
        throw 'C# compiler failed to create the failing-startup test executable.'
    }
    Copy-Item -LiteralPath $scriptPath -Destination (Join-Path $failurePayload 'updater.ps1') -Force
    Set-Content -LiteralPath (Join-Path $failurePayload 'VBCCPAS-Updater.exe') -Value 'test updater placeholder'
    Set-Content -LiteralPath (Join-Path $failurePayload 'Recover-VBCCPAS.bat') -Value 'test recovery launcher'
    Set-Content -LiteralPath (Join-Path $failurePayload 'assets\version.txt') -Value 'failing-package'
    @{ schema_version = 1; files = $newFiles } |
        ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath (Join-Path $failurePayload 'vbccpas-managed-files.json') -Encoding UTF8
    $failureOutput = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath -TargetExe $targetExe -PackageDirectory $failurePayload -Quiet 2>&1 | Out-String
    $failureExitCode = $LASTEXITCODE
    if ($failureExitCode -eq 0) {
        throw 'The updater reported success even though the replacement application exited during startup.'
    }
    if ((Get-FileHash -LiteralPath $targetExe -Algorithm SHA256).Hash -ne $expectedHash) {
        throw 'A failed startup did not restore the previously working application executable.'
    }
    if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'VBCCPAS\update-recovery.json')) {
        throw 'A failed-startup rollback left an unfinished recovery journal.'
    }
    if (-not (Select-String -LiteralPath $logPath -Pattern 'Update failed' -Quiet) -or
        -not (Select-String -LiteralPath $logPath -Pattern 'Rollback completed successfully' -Quiet)) {
        throw ("The updater did not report failure and successful rollback. Output: " + $failureOutput)
    }
    if ((Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash -ne $databaseHash) {
        throw 'The society database changed during failed-startup rollback.'
    }
    foreach ($sidecarName in $databaseSidecarNames) {
        $sidecarPath = Join-Path $installDirectory $sidecarName
        if ((Get-FileHash -LiteralPath $sidecarPath -Algorithm SHA256).Hash -ne $databaseSidecarHashes[$sidecarName]) {
            throw ('Failed-startup rollback changed protected SQLite sidecar: ' + $sidecarName)
        }
    }
    Remove-Item -LiteralPath $failureStage -Recurse -Force -ErrorAction SilentlyContinue

    # Simulate a crash after the new executable has been copied but before the
    # install journal is marked verified. Recovery must close the partial app,
    # restore the saved executable, remove the journal, and restart the old app.
    $recoveryBackup = Join-Path $env:LOCALAPPDATA 'VBCCPAS\UpdateBackups\simulation-recovery'
    New-Item -ItemType Directory -Path $recoveryBackup -Force | Out-Null
    $recoveryBackupExe = Join-Path $recoveryBackup 'VBCCPAS-Soft.exe'
    Copy-Item -LiteralPath $oldExe -Destination $recoveryBackupExe -Force
    $interruptedExe = Join-Path $tempRoot 'interrupted-app.exe'
    New-TestApplication $interruptedExe 'VBCCPASInterruptedUpdaterSimulation' 'VBCCPAS updater simulation - interrupted'
    Copy-Item -LiteralPath $interruptedExe -Destination $targetExe -Force
    $journalPath = Join-Path $env:LOCALAPPDATA 'VBCCPAS\update-recovery.json'
    $simulatedJournal = [pscustomobject]@{
        schema_version = 1
        target = $targetExe
        backup = $recoveryBackup
        state = 'installing'
        entries = @([pscustomobject]@{
            relative = 'VBCCPAS-Soft.exe'
            destination = $targetExe
            backup = $recoveryBackupExe
            existed = $true
            temporary = $null
        })
    }
    $simulatedJournal | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $journalPath -Encoding UTF8
    $interruptedProcess = Start-Process -FilePath $targetExe -PassThru
    $interruptedDeadline = (Get-Date).AddSeconds(20)
    do {
        $interruptedProcess.Refresh()
        if ($interruptedProcess.HasExited) { throw 'The interrupted test application exited before recovery began.' }
        if ($interruptedProcess.MainWindowHandle -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $interruptedDeadline)
    if ($interruptedProcess.MainWindowHandle -eq [IntPtr]::Zero) {
        throw 'The interrupted test application did not create a main window.'
    }

    $recoveryOutput = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath -TargetExe $targetExe -RecoverOnly -RestartAfterRecovery -Quiet 2>&1 | Out-String
    $recoveryExitCode = $LASTEXITCODE
    if ($recoveryExitCode -ne 0) {
        $recoveryLog = if (Test-Path -LiteralPath $logPath) { Get-Content -LiteralPath $logPath -Raw } else { 'No updater log was created.' }
        throw ("Interrupted-update recovery failed with exit code " + $recoveryExitCode + "." + [Environment]::NewLine + $recoveryOutput + [Environment]::NewLine + $recoveryLog)
    }
    $interruptedProcess.Refresh()
    if (-not $interruptedProcess.HasExited) {
        throw 'Recovery did not close the partially installed application before restoring files.'
    }
    if ((Get-FileHash -LiteralPath $targetExe -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $oldExe -Algorithm SHA256).Hash) {
        throw 'Recovery did not restore the original application executable.'
    }
    if (Test-Path -LiteralPath $journalPath) {
        throw 'Recovery left an unfinished installation journal.'
    }
    if ((Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash -ne $databaseHash) {
        throw 'The society database changed during interrupted-update recovery.'
    }
    foreach ($sidecarName in $databaseSidecarNames) {
        $sidecarPath = Join-Path $installDirectory $sidecarName
        if (-not (Test-Path -LiteralPath $sidecarPath -PathType Leaf) -or
            (Get-FileHash -LiteralPath $sidecarPath -Algorithm SHA256).Hash -ne $databaseSidecarHashes[$sidecarName]) {
            throw ('Recovery changed or removed protected SQLite sidecar: ' + $sidecarName)
        }
    }
    if (-not (Select-String -LiteralPath $logPath -Pattern 'Recovered application restarted' -Quiet)) {
        throw 'Recovery did not record a successful application restart.'
    }
    $recoveredProcesses = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='VBCCPAS-Soft.exe'" |
        Where-Object { $_.ExecutablePath -and [string]::Equals([IO.Path]::GetFullPath($_.ExecutablePath), $targetExe, [StringComparison]::OrdinalIgnoreCase) })
    if ($recoveredProcesses.Count -ne 1) {
        throw "Expected one recovered application process; found $($recoveredProcesses.Count)."
    }
    $recoveredProcess = Get-Process -Id ([int]$recoveredProcesses[0].ProcessId)
    $recoveredProcess.Refresh()
    if ($recoveredProcess.MainWindowHandle -eq [IntPtr]::Zero -or
        $recoveredProcess.MainWindowTitle -ne 'VBCCPAS updater simulation - old') {
        throw 'The original application did not restart after interrupted-update recovery.'
    }
    $recoveredProcess.CloseMainWindow() | Out-Null
    [void]$recoveredProcess.WaitForExit(10000)

    Write-Host 'Windows updater end-to-end simulation passed: package copy/download, SHA-256 verification, running-app closure, replacement, restart, interrupted-update recovery/restart, database/user-data preservation, and permission probes.'
} finally {
    if ($originalAcl) {
        try { Set-Acl -LiteralPath $permissionDirectory -AclObject $originalAcl } catch { }
    }
    if ($testUserCreated -and $testUserName) {
        try { Remove-LocalUser -Name $testUserName -ErrorAction SilentlyContinue } catch { }
        if ($testUserSid) {
            try {
                Get-CimInstance -ClassName Win32_UserProfile | Where-Object { $_.SID -eq $testUserSid.Value -and -not $_.Loaded } | Remove-CimInstance -ErrorAction SilentlyContinue
            } catch { }
        }
    }
    if ($standardUserRoot) {
        Remove-Item -LiteralPath $standardUserRoot -Recurse -Force -ErrorAction SilentlyContinue
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
    if ($interruptedProcess) {
        try {
            $interruptedProcess.Refresh()
            if (-not $interruptedProcess.HasExited) { Stop-Process -Id $interruptedProcess.Id -Force -ErrorAction SilentlyContinue }
        } catch { }
    }
    if ($recoveredProcess) {
        try {
            $recoveredProcess.Refresh()
            if (-not $recoveredProcess.HasExited) { Stop-Process -Id $recoveredProcess.Id -Force -ErrorAction SilentlyContinue }
        } catch { }
    }
    try {
        if ($targetExe -and (Test-Path -LiteralPath (Split-Path -Parent $targetExe))) {
            Get-CimInstance -ClassName Win32_Process -Filter "Name='VBCCPAS-Soft.exe'" |
                Where-Object { $_.ExecutablePath -and [string]::Equals([IO.Path]::GetFullPath($_.ExecutablePath), $targetExe, [StringComparison]::OrdinalIgnoreCase) } |
                ForEach-Object { Stop-Process -Id ([int]$_.ProcessId) -Force -ErrorAction SilentlyContinue }
        }
    } catch { }
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
