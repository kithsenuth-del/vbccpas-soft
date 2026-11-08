# Phase 3 — local-first synchronisation foundation

## What is included

Phase 3 adds the server protocol and local database primitives needed for offline-first multi-computer use. It deliberately does **not** turn on synchronisation in the desktop application or import any existing society data.

### Central API

New endpoints in `cloud_api/app.py`:

- `POST /v1/devices` — register a device for the signed-in account.
- `GET /v1/devices` — list the account's registered devices.
- `DELETE /v1/devices/{device_id}` — revoke one of the account's devices.
- `POST /v1/sync/push` — submit up to 100 local changes.
- `GET /v1/sync/pull?device_id=...&cursor=...` — retrieve ordered server changes.
- `GET /v1/sync/status?device_id=...` — check the server's latest change sequence.

The service stores device registrations, current record snapshots, an append-only change feed, and idempotency receipts in PostgreSQL.

### Synchronisation safeguards

- Only the five society data entities are accepted: Members, Events, Inventory, BorrowedItems and Attendance. User accounts, passwords, passkeys, sessions and audit records are not sync entities.
- Payload fields are allowlisted per entity. Individual payloads are limited to 256 KB; batches are limited to 100 changes.
- Only executive administrators and officers can push changes. Executive administrators, officers and read-only accounts can pull. Member accounts are denied shared-record reads until a safe member-to-record access mapping exists.
- Every record has a stable UUID and a server version. A push must include the expected base version.
- Repeated change IDs with identical content are acknowledged idempotently. Reuse with different content or from another device is rejected.
- A stale base version returns a conflict and the current server copy; the server does not silently overwrite it.
- Deletes are transmitted as tombstones, not hard deletes.
- Device registrations are account-bound, limited to ten active devices per account, and individually revocable.

### Local database migration 2

Migration 1 already adds stable record UUIDs and sync bookkeeping. Migration 2 adds stable UUID references to linked records:

- `BorrowedItems.MemberUUID` and `BorrowedItems.ItemUUID`
- `Attendance.MemberUUID` and `Attendance.EventUUID`

The original integer primary keys and foreign-key values are preserved. The new UUID references are populated by joining to the existing records. If a linked record is missing, migration 2 refuses to proceed and rolls back.

**Test on a copy first.** These commands are explicit and do not run at desktop startup:

```sh
python database_migrations.py --database ./society-test-copy.db --apply-sync-foundation
python database_migrations.py --database ./society-test-copy.db --apply-relationship-uuids
```

Both operations create and verify a backup before changing the selected database. Do not point these commands at the only copy of a live society database.

### Local sync engine

`local_sync.py` provides reusable operations for:

- coalescing pending local record snapshots into the SQLite outbox;
- acknowledging only the exact snapshot sent, and rebasing newer edits made while a request was in flight;
- saving server-version conflicts for manual resolution;
- applying ordered remote changes without creating an outbound echo;
- resolving related Member, Event and Inventory UUIDs into each computer's own integer IDs;
- applying soft-delete tombstones and persisting the pull cursor.

The local engine is not yet wired into the desktop CRUD screens or an HTTP client. The desktop app continues to work offline exactly as before.

## Tests

Run the focused suites:

```sh
python -m unittest discover -s tests -p 'test_database_migrations.py' -v
python -m unittest discover -s tests -p 'test_local_sync.py' -v
python -m unittest discover -s tests -p 'test_cloud_api.py' -v
```

Tests use disposable SQLite databases and the in-memory API test database. They do not touch the actual `society.db`.

## Not yet enabled

- No live cloud service has been deployed.
- The desktop application does not yet sign into the cloud service or sync automatically.
- No existing local records, local login credentials or passkeys are uploaded.
- No initial society dataset has been selected as authoritative.
- No UI for resolving conflicts or displaying sync status has been added.

The next implementation step is desktop integration: explicit device sign-in, protected token storage, a background/manual sync coordinator, safe filtering of tombstoned rows, conflict-resolution screens, and a first-dataset review/import wizard. That step must be tested with two disposable computer databases before real society records are considered.
