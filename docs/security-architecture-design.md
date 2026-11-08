# VBCCPAS Soft — Security Architecture Design

**Status:** Design baseline only. This document does not claim that the controls below are implemented or that the application is production-secure.

**Scope:** Windows desktop application, local browser dashboard, optional central account API, and the future offline-first synchronisation service. Normal desktop operation must remain available offline. Existing society records, local credentials, and passkeys must not be uploaded, reset, or rewritten as part of security work without an explicit, backed-up migration.

## 1. Security objectives

1. Protect society member, event, attendance, borrowing, and inventory records from unauthorised access or alteration.
2. Keep administrator credentials, passkey material, session tokens, invitation tokens, and deployment secrets out of source control, logs, sync payloads, and distributable packages.
3. Make every privileged action attributable to an authenticated account and auditable without logging passwords, tokens, or unnecessary personal data.
4. Preserve offline operation and local data ownership. Network loss must not make local records inaccessible or trigger destructive reconciliation.
5. Make updates verifiable, recoverable, and restricted to an explicit application-managed file allowlist.
6. Fail closed when identity, authorisation, integrity, or tenant ownership cannot be established.

## 2. Critical findings to resolve before enabling cloud sync

### P0 — Shared sync data is not tenant-scoped

The current central schema stores `SyncRecord` and `SyncEvent` without an organisation/society identifier. The push and pull paths authenticate an account and device, but the record/event lookup and pull feed are global. As written, separate customer societies could collide on entity UUIDs or receive one another's records. **Do not deploy or enable sync for multiple societies until tenant isolation is designed, migrated, enforced in every query/unique constraint, and covered by cross-tenant denial tests.**

Required design:
- Introduce an immutable `organisation_id` (tenant ID) and explicit account membership/role mapping.
- Bind every device, sync record, sync event, receipt, invitation, and relevant audit event to the tenant.
- Include tenant ID in uniqueness constraints and all read/write predicates.
- Derive tenant scope from the authenticated membership, never trust a client-supplied tenant ID alone.
- Reject cross-tenant device use, UUID collisions, push, pull, status, and administrative operations.
- Add tests with two organisations and deliberately reused entity UUIDs proving zero cross-tenant visibility or mutation.

### P0 — Weak first-run desktop credential

The desktop database bootstrap currently falls back to the known password `admin123` when no environment override is supplied. A predictable shared default is not suitable for a distributable application.

Required design:
- On a fresh database, generate a cryptographically random, one-time initial credential and show it only once in the trusted local setup flow.
- Require the administrator to replace it before normal use; never ship a universal password.
- Store only a salted, adaptive password hash. Keep legacy-hash migration, but do not weaken the new credential policy.
- Add a recovery procedure that verifies the operator, makes a verified database backup, and never deletes society records.
- Do not print bootstrap credentials or passkey setup tokens into persistent logs.

### P1 — Authentication throttling and account lifecycle

The cloud API has per-account lockout after repeated failures, but no explicit IP/device throttling or general request-rate policy. Unknown-account attempts do not follow the same persisted failure path. The local desktop password check also has no login throttle.

Required design:
- Add bounded per-account and per-source throttling at the API edge; use a shared limiter store for multiple API workers.
- Keep generic login errors and avoid account-existence disclosure. Use a safe dummy password-hash verification path for unknown accounts to reduce timing differences.
- Use progressive delay/temporary lockout with safe recovery; avoid permanent lockouts that let attackers deny service.
- Audit successful/failed sign-ins, lockouts, password changes, recovery, and administrator actions.
- Test concurrency, lockout expiry, unknown accounts, and recovery paths.

## 3. Identity, authentication, and authorisation

### Local Windows application
- Executive-administrator access is distinct from ordinary society membership.
- New passwords: minimum 12 characters, maximum 256; allow passphrases and password-manager generated values.
- Use Argon2id where supported for new password hashes; if PBKDF2 remains for compatibility, document and benchmark its parameters and migrate hashes after successful authentication.
- Compare secrets using constant-time comparison where applicable.
- Add login throttling and a deliberate password-change/recovery workflow.
- Never silently create a known default password on an existing database.

### Local browser dashboard / passkeys
- Keep the dashboard bound to loopback only. Do not expose the local service on LAN or public interfaces.
- Keep WebAuthn RP ID, origin, and expected host/port exact and consistent. Reject unexpected Host, Origin, Fetch Metadata, and non-loopback client addresses.
- Keep short-lived, single-use challenges; expire and consume them on every completion attempt.
- Require user verification. Validate challenge, origin, RP ID, credential ownership, and authenticator properties on every ceremony.
- Review the current device-bound-only passkey rule against the real recovery model. Do not silently weaken it; document how a lost Windows device is recovered without losing society records.
- Session cookies: HttpOnly, Secure, SameSite=Strict, host-only, random, short-lived; rotate on authentication and invalidate on logout/expiry. CSRF tokens are required for state-changing requests.
- Add a session revocation mechanism and test that restart, logout, expiry, and recovery invalidate the right sessions.

### Central API
- Keep Argon2id password hashing and opaque high-entropy bearer tokens; store only token digests.
- Enforce role checks at every route and object-level ownership checks for every account/device/resource.
- Define permission matrix for executive administrator, officer, read-only, and member roles. Default deny.
- Use short session lifetimes, revocation, and bounded concurrent sessions. Consider refresh-token rotation only if a documented need exists.
- Prevent role escalation through invitation fields, account status endpoints, or sync payloads.
- Require MFA/strong administrator authentication before production deployment.

## 4. Data isolation, privacy, and database safety

- Keep local database and backups in a per-user Windows data directory with restrictive ACLs; avoid relying on a writable application install folder such as Program Files.
- Use SQLite parameterised queries, foreign-key enforcement, transactions, and consistent backup API.
- Apply schema migrations only after a verified backup. Migration failure must roll back and leave the previous database usable.
- Never synchronise Users, Passwords, Passkeys, sessions, tokens, audit internals, or local security metadata.
- Allowlist sync entities and fields on both client and server. Validate types, lengths, nested structures, and total payload sizes.
- Use tenant-scoped versioning, idempotency receipts, append-only change events, tombstones, and explicit conflict resolution.
- Do not let a stale device overwrite a newer record. Deletions must remain recoverable under a documented retention policy.
- Encrypt network traffic with HTTPS/TLS. At rest, protect server database volumes and Windows backups using OS/device encryption; document that application-level encryption is not a substitute for access controls.
- Minimise personal data in logs and define backup retention, deletion, and incident response procedures.

## 5. API and browser hardening

- Keep OpenAPI/docs disabled in production unless protected and intentionally enabled.
- Add request size limits, strict JSON content-type checks, schema validation, timeouts, and safe error responses.
- Configure an explicit trusted HTTPS reverse proxy. Do not trust arbitrary forwarded headers or expose the app server/PostgreSQL directly.
- Add production CORS policy only if a browser client requires it; allow exact origins, never wildcard credentials.
- Add security headers and a restrictive Content Security Policy. Remove inline script allowances where practical by moving scripts to bundled files/nonces.
- Use dependency pinning and automated vulnerability/dependency scans. Review upgrades before packaging.
- Do not expose debug traces, SQL errors, filesystem paths, secrets, or raw tokens in responses.

## 6. Updates and software supply chain

- The updater must accept only the official repository/release and a complete Windows package for the expected product.
- Verify HTTPS, release metadata, SHA-256 asset digest, package structure, managed-file manifest, and each extracted file before replacement.
- Treat a checksum as corruption detection, not publisher authentication. Plan signed release manifests/packages with a separately protected signing key and verify signatures before install.
- Never overwrite the database, backups, exports, logs, user configuration, or other protected data.
- Close the application, wait for process exit, stage the package, journal every replacement, and atomically promote where possible.
- On failure or interruption, restore the prior managed files and prove the database remains unchanged.
- Publish only after all required Windows build, package, updater, and recovery checks succeed. A failed or incomplete run must never become the latest release.
- Keep signing keys and release credentials in protected GitHub Actions secrets/environments with least privilege; do not place secrets in repository files or build artifacts.

## 7. Audit, monitoring, and incident response

Record security-relevant events: login success/failure, lockout, password/passkey changes, recovery, account invitation/activation/suspension, device registration/revocation, sync conflict, privileged record change, backup/restore, update start/success/failure/rollback.

Audit records should include timestamp, actor, action, target identifier, outcome, and a correlation ID. Avoid storing passwords, bearer tokens, invitation tokens, passkey challenges, or full sensitive record payloads. Protect logs against casual modification and define retention and export procedures.

Prepare a concise incident procedure: disable/revoke affected accounts and devices, preserve logs, stop sync if tenant isolation is in doubt, restore from a verified backup, rotate compromised secrets, and notify the responsible society administrator.

## 8. Windows-specific controls

- Support Windows 10/11 only, matching the project's distribution target.
- Use per-user data storage under the user's profile and restrictive ACLs. Avoid requiring permanent administrator privileges.
- Do not request elevation for ordinary application use or updates unless a specific protected install location requires it.
- Keep the updater separate from the running application; validate paths and reject traversal, reparse-point/symlink escapes, unexpected files, and manifest paths outside the installation root.
- Verify Authenticode signatures for signed binaries once code signing is configured.
- Test standard-user installs, Windows Defender/SmartScreen behavior, locked files, low disk space, interrupted downloads, restart recovery, and database preservation on real Windows runners.

## 9. Security test gates

A release must not be called security-ready until these gates pass:

1. Unit tests for password hashing, constant-time comparisons, login throttling, lockout, session expiry/revocation, invitation one-time use, and role permissions.
2. WebAuthn negative tests for wrong origin/RP ID/challenge, replayed/expired challenge, unknown credential, and invalid user verification.
3. API tests for unauthenticated access, privilege escalation, object ownership, malformed payloads, size limits, rate limits, and safe errors.
4. Two-tenant isolation tests for every sync operation, including duplicate UUIDs and device IDs.
5. Two-device offline/online tests for idempotency, conflicts, tombstones, cursor replay, and interrupted sync.
6. Database migration/backup/restore tests on disposable copies of realistic schemas; verify row counts and relationships before/after.
7. Windows packaging and updater tests for tampered package, wrong manifest, path traversal, locked executable, failed health check, rollback, and preservation of user data.
8. Dependency and secret scanning, plus independent review of the threat model and deployment configuration.

## 10. Implementation sequence

**Phase A — Blockers and baseline**
- Remove known default credentials from fresh setup.
- Fix and test tenant isolation before any multi-society cloud sync.
- Add threat model, role matrix, security tests, and secrets scan.

**Phase B — Local Windows security**
- Harden local login throttling and recovery.
- Review passkey setup/recovery, local session lifecycle, Windows data-directory ACLs, and local server binding.
- Add audit coverage without sensitive values.

**Phase C — Central API security**
- Add tenant membership model, object-level checks, rate limits, hardened proxy/TLS configuration, and admin MFA.
- Add backup/restore and operational monitoring before deployment.

**Phase D — Sync and updater assurance**
- Enable sync only after cross-tenant tests and two-computer acceptance.
- Add signed release verification and complete Windows updater fault-injection/recovery tests.

## 11. Explicit non-goals and safety constraints

- No remote access to the local desktop database by opening port 8000.
- No automatic upload of existing society data, passwords, passkeys, or backups.
- No destructive migration or reset of live records during security implementation.
- No claims of encryption, signing, tenant isolation, or production readiness until the implementation and tests prove them.
- Linux/Ubuntu packaging is out of scope; Windows is the supported desktop target.

## 12. Definition of done

The design is complete when the threat model, role/permission matrix, data-flow boundaries, P0 blockers, implementation phases, and release gates are reviewed and agreed. The implementation is complete only when code changes land, automated tests pass, real Windows verification is recorded, and unresolved risks are disclosed.
