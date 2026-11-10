param(
    [Parameter(Mandatory=$true)]
    [string]$ExecutablePath
)

$ErrorActionPreference = "Stop"
$ExecutablePath = (Resolve-Path -LiteralPath $ExecutablePath).Path
$TestRoot = Join-Path ([IO.Path]::GetTempPath()) ("VBCCPAS-Desktop-Smoke-" + [Guid]::NewGuid().ToString("N"))
$TestExe = Join-Path $TestRoot "VBCCPAS-Soft.exe"
$Process = $null
$OriginalLocalAppData = $env:LOCALAPPDATA
$OriginalAdminPassword = $env:VBCCPAS_ADMIN_PASSWORD

try {
    New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
    $env:LOCALAPPDATA = Join-Path $TestRoot "LocalAppData"
    New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force | Out-Null
    Remove-Item Env:VBCCPAS_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $ExecutablePath -Destination $TestExe -Force

    Write-Host "Starting the packaged desktop application in an isolated temporary folder..."
    $Process = Start-Process -FilePath $TestExe -WorkingDirectory $TestRoot -PassThru
    $deadline = (Get-Date).AddSeconds(60)
    do {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "VBCCPAS-Soft.exe exited during startup (exit code $($Process.ExitCode))."
        }
        if ($Process.MainWindowHandle -ne [IntPtr]::Zero) { break }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)

    $Process.Refresh()
    if ($Process.HasExited -or $Process.MainWindowHandle -eq [IntPtr]::Zero) {
        throw "VBCCPAS-Soft.exe did not create its desktop window within 60 seconds."
    }
    if ([string]::IsNullOrWhiteSpace($Process.MainWindowTitle)) {
        throw "The packaged executable did not expose a titled desktop window."
    }
    $PasswordFile = Join-Path $env:LOCALAPPDATA "VBCCPAS\\first-run-admin-password.txt"
    if (-not (Test-Path -LiteralPath $PasswordFile -PathType Leaf)) {
        throw "The fresh desktop database did not create its one-time administrator credential."
    }
    $InitialPassword = (Get-Content -LiteralPath $PasswordFile -Raw).Trim()
    if ([string]::IsNullOrWhiteSpace($InitialPassword) -or $InitialPassword -eq "admin123") {
        throw "The first-run administrator credential was missing or used a predictable default."
    }

    Write-Host "PASS: packaged desktop executable opened a titled VBCCPAS window and generated a random first-run admin credential."
    Stop-Process -Id $Process.Id -Force
    if (-not $Process.WaitForExit(10000)) {
        throw "The desktop application process did not terminate after the smoke test."
    }
}
finally {
    if ($Process) {
        try {
            $Process.Refresh()
            if (-not $Process.HasExited) {
                Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            }
        } catch { }
    }
    $env:LOCALAPPDATA = $OriginalLocalAppData
    if ($null -eq $OriginalAdminPassword) {
        Remove-Item Env:VBCCPAS_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    } else {
        $env:VBCCPAS_ADMIN_PASSWORD = $OriginalAdminPassword
    }
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
