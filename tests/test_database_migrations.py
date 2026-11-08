"""Tests for the non-destructive local-first database migration foundation."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database_migrations import (
    apply_local_sync_foundation,
    apply_relationship_uuid_migration,
    create_verified_backup,
    database_inventory,
)


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "society.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE Members (MemberID INTEGER PRIMARY KEY, Name TEXT NOT NULL);
                CREATE TABLE Events (EventID INTEGER PRIMARY KEY, Title TEXT NOT NULL);
                CREATE TABLE Inventory (ItemID INTEGER PRIMARY KEY, Name TEXT NOT NULL);
                CREATE TABLE BorrowedItems (BorrowID INTEGER PRIMARY KEY, ItemID INTEGER, MemberID INTEGER);
                CREATE TABLE Attendance (AttID INTEGER PRIMARY KEY, MemberID INTEGER, EventID INTEGER);
                CREATE TABLE Users (UserID INTEGER PRIMARY KEY, Username TEXT UNIQUE);
                CREATE TABLE CustomLegacyTable (LegacyID INTEGER PRIMARY KEY, Payload TEXT);
                INSERT INTO Members VALUES (7, 'Preserve this member');
                INSERT INTO Events VALUES (8, 'Preserve this event');
                INSERT INTO Inventory VALUES (9, 'Preserve this camera');
                INSERT INTO BorrowedItems VALUES (10, 9, 7);
                INSERT INTO Attendance VALUES (11, 7, 8);
                INSERT INTO Users VALUES (12, 'existing-admin');
                INSERT INTO CustomLegacyTable VALUES (13, 'untouched');
            """)

    def tearDown(self):
        self.temp.cleanup()

    def test_verified_backup_matches_source(self):
        backup = create_verified_backup(self.db, self.root / "backups")
        self.assertTrue(backup.is_file())
        self.assertEqual(database_inventory(self.db)["tables"],
                         database_inventory(backup)["tables"])

    def test_migration_preserves_existing_records_and_primary_keys(self):
        result = apply_local_sync_foundation(self.db, self.root / "backups")
        self.assertEqual(result["status"], "migrated")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT Name FROM Members WHERE MemberID=7").fetchone()[0],
                             "Preserve this member")
            self.assertEqual(conn.execute("SELECT Title FROM Events WHERE EventID=8").fetchone()[0],
                             "Preserve this event")
            self.assertEqual(conn.execute("SELECT Name FROM Inventory WHERE ItemID=9").fetchone()[0],
                             "Preserve this camera")
            self.assertEqual(conn.execute("SELECT Username FROM Users WHERE UserID=12").fetchone()[0],
                             "existing-admin")
            self.assertEqual(conn.execute("SELECT Payload FROM CustomLegacyTable WHERE LegacyID=13").fetchone()[0],
                             "untouched")
            for table in ("Members", "Events", "Inventory", "BorrowedItems", "Attendance"):
                uuids = conn.execute(f"SELECT _sync_uuid FROM {table}").fetchall()
                self.assertTrue(uuids and all(row[0] for row in uuids))
                self.assertEqual(len(uuids), len(set(row[0] for row in uuids)))
        self.assertEqual(result["before"]["tables"]["Members"]["row_count"],
                         result["after"]["tables"]["Members"]["row_count"])

    def test_migration_is_idempotent(self):
        first = apply_local_sync_foundation(self.db, self.root / "backups")
        second = apply_local_sync_foundation(self.db, self.root / "backups")
        self.assertEqual(first["status"], "migrated")
        self.assertEqual(second["status"], "already_applied")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM SchemaMigrations").fetchone()[0], 1)

    def test_missing_sync_table_fails_without_partial_migration(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE Attendance")
        with self.assertRaisesRegex(sqlite3.DatabaseError, "required tables are missing"):
            apply_local_sync_foundation(self.db, self.root / "backups")
        with sqlite3.connect(self.db) as conn:
            self.assertNotIn("_sync_uuid", {
                row[1] for row in conn.execute("PRAGMA table_info(Members)")
            })
            self.assertFalse(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='SyncOutbox'"
            ).fetchone())

    def test_wrong_primary_key_rolls_back_every_change(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("ALTER TABLE Members RENAME TO Members_old")
            conn.execute("CREATE TABLE Members (WrongID INTEGER PRIMARY KEY, Name TEXT)")
            conn.execute("INSERT INTO Members VALUES (7, 'Preserve this member')")
        with self.assertRaisesRegex(sqlite3.DatabaseError, "expected primary key"):
            apply_local_sync_foundation(self.db, self.root / "backups")
        with sqlite3.connect(self.db) as conn:
            self.assertNotIn("_sync_uuid", {
                row[1] for row in conn.execute("PRAGMA table_info(Events)")
            })
            self.assertFalse(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='SyncOutbox'"
            ).fetchone())

    def test_relationship_migration_preserves_integer_links_and_adds_stable_uuids(self):
        apply_local_sync_foundation(self.db, self.root / "backups")
        first = apply_relationship_uuid_migration(self.db, self.root / "backups")
        second = apply_relationship_uuid_migration(self.db, self.root / "backups")
        self.assertEqual(first["status"], "migrated")
        self.assertEqual(second["status"], "already_applied")
        with sqlite3.connect(self.db) as conn:
            member_uuid = conn.execute(
                "SELECT _sync_uuid FROM Members WHERE MemberID=7").fetchone()[0]
            item_uuid = conn.execute(
                "SELECT _sync_uuid FROM Inventory WHERE ItemID=9").fetchone()[0]
            event_uuid = conn.execute(
                "SELECT _sync_uuid FROM Events WHERE EventID=8").fetchone()[0]
            self.assertEqual(conn.execute(
                "SELECT ItemID,MemberID,ItemUUID,MemberUUID FROM BorrowedItems WHERE BorrowID=10"
            ).fetchone(), (9, 7, item_uuid, member_uuid))
            self.assertEqual(conn.execute(
                "SELECT MemberID,EventID,MemberUUID,EventUUID FROM Attendance WHERE AttID=11"
            ).fetchone(), (7, 8, member_uuid, event_uuid))
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM SchemaMigrations").fetchone()[0], 2)

    def test_relationship_migration_refuses_orphaned_links(self):
        apply_local_sync_foundation(self.db, self.root / "backups")
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE BorrowedItems SET MemberID=999 WHERE BorrowID=10")
        with self.assertRaisesRegex(sqlite3.DatabaseError, "orphaned"):
            apply_relationship_uuid_migration(self.db, self.root / "backups")
        with sqlite3.connect(self.db) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(BorrowedItems)")}
            self.assertNotIn("MemberUUID", columns)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM BorrowedItems WHERE MemberID=999").fetchone()[0], 1)

    def test_missing_database_is_not_created_silently(self):
        missing = self.root / "missing.db"
        with self.assertRaises(FileNotFoundError):
            apply_local_sync_foundation(missing, self.root / "backups")
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
