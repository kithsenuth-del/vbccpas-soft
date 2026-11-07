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

On Windows, install Python 3.10+ and the Python launcher (py), then run in PowerShell:

    .\build_exes.ps1

The script builds dist\VBCCPAS-Soft.exe, copies the shortcut setup files into dist, and creates a desktop shortcut on the build computer. Distribute the entire dist folder (or ZIP its contents) so other computers receive the shortcut setup files too.

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
- The updater replaces only the application executable after the app closes. It does not include or overwrite `society.db`, backups, or passkey data.
- New updates are published automatically when changes are pushed to the `main` branch and the Windows build and integration checks pass.

## Data storage

The existing society.db remains beside the application executable. Keep the application in a folder where the signed-in Windows user has read/write permission. Back up the database regularly and store backups securely.

## Developer verification

    python -m pip install -r requirements.txt
    python -m py_compile main.py desktop_app.py server.py passkey_auth.py problem_checker.py
    python test_integration.py
    python verify_data_flow.py
    python check_frontend.py

Integration tests use disposable temporary databases. The Windows GUI and Windows Hello ceremony still require testing on a Windows computer.

## Security boundary

This is a single-computer application, not a network-hosted multi-user service. Keep the browser dashboard bound to 127.0.0.1 / localhost. Never expose port 8000 to the LAN or public internet.
