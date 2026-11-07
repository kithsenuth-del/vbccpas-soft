# VBCCPAS Soft

Society management system for VBCCPAS.

## Local setup

- Run `python main.py` to start the desktop application.
- Run `python run_server.py` to serve the local web interface at `http://127.0.0.1:8000`.
- The web server binds to loopback only. Do not expose it directly to a LAN or the public internet.

## Administrator account

For a new installation, set the `VBCCPAS_ADMIN_PASSWORD` environment variable before the first launch to choose the initial administrator password. If it is not set, the app generates a unique one and prints it to the launching terminal; save it securely. Existing installations retain their current credentials, so change any old shared/default password immediately using the Security screen.

New passwords must be at least 10 characters. Passwords are stored using salted PBKDF2-SHA256. Existing legacy SHA-256 password hashes are upgraded after a successful desktop login.

## Verification

Run `python verify_data_flow.py` to exercise the summary, member, event, and inventory API paths. The test uses a temporary SQLite database and does not write to the real `society.db`.

## Security scope

The local API rejects cross-origin browser requests, validates JSON request sizes and content types, restricts static-file paths to the application directory, and escapes database values before rendering them in the web interface. The web API still does not provide per-user login/session authorization; keep it on a trusted local machine only. Add authenticated sessions and role-based API authorization before any multi-user or network deployment.
