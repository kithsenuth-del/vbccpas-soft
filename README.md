# VBCCPAS Soft

VBCCPAS Soft is a local society-management application for Windows. The unified desktop application contains the existing desktop management modules and can start/open the local browser dashboard from the same interface.

## Run the application (Windows)

The packaged **VBCCPAS-Soft.exe** opens the desktop application. Sign in with the executive administrator account. The local web server starts from inside the application; use **Web Dashboard** in the sidebar to open the browser dashboard.

No Command Prompt window or separate server process is needed for normal use.

## One-click desktop shortcut

1. Extract the complete ZIP to a permanent folder on your Windows computer.
2. Keep VBCCPAS-Soft.exe, Create-Desktop-Shortcut.ps1, and Setup-VBCCPAS-Shortcut.bat together in that folder.
3. Double-click Setup-VBCCPAS-Shortcut.bat once. It creates a **VBCCPAS Soft** shortcut on your desktop and launches the application.
4. After setup, double-click the desktop shortcut whenever you want to launch VBCCPAS Soft.

Do not move or delete the extracted application folder after creating the shortcut; the shortcut points to the EXE in that folder. The EXE can also be opened directly.

## Build from source

On Windows, install **Python 3.11** with the Python launcher (`py`). Node.js is recommended for the dashboard JavaScript syntax check. From PowerShell, run:

    .\build_exes.ps1

The build script installs the Python dependencies, stamps the current Git commit into the executable, runs Python compilation, passkey recovery tests, updater download/package tests, data-flow safety checks, PowerShell syntax validation, an interrupted-update rollback test, and a Windows end-to-end updater simulation. The Windows simulation checks normal application closure, waiting for the old process, ZIP download/integrity verification, replacement, restart, permission probes, and preservation of society.db, backups, and user-data. The build also verifies every ZIP entry and writes a SHA-256 sidecar. If Node.js is installed, it checks the dashboard JavaScript. The isolated web/desktop integration suite runs in the preceding Linux CI job; it is kept out of the local Windows packaging script because its temporary SQLite-file cleanup currently relies on POSIX file-unlink behavior.

A successful build produces:

- `dist\VBCCPAS-Soft.exe` — the main desktop application
- `dist\VBCCPAS-Updater.exe` — the independent graphical updater
- `dist\updater.ps1` — the safe package installation/rollback script
- `dist\Recover-VBCCPAS.bat` — emergency recovery launcher for interrupted installs or a damaged updater
- `dist\vbccpas-managed-files.json` — the updater's managed-file manifest
- `dist\VBCCPAS-Soft-Windows.zip` — the complete distributable package, including shortcut setup files

The build script does not create a shortcut on the build computer by default. To also create one there, run:

    .\build_exes.ps1 -CreateDesktopShortcut

Distribute the complete `VBCCPAS-Soft-Windows.zip` rather than only the main EXE. Extract it to a permanent folder on the destination computer and run `Setup-VBCCPAS-Shortcut.bat`.

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
