"""Local-only WebAuthn passkey support for the single executive administrator."""
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from base64 import urlsafe_b64encode

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

RP_ID = "localhost"
RP_NAME = "VBCCPAS Soft"
ORIGIN = "http://localhost:8000"
PENDING_TTL_SECONDS = 300
BOOTSTRAP_TOKEN = secrets.token_urlsafe(32)
_PENDING = {}
_PENDING_LOCK = threading.Lock()


def _b64u(value):
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@contextmanager
def _db(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(db_path):
    with _db(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS Passkeys (
                CredentialID TEXT PRIMARY KEY,
                PublicKey TEXT NOT NULL,
                SignCount INTEGER NOT NULL DEFAULT 0,
                DeviceType TEXT NOT NULL,
                BackedUp INTEGER NOT NULL DEFAULT 0,
                Transports TEXT NOT NULL DEFAULT '[]',
                CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS PasskeyMeta (
                MetaKey TEXT PRIMARY KEY,
                MetaValue TEXT NOT NULL
            )
        """)


def configured(db_path):
    initialize(db_path)
    with _db(db_path) as conn:
        return conn.execute("SELECT 1 FROM Passkeys LIMIT 1").fetchone() is not None


def _store_pending(kind, challenge):
    now = time.time()
    transaction = secrets.token_urlsafe(24)
    with _PENDING_LOCK:
        expired = [key for key, value in _PENDING.items() if value["expires"] < now]
        for key in expired:
            _PENDING.pop(key, None)
        if len(_PENDING) >= 100:
            raise RuntimeError("Too many pending authentication attempts.")
        _PENDING[transaction] = {
            "kind": kind,
            "challenge": challenge,
            "expires": now + PENDING_TTL_SECONDS,
        }
    return transaction


def _take_pending(transaction, kind):
    with _PENDING_LOCK:
        state = _PENDING.pop(str(transaction), None)
    if not state or state["kind"] != kind or state["expires"] < time.time():
        raise ValueError("The passkey request expired. Please try again.")
    return state["challenge"]


def begin_registration(db_path, supplied_token):
    if not secrets.compare_digest(str(supplied_token or ""), BOOTSTRAP_TOKEN):
        raise PermissionError("The local setup token is incorrect.")
    if configured(db_path):
        raise PermissionError("The executive administrator passkey is already configured.")
    with _db(db_path) as conn:
        row = conn.execute("SELECT MetaValue FROM PasskeyMeta WHERE MetaKey='user_handle'").fetchone()
        user_handle = base64url_to_bytes(row["MetaValue"]) if row else secrets.token_bytes(32)
        if not row:
            conn.execute(
                "INSERT INTO PasskeyMeta (MetaKey, MetaValue) VALUES ('user_handle', ?)",
                (_b64u(user_handle),),
            )
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=user_handle,
        user_name="executive-administrator",
        user_display_name="Executive Administrator",
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    transaction = _store_pending("registration", options.challenge)
    return {"transaction": transaction, "options": json.loads(options_to_json(options))}


def complete_registration(db_path, supplied_token, transaction, credential):
    if not secrets.compare_digest(str(supplied_token or ""), BOOTSTRAP_TOKEN):
        raise PermissionError("The local setup token is incorrect.")
    if configured(db_path):
        raise PermissionError("An executive administrator passkey is already registered.")
    if not isinstance(credential, dict) or credential.get("authenticatorAttachment") != "platform":
        raise ValueError("Use this computer's built-in authenticator, such as Windows Hello.")
    challenge = _take_pending(transaction, "registration")
    verification = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        require_user_verification=True,
    )
    device_type = getattr(verification.credential_device_type, "value", str(verification.credential_device_type))
    if device_type != CredentialDeviceType.SINGLE_DEVICE.value or verification.credential_backed_up:
        raise ValueError("This passkey appears to be synced or backed up. Create a device-bound passkey on this computer instead.")
    credential_id = _b64u(verification.credential_id)
    transports = credential.get("response", {}).get("transports", [])
    with _db(db_path) as conn:
        if conn.execute("SELECT 1 FROM Passkeys LIMIT 1").fetchone():
            raise PermissionError("An executive administrator passkey is already registered.")
        conn.execute(
            """INSERT INTO Passkeys
               (CredentialID, PublicKey, SignCount, DeviceType, BackedUp, Transports)
               VALUES (?, ?, ?, ?, 0, ?)""",
            (
                credential_id,
                _b64u(verification.credential_public_key),
                int(verification.sign_count),
                device_type,
                json.dumps(transports if isinstance(transports, list) else []),
            ),
        )
    return {"ok": True, "message": "The executive administrator passkey is registered on this computer."}


def begin_login(db_path):
    if not configured(db_path):
        raise PermissionError("The executive administrator passkey has not been set up yet.")
    with _db(db_path) as conn:
        rows = conn.execute("SELECT CredentialID, Transports FROM Passkeys").fetchall()
    allowed = [
        PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(row["CredentialID"]),
            transports=json.loads(row["Transports"] or "[]") or None,
        )
        for row in rows
    ]
    options = generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=allowed,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    transaction = _store_pending("authentication", options.challenge)
    return {"transaction": transaction, "options": json.loads(options_to_json(options))}


def complete_login(db_path, transaction, credential):
    if not isinstance(credential, dict):
        raise ValueError("Invalid passkey response.")
    challenge = _take_pending(transaction, "authentication")
    credential_id = str(credential.get("id", ""))
    with _db(db_path) as conn:
        row = conn.execute("SELECT * FROM Passkeys WHERE CredentialID=?", (credential_id,)).fetchone()
    if not row:
        raise PermissionError("This passkey is not registered for the executive administrator.")
    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        credential_public_key=base64url_to_bytes(row["PublicKey"]),
        credential_current_sign_count=int(row["SignCount"]),
        require_user_verification=True,
    )
    device_type = getattr(verification.credential_device_type, "value", str(verification.credential_device_type))
    if device_type != CredentialDeviceType.SINGLE_DEVICE.value or verification.credential_backed_up:
        raise PermissionError("Only the registered device-bound passkey is permitted.")
    with _db(db_path) as conn:
        conn.execute(
            "UPDATE Passkeys SET SignCount=?, DeviceType=?, BackedUp=0 WHERE CredentialID=?",
            (int(verification.new_sign_count), device_type, credential_id),
        )
    return True


def passkey_summary(db_path):
    if not configured(db_path):
        return {"configured": False}
    with _db(db_path) as conn:
        row = conn.execute("SELECT CreatedAt, DeviceType, BackedUp FROM Passkeys LIMIT 1").fetchone()
    return {
        "configured": True,
        "created_at": row["CreatedAt"],
        "device_type": row["DeviceType"],
        "backed_up": bool(row["BackedUp"]),
    }
