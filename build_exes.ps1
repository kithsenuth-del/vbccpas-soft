[CmdletBinding()]
param(
    [switch]$CreateDesktopShortcut
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Invoke-Python {
    & py -3.11 @args
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

$BuildInfoPath = Join-Path $Root "build_info.py"
$OriginalBuildInfo = Get-Content -LiteralPath $BuildInfoPath -Raw
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"

try {
    Write-Host "Installing application and Windows build dependencies..."
    & py -3.11 -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
    & py -3.11 -m pip install -r requirements.txt pyinstaller pillow
    if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }

    $Commit = (& git rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or $Commit -notmatch '^[0-9a-fA-F]{40}$') {
        $Commit = "development"
        Write-Warning "Git commit metadata is unavailable; this build will identify itself as development."
    }
    Set-Content -LiteralPath $BuildInfoPath -Value "BUILD_COMMIT = '$Commit'" -Encoding ascii

    Write-Host "Generating the official VBCCPAS Windows application icon..."
    Invoke-Python -c "from PIL import Image; im=Image.open(r'assets/society-logo.gif').convert('RGBA'); im.save(r'assets/society-logo.ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    if (-not (Test-Path -LiteralPath (Join-Path $Root "assets\society-logo.ico") -PathType Leaf)) { throw "Official society icon generation failed." }

    Write-Host "Running Python compilation checks..."
    Invoke-Python -m py_compile main.py desktop_app.py server.py passkey_auth.py problem_checker.py updater.py updater_app.py build_info.py database_migrations.py test_integration.py verify_data_flow.py tests/test_passkey_recovery.py tests/test_updater.py tests/test_updater_package.py

    Write-Host "Running isolated integration and safety tests..."
    Invoke-Python tests/test_passkey_recovery.py
    Invoke-Python tests/test_updater.py
    Invoke-Python tests/test_updater_package.py
    Invoke-Python verify_data_flow.py

    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Host "Checking dashboard JavaScript..."
        Invoke-Python check_frontend.py
    } else {
        Write-Warning "Node.js was not found; dashboard JavaScript syntax check was skipped."
    }

    Write-Host "Validating the PowerShell updater syntax..."
    $Tokens = $null
    $ParseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $Root "updater.ps1"), [ref]$Tokens, [ref]$ParseErrors
    ) | Out-Null
    if ($ParseErrors.Count -gt 0) {
        throw ("PowerShell updater syntax check failed:" + [Environment]::NewLine + ($ParseErrors | Out-String))
    }
    Write-Host "Testing interrupted-update recovery and database preservation..."
    & (Join-Path $Root "tests\test_updater_recovery.ps1")
    if ($LASTEXITCODE -ne 0) { throw "PowerShell recovery regression tests failed." }

    if (Test-Path -LiteralPath $Dist) {
        Remove-Item -LiteralPath $Dist -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Dist -Force | Out-Null
    New-Item -ItemType Directory -Path $Build -Force | Out-Null

    Write-Host "Building VBCCPAS-Soft.exe with the official society icon... "
    Invoke-Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath (Join-Path $Build "desktop") (Join-Path $Root "desktop_app.spec")

    Write-Host "Building VBCCPAS-Updater.exe with the official society icon..."
    Invoke-Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath (Join-Path $Build "updater") (Join-Path $Root "updater_app.spec")

    Write-Host "Adding launcher and updater support files..."
    Copy-Item -LiteralPath (Join-Path $Root "Create-Desktop-Shortcut.ps1") -Destination $Dist -Force
    Copy-Item -LiteralPath (Join-Path $Root "Setup-VBCCPAS-Shortcut.bat") -Destination $Dist -Force
    Copy-Item -LiteralPath (Join-Path $Root "updater.ps1") -Destination $Dist -Force

    $Required = @(
        "VBCCPAS-Soft.exe",
        "VBCCPAS-Updater.exe",
        "Create-Desktop-Shortcut.ps1",
        "Setup-VBCCPAS-Shortcut.bat",
        "updater.ps1"
    )
    foreach ($Name in $Required) {
        if (-not (Test-Path -LiteralPath (Join-Path $Dist $Name) -PathType Leaf)) {
            throw "Build output is missing required file: $Name"
        }
    }

    $ManifestName = "vbccpas-managed-files.json"
    $Files = @(
        Get-ChildItem -LiteralPath $Dist -File -Recurse |
        Where-Object { $_.Name -ne $ManifestName -and $_.Name -ne "VBCCPAS-Soft-Windows.zip" } |
        ForEach-Object {
            [IO.Path]::GetRelativePath($Dist, $_.FullName).Replace('\', '/')
        } |
        Sort-Object
    )
    $Manifest = @{ schema_version = 1; files = $Files }
    $Manifest | ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath (Join-Path $Dist $ManifestName) -Encoding utf8

    Write-Host "Creating complete Windows distribution ZIP..."
    $ZipPath = Join-Path $Dist "VBCCPAS-Soft-Windows.zip"
    Compress-Archive -Path (Join-Path $Dist "*") -DestinationPath $ZipPath -Force

    Write-Host "Verifying the update package contents..."
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $Entries = @($Archive.Entries | ForEach-Object { $_.FullName })
        foreach ($Name in ($Required + $ManifestName)) {
            if ($Entries -notcontains $Name) {
                throw "Windows ZIP is missing required package entry: $Name"
            }
        }
        $ManifestEntry = $Archive.GetEntry($ManifestName)
        $Reader = New-Object System.IO.StreamReader($ManifestEntry.Open())
        try {
            $ArchivedManifest = $Reader.ReadToEnd() | ConvertFrom-Json
        } finally {
            $Reader.Dispose()
        }
        $ArchivedFiles = @($ArchivedManifest.files)
        $Unexpected = @($Entries | Where-Object { $_ -ne $ManifestName } | Where-Object { $ArchivedFiles -notcontains $_ })
        if ($Unexpected.Count -gt 0 -or $ArchivedFiles.Count -ne ($Entries.Count - 1)) {
            throw "Windows ZIP contents do not match the managed-file manifest."
        }
    } finally {
        $Archive.Dispose()
    }
} finally {
    Set-Content -LiteralPath $BuildInfoPath -Value $OriginalBuildInfo -Encoding utf8
}

if ($CreateDesktopShortcut) {
    Write-Host "Creating a desktop shortcut on this computer..."
    & (Join-Path $Dist "Create-Desktop-Shortcut.ps1") -TargetExe (Join-Path $Dist "VBCCPAS-Soft.exe")
}

Write-Host ""
Write-Host "Build and verification complete."
Write-Host "Application: $Dist\VBCCPAS-Soft.exe"
Write-Host "Standalone updater: $Dist\VBCCPAS-Updater.exe"
Write-Host "Complete distribution: $Dist\VBCCPAS-Soft-Windows.zip"
Write-Host "For another computer, extract the ZIP to a permanent folder and run Setup-VBCCPAS-Shortcut.bat."
