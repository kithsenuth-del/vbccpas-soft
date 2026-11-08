# Phase 1 — database migration safety

VBCCPAS Soft currently uses a local SQLite database. This phase introduces an
explicit, testable migration foundation without changing application startup
or touching the live database automatically.

## Current findings

- `main.py` creates and uses `society.db` beside the executable.
- The desktop application and local browser dashboard share that database.
- `server.py` is loopback-only; the current app is not a multi-computer service.
- Existing application tables include Users, Members, Events, Inventory,
  BorrowedItems and Attendance.
- Some legacy conversion routines in `main.py` rename and drop source tables.
  They need a separate compatibility review before they can be considered safe
  for every historical schema.

## New migration foundation

`database_migrations.py` provides:

- Read-only database inventory (integrity, table names, columns and row counts).
- SQLite online backup using the SQLite backup API.
- Backup verification against source integrity, schemas and row counts.
- Explicit migration 1 adding stable UUIDs and version/tombstone metadata to
  Members, Events, Inventory, BorrowedItems and Attendance.
- Local SyncOutbox, SyncState and SyncConflicts tables.
- A SchemaMigrations ledger and idempotent execution.

The migration retains existing integer primary keys and all existing columns.
It does not migrate user password hashes or passkey credentials to a server.
That requires a separately designed account migration.

## Inspect and test

Install the existing dependencies, then run:

```powershell
python database_migrations.py --database .\society.db
python database_migrations.py --database .\society.db --backup-only
python -m unittest tests.test_database_migrations -v
```

To apply migration 1, use a disposable copy first:

```powershell
python database_migrations.py --database .\test-copy\society.db --apply-sync-foundation
```

A verified backup is created in `database_backups` beside the database unless
a backup directory is supplied by the Python API.

## Important safety boundary

Migration 1 is intentionally not called from application startup. Do not run
it against the only copy of a production database. Do not copy a live SQLite
file between computers. The next step is to test this migration against
representative legacy database copies, review the legacy schema conversions,
and only then integrate a controlled upgrade path.
