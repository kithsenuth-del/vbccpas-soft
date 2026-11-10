"""Tests for password policy, security auditing, and safe database recovery."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from security_foundation import (
    record_security_event,
    restore_database,
    validate_password,
    validate_restore_candidate,
)


def make_database(path: Path, member_name: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE Users (
                UserID INTEGER PRIMARY KEY,
                Username TEXT NOT NULL,
                Password TEXT NOT NULL,
                Role TEXT NOT NULL
            );
            CREATE TABLE Members (MemberID INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE Events (EventID INTEGER PRIMARY KEY, Title TEXT);
            CREATE TABLE Inventory (ItemID INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE BorrowedItems (BorrowID INTEGER PRIMARY KEY, ItemID INTEGER, MemberID INTEGER);
            CREATE TABLE Attendance (AttID INTEGER PRIMARY KEY, MemberID INTEGER, EventID INTEGER);
            INSERT INTO Users VALUES (1, 'admin', 'test-hash-not-a-real-password', 'admin');
        """)
        conn.execute("INSERT INTO Members VALUES (1, ?)", (member_name,))


class SecurityFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live = self.root / "VBCCPAS live data" / "society.db"
        self.live.parent.mkdir(parents=True)
        make_database(self.live, "Current member")

    def tearDown(self):
        self.temp.cleanup()

    def test_password_policy(self):
        self.assertFalse(validate_password("1234567")[0])
        self.assertTrue(validate_password("12345678")[0])
        self.assertFalse(validate_password(" " * 8)[0])
        self.assertTrue(validate_password("a secure phrase 2026")[0])
        self.assertFalse(validate_password("x" * 129)[0])

    def test_audit_event_does_not_need_or_store_secrets(self):
        record_security_event(self.live, "admin", "backup_created", "society.db", "Verified backup")
        with sqlite3.connect(self.live) as conn:
            row = conn.execute(
                "SELECT Actor, Action, Target, Details FROM SecurityAudit"
            ).fetchone()
        self.assertEqual(row, ("admin", "backup_created", "society.db", "Verified backup"))

    def test_restore_creates_safety_backup_and_replaces_database(self):
        selected = self.root / "selected backup.db"
        make_database(selected, "Restored member")
        safety = restore_database(selected, self.live, self.root / "safety copies")
        self.assertTrue(safety.is_file())
        with sqlite3.connect(self.live) as conn:
            self.assertEqual(
                conn.execute("SELECT Name FROM Members WHERE MemberID=1").fetchone()[0],
                "Restored member",
            )
        with sqlite3.connect(safety) as conn:
            self.assertEqual(
                conn.execute("SELECT Name FROM Members WHERE MemberID=1").fetchone()[0],
                "Current member",
            )

    def test_invalid_backup_is_rejected_without_changing_live_database(self):
        invalid = self.root / "not-a-database.db"
        with sqlite3.connect(invalid) as conn:
            conn.execute("CREATE TABLE unrelated (value TEXT)")
        with self.assertRaises(sqlite3.DatabaseError):
            restore_database(invalid, self.live, self.root / "safety")
        with sqlite3.connect(self.live) as conn:
            self.assertEqual(
                conn.execute("SELECT Name FROM Members WHERE MemberID=1").fetchone()[0],
                "Current member",
            )
        self.assertFalse((self.root / "safety").exists())

    def test_backup_without_admin_is_rejected(self):
        candidate = self.root / "no-admin.db"
        make_database(candidate, "Should not restore")
        with sqlite3.connect(candidate) as conn:
            conn.execute("DELETE FROM Users")
        with self.assertRaisesRegex(sqlite3.DatabaseError, "no administrator"):
            validate_restore_candidate(candidate)


if __name__ == "__main__":
    unittest.main()
