import unittest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from cloud_api.app import Account, Base, app, db_session, password_hash

class CloudApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()
        app.dependency_overrides[db_session] = override_db
        with self.Session() as db:
            db.add(Account(email="admin@example.com", display_name="Test Admin", role="executive_admin",
                           password_hash=password_hash("Correct-Horse-Battery-42!")))
            db.commit()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def login(self, email="admin@example.com", password="Correct-Horse-Battery-42!"):
        return self.client.post("/v1/auth/login", json={"email": email, "password": password})

    def test_health_is_public(self):
        response = self.client.get("/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_login_and_logout_revoke_session(self):
        response = self.login()
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        self.assertEqual(self.client.get("/v1/auth/me", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/v1/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/v1/auth/me", headers=headers).status_code, 401)

    def test_bad_password_is_rejected_and_does_not_reveal_account(self):
        known = self.login(password="incorrect")
        unknown = self.login(email="unknown@example.com", password="incorrect")
        self.assertEqual(known.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(known.json(), unknown.json())

    def test_invitation_creates_member_once(self):
        token = self.login().json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        invite = self.client.post("/v1/admin/invitations", headers=headers,
            json={"email": "member@example.com", "display_name": "Test Member", "role": "member"})
        self.assertEqual(invite.status_code, 201, invite.text)
        raw_invite = invite.json()["invitation_token"]
        accepted = self.client.post("/v1/auth/accept-invitation", json={
            "invitation_token": raw_invite, "password": "Member-Strong-Password-42!"})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        repeated = self.client.post("/v1/auth/accept-invitation", json={
            "invitation_token": raw_invite, "password": "Member-Strong-Password-42!"})
        self.assertEqual(repeated.status_code, 400)

    def test_non_admin_cannot_invite(self):
        with self.Session() as db:
            db.add(Account(email="officer@example.com", display_name="Officer", role="officer",
                           password_hash=password_hash("Officer-Strong-Password-42!")))
            db.commit()
        token = self.login("officer@example.com", "Officer-Strong-Password-42!").json()["access_token"]
        response = self.client.post("/v1/admin/invitations", headers={"Authorization": "Bearer " + token},
            json={"email": "new@example.com", "display_name": "New", "role": "member"})
        self.assertEqual(response.status_code, 403)

    def test_invitation_cannot_create_another_executive_admin(self):
        token = self.login().json()["access_token"]
        response = self.client.post("/v1/admin/invitations", headers={"Authorization": "Bearer " + token},
            json={"email": "other-admin@example.com", "display_name": "Other", "role": "executive_admin"})
        self.assertEqual(response.status_code, 400)


    def test_admin_can_suspend_account_and_revoke_its_session(self):
        with self.Session() as db:
            member = Account(email="suspend@example.com", display_name="Suspend Me", role="member",
                             password_hash=password_hash("Suspend-Strong-Password-42!"))
            db.add(member)
            db.commit()
            member_id = member.id
        member_token = self.login("suspend@example.com", "Suspend-Strong-Password-42!").json()["access_token"]
        admin_token = self.login().json()["access_token"]
        response = self.client.patch("/v1/admin/accounts/" + member_id + "/status",
            headers={"Authorization": "Bearer " + admin_token}, json={"is_active": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/v1/auth/me",
            headers={"Authorization": "Bearer " + member_token}).status_code, 401)

    def test_last_active_admin_cannot_be_suspended(self):
        token = self.login().json()["access_token"]
        with self.Session() as db:
            admin_id = db.query(Account).filter_by(email="admin@example.com").one().id
        response = self.client.patch("/v1/admin/accounts/" + admin_id + "/status",
            headers={"Authorization": "Bearer " + token}, json={"is_active": False})
        self.assertEqual(response.status_code, 400)

if __name__ == "__main__":
    unittest.main()
