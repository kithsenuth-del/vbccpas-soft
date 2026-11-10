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
    $DatabasePath = Join-Path $TestRoot "society.db"
    $PasswordFile = Join-Path (Join-Path $env:LOCALAPPDATA "VBCCPAS") "first-run-admin-password.txt"
    $deadline = (Get-Date).AddSeconds(45)
    do {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "VBCCPAS-Soft.exe exited during startup (exit code $($Process.ExitCode))."
        }
        if ((Test-Path -LiteralPath $DatabasePath -PathType Leaf) -and
            (Test-Path -LiteralPath $PasswordFile -PathType Leaf)) { break }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)

    $Process.Refresh()
    if ($Process.HasExited) {
        throw "VBCCPAS-Soft.exe exited during startup (exit code $($Process.ExitCode))."
    }
    if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) {
        throw "VBCCPAS-Soft.exe did not initialize its isolated SQLite database within 45 seconds."
    }
    if (-not (Test-Path -LiteralPath $PasswordFile -PathType Leaf)) {
        throw "The fresh desktop database did not create its one-time administrator credential."
    }
    $InitialPassword = (Get-Content -LiteralPath $PasswordFile -Raw).Trim()
    if ([string]::IsNullOrWhiteSpace($InitialPassword) -or $InitialPassword -eq "admin123") {
        throw "The first-run administrator credential was missing or used a predictable default."
    }

    Write-Host "PASS: packaged desktop executable stayed running and initialized its isolated database and random first-run admin credential."
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
