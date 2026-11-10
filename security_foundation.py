"""Security and recovery primitives for the VBCCPAS Windows desktop app.

These helpers never store passwords, PINs, tokens, or credential material in
audit records. Restore is deliberately conservative and creates a verified
backup of the current database before replacing it.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from database_migrations import create_verified_backup, database_inventory

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
REQUIRED_RESTORE_TABLES = {
    "Users", "Members", "Events", "Inventory",
    "BorrowedItems", "Attendance",
}


def validate_password(password: str) -> tuple[bool, str]:
    """Enforce the local account password length policy without trimming input."""
    if not isinstance(password, str):
        return False, "Password must be text."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Use a password with at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return False, f"Password must be no more than {MAX_PASSWORD_LENGTH} characters."
    if not password.strip():
        return False, "Password cannot contain only whitespace."
    return True, ""


def record_security_event(
    database_path: str | os.PathLike[str],
    actor: str,
    action: str,
    target: str = "",
    details: str = "",
) -> None:
    """Append a minimal audit event; callers must never pass secrets in details."""
    path = Path(database_path).expanduser().resolve()
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS SecurityAudit (
                AuditID INTEGER PRIMARY KEY AUTOINCREMENT,
                OccurredAt TEXT NOT NULL,
                Actor TEXT NOT NULL,
                Action TEXT NOT NULL,
                Target TEXT,
                Details TEXT
            )
        """)
        conn.execute(
            """INSERT INTO SecurityAudit
               (OccurredAt, Actor, Action, Target, Details)
               VALUES (?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), str(actor)[:120],
             str(action)[:80], str(target)[:160], str(details)[:500]),
        )
        conn.commit()
    finally:
        conn.close()


def validate_restore_candidate(candidate_path: str | os.PathLike[str]) -> dict:
    """Check SQLite integrity, required tables, and presence of an admin account."""
    path = Path(candidate_path).expanduser().resolve()
    inventory = database_inventory(path)
    if inventory["integrity"] != "ok":
        raise sqlite3.DatabaseError("The selected backup failed SQLite integrity validation.")
    missing = REQUIRED_RESTORE_TABLES - set(inventory["tables"])
    if missing:
        raise sqlite3.DatabaseError(
            "The selected file is not a VBCCPAS database; missing tables: "
            + ", ".join(sorted(missing))
        )
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)
    try:
        columns = {row[1].lower() for row in conn.execute("PRAGMA table_info(Users)")}
        if not {"username", "role", "password"}.issubset(columns):
            raise sqlite3.DatabaseError("The backup has an incompatible Users table.")
        admins = conn.execute(
            "SELECT COUNT(*) FROM Users WHERE lower(Role)='admin'"
        ).fetchone()[0]
        if admins < 1:
            raise sqlite3.DatabaseError(
                "The backup contains no administrator account; restore was refused."
            )
    finally:
        conn.close()
    return inventory


def restore_database(
    backup_path: str | os.PathLike[str],
    database_path: str | os.PathLike[str],
    backup_directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Safely restore a validated backup after backing up the current database.

    The temporary replacement is created beside the destination so os.replace
    is atomic on the same volume. On any validation/copy failure, the live DB
    remains untouched.
    """
    source = Path(backup_path).expanduser().resolve()
    destination = Path(database_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Backup file not found: {source}")
    if not destination.is_file():
        raise FileNotFoundError(f"Current database not found: {destination}")
    if source == destination:
        raise ValueError("Choose a backup file different from the active database.")

    validate_restore_candidate(source)
    safety_backup = create_verified_backup(destination, backup_directory)

    # Fold any committed WAL content into the main file before replacement.
    # Stale -wal/-shm sidecars must never be allowed to attach to the restored DB.
    checkpoint = sqlite3.connect(str(destination), timeout=10)
    try:
        checkpoint_result = checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint_result and checkpoint_result[0] != 0:
            raise sqlite3.OperationalError(
                "The active database is busy; close other VBCCPAS windows before restoring."
            )
    finally:
        checkpoint.close()

    fd, temporary_name = tempfile.mkstemp(
        prefix=destination.stem + "-restore-",
        suffix=destination.suffix + ".tmp",
        dir=str(destination.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        validate_restore_candidate(temporary)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(destination) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return safety_backup
