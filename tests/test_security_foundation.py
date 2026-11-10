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


    def test_recovery_code_resets_password_once_and_is_not_reusable(self):
        from security_foundation import (
            create_admin_recovery_code, reset_admin_password_with_recovery_code
        )
        code = create_admin_recovery_code(self.live, "admin")
        username = reset_admin_password_with_recovery_code(
            self.live, code, "new-secure-password"
        )
        self.assertEqual(username, "admin")
        with sqlite3.connect(self.live) as conn:
            self.assertTrue(conn.execute(
                "SELECT UsedAt FROM AdminRecovery WHERE RecoveryID=1"
            ).fetchone()[0])
            self.assertTrue(conn.execute(
                "SELECT Password FROM Users WHERE Username='admin'"
            ).fetchone()[0].startswith("pbkdf2_sha256$"))
        with self.assertRaises(PermissionError):
            reset_admin_password_with_recovery_code(
                self.live, code, "another-secure-password"
            )

    def test_wrong_recovery_code_is_rejected_and_rate_limited(self):
        from security_foundation import (
            create_admin_recovery_code, reset_admin_password_with_recovery_code,
            record_login_failure, login_lock_remaining
        )
        create_admin_recovery_code(self.live, "admin")
        for _ in range(5):
            record_login_failure(self.live)
        self.assertGreater(login_lock_remaining(self.live), 0)
        with self.assertRaises(PermissionError):
            reset_admin_password_with_recovery_code(
                self.live, "0" * 32, "new-secure-password"
            )

    def test_recovery_code_is_stored_as_verifier_not_plaintext(self):
        from security_foundation import create_admin_recovery_code
        code = create_admin_recovery_code(self.live, "admin")
        with sqlite3.connect(self.live) as conn:
            salt, digest = conn.execute(
                "SELECT Salt, Digest FROM AdminRecovery WHERE RecoveryID=1"
            ).fetchone()
        self.assertNotEqual(salt, code.encode("ascii"))
        self.assertNotEqual(digest, code.encode("ascii"))


    def test_five_digit_pin_is_salted_and_verified(self):
        from security_foundation import (
            set_admin_pin, verify_admin_pin, validate_admin_pin
        )
        self.assertFalse(validate_admin_pin("2847"))
        self.assertFalse(validate_admin_pin("28472x"))
        set_admin_pin(self.live, "admin", "58319")
        self.assertTrue(verify_admin_pin(self.live, "admin", "58319"))
        self.assertFalse(verify_admin_pin(self.live, "admin", "58318"))
        with sqlite3.connect(self.live) as conn:
            salt, digest = conn.execute(
                "SELECT Salt, Digest FROM AdminPin WHERE PinID=1"
            ).fetchone()
        self.assertNotEqual(digest, b"58319")
        self.assertNotEqual(salt, b"58319")

    def test_recovery_replaces_both_password_and_pin(self):
        from security_foundation import (
            create_admin_recovery_code, set_admin_pin,
            reset_admin_password_with_recovery_code, verify_admin_pin
        )
        set_admin_pin(self.live, "admin", "58319")
        code = create_admin_recovery_code(self.live, "admin")
        reset_admin_password_with_recovery_code(
            self.live, code, "recovered-password", "19462"
        )
        self.assertTrue(verify_admin_pin(self.live, "admin", "19462"))
        self.assertFalse(verify_admin_pin(self.live, "admin", "58319"))


if __name__ == "__main__":
    unittest.main()
