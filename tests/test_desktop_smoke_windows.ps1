param(
    [Parameter(Mandatory=$true)]
    [string]$ExecutablePath
)

$ErrorActionPreference = "Stop"
$ExecutablePath = (Resolve-Path -LiteralPath $ExecutablePath).Path
$TestRoot = Join-Path ([IO.Path]::GetTempPath()) ("VBCCPAS-Desktop-Smoke-" + [Guid]::NewGuid().ToString("N"))
$TestExe = Join-Path $TestRoot "VBCCPAS-Soft.exe"
$Process = $null

try {
    New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
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
    if ($Process.MainWindowTitle -notlike "*VBCCPAS*Photographic Arts Society*") {
        throw "The packaged executable opened an unexpected window: '$($Process.MainWindowTitle)'."
    }

    Write-Host "PASS: packaged desktop executable opened its expected VBCCPAS window."
    [void]$Process.CloseMainWindow()
    if (-not $Process.WaitForExit(10000)) {
        throw "The desktop application did not close normally after the smoke test."
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
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
