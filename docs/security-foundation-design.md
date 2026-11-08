# VBCCPAS Soft — Security Foundation Design

**Status:** Design baseline only; no security controls are changed by this document.  
**Target:** Windows desktop application with required offline operation and optional central account/synchronisation service.  
**Primary rule:** Preserve existing society records and keep offline operation available. Security changes must be additive, migration-safe, and tested against disposable database copies before release.

## 1. Security objectives

1. Protect member, event, attendance, inventory, borrowing and society-administration data from unauthorised access or alteration.
2. Keep the Windows application usable without internet access; never make cloud authentication a prerequisite for local offline use.
3. Prevent a compromised or revoked online account/device from continuing to access the central service.
4. Prevent updates, recovery tools and synchronisation from overwriting or uploading credentials, passkeys, backups or unrelated user files.
5. Make security events understandable to the administrator without writing passwords, tokens, passkey material or sensitive record contents to logs.
6. Fail safely: when authentication, integrity verification, backup or migration checks fail, do not perform destructive work.

## 2. System boundaries and threat model

### Protected assets
- The local `society.db`, its backups and exports.
- Local administrator password hashes and WebAuthn credential public keys.
- Cloud account password hashes, API-session digests, invitations and device registrations.
- Society records and the synchronisation outbox, conflict queue and change feed.
- Update packages, updater journal, rollback copies and installation files.
- Bootstrap credentials, signing material and deployment secrets.

### Trust boundaries
- **Windows device:** local desktop process, local browser dashboard, SQLite database, Windows user profile and filesystem.
- **Local HTTP interface:** loopback-only server and browser. It is not a LAN service.
- **Central service:** FastAPI API, PostgreSQL, reverse proxy and deployment secret store.
- **Update channel:** GitHub release metadata and downloadable Windows package.
- **Synchronisation:** an authenticated device submits or retrieves allowlisted society-record changes.

### Threats to address
Unauthorised local use; weak/default passwords; repeated password guessing; stolen sessions; browser-origin/CSRF abuse; malicious or malformed API input; cross-account or cross-society data access; a revoked device continuing to sync; leaked invitation or bootstrap tokens; database theft; accidental data loss during migration/update; tampered update packages; and secrets accidentally committed to Git or emitted in logs.

The design does not claim to defeat malware or a Windows administrator who already controls the computer. Those risks require endpoint protection, Windows account security and full-disk encryption.

## 3. Findings in the current repository to resolve

These are design-review findings from the current `main` source, not claims that an exploit has been demonstrated.

| Priority | Current condition | Required treatment |
|---|---|---|
| Critical | Fresh local databases fall back to the known password `admin123` when `VBCCPAS_ADMIN_PASSWORD` is absent. | Remove the built-in password fallback. Generate a cryptographically random one-time setup secret, show it once, require an immediate administrator-chosen password, and never log or commit the secret. Existing databases must not be silently reset. |
| High | Local desktop password verification has no persistent failed-attempt throttling and distinguishes unknown usernames from incorrect passwords. | Use a generic failure message and persistent, carefully bounded throttling; avoid a permanent lockout that lets another person deny service to the administrator. |
| High | The local passkey bootstrap token is printed to standard output during first setup. | Display it only in a controlled one-time setup surface; redact logs and invalidate it immediately after successful registration or expiry. |
| High | The local browser service uses an in-memory session map. Sessions disappear on restart, but there is no explicit idle timeout or session rotation policy. | Keep sessions short-lived, add idle expiry, rotate on authentication and privilege changes, and ensure logout/recovery invalidates sessions. |
| High | Cloud login has account-level lockout but no visible IP/device rate limiter; a locked known account receives a different status from an unknown account. | Add edge and application throttling, generic public authentication errors, monitoring and a safe recovery path. |
| High | Cloud sync records/events are not visibly scoped by a society/tenant identifier. | Before multi-society deployment, add tenant ownership to accounts/devices/records/events and enforce it in every read/write query. Never rely on a client-supplied tenant ID alone. |
| High | Cloud API deployment depends on a correctly configured HTTPS reverse proxy; the application itself does not establish public TLS. | Make HTTPS, trusted-proxy configuration, secure headers and production configuration checks deployment gates. Never expose the API or database directly. |
| Medium | Audit events cover important account actions but do not yet define a consistent event schema, retention policy or redaction standard. | Add structured audit events for security-sensitive actions while excluding credentials, raw tokens and society-record contents. |
| Medium | Local SQLite data is stored beside the application executable. | Move toward a per-user data directory with restrictive Windows ACLs. Keep migration/backups and existing-data discovery safe; do not move or delete the current database without verified backup and user-visible confirmation. |
| Medium | The online API and local application have separate authentication systems. | Keep the boundary explicit. Do not silently reuse local passwords/passkeys as cloud credentials or upload local credential tables. |

## 4. Target security architecture

### A. First-run and local administrator access
- Fresh install creates a random, one-time bootstrap credential using the operating system cryptographic random source.
- The bootstrap value is displayed once in the trusted desktop setup flow, is never written to logs, and expires after a short setup window.
- The administrator must create a strong unique password before accessing administration features; no shared default is permitted.
- Existing installations retain their current account and passkey records. A migration must detect legacy hashes and upgrade them only after a successful login; it must never overwrite a working credential.
- Use a generic sign-in failure message, persistent throttling with progressive delays, and a safe administrator recovery process that requires a verified database backup.
- Add explicit lock, logout and inactivity timeout behaviour. Clear sensitive UI fields when the session ends.

### B. Local passkey and browser session
- Retain WebAuthn challenge verification, expected RP ID/origin checks, user verification and single-use short-lived challenges.
- Bind the HTTP server exclusively to loopback addresses. Validate Host, Origin and Fetch Metadata on every request; reject non-loopback clients.
- Keep CSRF protection on state-changing browser requests. Keep cookies HttpOnly, Secure and SameSite=Strict; never expose session identifiers to JavaScript.
- Add idle expiry and absolute expiry; rotate the session after login and any re-authentication. Invalidate sessions on logout, passkey reset and recovery.
- Replace console bootstrap-token output with a one-time desktop-controlled setup handoff. Redact authentication-related diagnostics.
- Review the Content Security Policy and remove inline script/style allowances where practical by using nonces or external assets.

### C. Windows data and secret protection
- Store application data under a dedicated per-user application-data directory with Windows ACLs granting access only to the intended Windows user and SYSTEM/administrators as required.
- Preserve discovery and migration of existing `society.db` beside the current installation. Copy via SQLite's backup API, verify integrity and row counts, and retain the source until the new location is verified.
- Protect any locally cached cloud refresh/access credentials with Windows DPAPI (CurrentUser scope); never store raw bearer tokens in source files, JSON settings or logs.
- Recommend and document Windows Device Encryption/BitLocker for full-disk protection. Do not invent custom database encryption. Evaluate SQLCipher only as a separately tested migration with recovery and compatibility planning.
- Backups should be encrypted/protected at rest, clearly labelled, and restorable using a documented recovery procedure. A backup must never contain an export of passkey private keys.

### D. Central API and account security
- Enforce HTTPS at the reverse proxy; reject insecure production configuration. Trust forwarded headers only from explicitly configured proxy addresses.
- Use Argon2id for passwords, validate password length/size, and keep generic login errors. Add rate limits by source and account identifier at both proxy and application layers.
- Keep opaque high-entropy session tokens; store only digests server-side. Add session listing/revocation, absolute and idle expiry, and revocation on password reset, account suspension and role change.
- Add a password-change/reset workflow with one-time, short-lived, hashed reset tokens delivered through a verified channel. Do not expose reset tokens in logs.
- Restrict invitation roles to the supported non-admin roles; keep invitation tokens single-use, short-lived and stored only as digests.
- Validate all request sizes, content types, identifiers, enum values and payload fields. Apply request timeouts and bounded database work.
- Add a tenant/society boundary before shared data is enabled for multiple societies. Every account, device, sync record, event, receipt and audit record must be scoped to a tenant. Cross-tenant tests are release blockers.
- Keep database credentials in a managed secret store/environment injection, rotate them, and never commit production secrets. Use least-privilege database credentials and automated encrypted backups.

### E. Device and synchronisation security
- A device registration belongs to one account and one tenant. Device revocation must immediately block push, pull and status endpoints.
- Store only the minimum device metadata; do not treat a user-supplied device name as proof of identity.
- Keep the entity and field allowlists. Explicitly reject credential, password, passkey, session, audit, bootstrap and local filesystem fields from sync payloads.
- Apply per-record and per-batch size limits, idempotency receipts, version checks and conflict handling. Bound cursor values and page sizes.
- Authorise every operation on the server; never trust client-side role checks. Ensure read-only accounts cannot push and member accounts cannot read shared records without an explicit access mapping.
- Add replay, cross-account, cross-tenant, revoked-device, duplicate-change and oversized-payload tests.
- Keep sync disabled in the desktop UI until the account/device workflow, secure token storage, tenant model and two-computer tests are complete. Offline local CRUD must continue to work.

### F. Update and recovery security
- Accept update packages only from the configured official repository/release channel over HTTPS.
- Verify the release metadata, asset identity, SHA-256 digest and package manifest before extracting or replacing files. A digest detects corruption; a signed manifest/signature with a protected signing key is the stronger future authenticity control.
- Reject path traversal, absolute paths, unexpected files, duplicate paths, reparse-point escapes and writes outside the installation root.
- Maintain an explicit managed-file allowlist. Never overwrite the database, backups, exports, logs, local settings, credentials or user-data directories.
- Create and verify rollback copies before replacement. Write a durable journal before each file operation; recover deterministically after interruption.
- Verify installed hashes and application startup health before marking an update complete. On failure, roll back and report the exact recovery status.
- Do not elevate privileges silently. Explain when a Windows permission change is required and keep the normal user installation path writable by that user.

## 5. Implementation sequence and release gates

### Phase 0 — Security baseline (first implementation)
1. Remove the fresh-install `admin123` fallback and implement one-time first-run credential setup.
2. Add tests for local password policy, generic failure behaviour and throttling.
3. Remove passkey bootstrap-token console logging and add one-time expiry/invalidation.
4. Add session idle expiry, rotation and explicit invalidation.
5. Add a security regression suite that runs on Windows CI and standard Python CI.

**Gate:** existing database fixtures, legacy password upgrade, first-run setup, passkey setup and offline sign-in all pass without modifying production `society.db`.

### Phase 1 — Windows storage and recovery
1. Establish the per-user data directory and restrictive ACLs.
2. Migrate an existing database only after verified backup, integrity check and comparison.
3. Protect locally cached cloud credentials with DPAPI.
4. Add backup restore drills and permission tests for standard Windows users.

**Gate:** old installations retain all society records and remain usable offline; simulated failure restores the original database.

### Phase 2 — Central API hardening
1. Add production HTTPS/proxy validation, rate limiting and generic authentication responses.
2. Add session management, password reset/change and expanded audit logging.
3. Add tenant isolation to the schema and every sync query before multi-society use.
4. Add dependency/security scanning and deployment configuration checks.

**Gate:** cross-tenant and cross-account tests prove isolation; revocation takes effect; no secrets appear in logs or build artifacts.

### Phase 3 — Secure synchronisation enablement
1. Complete Windows DPAPI token storage and explicit device sign-in.
2. Enforce tenant, role, device, field and version checks on every operation.
3. Add two-computer offline/online, replay, conflict and revoked-device tests.
4. Require a reviewed first-dataset import; never automatically upload the existing local database.

**Gate:** two disposable Windows databases synchronise safely, and offline operation remains fully functional.

### Phase 4 — Update-chain hardening
1. Implement strict package/manifest validation and safe extraction.
2. Add protected signing-key management and signed release manifests.
3. Verify installed-file hashes, restart health and rollback behaviour.
4. Test interrupted update, permission denial, damaged download and recovery bootstrap.

**Gate:** no failed or incomplete build can be published as the latest usable Windows release; user data remains untouched in every recovery test.

## 6. Security test matrix

| Area | Required tests |
|---|---|
| First-run | No default password; bootstrap expires; second registration is denied; setup secret never appears in logs. |
| Local login | Wrong and unknown usernames return equivalent public errors; throttling works across restart; valid credentials still work offline. |
| Passkey | Wrong origin/RP ID, expired/replayed challenge, wrong device, missing user verification and revoked/reset credential are rejected. |
| Sessions/CSRF | Expired/idle session, logout, session rotation, missing/incorrect CSRF token and forged Origin are rejected. |
| Filesystem | Standard user can use the app; other local users cannot read protected data where ACLs apply; migration failure leaves source untouched. |
| Cloud accounts | Rate limits, invitation replay/expiry, suspended account, password reset and session revocation are enforced. |
| Tenant/device | Cross-tenant read/write, another user's device ID, revoked device and read-only push are rejected. |
| Sync | Unknown fields, credential fields, oversized payloads, duplicate IDs with altered content, stale versions and invalid cursors are rejected. |
| Updates | Tampered digest, invalid signature, traversal path, unexpected file, locked file, interrupted replacement and failed health check all fail safely. |
| Data safety | All tests use disposable databases; record counts and integrity checks match before/after; no test mutates the live society database. |

## 7. Operational rules

- Keep the Windows desktop app offline-capable. Cloud services are optional enhancements, not a startup dependency.
- Never upload local passwords, passkey data, session data, audit data or the whole SQLite file.
- Never commit API keys, passwords, signing keys, bootstrap tokens, real member data or production database files.
- Keep security logs minimal and redacted; restrict access and define retention before production deployment.
- Run dependency vulnerability checks, secret scanning, static analysis and the full regression suite on every pull request.
- Require human review for schema migrations, tenant-boundary changes, credential recovery and release-signing changes.
- Security work is not complete merely because tests pass: production deployment configuration, Windows packaging, restore drills and an independent review remain required.

## 8. Definition of done

The security foundation is ready for release only when all applicable phases have implementation, automated tests, Windows-specific evidence, migration/rollback evidence, and a reviewed threat-model update. A design document or passing unit tests alone must never be described as a fully secured application.
