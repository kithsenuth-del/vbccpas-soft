"""Regression tests for safe first-time passkey setup recovery."""
import sqlite3
import tempfile
from contextlib import closing
import unittest
import sys
from pathlib import Path

# Running a script from tests/ puts that directory, not the repository root,
# on sys.path. Add the project root so top-level application modules import
# consistently in GitHub Actions and local test runs.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import passkey_auth


class PasskeyRecoveryTests(unittest.TestCase):
    def test_recovery_clears_only_passkey_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "society.db")
            passkey_auth.initialize(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("""CREATE TABLE Members (
                    MemberID INTEGER PRIMARY KEY, Name TEXT NOT NULL
                )""")
                conn.execute("INSERT INTO Members (Name) VALUES ('Test Member')")
                conn.execute(
                    """INSERT INTO Passkeys
                       (CredentialID, PublicKey, DeviceType)
                       VALUES ('stale-id', 'stale-key', 'singleDevice')"""
                )
                conn.execute(
                    "INSERT INTO PasskeyMeta (MetaKey, MetaValue) VALUES "
                    "('user_handle', 'preserved-handle')"
                )
                conn.commit()

            self.assertTrue(passkey_auth.configured(db_path))
            removed = passkey_auth.reset_registration_state(db_path)

            self.assertEqual(removed, 1)
            self.assertFalse(passkey_auth.configured(db_path))
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(conn.execute("SELECT Name FROM Members").fetchone()[0],
                                 "Test Member")
                self.assertEqual(
                    conn.execute(
                        "SELECT MetaValue FROM PasskeyMeta "
                        "WHERE MetaKey='user_handle'"
                    ).fetchone()[0],
                    "preserved-handle",
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM Passkeys").fetchone()[0], 0
                )

    def test_recovery_is_safe_when_no_passkey_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "society.db")
            self.assertEqual(passkey_auth.reset_registration_state(db_path), 0)
            self.assertFalse(passkey_auth.configured(db_path))


if __name__ == "__main__":
    unittest.main()
