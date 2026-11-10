# Phase D — Final Windows Release Readiness

## Release gate

Phase D may prepare the release process, but the final package must not be published until Phase C is complete and accepted:

- Administrator password + five-digit PIN sign-in, one-time recovery, and lockout tests pass.
- Verified SQLite backup and restore pass on Windows.
- The standalone updater reliability acceptance criteria in issue #40 are met.
- The final Phase A/B modules pass regression tests without changing existing society records.

## Windows-only release matrix

| Area | Required verification |
| --- | --- |
| Clean installation | Install and launch on a disposable Windows 10/11 machine. |
| Upgrade | Update a prior supported installation without replacing user data. |
| Paths | Test a permanent folder containing spaces and a OneDrive-synced folder. |
| Branding | Confirm the supplied VBCCPAS society logo is used for the app icon and shortcut; no Python icon remains. |
| First run | Create the administrator password, five-digit additional PIN, and save the one-time recovery code. |
| Recovery | Test password/PIN recovery, single-use code behavior, and temporary lockout. |
| Database | Back up and restore a disposable database; verify records and SQLite integrity before/after. |
| Updater | Test update, repair, interrupted replacement, rollback, restart health check, and checksum validation. |
| Data protection | Confirm society.db, SQLite WAL/SHM files, backups, passkeys, exports, logs and user-data folders are preserved. |
| Offline use | Confirm core desktop modules remain usable without internet access. |
| Package | Verify the Windows executable/package contents, manifest, SHA-256 sidecar and release metadata. |

## Test rules

- Use disposable databases and test installations only.
- Never perform restore, migration, or updater fault-injection tests against the only copy of live society records.
- Do not publish draft releases or offer unverified packages through the updater.
- This release is Windows-only; no Linux or Ubuntu build is required.
- Cloud synchronisation remains deferred until it is separately prioritised and fully tested.

## Completion evidence

Attach the final CI run links, Windows clean-install and upgrade results, updater/rollback results, package checksum verification, and a short operator recovery guide before marking Phase D complete.
