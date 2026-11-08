"""Local-first synchronisation primitives.

These functions operate only on an explicitly selected database. They never
connect to a network or run automatically during desktop startup.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ENTITY_FIELDS = {
    "Members": ("Name", "Class", "Roll", "Email"),
    "Events": ("Title", "Date", "Location", "Notes"),
    "Inventory": ("Name", "Quantity", "Notes"),
    "BorrowedItems": ("MemberUUID", "ItemUUID", "BorrowDate", "ReturnDate", "Returned"),
    "Attendance": ("MemberUUID", "EventUUID", "Present", "Timestamp"),
}
PRIMARY_KEYS = {
    "Members": "MemberID", "Events": "EventID", "Inventory": "ItemID",
    "BorrowedItems": "BorrowID", "Attendance": "AttID",
}


class SyncNotReadyError(RuntimeError):
    pass


class MissingDependencyError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(database_path):
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_ready(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "SchemaMigrations" not in tables:
        raise SyncNotReadyError("Apply local sync migration 1 first.")
    applied = {row[0] for row in conn.execute(
        "SELECT Version FROM SchemaMigrations")}
    if not {1, 2}.issubset(applied):
        raise SyncNotReadyError("Apply local sync migrations 1 and 2 first.")
    if "SyncOutbox" not in tables or "SyncState" not in tables:
        raise SyncNotReadyError("Local sync queue tables are missing.")


def _state(conn, key, default="0"):
    row = conn.execute("SELECT StateValue FROM SyncState WHERE StateKey=?", (key,)).fetchone()
    return row[0] if row else default


def _set_state(conn, key, value):
    conn.execute(
        "INSERT INTO SyncState(StateKey,StateValue) VALUES(?,?) "
        "ON CONFLICT(StateKey) DO UPDATE SET StateValue=excluded.StateValue",
        (key, str(value)),
    )


def queue_local_change(database_path, entity_type, entity_uuid, operation="upsert", payload=None):
    """Queue/coalesce a record snapshot; deletes become tombstones, never SQL DELETEs."""
    if entity_type not in ENTITY_FIELDS:
        raise ValueError("Unsupported synchronisation entity.")
    if operation not in {"upsert", "delete"}:
        raise ValueError("Operation must be upsert or delete.")
    entity_uuid = str(uuid.UUID(str(entity_uuid)))
    conn = _connect(database_path)
    try:
        _ensure_ready(conn)
        if operation == "delete":
            payload = {}
            conn.execute(
                f'UPDATE "{entity_type}" SET _sync_deleted_at=? WHERE _sync_uuid=?',
                (_now(), entity_uuid),
            )
        elif payload is None:
            row = conn.execute(
                f'SELECT * FROM "{entity_type}" WHERE _sync_uuid=?', (entity_uuid,)
            ).fetchone()
            if row is None:
                raise KeyError(f"{entity_type} record not found.")
            payload = {key: row[key] for key in ENTITY_FIELDS[entity_type]
                       if key in row.keys()}
        if not isinstance(payload, dict) or set(payload) - set(ENTITY_FIELDS[entity_type]):
            raise ValueError("Payload contains unsupported fields.")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 262144:
            raise ValueError("Payload exceeds 256 KB.")
        base_key = f"remote_version:{entity_type}:{entity_uuid}"
        base_version = int(_state(conn, base_key))
        existing = conn.execute(
            "SELECT ChangeID, BaseVersion FROM SyncOutbox "
            "WHERE EntityType=? AND EntityUUID=? AND Status='pending' "
            "ORDER BY CreatedAt LIMIT 1", (entity_type, entity_uuid)
        ).fetchone()
        if existing:
            change_id, base_version = existing["ChangeID"], int(existing["BaseVersion"] or 0)
            conn.execute(
                "UPDATE SyncOutbox SET Operation=?,Payload=?,CreatedAt=?,AttemptCount=0,LastError=NULL "
                "WHERE ChangeID=?",
                (operation, encoded, _now(), change_id),
            )
        else:
            change_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO SyncOutbox(ChangeID,EntityType,EntityUUID,Operation,Payload,"
                "BaseVersion,CreatedAt,Status) VALUES(?,?,?,?,?,?,?,'pending')",
                (change_id, entity_type, entity_uuid, operation, encoded, base_version, _now()),
            )
        conn.commit()
        return {"change_id": change_id, "entity_type": entity_type,
                "entity_uuid": entity_uuid, "operation": operation,
                "base_version": base_version, "payload": payload}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def pending_changes(database_path, limit=100):
    if not 1 <= limit <= 100:
        raise ValueError("Batch size must be between 1 and 100.")
    conn = _connect(database_path)
    try:
        _ensure_ready(conn)
        rows = conn.execute(
            "SELECT ChangeID,EntityType,EntityUUID,Operation,Payload,BaseVersion "
            "FROM SyncOutbox WHERE Status='pending' ORDER BY CreatedAt,ChangeID LIMIT ?",
            (limit,),
        ).fetchall()
        return [{
            "change_id": row["ChangeID"], "entity_type": row["EntityType"],
            "entity_uuid": row["EntityUUID"], "operation": row["Operation"],
            "payload": json.loads(row["Payload"]), "base_version": row["BaseVersion"],
        } for row in rows]
    finally:
        conn.close()


def acknowledge_change(database_path, change_id, sent_payload, applied_version):
    """Acknowledge only the exact snapshot sent; preserve edits made in-flight."""
    conn = _connect(database_path)
    try:
        _ensure_ready(conn)
        row = conn.execute(
            "SELECT EntityType,EntityUUID,Payload,Status FROM SyncOutbox WHERE ChangeID=?",
            (str(change_id),),
        ).fetchone()
        if not row:
            return False
        key = f"remote_version:{row['EntityType']}:{row['EntityUUID']}"
        _set_state(conn, key, int(applied_version))
        current_payload = json.loads(row["Payload"])
        if current_payload == sent_payload:
            conn.execute("UPDATE SyncOutbox SET Status='synced',LastError=NULL WHERE ChangeID=?",
                         (str(change_id),))
        else:
            # The user edited the record while this snapshot was being sent.
            # Rebase the newer snapshot on the version just accepted by server.
            conn.execute(
                "UPDATE SyncOutbox SET ChangeID=?,BaseVersion=?,Status='pending',"
                "AttemptCount=0,LastError=NULL WHERE ChangeID=?",
                (str(uuid.uuid4()), int(applied_version), str(change_id)),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_conflict(database_path, change_id, server_version, server_payload, server_deleted=False):
    conn = _connect(database_path)
    try:
        _ensure_ready(conn)
        row = conn.execute(
            "SELECT EntityType,EntityUUID,Payload,BaseVersion FROM SyncOutbox WHERE ChangeID=?",
            (str(change_id),),
        ).fetchone()
        if not row:
            return False
        conflict_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO SyncConflicts(ConflictID,EntityType,EntityUUID,LocalPayload,"
            "RemotePayload,LocalVersion,RemoteVersion,Status,CreatedAt) "
            "VALUES(?,?,?,?,?,?,?,'open',?)",
            (conflict_id, row["EntityType"], row["EntityUUID"], row["Payload"],
             json.dumps({"payload": server_payload, "deleted": bool(server_deleted)},
                        ensure_ascii=False, sort_keys=True),
             int(row["BaseVersion"] or 0), int(server_version), _now()),
        )
        conn.execute("UPDATE SyncOutbox SET Status='conflict',LastError=? WHERE ChangeID=?",
                     (f"Server version is {server_version}", str(change_id)))
        conn.commit()
        return conflict_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_remote_change(database_path, change):
    """Apply one ordered server event without generating an outbound echo."""
    entity_type = change["entity_type"]
    if entity_type not in ENTITY_FIELDS:
        raise ValueError("Unsupported synchronisation entity.")
    entity_uuid = str(uuid.UUID(str(change["entity_uuid"])))
    payload = change.get("payload") or {}
    deleted = bool(change.get("deleted"))
    version = int(change["version"])
    if version < 1 or not isinstance(payload, dict):
        raise ValueError("Invalid remote change.")
    if set(payload) - set(ENTITY_FIELDS[entity_type]):
        raise ValueError("Remote payload contains unsupported fields.")
    conn = _connect(database_path)
    try:
        _ensure_ready(conn)
        conn.execute("BEGIN IMMEDIATE")
        state_key = f"remote_version:{entity_type}:{entity_uuid}"
        known_version = int(_state(conn, state_key))
        if version <= known_version:
            conn.rollback()
            return "already_applied"
        row = conn.execute(
            f'SELECT "{PRIMARY_KEYS[entity_type]}" FROM "{entity_type}" WHERE _sync_uuid=?',
            (entity_uuid,),
        ).fetchone()
        _set_state(conn, "applying_remote", "1")
        if deleted:
            if row:
                conn.execute(
                    f'UPDATE "{entity_type}" SET _sync_deleted_at=?,_sync_version=?,'
                    f'_sync_updated_at=? WHERE _sync_uuid=?',
                    (_now(), version, _now(), entity_uuid),
                )
        else:
            values = dict(payload)
            if entity_type == "BorrowedItems":
                for uuid_field, table, pk in (
                    ("MemberUUID", "Members", "MemberID"),
                    ("ItemUUID", "Inventory", "ItemID"),
                ):
                    ref_uuid = values.pop(uuid_field, None)
                    if ref_uuid:
                        ref = conn.execute(
                            f'SELECT "{pk}" FROM "{table}" WHERE _sync_uuid=? AND _sync_deleted_at IS NULL',
                            (str(ref_uuid),),
                        ).fetchone()
                        if not ref:
                            raise MissingDependencyError(f"Missing {table} dependency {ref_uuid}")
                        values["MemberID" if table == "Members" else "ItemID"] = ref[0]
            elif entity_type == "Attendance":
                for uuid_field, table, pk in (
                    ("MemberUUID", "Members", "MemberID"),
                    ("EventUUID", "Events", "EventID"),
                ):
                    ref_uuid = values.pop(uuid_field, None)
                    if ref_uuid:
                        ref = conn.execute(
                            f'SELECT "{pk}" FROM "{table}" WHERE _sync_uuid=? AND _sync_deleted_at IS NULL',
                            (str(ref_uuid),),
                        ).fetchone()
                        if not ref:
                            raise MissingDependencyError(f"Missing {table} dependency {ref_uuid}")
                        values["MemberID" if table == "Members" else "EventID"] = ref[0]
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{entity_type}")')}
            values = {key: value for key, value in values.items()
                      if key in columns and key != PRIMARY_KEYS[entity_type]}
            values.update({"_sync_uuid": entity_uuid, "_sync_version": version,
                           "_sync_updated_at": change.get("changed_at") or _now(),
                           "_sync_deleted_at": None})
            if row:
                assignments = ",".join(f'"{key}"=?' for key in values)
                conn.execute(
                    f'UPDATE "{entity_type}" SET {assignments} WHERE _sync_uuid=?',
                    (*values.values(), entity_uuid),
                )
            else:
                keys = list(values)
                quoted = ",".join(f'"{key}"' for key in keys)
                placeholders = ",".join("?" for _ in keys)
                conn.execute(
                    f'INSERT INTO "{entity_type}" ({quoted}) VALUES ({placeholders})',
                    tuple(values.values()),
                )
        _set_state(conn, state_key, version)
        _set_state(conn, "applying_remote", "0")
        conn.commit()
        return "applied"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_pull_cursor(database_path, cursor):
    cursor = int(cursor)
    if cursor < 0:
        raise ValueError("Cursor cannot be negative.")
    conn = _connect(database_path)
    try:
        _ensure_ready(conn)
        _set_state(conn, "pull_cursor", cursor)
        conn.commit()
    finally:
        conn.close()


def get_pull_cursor(database_path):
    conn = _connect(database_path)
    try:
        _ensure_ready(conn)
        return int(_state(conn, "pull_cursor"))
    finally:
        conn.close()
