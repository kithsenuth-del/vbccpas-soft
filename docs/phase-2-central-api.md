# Phase 2 — central API and shared accounts

## Implemented foundation

The new `cloud_api` service is separate from the Windows desktop executable.

- FastAPI service with versioned `/v1` routes.
- PostgreSQL production connection through psycopg; SQLite can be used only for isolated tests.
- Individual accounts with `executive_admin`, `officer`, `member`, and `read_only` roles.
- Argon2id password hashing; passwords must be at least 12 characters.
- Random opaque bearer sessions; only SHA-256 token digests are stored server-side.
- Twelve-hour sessions by default, server-side logout revocation, and revocation on account suspension.
- Five failed password attempts trigger a 15-minute lockout.
- One-time 48-hour invitations. The executive administrator can invite officers, members, and read-only users, but cannot delegate executive administrator privileges.
- Executive administrator account listing and account suspension/reactivation.
- Audit events for login, logout, invitations, account creation and status changes.
- One-time bootstrap command for the first cloud executive administrator.
- Dockerfile and Compose stack with a persistent PostgreSQL volume. The API port is bound to host loopback only.

## Initialising the service

From the repository root, install the cloud dependencies:

```sh
python -m pip install -r cloud_api/requirements.txt
```

For deployment, set a unique database password in the host's secret environment. Then start the stack:

```sh
export VBCCPAS_DB_PASSWORD="$(openssl rand -hex 32)"
docker compose -f cloud_api/docker-compose.yml up -d --build
```

The Compose file binds the API to `127.0.0.1:8080`. Put a maintained reverse proxy with HTTPS in front of it. Do not expose port 8080 or PostgreSQL directly to the internet. Use a managed secret store rather than a committed environment file.

Create the first executive administrator once from a one-off container command:

```sh
docker compose -f cloud_api/docker-compose.yml run --rm \
  -e VBCCPAS_BOOTSTRAP_ADMIN_EMAIL='your-admin-email' \
  -e VBCCPAS_BOOTSTRAP_ADMIN_PASSWORD='a-unique-password-of-at-least-12-characters' \
  api python -m cloud_api.bootstrap_admin
```

The bootstrap command refuses to run if any account already exists. It creates the initial tables and first administrator. Remove bootstrap secrets after successful provisioning. Do not reuse the local desktop administrator password.

## API routes

- `GET /v1/health` — service health.
- `POST /v1/auth/login` — sign in and issue a bearer session.
- `POST /v1/auth/logout` — revoke the current session.
- `GET /v1/auth/me` — current account profile.
- `POST /v1/admin/invitations` — issue a one-time account invitation.
- `POST /v1/auth/accept-invitation` — accept an invitation and create an account.
- `GET /v1/admin/accounts` — list accounts.
- `PATCH /v1/admin/accounts/{account_id}/status` — suspend or reactivate an account.

The invitation endpoint returns the raw invitation token once so the administrator can deliver it through a trusted channel. Do not post invitation tokens in public messages.

## Tests

The central API tests use a disposable in-memory SQLite database. They do not connect to or alter the society's existing local database.

```sh
python -m unittest discover -s tests -p 'test_cloud_api.py' -v
```

CI compiles the service and runs its account lifecycle tests on pull requests.

## Important limitations

**This is not yet a live cloud deployment and does not yet synchronise society records.** The desktop app has not been connected to this service. Existing local passwords, passkeys, and `society.db` are not uploaded or modified. Do not treat the API as production-ready until deployed behind HTTPS, configured with managed secrets, database backups and restore tests, and independently security-reviewed.

The next phase must implement device registration and local-first record synchronisation: change capture, authenticated sync batches, stable UUIDs, record versions, tombstones, idempotent retries, and conflict resolution. An explicit first-dataset import must be reviewed before any existing society records are uploaded.
