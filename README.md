# VBCCPAS Soft

VBCCPAS Soft is a local society-management application for Windows. The unified desktop application contains the existing desktop management modules and can start/open the local browser dashboard from the same interface.

## Install VBCCPAS Soft on Windows

The recommended installation is the signed-in Windows user's own account; administrator permission is not required.

1. Download **VBCCPAS-Soft-Setup.exe** from the latest GitHub release.
2. Double-click the setup file. If Windows displays its normal publisher/security prompt, verify that the file came from this repository before continuing.
3. Follow the setup wizard and keep the suggested install folder, or choose another folder where your Windows account has write permission.
4. Optionally select **Create a desktop shortcut**.
5. Finish setup. You can launch **VBCCPAS Soft** from the Start menu or desktop shortcut.

The installer places the application in `%LOCALAPPDATA%\Programs\VBCCPAS Soft` by default, so the application can write its local SQLite database without requiring administrator access. The application opens its desktop interface; use **Web Dashboard** in the sidebar to open the integrated local browser dashboard. No separate server or Command Prompt is needed.

### Uninstall VBCCPAS Soft

1. Close VBCCPAS Soft.
2. Open **Windows Settings → Apps → Installed apps** (or **Apps & features** on some Windows versions).
3. Find **VBCCPAS Soft**, choose **Uninstall**, and follow the prompts. You can also use **Uninstall VBCCPAS Soft** in the Start menu.

**Your society data is deliberately preserved.** The uninstaller removes the installed application files and shortcuts, but does not remove `society.db`, backups, exports, or other user-created files in the installation folder. Reinstalling into the same folder can therefore retain the existing database. If you ever want to remove that data too, first make and verify a separate backup; delete the remaining data folder yourself only after confirming it is no longer needed.

### Portable installation (alternative)

You can still use the portable ZIP if you prefer not to run a setup wizard:

1. Extract the complete **VBCCPAS-Soft-Windows.zip** to a permanent folder where your Windows account has read/write permission.
2. Double-click **Setup-VBCCPAS-Shortcut.bat** once to create a desktop shortcut and launch the application.
3. Keep the application folder in place; the shortcut points to the executable inside it.

## Build from source

On Windows, install **Python 3.11** with the Python launcher (`py`). Node.js is recommended for the dashboard JavaScript syntax check. From PowerShell, run:

    .\build_exes.ps1

The build script installs the Python dependencies, requires Inno Setup 6 to compile the Windows installer, stamps the current Git commit into the executable, runs Python compilation, passkey recovery tests, updater download/package tests, data-flow safety checks, PowerShell syntax validation, an interrupted-update rollback test, and a Windows end-to-end updater simulation. The Windows simulations check normal application closure, waiting for the old process, ZIP download/integrity verification, replacement, restart, permission probes, preservation of society.db/backups/user-data, and setup/uninstall while proving that user data survives uninstall. The build also verifies every ZIP entry and writes a SHA-256 sidecar. If Node.js is installed, it checks the dashboard JavaScript. The isolated web/desktop integration suite runs in the preceding Linux CI job; it is kept out of the local Windows packaging script because its temporary SQLite-file cleanup currently relies on POSIX file-unlink behavior.

A successful build produces:

- `dist\VBCCPAS-Soft.exe` — the main desktop application
- `dist\VBCCPAS-Updater.exe` — the independent graphical updater
- `dist\updater.ps1` — the safe package installation/rollback script
- `dist\Recover-VBCCPAS.bat` — emergency recovery launcher for interrupted installs or a damaged updater
- `dist\vbccpas-managed-files.json` — the updater's managed-file manifest
- `dist\VBCCPAS-Soft-Windows.zip` — the complete distributable package, including shortcut setup files

The build script does not create a shortcut on the build computer by default. To also create one there, run:

    .\build_exes.ps1 -CreateDesktopShortcut

For normal use, distribute `VBCCPAS-Soft-Setup.exe` and its `.sha256` checksum. The portable ZIP remains available as an alternative.

## First-time administrator setup

On a new database, the desktop interface displays a randomly generated administrator password once. Save it securely and sign in with username admin. Change the password from the Security section.

On first successful desktop login, the integrated local web server initializes. If a browser passkey has not yet been registered, the desktop interface displays the one-time passkey setup token with a Copy button. Open Web Dashboard, paste the token into the local setup page, and register the computer's platform authenticator (for example, Windows Hello). Keep the token private.

The web dashboard uses a device-bound passkey and an in-memory eight-hour session. The passkey private key remains with the platform authenticator. The local web server binds only to loopback and must not be exposed to a LAN or the public internet.

## Built-in maintenance

- Maintenance & Backups → Run Problem Checker runs safe application and database checks from the desktop interface.
- Back Up Database creates a consistent SQLite backup using SQLite's backup API.
- The problem checker is read-only. It does not repair, edit, or delete application records.
- A database backup does not back up the passkey private key.

## Software updates

- Open **Maintenance & Backups → Check for Software Updates** while connected to the internet.
- The app checks the latest published Windows release from this official GitHub repository.
- If an update is available, you can choose whether to install it. The package is downloaded over HTTPS and verified against GitHub's SHA-256 asset digest before installation.
- The updater replaces only application-managed files after the app closes. It does not include or overwrite `society.db`, backups, exports, logs, or protected user-data folders.
- The package is SHA-256 verified, each installed file is hash-checked, and the previous managed files are retained in `%LOCALAPPDATA%\\VBCCPAS\\UpdateBackups`.
- The installer writes a per-file recovery journal before replacing files. If Windows restarts or the update is interrupted, launch `VBCCPAS-Updater.exe`; it will attempt to roll back the unfinished transaction before checking for another update.
- To reinstall the latest complete application package, open `VBCCPAS-Updater.exe` and select **Repair Installation**. This can restore a missing main EXE as long as the updater EXE and installation folder are still available.
- If the updater will not open after an interrupted replacement, double-click `Recover-VBCCPAS.bat`. It uses the separate recovery bootstrap and then opens the updater (or the main app if the updater is unavailable).
- Update diagnostics are stored in `%LOCALAPPDATA%\\VBCCPAS\\update.log`. Keep the matching rollback folder if support/recovery is needed.
- New updates are published only after the integration checks, Windows build, ZIP/manifest verification, and SHA-256 sidecar verification all pass. A push to `main` publishes the latest release. A manually dispatched Windows build also publishes a release; if it is run from a non-main branch, it is not marked as the latest release.

## Data storage

The existing society.db remains beside the application executable. Keep the application in a folder where the signed-in Windows user has read/write permission. Back up the database regularly and store backups securely.

## Developer verification

Install dependencies and run the checks from the repository root:

    python -m pip install -r requirements.txt
    python -m py_compile main.py desktop_app.py server.py passkey_auth.py problem_checker.py updater.py updater_app.py build_info.py database_migrations.py test_integration.py verify_data_flow.py tests/test_passkey_recovery.py tests/test_updater.py tests/test_updater_package.py
    python test_integration.py
    python tests/test_passkey_recovery.py
    python tests/test_updater.py
    python tests/test_updater_package.py
    python verify_data_flow.py
    python check_frontend.py

Integration tests use disposable temporary databases. The Windows GUI, shortcut COM integration, Windows Hello ceremony, and final packaged executable still require testing on a Windows computer. The GitHub Actions workflow performs the automated Windows build and package checks on pushes to `main`, pull requests, and manual dispatches. Release publication is a separate job that re-downloads and re-verifies the complete ZIP and checksum before it can create or update a release.

## Security boundary

This is a single-computer application, not a network-hosted multi-user service. Keep the browser dashboard bound to 127.0.0.1 / localhost. Never expose port 8000 to the LAN or public internet.


## Central service and local-first synchronisation

The repository now contains a separately deployable central account API and the first versioned sync protocol/local SQLite engine. These components are not yet connected to the Windows desktop screens; offline desktop operation is unchanged. Existing society records, local passwords and passkeys are not uploaded.

- [Phase 2: central API and shared accounts](docs/phase-2-central-api.md)
- [Phase 3: local-first synchronisation foundation](docs/phase-3-local-first-sync.md)

Only test copies should be used for the explicit schema migrations until the later desktop integration and two-computer tests are complete.
