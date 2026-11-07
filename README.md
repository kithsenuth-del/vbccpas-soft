# VBCCPAS Soft

VBCCPAS Soft is a local society-management application for Windows. The unified desktop application contains the existing desktop management modules and can start/open the local browser dashboard from the same interface.

## Run the application (Windows)

The recommended experience is the packaged **VBCCPAS-Soft.exe**. Double-click it to open the desktop application. Sign in with the executive administrator account. The local web server starts from inside the application; use **Web Dashboard** in the sidebar to open the browser dashboard.

No Command Prompt window or separate server process is needed for normal use.

## Build the Windows application from source

Install Python 3.10 or newer, then run the following from PowerShell in the project folder:

```powershell
.\build_exes.ps1
```

The packaged application is created at `dist\VBCCPAS-Soft.exe`. The build script installs the required build tools.

## First-time administrator setup

On a new database, the desktop interface displays a randomly generated administrator password once. Save it securely and sign in with username `admin`. Change the password from the Security section.

On first successful desktop login, the integrated local web server initializes. If a browser passkey has not yet been registered, the desktop interface displays the one-time passkey setup token with a Copy button. Open **Web Dashboard**, paste the token into the local setup page, and register the computer's platform authenticator (for example, Windows Hello). Keep the token private.

The web dashboard uses a device-bound passkey and an in-memory eight-hour session. The passkey private key remains with the platform authenticator. The local web server binds only to loopback and must not be exposed to a LAN or the public internet.

## Built-in maintenance

- **Maintenance & Backups → Run Problem Checker** runs safe application and database checks from the desktop interface.
- **Back Up Database** creates a consistent SQLite backup using SQLite's backup API.
- The problem checker is read-only. It does not repair, edit, or delete application records.
- A database backup does not back up the passkey private key.

## Data storage

The existing `society.db` remains beside the application executable. Keep the application in a folder where the signed-in Windows user has read/write permission. Back up the database regularly and store backups securely.

## Developer verification

```bash
python -m pip install -r requirements.txt
python -m py_compile main.py desktop_app.py server.py passkey_auth.py problem_checker.py
python test_integration.py
python verify_data_flow.py
python check_frontend.py
```

The integration tests use disposable temporary databases. The Windows GUI itself and Windows Hello ceremony still require testing on a Windows computer.

## Security boundary

This is a single-computer application, not a network-hosted multi-user service. Keep the browser dashboard bound to `127.0.0.1` / `localhost`. Never expose port 8000 to the LAN or public internet.
