$ErrorActionPreference = 'Stop'
$scriptPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\updater.ps1'))
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('vbccpas-updater-recovery-test-' + [Guid]::NewGuid().ToString('N'))
$env:LOCALAPPDATA = Join-Path $tempRoot 'local'
$targetDirectory = Join-Path $tempRoot 'installation'
$targetExe = Join-Path $targetDirectory 'VBCCPAS-Soft.exe'
$backupDirectory = Join-Path $env:LOCALAPPDATA 'VBCCPAS\UpdateBackups\test'
$journalPath = Join-Path $env:LOCALAPPDATA 'VBCCPAS\update-recovery.json'
try {
    New-Item -ItemType Directory -Path $targetDirectory, $backupDirectory -Force | Out-Null
    Set-Content -LiteralPath $targetExe -Value 'partially installed'
    Set-Content -LiteralPath (Join-Path $targetDirectory 'new-file.txt') -Value 'new file'
    Set-Content -LiteralPath (Join-Path $targetDirectory 'society.db') -Value 'preserve this database'
    $backupExe = Join-Path $backupDirectory 'VBCCPAS-Soft.exe'
    Set-Content -LiteralPath $backupExe -Value 'previous working application'
    $journal = @{
        state = 'installing'
        target = $targetExe
        backup = $backupDirectory
        entries = @(
            @{ relative = 'VBCCPAS-Soft.exe'; backup = $backupExe; existed = $true },
            @{ relative = 'new-file.txt'; backup = (Join-Path $backupDirectory 'new-file.txt'); existed = $false }
        )
    }
    $journal | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $journalPath -Encoding UTF8
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath -TargetExe $targetExe -RecoverOnly -Quiet
    if ($LASTEXITCODE -ne 0) { throw 'Recovery bootstrap returned a failure exit code.' }
    if ((Get-Content -LiteralPath $targetExe -Raw).Trim() -ne 'previous working application') {
        throw 'The previous application executable was not restored.'
    }
    if (Test-Path -LiteralPath (Join-Path $targetDirectory 'new-file.txt')) {
        throw 'A newly introduced file was not removed during rollback.'
    }
    if ((Get-Content -LiteralPath (Join-Path $targetDirectory 'society.db') -Raw).Trim() -ne 'preserve this database') {
        throw 'The society database was changed by recovery.'
    }
    if (Test-Path -LiteralPath $journalPath) { throw 'The completed recovery journal was not cleared.' }

    # A missing backup must fail closed and retain the journal for manual recovery.
    $journal.entries = @(@{ relative = 'VBCCPAS-Soft.exe'; backup = (Join-Path $backupDirectory 'missing.exe'); existed = $true })
    $journal | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $journalPath -Encoding UTF8
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath -TargetExe $targetExe -RecoverOnly -Quiet
    if ($LASTEXITCODE -eq 0) { throw 'Recovery unexpectedly succeeded without its required backup.' }
    if (-not (Test-Path -LiteralPath $journalPath)) { throw 'Failed recovery journal was not retained.' }

    # A verified install only needs journal cleanup; it must not be rolled back.
    $journal.state = 'verified'
    $journal.entries = @()
    $journal | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $journalPath -Encoding UTF8
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptPath -TargetExe $targetExe -RecoverOnly -Quiet
    if ($LASTEXITCODE -ne 0) { throw 'Verified journal finalisation failed.' }
    if (Test-Path -LiteralPath $journalPath) { throw 'Verified journal was not finalised.' }
    if ((Get-Content -LiteralPath $targetExe -Raw).Trim() -ne 'previous working application') {
        throw 'Verified journal finalisation unexpectedly changed the application.'
    }
    Write-Host 'PowerShell updater recovery tests passed.'
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
