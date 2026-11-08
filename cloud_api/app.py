"""Central account service foundation for VBCCPAS Soft."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, create_engine, func, select, update
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
ROLES = {"executive_admin", "officer", "member", "read_only"}

class Base(DeclarativeBase):
    pass

def now_utc():
    return datetime.now(timezone.utc)

def is_after(value, reference):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value > reference

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

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response

@app.get("/v1/health")
def health():
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
