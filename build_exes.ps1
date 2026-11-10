[CmdletBinding()]
param(
    [switch]$CreateDesktopShortcut,
    # CI has already run the Python test suite in the required integration job.
    # Keep the full checks enabled by default for local builds.
    [switch]$SkipPreBuildTests
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
    # setup-python caches pip downloads in CI; avoid upgrading pip on every runner.
    & py -3.11 -m pip install --disable-pip-version-check -r requirements.txt pyinstaller pillow
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

    if (-not $SkipPreBuildTests) {
        Write-Host "Running Python compilation checks..."
        Invoke-Python -m py_compile main.py desktop_app.py server.py passkey_auth.py problem_checker.py updater.py updater_app.py build_info.py database_migrations.py test_integration.py verify_data_flow.py tests/test_passkey_recovery.py tests/test_updater.py tests/test_updater_package.py tests/test_updater_handoff_windows.py

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
    } else {
        Write-Host "Skipping Python checks already completed by the required integration job."
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
    Write-Host "Simulating a complete Windows update, running-app closure, restart and permission checks..."
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $Root "tests\test_updater_windows.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Windows updater end-to-end simulation failed." }
    Write-Host "Testing the real Python-to-PowerShell handoff after the launcher exits..."
    Invoke-Python tests/test_updater_handoff_windows.py

    if (Test-Path -LiteralPath $Dist) {
        Remove-Item -LiteralPath $Dist -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Dist -Force | Out-Null
    New-Item -ItemType Directory -Path $Build -Force | Out-Null

    Write-Host "Building VBCCPAS-Soft.exe with the official society icon... "
    Invoke-Python -m PyInstaller --noconfirm --distpath $Dist --workpath (Join-Path $Build "desktop") (Join-Path $Root "desktop_app.spec")

    Write-Host "Building VBCCPAS-Updater.exe with the official society icon..."
    Invoke-Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath (Join-Path $Build "updater") (Join-Path $Root "updater_app.spec")

    Write-Host "Adding launcher and updater support files..."
    Copy-Item -LiteralPath (Join-Path $Root "Create-Desktop-Shortcut.ps1") -Destination $Dist -Force
    Copy-Item -LiteralPath (Join-Path $Root "Setup-VBCCPAS-Shortcut.bat") -Destination $Dist -Force
    Copy-Item -LiteralPath (Join-Path $Root "updater.ps1") -Destination $Dist -Force
    Copy-Item -LiteralPath (Join-Path $Root "Recover-VBCCPAS.bat") -Destination $Dist -Force

    $Required = @(
        "VBCCPAS-Soft.exe",
        "VBCCPAS-Updater.exe",
        "Create-Desktop-Shortcut.ps1",
        "Setup-VBCCPAS-Shortcut.bat",
        "updater.ps1",
        "Recover-VBCCPAS.bat"
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
        $FileEntries = @($Archive.Entries | Where-Object { -not $_.FullName.EndsWith('/') } | ForEach-Object { $_.FullName })
        foreach ($Name in ($Required + $ManifestName)) {
            if ($FileEntries -notcontains $Name) {
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
        $Unexpected = @($FileEntries | Where-Object { $_ -ne $ManifestName } | Where-Object { $ArchivedFiles -notcontains $_ })
        if ($Unexpected.Count -gt 0 -or $ArchivedFiles.Count -ne ($FileEntries.Count - 1)) {
            throw "Windows ZIP contents do not match the managed-file manifest."
        }
        $ProtectedEntries = @($FileEntries | Where-Object {
            $parts = $_ -split '/'
            @('society.db','society.db-wal','society.db-shm','society.db-journal','backups','exports','logs','user-data','user_data','.vbccpas-data') |
                Where-Object { $parts -contains $_ } | Select-Object -First 1
        })
        if ($ProtectedEntries.Count -gt 0) {
            throw ("Windows ZIP contains protected user data: " + ($ProtectedEntries -join ', '))
        }
        # Read every compressed entry so corrupt data streams fail the build,
        # rather than only checking the central-directory names.
        foreach ($Entry in $Archive.Entries) {
            if ($Entry.FullName.EndsWith('/')) { continue }
            $Stream = $Entry.Open()
            try {
                $Buffer = New-Object byte[] 65536
                while ($Stream.Read($Buffer, 0, $Buffer.Length) -gt 0) { }
            } finally {
                $Stream.Dispose()
            }
        }
    } finally {
        $Archive.Dispose()
    }

    $ChecksumPath = $ZipPath + ".sha256"
    $PackageHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $ChecksumPath -Value ($PackageHash + "  VBCCPAS-Soft-Windows.zip") -Encoding ASCII -NoNewline
    $ChecksumLine = Get-Content -LiteralPath $ChecksumPath -Raw
    if ($ChecksumLine -ne ($PackageHash + "  VBCCPAS-Soft-Windows.zip")) {
        throw "The Windows ZIP SHA-256 checksum file could not be verified."
    }
    if ((Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $PackageHash) {
        throw "The Windows ZIP changed after its checksum was generated."
    }

    Write-Host "Building the Windows installer and uninstaller..."
    $InnoCompiler = (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue).Source
    if (-not $InnoCompiler) {
        $ProgramFilesX86 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
        $DefaultInnoCompiler = Join-Path $ProgramFilesX86 "Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $DefaultInnoCompiler -PathType Leaf) {
            $InnoCompiler = $DefaultInnoCompiler
        }
    }
    if (-not $InnoCompiler) {
        throw "Inno Setup 6 is required to build the Windows installer. Install Inno Setup 6 and run this build again."
    }
    & $InnoCompiler (Join-Path $Root "installer\vbccpas-installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed to compile the Windows installer." }

    $InstallerPath = Join-Path $Dist "VBCCPAS-Soft-Setup.exe"
    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw "The Windows installer executable was not generated."
    }
    $InstallerHash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath ($InstallerPath + ".sha256") -Value ($InstallerHash + "  VBCCPAS-Soft-Setup.exe") -Encoding ASCII -NoNewline
    if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $InstallerHash) {
        throw "The Windows installer checksum verification failed."
    }
    Write-Host "Testing silent install, uninstaller and preservation of society.db..."
    & (Join-Path $Root "tests\test_installer_windows.ps1") -InstallerPath $InstallerPath
    if ($LASTEXITCODE -ne 0) { throw "Windows installer/uninstaller preservation test failed." }
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
Write-Host "Windows installer: $Dist\VBCCPAS-Soft-Setup.exe"
Write-Host "Complete portable distribution: $Dist\VBCCPAS-Soft-Windows.zip"
Write-Host "SHA-256 checksum: $Dist\VBCCPAS-Soft-Windows.zip.sha256"
Write-Host "For another computer, extract the ZIP to a permanent folder and run Setup-VBCCPAS-Shortcut.bat."
