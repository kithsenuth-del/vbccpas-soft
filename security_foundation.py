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


# Administrator recovery codes are generated once, displayed once, and stored
# only as salted PBKDF2 verifiers. They are independent of the account password.
RECOVERY_HASH_ITERATIONS = 310000
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_BASE_SECONDS = 300
LOGIN_LOCK_MAX_SECONDS = 3600


def create_admin_recovery_code(database_path, username="admin"):
    """Rotate and return a one-time administrator recovery code."""
    import hashlib
    import secrets

    code = secrets.token_hex(16).upper()
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", code.encode("ascii"), salt, RECOVERY_HASH_ITERATIONS
    )
    with sqlite3.connect(str(Path(database_path).expanduser().resolve()), timeout=10) as conn:
        admin = conn.execute(
            "SELECT 1 FROM Users WHERE Username=? AND lower(Role)='admin'",
            (username,),
        ).fetchone()
        if not admin:
            raise PermissionError("An administrator account is required to create a recovery code.")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS AdminRecovery (
                Username TEXT PRIMARY KEY,
                Salt BLOB NOT NULL,
                Digest BLOB NOT NULL,
                Iterations INTEGER NOT NULL,
                CreatedAt TEXT NOT NULL,
                UsedAt TEXT
            )
        """)
        conn.execute("""
            INSERT INTO AdminRecovery
                (Username, Salt, Digest, Iterations, CreatedAt, UsedAt)
            VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(Username) DO UPDATE SET
                Salt=excluded.Salt, Digest=excluded.Digest,
                Iterations=excluded.Iterations, CreatedAt=excluded.CreatedAt,
                UsedAt=NULL
        """, (username, salt, digest, RECOVERY_HASH_ITERATIONS,
              datetime.now(timezone.utc).isoformat()))
    return code


def login_lock_remaining(database_path):
    """Return remaining global desktop-login lockout seconds, or zero."""
    path = Path(database_path).expanduser().resolve()
    with sqlite3.connect(str(path), timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS LoginGuard (
                GuardID INTEGER PRIMARY KEY CHECK(GuardID=1),
                Failures INTEGER NOT NULL DEFAULT 0,
                LockedUntil REAL NOT NULL DEFAULT 0,
                UpdatedAt TEXT NOT NULL
            )
        """)
        row = conn.execute(
            "SELECT LockedUntil FROM LoginGuard WHERE GuardID=1"
        ).fetchone()
    return max(0, int((row[0] if row else 0) - __import__("time").time() + 0.999))


def record_login_failure(database_path):
    """Record a failed password/recovery attempt and apply escalating lockout."""
    import time
    path = Path(database_path).expanduser().resolve()
    now = time.time()
    with sqlite3.connect(str(path), timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS LoginGuard (
                GuardID INTEGER PRIMARY KEY CHECK(GuardID=1),
                Failures INTEGER NOT NULL DEFAULT 0,
                LockedUntil REAL NOT NULL DEFAULT 0,
                UpdatedAt TEXT NOT NULL
            )
        """)
        row = conn.execute(
            "SELECT Failures, LockedUntil FROM LoginGuard WHERE GuardID=1"
        ).fetchone()
        failures, locked_until = row if row else (0, 0)
        if locked_until > now:
            return max(1, int(locked_until - now + 0.999))
        failures += 1
        lock_seconds = 0
        if failures >= LOGIN_FAILURE_LIMIT:
            exponent = min(failures - LOGIN_FAILURE_LIMIT, 4)
            lock_seconds = min(LOGIN_LOCK_BASE_SECONDS * (2 ** exponent),
                               LOGIN_LOCK_MAX_SECONDS)
        conn.execute("""
            INSERT INTO LoginGuard (GuardID, Failures, LockedUntil, UpdatedAt)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(GuardID) DO UPDATE SET
                Failures=excluded.Failures, LockedUntil=excluded.LockedUntil,
                UpdatedAt=excluded.UpdatedAt
        """, (failures, now + lock_seconds, datetime.now(timezone.utc).isoformat()))
    return lock_seconds


def clear_login_failures(database_path):
    path = Path(database_path).expanduser().resolve()
    with sqlite3.connect(str(path), timeout=10) as conn:
        conn.execute("DELETE FROM LoginGuard WHERE GuardID=1")


def validate_admin_pin(pin):
    """Require a user-chosen five-digit secondary administrator PIN."""
    import re
    return isinstance(pin, str) and bool(re.fullmatch(r"[0-9]{5}", pin))


def admin_pin_configured(database_path, username="admin"):
    path = Path(database_path).expanduser().resolve()
    with sqlite3.connect(str(path), timeout=10) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='AdminPin'"
        ).fetchone()
        if not exists:
            return False
        return conn.execute(
            "SELECT 1 FROM AdminPin WHERE Username=?",
            (username,),
        ).fetchone() is not None


def set_admin_pin(database_path, username, pin):
    """Set a five-digit PIN using a salted, deliberately slow verifier."""
    import hashlib
    import secrets
    if not validate_admin_pin(pin):
        raise ValueError("Administrator PIN must contain exactly five digits.")
    path = Path(database_path).expanduser().resolve()
    salt = secrets.token_bytes(16)
    iterations = 310000
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, iterations)
    with sqlite3.connect(str(path), timeout=10) as conn:
        if not conn.execute(
            "SELECT 1 FROM Users WHERE Username=? AND lower(Role)='admin'",
            (username,),
        ).fetchone():
            raise PermissionError("An administrator account is required to set a PIN.")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS AdminPin (
                Username TEXT PRIMARY KEY,
                Salt BLOB NOT NULL,
                Digest BLOB NOT NULL,
                Iterations INTEGER NOT NULL,
                UpdatedAt TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO AdminPin (Username, Salt, Digest, Iterations, UpdatedAt)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(Username) DO UPDATE SET
                Salt=excluded.Salt, Digest=excluded.Digest,
                Iterations=excluded.Iterations, UpdatedAt=excluded.UpdatedAt
        """, (username, salt, digest, iterations, datetime.now(timezone.utc).isoformat()))


def verify_admin_pin(database_path, username, pin):
    """Verify a stored administrator PIN without exposing its verifier."""
    import hashlib
    import hmac
    if not isinstance(pin, str) or not pin.isdigit() or len(pin) != 5:
        return False
    path = Path(database_path).expanduser().resolve()
    with sqlite3.connect(str(path), timeout=10) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='AdminPin'"
        ).fetchone()
        if not exists:
            return False
        row = conn.execute(
            "SELECT Salt, Digest, Iterations FROM AdminPin WHERE Username=?",
            (username,),
        ).fetchone()
    if not row:
        return False
    supplied = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), row[0], int(row[2]))
    return hmac.compare_digest(supplied, row[1])


def reset_admin_password_with_recovery_code(
    database_path, recovery_code, new_password, new_pin=None, username='admin'
):
    """Consume the one-time recovery code and replace the admin password."""
    import hashlib
    import hmac
    import re
    import time

    valid, reason = validate_password(new_password)
    if not valid:
        raise ValueError(reason)
    if new_pin is not None and not validate_admin_pin(new_pin):
        raise ValueError("Administrator PIN must contain exactly five digits.")
    code = re.sub(r"[-\\s]", "", str(recovery_code or "")).upper()
    if not re.fullmatch(r"[0-9A-F]{32}", code):
        record_login_failure(database_path)
        raise PermissionError("The recovery code was not accepted.")
    path = Path(database_path).expanduser().resolve()
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS LoginGuard (
                GuardID INTEGER PRIMARY KEY CHECK(GuardID=1),
                Failures INTEGER NOT NULL DEFAULT 0,
                LockedUntil REAL NOT NULL DEFAULT 0,
                UpdatedAt TEXT NOT NULL
            )
        """)
        now = time.time()
        guard = conn.execute(
            "SELECT LockedUntil FROM LoginGuard WHERE GuardID=1"
        ).fetchone() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='LoginGuard'"
        ).fetchone() else None
        if guard and guard[0] > now:
            raise PermissionError("Too many failed attempts. Wait before trying again.")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS AdminRecovery (
                Username TEXT PRIMARY KEY,
                Salt BLOB NOT NULL, Digest BLOB NOT NULL,
                Iterations INTEGER NOT NULL, CreatedAt TEXT NOT NULL, UsedAt TEXT
            )
        """)
        row = conn.execute(
            "SELECT Username, Salt, Digest, Iterations, UsedAt "
            "FROM AdminRecovery WHERE Username=?",
            (username,),
        ).fetchone()
        if not row or row[4]:
            raise PermissionError("The recovery code was not accepted or has already been used.")
        username, salt, stored_digest, iterations, _used = row
        supplied = hashlib.pbkdf2_hmac(
            "sha256", code.encode("ascii"), salt, int(iterations)
        )
        if not hmac.compare_digest(supplied, stored_digest):
            conn.rollback()
            record_login_failure(path)
            raise PermissionError("The recovery code was not accepted.")
        admin = conn.execute(
            "SELECT 1 FROM Users WHERE Username=? AND lower(Role)='admin'",
            (username,),
        ).fetchone()
        if not admin:
            raise PermissionError("The administrator account could not be verified.")
        conn.execute(
            "UPDATE Users SET Password=? WHERE Username=?",
            (_password_hash_for_recovery(new_password), username),
        )
        if new_pin is not None:
            import secrets
            salt = secrets.token_bytes(16)
            iterations = 310000
            digest = hashlib.pbkdf2_hmac("sha256", new_pin.encode("ascii"), salt, iterations)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS AdminPin (
                    Username TEXT PRIMARY KEY,
                Salt BLOB NOT NULL, Digest BLOB NOT NULL,
                    Iterations INTEGER NOT NULL, UpdatedAt TEXT NOT NULL
                )
            """)
            conn.execute("""
                INSERT INTO AdminPin (PinID, Username, Salt, Digest, Iterations, UpdatedAt)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(PinID) DO UPDATE SET Username=excluded.Username,
                    Salt=excluded.Salt, Digest=excluded.Digest,
                    Iterations=excluded.Iterations, UpdatedAt=excluded.UpdatedAt
            """, (username, salt, digest, iterations, datetime.now(timezone.utc).isoformat()))
        conn.execute(
            "UPDATE AdminRecovery SET UsedAt=? WHERE Username=?",
            (datetime.now(timezone.utc).isoformat(), username),
        )
        conn.execute("DELETE FROM LoginGuard WHERE GuardID=1")
        conn.commit()
        return username
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _password_hash_for_recovery(password):
    import hashlib
    import secrets
    salt = secrets.token_bytes(16)
    iterations = 310000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$" + str(iterations) + "$" + salt.hex() + "$" + digest.hex()
