"""Non-destructive foundation for VBCCPAS local-first database migrations.

This module is deliberately NOT called automatically by application startup.
Run migrations explicitly after making a verified SQLite backup. Existing
application tables and integer primary keys are retained; stable UUIDs are
added alongside them for future cross-device synchronisation.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIGRATION_VERSION = 1
RELATIONSHIP_MIGRATION_VERSION = 2
SYNC_TABLES = {
    "Members": "MemberID",
    "Events": "EventID",
    "Inventory": "ItemID",
    "BorrowedItems": "BorrowID",
    "Attendance": "AttID",
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def database_inventory(database_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return integrity, table schemas, and row counts without modifying the DB."""
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {}
        names = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (name,) in names:
            quoted = '"' + name.replace('"', '""') + '"'
            count = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            columns = [
                {"name": row[1], "type": row[2], "notnull": bool(row[3]),
                 "pk": int(row[5])}
                for row in conn.execute(f"PRAGMA table_info({quoted})")
            ]
            tables[name] = {"row_count": count, "columns": columns}
        return {
            "database": str(path),
            "integrity": integrity,
            "tables": tables,
            "total_records": sum(item["row_count"] for item in tables.values()),
        }
    finally:
        conn.close()


def create_verified_backup(
    database_path: str | os.PathLike[str],
    backup_directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Create an online SQLite backup and verify its integrity and table counts."""
    source_path = Path(database_path).expanduser().resolve()
    before = database_inventory(source_path)
    if before["integrity"] != "ok":
        raise sqlite3.DatabaseError("Source database failed PRAGMA integrity_check")

    directory = (Path(backup_directory).expanduser().resolve()
                 if backup_directory else source_path.parent / "database_backups")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = directory / f"society-pre-migration-{stamp}-{uuid.uuid4().hex[:8]}.db"
    temporary = destination.with_suffix(".db.tmp")

    source = _connect(source_path)
    target = sqlite3.connect(str(temporary), timeout=30)
    try:
        source.backup(target)
        target.commit()
    finally:
        # sqlite3 connection context managers do not close connections.
        # Explicit close is essential before atomic replacement on Windows.
        target.close()
        source.close()

    try:
        after = database_inventory(temporary)
        if after["integrity"] != "ok" or after["tables"] != before["tables"]:
            raise sqlite3.DatabaseError(
                "Backup verification failed: integrity, schema, or row counts differ"
            )
        os.replace(temporary, destination)
        return destination
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    quoted = '"' + table.replace('"', '""') + '"'
    return {row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")}


def apply_local_sync_foundation(
    database_path: str | os.PathLike[str],
    backup_directory: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Back up, then add stable IDs and local sync bookkeeping in one transaction.

    The migration is idempotent. It never drops, renames, or rewrites existing
    application tables or their existing columns/primary keys.
    """
    path = Path(database_path).expanduser().resolve()
    before = database_inventory(path)
    if before["integrity"] != "ok":
        raise sqlite3.DatabaseError("Refusing to migrate a database with integrity errors")

    backup = create_verified_backup(path, backup_directory)
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS SchemaMigrations ("
            "Version INTEGER PRIMARY KEY, AppliedAt TEXT NOT NULL, Description TEXT NOT NULL)"
        )
        applied = conn.execute(
            "SELECT 1 FROM SchemaMigrations WHERE Version=?", (MIGRATION_VERSION,)
        ).fetchone()
        if applied:
            conn.rollback()
            return {"status": "already_applied", "backup": str(backup),
                    "version": MIGRATION_VERSION}

        missing_tables = sorted(set(SYNC_TABLES) - set(before["tables"]))
        if missing_tables:
            raise sqlite3.DatabaseError(
                "Refusing partial migration; required tables are missing: "
                + ", ".join(missing_tables)
            )

        for table, expected_pk in SYNC_TABLES.items():
            columns = _table_columns(conn, table)
            quoted = '"' + table.replace('"', '""') + '"'
            pk_columns = [row[1] for row in conn.execute(
                f"PRAGMA table_info({quoted})") if row[5]]
            if expected_pk not in pk_columns:
                raise sqlite3.DatabaseError(
                    f"{table} does not have the expected primary key {expected_pk}; "
                    "no changes were committed"
                )
            additions = (
                ("_sync_uuid", "TEXT"),
                ("_sync_version", "INTEGER NOT NULL DEFAULT 1"),
                ("_sync_updated_at", "TEXT"),
                ("_sync_deleted_at", "TEXT"),
            )
            for column, declaration in additions:
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE {quoted} ADD COLUMN {column} {declaration}"
                    )
            rows = conn.execute(
                f"SELECT {expected_pk} FROM {quoted} WHERE _sync_uuid IS NULL OR _sync_uuid=''"
            ).fetchall()
            for (record_id,) in rows:
                conn.execute(
                    f"UPDATE {quoted} SET _sync_uuid=? WHERE {expected_pk}=?",
                    (str(uuid.uuid4()), record_id),
                )
            index_name = f"ux_{table.lower()}_sync_uuid"
            conn.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS "{index_name}" '
                f"ON {quoted} (_sync_uuid)"
            )

        conn.execute(
            "CREATE TABLE IF NOT EXISTS SyncOutbox ("
            "ChangeID TEXT PRIMARY KEY, EntityType TEXT NOT NULL, EntityUUID TEXT NOT NULL, "
            "Operation TEXT NOT NULL CHECK(Operation IN ('upsert','delete')), "
            "Payload TEXT NOT NULL, BaseVersion INTEGER, CreatedAt TEXT NOT NULL, "
            "AttemptCount INTEGER NOT NULL DEFAULT 0, LastError TEXT, "
            "Status TEXT NOT NULL DEFAULT 'pending' "
            "CHECK(Status IN ('pending','in_flight','conflict','synced')))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_sync_outbox_status_created "
            "ON SyncOutbox(Status, CreatedAt)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS SyncState ("
            "StateKey TEXT PRIMARY KEY, StateValue TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS SyncConflicts ("
            "ConflictID TEXT PRIMARY KEY, EntityType TEXT NOT NULL, EntityUUID TEXT NOT NULL, "
            "LocalPayload TEXT NOT NULL, RemotePayload TEXT NOT NULL, "
            "LocalVersion INTEGER, RemoteVersion INTEGER, Status TEXT NOT NULL DEFAULT 'open', "
            "CreatedAt TEXT NOT NULL, ResolvedAt TEXT)"
        )
        conn.execute(
            "INSERT INTO SchemaMigrations (Version, AppliedAt, Description) VALUES (?, ?, ?)",
            (MIGRATION_VERSION, datetime.now(timezone.utc).isoformat(),
             "Add stable UUIDs and local-first synchronisation bookkeeping"),
        )
        # Validate the complete migration while it is still rollback-safe.
        for table, old in before["tables"].items():
            current_count = conn.execute(
                'SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"'
            ).fetchone()[0]
            if current_count != old["row_count"]:
                raise sqlite3.DatabaseError(
                    f"Record count changed unexpectedly in {table}"
                )
        for table in SYNC_TABLES:
            null_count, distinct_count, total_count = conn.execute(
                f'SELECT COUNT(*) - COUNT(_sync_uuid), '
                f'COUNT(DISTINCT _sync_uuid), COUNT(*) FROM "{table}"'
            ).fetchone()
            if null_count or distinct_count != total_count:
                raise sqlite3.DatabaseError(
                    f"Stable UUID validation failed for {table}"
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    after = database_inventory(path)
    for table, old in before["tables"].items():
        if table not in after["tables"] or after["tables"][table]["row_count"] != old["row_count"]:
            raise sqlite3.DatabaseError(
                f"Post-migration verification failed for existing table {table}"
            )
    if after["integrity"] != "ok":
        raise sqlite3.DatabaseError("Post-migration SQLite integrity check failed")
    return {
        "status": "migrated",
        "version": MIGRATION_VERSION,
        "backup": str(backup),
        "before": before,
        "after": after,
    }



def apply_relationship_uuid_migration(
    database_path: str | os.PathLike[str],
    backup_directory: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Add stable UUID foreign references while preserving integer IDs and rows."""
    path = Path(database_path).expanduser().resolve()
    before = database_inventory(path)
    if before["integrity"] != "ok":
        raise sqlite3.DatabaseError("Refusing relationship migration on an invalid database")
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    if "SchemaMigrations" not in before["tables"]:
        raise sqlite3.DatabaseError("Apply migration 1 before relationship UUID migration")
    backup = create_verified_backup(path, backup_directory)
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(
            "SELECT 1 FROM SchemaMigrations WHERE Version=?", (MIGRATION_VERSION,)
        ).fetchone():
            raise sqlite3.DatabaseError("Apply migration 1 before relationship UUID migration")
        if conn.execute(
            "SELECT 1 FROM SchemaMigrations WHERE Version=?",
            (RELATIONSHIP_MIGRATION_VERSION,),
        ).fetchone():
            conn.rollback()
            return {"status": "already_applied", "version": RELATIONSHIP_MIGRATION_VERSION,
                    "backup": str(backup)}
        required = {
            "Members": {"MemberID", "_sync_uuid"},
            "Events": {"EventID", "_sync_uuid"},
            "Inventory": {"ItemID", "_sync_uuid"},
            "BorrowedItems": {"BorrowID", "MemberID", "ItemID", "_sync_uuid"},
            "Attendance": {"AttID", "MemberID", "EventID", "_sync_uuid"},
        }
        for table, columns in required.items():
            present = _table_columns(conn, table)
            if not columns.issubset(present):
                raise sqlite3.DatabaseError(
                    f"{table} is missing required columns: {', '.join(sorted(columns-present))}"
                )
        additions = {
            "BorrowedItems": (("MemberUUID", "TEXT"), ("ItemUUID", "TEXT")),
            "Attendance": (("MemberUUID", "TEXT"), ("EventUUID", "TEXT")),
        }
        for table, columns in additions.items():
            present = _table_columns(conn, table)
            quoted = '"' + table + '"'
            for column, declaration in columns:
                if column not in present:
                    conn.execute(f"ALTER TABLE {quoted} ADD COLUMN {column} {declaration}")
        conn.execute(
            "UPDATE BorrowedItems SET MemberUUID=(SELECT _sync_uuid FROM Members "
            "WHERE Members.MemberID=BorrowedItems.MemberID) WHERE MemberID IS NOT NULL"
        )
        conn.execute(
            "UPDATE BorrowedItems SET ItemUUID=(SELECT _sync_uuid FROM Inventory "
            "WHERE Inventory.ItemID=BorrowedItems.ItemID) WHERE ItemID IS NOT NULL"
        )
        conn.execute(
            "UPDATE Attendance SET MemberUUID=(SELECT _sync_uuid FROM Members "
            "WHERE Members.MemberID=Attendance.MemberID) WHERE MemberID IS NOT NULL"
        )
        conn.execute(
            "UPDATE Attendance SET EventUUID=(SELECT _sync_uuid FROM Events "
            "WHERE Events.EventID=Attendance.EventID) WHERE EventID IS NOT NULL"
        )
        orphan_checks = (
            ("BorrowedItems", "MemberID", "MemberUUID"),
            ("BorrowedItems", "ItemID", "ItemUUID"),
            ("Attendance", "MemberID", "MemberUUID"),
            ("Attendance", "EventID", "EventUUID"),
        )
        for table, local_fk, uuid_fk in orphan_checks:
            count = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{local_fk}" IS NOT NULL AND '
                f'("{uuid_fk}" IS NULL OR "{uuid_fk}" = \'\')'
            ).fetchone()[0]
            if count:
                raise sqlite3.DatabaseError(
                    f"Cannot map {count} orphaned {table}.{local_fk} references to stable UUIDs"
                )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_borrowed_member_uuid ON BorrowedItems(MemberUUID)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_borrowed_item_uuid ON BorrowedItems(ItemUUID)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_attendance_member_uuid ON Attendance(MemberUUID)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_attendance_event_uuid ON Attendance(EventUUID)")
        conn.execute(
            "INSERT INTO SchemaMigrations (Version, AppliedAt, Description) VALUES (?, ?, ?)",
            (RELATIONSHIP_MIGRATION_VERSION, datetime.now(timezone.utc).isoformat(),
             "Add stable UUID references for cross-device relationships"),
        )
        for table, old in before["tables"].items():
            count = conn.execute(
                'SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"'
            ).fetchone()[0]
            if count != old["row_count"]:
                raise sqlite3.DatabaseError(f"Record count changed unexpectedly in {table}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    after = database_inventory(path)
    if after["integrity"] != "ok":
        raise sqlite3.DatabaseError("Post-migration integrity check failed")
    for table, old in before["tables"].items():
        if after["tables"][table]["row_count"] != old["row_count"]:
            raise sqlite3.DatabaseError(f"Post-migration record count changed in {table}")
    return {"status": "migrated", "version": RELATIONSHIP_MIGRATION_VERSION,
            "backup": str(backup), "before": before, "after": after}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly prepare a VBCCPAS SQLite database for synchronisation."
    )
    parser.add_argument("--database", required=True, help="Path to society.db")
    parser.add_argument("--backup-only", action="store_true",
                        help="Create and verify a backup without changing the database")
    parser.add_argument("--apply-sync-foundation", action="store_true",
                        help="Back up and apply migration 1; use a test copy first")
    parser.add_argument("--apply-relationship-uuids", action="store_true",
                        help="Back up and apply migration 2 after migration 1")
    args = parser.parse_args()
    if sum((args.backup_only, args.apply_sync_foundation, args.apply_relationship_uuids)) > 1:
        parser.error("Choose only one operation")
    if args.apply_relationship_uuids:
        result = apply_relationship_uuid_migration(args.database)
    elif args.apply_sync_foundation:
        result = apply_local_sync_foundation(args.database)
    elif args.backup_only:
        result = {"backup": str(create_verified_backup(args.database))}
    else:
        result = database_inventory(args.database)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
