import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from database_migrations import apply_local_sync_foundation, apply_relationship_uuid_migration
from local_sync import (
    acknowledge_change, apply_remote_change, get_pull_cursor, pending_changes,
    queue_local_change, record_conflict, save_pull_cursor,
)


class LocalSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "society.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE Members (MemberID INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT NOT NULL,
                    Class TEXT, Roll TEXT, Email TEXT);
                CREATE TABLE Events (EventID INTEGER PRIMARY KEY AUTOINCREMENT, Title TEXT NOT NULL,
                    Date TEXT, Location TEXT, Notes TEXT);
                CREATE TABLE Inventory (ItemID INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT NOT NULL,
                    Quantity INTEGER DEFAULT 0, Notes TEXT);
                CREATE TABLE BorrowedItems (BorrowID INTEGER PRIMARY KEY AUTOINCREMENT, ItemID INTEGER,
                    MemberID INTEGER, BorrowDate TEXT, ReturnDate TEXT, Returned BOOLEAN DEFAULT 0);
                CREATE TABLE Attendance (AttID INTEGER PRIMARY KEY AUTOINCREMENT, MemberID INTEGER,
                    EventID INTEGER, Present BOOLEAN DEFAULT 0, Timestamp TEXT);
                INSERT INTO Members(Name,Class) VALUES('Local Member','10-A');
                INSERT INTO Events(Title) VALUES('Local Event');
                INSERT INTO Inventory(Name,Quantity) VALUES('Camera',2);
            """)
        apply_local_sync_foundation(self.db, self.root / "backups")
        apply_relationship_uuid_migration(self.db, self.root / "backups")

    def tearDown(self):
        self.temp.cleanup()

    def test_queue_coalesces_updates_without_losing_base_version(self):
        with sqlite3.connect(self.db) as conn:
            member_uuid = conn.execute("SELECT _sync_uuid FROM Members WHERE MemberID=1").fetchone()[0]
        first = queue_local_change(self.db, "Members", member_uuid)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE Members SET Name='Updated Member' WHERE MemberID=1")
        second = queue_local_change(self.db, "Members", member_uuid)
        pending = pending_changes(self.db)
        self.assertEqual(first["change_id"], second["change_id"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["base_version"], 0)
        self.assertEqual(pending[0]["payload"]["Name"], "Updated Member")

    def test_acknowledgement_rebases_edits_made_during_upload(self):
        with sqlite3.connect(self.db) as conn:
            member_uuid = conn.execute("SELECT _sync_uuid FROM Members WHERE MemberID=1").fetchone()[0]
        sent = queue_local_change(self.db, "Members", member_uuid)
        sent_payload = dict(sent["payload"])
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE Members SET Name='Changed while uploading' WHERE MemberID=1")
        queue_local_change(self.db, "Members", member_uuid)
        self.assertTrue(acknowledge_change(self.db, sent["change_id"], sent_payload, 1, sent["operation"]))
        pending = pending_changes(self.db)
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0]["change_id"], sent["change_id"])
        self.assertEqual(pending[0]["base_version"], 1)
        self.assertEqual(pending[0]["payload"]["Name"], "Changed while uploading")

    def test_remote_apply_resolves_stable_relationships_and_does_not_echo(self):
        member_uuid, event_uuid = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertEqual(apply_remote_change(self.db, {
            "entity_type": "Members", "entity_uuid": member_uuid, "version": 1,
            "payload": {"Name": "Remote Member", "Class": "11-B"}, "deleted": False
        }), "applied")
        self.assertEqual(apply_remote_change(self.db, {
            "entity_type": "Events", "entity_uuid": event_uuid, "version": 1,
            "payload": {"Title": "Remote Event"}, "deleted": False
        }), "applied")
        self.assertEqual(apply_remote_change(self.db, {
            "entity_type": "Attendance", "entity_uuid": str(uuid.uuid4()), "version": 1,
            "payload": {"MemberUUID": member_uuid, "EventUUID": event_uuid,
                        "Present": 1, "Timestamp": "2026-11-08T00:00:00Z"},
            "deleted": False
        }), "applied")
        with sqlite3.connect(self.db) as conn:
            link = conn.execute(
                "SELECT MemberID,EventID,MemberUUID,EventUUID FROM Attendance "
                "WHERE MemberUUID=? AND EventUUID=?", (member_uuid,event_uuid)).fetchone()
            self.assertIsNotNone(link)
            self.assertEqual(link[0], conn.execute(
                "SELECT MemberID FROM Members WHERE _sync_uuid=?", (member_uuid,)).fetchone()[0])
            self.assertEqual(link[1], conn.execute(
                "SELECT EventID FROM Events WHERE _sync_uuid=?", (event_uuid,)).fetchone()[0])
        self.assertEqual(pending_changes(self.db), [])

    def test_remote_tombstone_is_soft_and_versioned(self):
        with sqlite3.connect(self.db) as conn:
            member_uuid = conn.execute("SELECT _sync_uuid FROM Members WHERE MemberID=1").fetchone()[0]
        result = apply_remote_change(self.db, {
            "entity_type": "Members", "entity_uuid": member_uuid, "version": 2,
            "payload": {}, "deleted": True
        })
        self.assertEqual(result, "applied")
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT Name,_sync_deleted_at,_sync_version FROM Members WHERE _sync_uuid=?",
                               (member_uuid,)).fetchone()
            self.assertEqual(row[0], "Local Member")
            self.assertIsNotNone(row[1])
            self.assertEqual(row[2], 2)

    def test_conflict_is_stored_for_manual_resolution(self):
        with sqlite3.connect(self.db) as conn:
            member_uuid = conn.execute("SELECT _sync_uuid FROM Members WHERE MemberID=1").fetchone()[0]
        change = queue_local_change(self.db, "Members", member_uuid)
        conflict = record_conflict(self.db, change["change_id"], 3, {"Name": "Server copy"})
        self.assertTrue(conflict)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT Status FROM SyncOutbox WHERE ChangeID=?", (change["change_id"],)
            ).fetchone()[0], "conflict")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM SyncConflicts WHERE Status='open'"
            ).fetchone()[0], 1)

    def test_pull_cursor_is_persisted(self):
        self.assertEqual(get_pull_cursor(self.db), 0)
        save_pull_cursor(self.db, 27)
        self.assertEqual(get_pull_cursor(self.db), 27)


if __name__ == "__main__":
    unittest.main()
