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


    def register_device(self, token, name="Test desktop"):
        return self.client.post("/v1/devices",
            headers={"Authorization": "Bearer " + token},
            json={"device_name": name, "platform": "Windows 11"})

    def test_device_sync_push_pull_idempotency_and_conflict_detection(self):
        token = self.login().json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        registered = self.register_device(token)
        self.assertEqual(registered.status_code, 201, registered.text)
        device_id = registered.json()["device_id"]
        entity_uuid = "f45a21d1-5b76-4a4e-9a2c-1f5b9c8d5a10"
        change_id = "9c2c2f8b-4d32-4e1b-a0b5-22b46b8c0c14"
        change = {"change_id": change_id, "entity_type": "Members",
                  "entity_uuid": entity_uuid, "operation": "upsert",
                  "base_version": 0, "payload": {"Name": "Synced member", "Class": "10-A"}}
        pushed = self.client.post("/v1/sync/push", headers=headers,
            json={"device_id": device_id, "changes": [change]})
        self.assertEqual(pushed.status_code, 200, pushed.text)
        self.assertEqual(pushed.json()["accepted"][0]["version"], 1)
        duplicate = self.client.post("/v1/sync/push", headers=headers,
            json={"device_id": device_id, "changes": [change]})
        self.assertTrue(duplicate.json()["accepted"][0]["duplicate"])
        pulled = self.client.get("/v1/sync/pull",
            params={"device_id": device_id, "cursor": 0}, headers=headers)
        self.assertEqual(pulled.status_code, 200, pulled.text)
        self.assertEqual(pulled.json()["changes"][0]["payload"]["Name"], "Synced member")
        self.assertGreater(pulled.json()["next_cursor"], 0)
        stale = dict(change, change_id="b04d1c75-7c6a-43a9-8b5f-1ad5b5f19b44",
                     payload={"Name": "Stale edit"})
        conflict = self.client.post("/v1/sync/push", headers=headers,
            json={"device_id": device_id, "changes": [stale]})
        self.assertEqual(conflict.status_code, 200, conflict.text)
        self.assertEqual(conflict.json()["conflicts"][0]["server_version"], 1)
        self.assertEqual(conflict.json()["conflicts"][0]["server_payload"]["Name"], "Synced member")

    def test_revoked_device_cannot_sync(self):
        token = self.login().json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        registered = self.register_device(token)
        device_id = registered.json()["device_id"]
        self.assertEqual(self.client.delete("/v1/devices/" + device_id,
            headers=headers).status_code, 200)
        denied = self.client.get("/v1/sync/pull",
            params={"device_id": device_id, "cursor": 0}, headers=headers)
        self.assertEqual(denied.status_code, 403)

    def test_read_only_account_cannot_push_sync_changes(self):
        with self.Session() as db:
            db.add(Account(email="readonly@example.com", display_name="Read Only", role="read_only",
                           password_hash=password_hash("Read-Only-Strong-Password-42!")))
            db.commit()
        token = self.login("readonly@example.com", "Read-Only-Strong-Password-42!").json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        device = self.register_device(token).json()["device_id"]
        response = self.client.post("/v1/sync/push", headers=headers, json={
            "device_id": device, "changes": [{
                "change_id": "bc0b3b4e-5f74-4c2f-9a5a-7e8f9b1c2d3e",
                "entity_type": "Members",
                "entity_uuid": "aa0b3b4e-5f74-4c2f-9a5a-7e8f9b1c2d3e",
                "operation": "upsert", "base_version": 0,
                "payload": {"Name": "Not allowed"}
            }]
        })
        self.assertEqual(response.status_code, 403)

    def test_sync_rejects_account_and_security_tables(self):
        token = self.login().json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        device = self.register_device(token).json()["device_id"]
        response = self.client.post("/v1/sync/push", headers=headers, json={
            "device_id": device, "changes": [{
                "change_id": "dc0b3b4e-5f74-4c2f-9a5a-7e8f9b1c2d3e",
                "entity_type": "Members",
                "entity_uuid": "da0b3b4e-5f74-4c2f-9a5a-7e8f9b1c2d3e",
                "operation": "upsert", "base_version": 0,
                "payload": {"Name": "Bad field", "Password": "must never sync"}
            }]
        })
        self.assertEqual(response.status_code, 422)


    def test_member_account_cannot_pull_shared_society_records(self):
        with self.Session() as db:
            db.add(Account(email="member-sync@example.com", display_name="Member", role="member",
                           password_hash=password_hash("Member-Strong-Password-42!")))
            db.commit()
        token = self.login("member-sync@example.com", "Member-Strong-Password-42!").json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        device = self.register_device(token).json()["device_id"]
        response = self.client.get("/v1/sync/pull",
            params={"device_id": device, "cursor": 0}, headers=headers)
        self.assertEqual(response.status_code, 403)

if __name__ == "__main__":
    unittest.main()
