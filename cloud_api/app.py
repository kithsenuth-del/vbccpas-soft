"""Central account service foundation for VBCCPAS Soft."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
import json
import re
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, JSON, Text, UniqueConstraint, create_engine, func, select, update, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
import hashlib
import secrets

DATABASE_URL = os.environ.get("VBCCPAS_DATABASE_URL", "sqlite:///./vbccpas-cloud-test.db")
engine_options = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
bearer = HTTPBearer(auto_error=False)
SESSION_HOURS = int(os.environ.get("VBCCPAS_SESSION_HOURS", "12"))

class Base(DeclarativeBase):
    pass

def now_utc():
    return datetime.now(timezone.utc)

def is_after(value, reference):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return value > reference

class DeploymentIdentity(Base):
    """Binds one database to exactly one society deployment."""
    __tablename__ = "deployment_identity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    society_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ApiSession(Base):
    __tablename__ = "api_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(254), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80))
    subject: Mapped[str | None] = mapped_column(String(254), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    device_name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class SyncRecord(Base):
    __tablename__ = "sync_records"
    __table_args__ = (UniqueConstraint("entity_type", "entity_uuid", name="uq_sync_record_entity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_uuid: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class SyncEvent(Base):
    __tablename__ = "sync_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_uuid: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class SyncReceipt(Base):
    __tablename__ = "sync_receipts"
    change_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_uuid: Mapped[str] = mapped_column(String(36))
    applied_version: Mapped[int] = mapped_column(Integer)
    payload_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

class InviteBody(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    role: str = "member"

class AcceptBody(BaseModel):
    invitation_token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=12, max_length=256)


class DeviceRegisterBody(BaseModel):
    device_name: str = Field(min_length=1, max_length=100)
    platform: str = Field(min_length=1, max_length=40)

class SyncChangeInput(BaseModel):
    change_id: UUID
    entity_type: Literal["Members", "Events", "Inventory", "BorrowedItems", "Attendance"]
    entity_uuid: UUID
    operation: Literal["upsert", "delete"]
    base_version: int = Field(ge=0)
    payload: dict = Field(default_factory=dict)

class SyncPushBody(BaseModel):
    device_id: UUID
    changes: list[SyncChangeInput] = Field(min_length=1, max_length=100)

class AccountStatusBody(BaseModel):
    is_active: bool

def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def make_token() -> str:
    return secrets.token_urlsafe(48)

def password_hash(value: str) -> str:
    if len(value) < 12 or len(value) > 256:
        raise ValueError("Password must be 12–256 characters.")
    return hasher.hash(value)

def password_matches(value: str, encoded: str) -> bool:
    try:
        return hasher.verify(encoded, value)
    except (VerifyMismatchError, VerificationError, ValueError, TypeError):
        return False

app = FastAPI(title="VBCCPAS Central API", version="1.0", docs_url=None, redoc_url=None, openapi_url=None)

DEPLOYMENT_ENV = os.environ.get("VBCCPAS_ENV", "development").strip().lower()
SINGLE_SOCIETY_ID = os.environ.get("VBCCPAS_SINGLE_SOCIETY_ID", "").strip()


def bind_single_society_deployment(db: Session, society_id: str):
    """Persist and verify the one-society identity for this API database."""
    value = str(society_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise RuntimeError("A valid VBCCPAS_SINGLE_SOCIETY_ID is required.")
    identity = db.get(DeploymentIdentity, 1)
    if identity:
        if not secrets.compare_digest(identity.society_id, value):
            raise RuntimeError(
                "This database is already bound to a different society. "
                "Use a separate database and deployment for each society."
            )
        return identity
    identity = DeploymentIdentity(id=1, society_id=value)
    db.add(identity)
    db.commit()
    return identity


def verify_production_deployment():
    """Fail closed unless production uses PostgreSQL and a pinned society identity."""
    if DEPLOYMENT_ENV != "production":
        return
    if not DATABASE_URL.startswith(("postgresql://", "postgresql+")):
        raise RuntimeError("Production cloud API deployments must use PostgreSQL.")
    if not SINGLE_SOCIETY_ID:
        raise RuntimeError("Set VBCCPAS_SINGLE_SOCIETY_ID for this dedicated society deployment.")
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        bind_single_society_deployment(db, SINGLE_SOCIETY_ID)


@app.on_event("startup")
def startup_deployment_guard():
    verify_production_deployment()

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

@app.get("/v1/health")
def health(db: Session = Depends(db_session)):
    try:
        db.execute(select(1))
    except Exception:
        raise HTTPException(503, "Central database is unavailable.")
    return {"status": "ok", "service": "vbccpas-central-api"}

def authenticated(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(db_session)):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Sign in is required.")
    session = db.scalar(select(ApiSession).where(ApiSession.digest == digest(credentials.credentials)))
    if not session or session.revoked_at or is_after(now_utc(), session.expires_at.replace(tzinfo=timezone.utc) if session.expires_at.tzinfo is None else session.expires_at):
        raise HTTPException(401, "Session is invalid or expired.")
    account = db.get(Account, session.account_id)
    if not account or not account.is_active:
        raise HTTPException(401, "Account is unavailable.")
    return account

def administrator(account: Account = Depends(authenticated)):
    if account.role != "executive_admin":
        raise HTTPException(403, "Executive administrator permission required.")
    return account

@app.post("/v1/auth/login")
def login(body: LoginBody, db: Session = Depends(db_session)):
    email = str(body.email).strip().lower()
    account = db.scalar(select(Account).where(Account.email == email))
    now = now_utc()
    if not account or not account.is_active:
        raise HTTPException(401, "Email or password is incorrect.")
    if account.locked_until and is_after(account.locked_until, now):
        raise HTTPException(429, "Too many attempts. Try again later.")
    if not password_matches(body.password, account.password_hash):
        account.failed_logins += 1
        if account.failed_logins >= 5:
            account.failed_logins = 0
            account.locked_until = now + timedelta(minutes=15)
        db.add(AuditEvent(actor_id=account.id, event_type="auth.login_failed", subject=email))
        db.commit()
        raise HTTPException(401, "Email or password is incorrect.")
    account.failed_logins = 0
    account.locked_until = None
    raw = make_token()
    db.add(ApiSession(account_id=account.id, digest=digest(raw), expires_at=now + timedelta(hours=SESSION_HOURS)))
    db.add(AuditEvent(actor_id=account.id, event_type="auth.login", subject=email))
    db.commit()
    return {"access_token": raw, "token_type": "bearer", "expires_in": SESSION_HOURS * 3600,
            "account": {"id": account.id, "email": account.email, "display_name": account.display_name, "role": account.role}}

@app.post("/v1/auth/logout")
def logout(account: Account = Depends(authenticated), credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(db_session)):
    session = db.scalar(select(ApiSession).where(ApiSession.digest == digest(credentials.credentials)))
    if session and not session.revoked_at:
        session.revoked_at = now_utc()
        db.add(AuditEvent(actor_id=account.id, event_type="auth.logout"))
        db.commit()
    return {"ok": True}

@app.get("/v1/auth/me")
def me(account: Account = Depends(authenticated)):
    return {"id": account.id, "email": account.email, "display_name": account.display_name, "role": account.role}

@app.post("/v1/admin/invitations", status_code=201)
def invite(body: InviteBody, admin: Account = Depends(administrator), db: Session = Depends(db_session)):
    email = str(body.email).strip().lower()
    role = body.role.strip().lower()
    if role not in {"officer", "member", "read_only"}:
        raise HTTPException(400, "Choose officer, member, or read_only. Admin roles cannot be assigned by invitation.")
    if db.scalar(select(Account.id).where(Account.email == email)):
        raise HTTPException(409, "An account with this email already exists.")
    raw = make_token()
    db.add(Invitation(email=email, display_name=body.display_name.strip(), role=role,
                      digest=digest(raw), expires_at=now_utc() + timedelta(hours=48), created_by=admin.id))
    db.add(AuditEvent(actor_id=admin.id, event_type="account.invited", subject=email))
    db.commit()
    return {"invitation_token": raw, "email": email, "role": role, "expires_in": 172800}

@app.post("/v1/auth/accept-invitation", status_code=201)
def accept_invitation(body: AcceptBody, db: Session = Depends(db_session)):
    invitation = db.scalar(select(Invitation).where(Invitation.digest == digest(body.invitation_token)))
    now = now_utc()
    if not invitation or invitation.accepted_at or is_after(now, invitation.expires_at):
        raise HTTPException(400, "Invitation is invalid or expired.")
    if db.scalar(select(Account.id).where(Account.email == invitation.email)):
        raise HTTPException(409, "An account with this email already exists.")
    account = Account(email=invitation.email, display_name=invitation.display_name,
                      role=invitation.role, password_hash=password_hash(body.password))
    invitation.accepted_at = now
    db.add(account)
    db.flush()
    db.add(AuditEvent(actor_id=account.id, event_type="account.created", subject=account.email))
    db.commit()
    return {"id": account.id, "email": account.email, "display_name": account.display_name, "role": account.role}

@app.get("/v1/admin/accounts")
def accounts(_admin: Account = Depends(administrator), db: Session = Depends(db_session)):
    rows = db.scalars(select(Account).order_by(Account.created_at)).all()
    return [{"id": row.id, "email": row.email, "display_name": row.display_name, "role": row.role,
             "is_active": row.is_active, "created_at": row.created_at.isoformat()} for row in rows]

@app.patch("/v1/admin/accounts/{account_id}/status")
def update_account_status(account_id: str, body: AccountStatusBody,
                          admin: Account = Depends(administrator), db: Session = Depends(db_session)):
    target = db.get(Account, account_id)
    if not target:
        raise HTTPException(404, "Account not found.")
    if target.id == admin.id and not body.is_active:
        raise HTTPException(400, "You cannot deactivate your own administrator account.")
    if target.role == "executive_admin" and target.is_active and not body.is_active:
        active_admins = db.scalar(select(func.count()).select_from(Account).where(
            Account.role == "executive_admin", Account.is_active.is_(True)))
        if active_admins <= 1:
            raise HTTPException(400, "The last active executive administrator cannot be deactivated.")
    target.is_active = body.is_active
    if not body.is_active:
        db.execute(update(ApiSession).where(ApiSession.account_id == target.id,
                     ApiSession.revoked_at.is_(None)).values(revoked_at=now_utc()))
    db.add(AuditEvent(actor_id=admin.id, event_type="account.activated" if body.is_active else "account.deactivated",
                      subject=target.email))
    db.commit()
    return {"id": target.id, "email": target.email, "is_active": target.is_active}

SYNC_REQUIRED = {"Members": {"Name"}, "Events": {"Title"}, "Inventory": {"Name"}}

SYNC_ENTITIES = {
    "Members": {"Name", "Class", "Roll", "Email"},
    "Events": {"Title", "Date", "Location", "Notes"},
    "Inventory": {"Name", "Quantity", "Notes"},
    "BorrowedItems": {"MemberUUID", "ItemUUID", "BorrowDate", "ReturnDate", "Returned"},
    "Attendance": {"MemberUUID", "EventUUID", "Present", "Timestamp"},
}

def require_device(device_id: UUID, account: Account, db: Session) -> Device:
    device = db.get(Device, str(device_id))
    if not device or device.account_id != account.id or device.revoked_at:
        raise HTTPException(403, "This device is not registered or has been revoked.")
    device.last_seen_at = now_utc()
    return device

def sync_writer(account: Account):
    if account.role not in {"executive_admin", "officer"}:
        raise HTTPException(403, "Your account can view synchronised records but cannot change them.")
    return account

def sync_reader(account: Account):
    if account.role not in {"executive_admin", "officer", "read_only"}:
        raise HTTPException(403, "Your account is not authorised to access shared society records.")
    return account

@app.post("/v1/devices", status_code=201)
def register_device(body: DeviceRegisterBody, account: Account = Depends(authenticated),
                    db: Session = Depends(db_session)):
    active_count = db.scalar(select(func.count()).select_from(Device).where(
        Device.account_id == account.id, Device.revoked_at.is_(None)))
    if active_count >= 10:
        raise HTTPException(409, "This account already has ten active devices. Revoke an unused device first.")
    device = Device(account_id=account.id, device_name=body.device_name.strip(),
                    platform=body.platform.strip())
    db.add(device)
    db.add(AuditEvent(actor_id=account.id, event_type="device.registered", subject=body.device_name[:100]))
    db.commit()
    return {"device_id": device.id, "device_name": device.device_name, "platform": device.platform,
            "created_at": device.created_at.isoformat()}

@app.get("/v1/devices")
def list_devices(account: Account = Depends(authenticated), db: Session = Depends(db_session)):
    rows = db.scalars(select(Device).where(Device.account_id == account.id).order_by(Device.created_at)).all()
    return [{"device_id": row.id, "device_name": row.device_name, "platform": row.platform,
             "created_at": row.created_at.isoformat(), "last_seen_at": row.last_seen_at.isoformat(),
             "revoked": row.revoked_at is not None} for row in rows]

@app.delete("/v1/devices/{device_id}")
def revoke_device(device_id: UUID, account: Account = Depends(authenticated), db: Session = Depends(db_session)):
    device = db.get(Device, str(device_id))
    if not device or device.account_id != account.id:
        raise HTTPException(404, "Device not found.")
    if not device.revoked_at:
        device.revoked_at = now_utc()
        db.add(AuditEvent(actor_id=account.id, event_type="device.revoked", subject=device.device_name))
        db.commit()
    return {"device_id": device.id, "revoked": True}

@app.post("/v1/sync/push")
def sync_push(body: SyncPushBody, account: Account = Depends(authenticated),
              db: Session = Depends(db_session)):
    sync_writer(account)
    device = require_device(body.device_id, account, db)
    # Serialize concurrent writes to the shared version table on PostgreSQL.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("LOCK TABLE sync_records IN SHARE ROW EXCLUSIVE MODE"))
    accepted, conflicts = [], []
    for change in body.changes:
        entity_type = change.entity_type
        entity_uuid = str(change.entity_uuid)
        change_id = str(change.change_id)
        allowed = SYNC_ENTITIES[entity_type]
        if set(change.payload) - allowed:
            raise HTTPException(422, f"Unsupported fields in {entity_type} payload.")
        if change.operation == "upsert" and not SYNC_REQUIRED.get(entity_type, set()).issubset(change.payload):
            raise HTTPException(422, f"{entity_type} payload is missing required fields.")
        if len(json.dumps(change.payload, ensure_ascii=False).encode("utf-8")) > 262144:
            raise HTTPException(413, "A synchronisation record exceeds 256 KB.")
        if change.operation == "delete" and change.payload:
            raise HTTPException(422, "Delete changes must use an empty payload.")
        fingerprint = digest(json.dumps({
            "entity_type": entity_type, "entity_uuid": entity_uuid,
            "operation": change.operation, "base_version": change.base_version,
            "payload": change.payload
        }, sort_keys=True, separators=(",", ":")))
        receipt = db.get(SyncReceipt, change_id)
        if receipt:
            if receipt.account_id != account.id or receipt.device_id != device.id:
                raise HTTPException(409, "Change identifier was already used by another device.")
            if receipt.payload_digest != fingerprint:
                raise HTTPException(409, "Change identifier was reused with different content.")
            accepted.append({"change_id": change_id, "version": receipt.applied_version, "duplicate": True})
            continue
        record = db.scalar(select(SyncRecord).where(
            SyncRecord.entity_type == entity_type, SyncRecord.entity_uuid == entity_uuid).with_for_update())
        current_version = record.version if record else 0
        if change.base_version != current_version:
            conflicts.append({"change_id": change_id, "entity_type": entity_type,
                "entity_uuid": entity_uuid, "local_base_version": change.base_version,
                "server_version": current_version,
                "server_payload": record.payload if record else {},
                "server_deleted": record.deleted if record else False})
            continue
        next_version = current_version + 1
        payload = {} if change.operation == "delete" else change.payload
        deleted = change.operation == "delete"
        if record:
            record.version = next_version
            record.payload = payload
            record.deleted = deleted
            record.updated_at = now_utc()
        else:
            record = SyncRecord(entity_type=entity_type, entity_uuid=entity_uuid,
                version=next_version, payload=payload, deleted=deleted, updated_at=now_utc())
            db.add(record)
        db.flush()
        db.add(SyncEvent(entity_type=entity_type, entity_uuid=entity_uuid, version=next_version,
                         payload=payload, deleted=deleted, changed_at=now_utc()))
        db.add(SyncReceipt(change_id=change_id, account_id=account.id, device_id=device.id,
            entity_type=entity_type, entity_uuid=entity_uuid, applied_version=next_version,
            payload_digest=fingerprint))
        accepted.append({"change_id": change_id, "version": next_version, "duplicate": False})
    db.add(AuditEvent(actor_id=account.id, event_type="sync.push",
                      subject=f"{len(accepted)} accepted; {len(conflicts)} conflicts"))
    db.commit()
    return {"accepted": accepted, "conflicts": conflicts}

@app.get("/v1/sync/pull")
def sync_pull(device_id: UUID, cursor: int = 0, limit: int = 200,
              account: Account = Depends(authenticated), db: Session = Depends(db_session)):
    sync_reader(account)
    if cursor < 0:
        raise HTTPException(400, "Cursor cannot be negative.")
    if limit < 1 or limit > 500:
        raise HTTPException(400, "Pull limit must be between 1 and 500.")
    device = require_device(device_id, account, db)
    rows = db.scalars(select(SyncEvent).where(SyncEvent.id > cursor)
        .order_by(SyncEvent.id).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = rows[-1].id if rows else cursor
    db.add(AuditEvent(actor_id=account.id, event_type="sync.pull", subject=f"device:{device.id}"))
    db.commit()
    return {"changes": [{"sequence": row.id, "entity_type": row.entity_type,
        "entity_uuid": row.entity_uuid, "version": row.version,
        "payload": row.payload, "deleted": row.deleted,
        "changed_at": row.changed_at.isoformat()} for row in rows],
        "next_cursor": next_cursor, "has_more": has_more}

@app.get("/v1/sync/status")
def sync_status(device_id: UUID, account: Account = Depends(authenticated),
                db: Session = Depends(db_session)):
    sync_reader(account)
    device = require_device(device_id, account, db)
    latest = db.scalar(select(func.max(SyncEvent.id))) or 0
    db.commit()
    return {"device_id": device.id, "latest_sequence": latest, "status": "ready"}
