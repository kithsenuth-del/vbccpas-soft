"""Provision the first cloud executive administrator exactly once."""
import os
from sqlalchemy import select
from .app import Account, AuditEvent, Base, SessionLocal, engine, now_utc, password_hash, verify_production_deployment

def main():
    email = os.environ.get("VBCCPAS_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("VBCCPAS_BOOTSTRAP_ADMIN_PASSWORD", "")
    display_name = os.environ.get("VBCCPAS_BOOTSTRAP_ADMIN_NAME", "Executive Administrator").strip()
    if not email or not password:
        raise SystemExit("Set VBCCPAS_BOOTSTRAP_ADMIN_EMAIL and VBCCPAS_BOOTSTRAP_ADMIN_PASSWORD using deployment secrets.")
    verify_production_deployment()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(Account.id).limit(1)):
            raise SystemExit("Bootstrap refused: an account already exists. Use the invitation workflow.")
        account = Account(email=email, display_name=display_name, role="executive_admin", password_hash=password_hash(password))
        db.add(account)
        db.flush()
        db.add(AuditEvent(actor_id=account.id, event_type="account.bootstrap_created", subject=email))
        db.commit()
    print("Executive administrator created. Remove bootstrap secrets from the deployment environment.")

if __name__ == "__main__":
    main()
