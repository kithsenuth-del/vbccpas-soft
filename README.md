# VBCCPAS Soft

Local society management system for VBCCPAS.

## Run the local web application

Use Python 3.10 or newer.

```bash
python -m pip install -r requirements.txt
python run_server.py
```

Open **http://localhost:8000** on the same computer. Do not use the computer's LAN IP, expose port 8000 to a network, or publish this server to the internet. The application deliberately accepts only the local browser origin.

## First-time executive administrator setup

1. Start the server from a terminal.
2. On first run, the terminal prints a one-time setup token. Keep it private.
3. Open `http://localhost:8000` on this computer.
4. Paste the token into the setup screen and choose the built-in platform authenticator (for example, Windows Hello).
5. Complete the operating-system user verification prompt.

The web dashboard has one executive administrator and one device-bound passkey. Public account creation and web user-management endpoints are disabled. Synced/backed-up credentials are rejected. The passkey's private key remains with the computer's platform authenticator; only its public credential and verification metadata are stored in the local `society.db` file. No passkey private key is uploaded to this application.

The web login uses an in-memory, eight-hour session and a CSRF token. Restarting the server invalidates active sessions. Sign out when finished.

## Local data and backups

The database is stored beside the application. Back up `society.db` securely and keep access to the computer's operating-system account protected. A database backup does **not** back up the passkey private key. If the Windows profile or device-bound authenticator is lost, the passkey must be re-enrolled through a controlled local recovery procedure.

## Desktop application

Run `python main.py` for the separate Tkinter desktop application. Its local sign-in remains password-based and is restricted to administrator-role accounts; the browser dashboard uses the passkey flow described above.

## Verification

Run `python verify_data_flow.py` to check that unauthenticated requests are rejected and that the test does not touch the real society database. The test uses a temporary SQLite database. GitHub Actions also compiles the Python modules and checks the dashboard JavaScript syntax.

## Security boundary

This is a single-computer application, not a multi-user or network-hosted service. Keep it bound to loopback. Do not expose it to a LAN or the public internet.
