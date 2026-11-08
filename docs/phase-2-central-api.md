# Phase 2 — central API and shared accounts

## Scope

This phase introduces a separately deployable central service for shared user accounts. It does not upload, replace, or migrate the existing desktop `society.db`. The desktop application remains local-only until a later client integration phase.

Planned capabilities:

- Versioned HTTPS API backed by PostgreSQL.
- Individual accounts with executive administrator, officer, member, and read-only roles.
- Strong password hashing, short-lived revocable sessions, login throttling, and audit events.
- One-time invitations controlled by the executive administrator.
- A one-time bootstrap process for the first cloud administrator.
- Container deployment with persistent database storage and no public database port.

## Safety boundaries

- Never copy local desktop passwords or passkey credentials to the central service.
- Never expose PostgreSQL or an unencrypted API port directly to the internet.
- Keep the current local database and backups unchanged during this phase.
- Do not enable record synchronisation until the later sync protocol has UUIDs, version checks, tombstones, idempotency, and conflict handling.
- Before production deployment, configure TLS, managed secrets, database backups, and restore testing.

## Completion criteria

The central API and account lifecycle must be tested independently using disposable test databases. A future desktop integration phase will add device registration and sign-in without disabling offline access.