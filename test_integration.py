"""Isolated web/desktop integration tests for VBCCPAS Soft.

All tests use a disposable SQLite database. They never open or modify society.db.
The desktop UI itself requires Windows/Tk and is not launched in CI; desktop
checks exercise its real database-facing methods and SQL data contract.
"""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import main
import passkey_auth
import server


class FakeTree:
    """Small Treeview substitute for exercising the desktop return action."""
    def __init__(self, borrow_id):
        self.borrow_id = borrow_id

    def selection(self):
        return ["selected"]

    def item(self, _item):
        return {"values": [self.borrow_id]}


class WebDesktopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="vbccpas-integration-")
        self.db_path = os.path.join(self.temp_dir.name, "integration-only.sqlite3")
        self.old_main_db = main.DB_PATH
        self.old_server_db = server.DB_PATH
        self.old_port = server.PORT
        self.old_host = server.EXPECTED_HOST
        self.old_origin = server.EXPECTED_ORIGIN
        self.old_auth_origin = passkey_auth.ORIGIN
        self.old_admin_password = os.environ.get("VBCCPAS_ADMIN_PASSWORD")

        main.DB_PATH = self.db_path
        server.DB_PATH = self.db_path
        os.environ["VBCCPAS_ADMIN_PASSWORD"] = "temporary-integration-password"
        main.setup_database()
        passkey_auth.initialize(self.db_path)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        server.PORT = self.httpd.server_port
        server.EXPECTED_HOST = "localhost:" + str(server.PORT)
        server.EXPECTED_ORIGIN = "http://localhost:" + str(server.PORT)
        passkey_auth.ORIGIN = server.EXPECTED_ORIGIN
        server.SESSIONS.clear()
        self.session_token = "integration-test-session"
        self.csrf_token = "integration-test-csrf"
        with server.SESSIONS_LOCK:
            server.SESSIONS[self.session_token] = {
                "csrf": self.csrf_token,
                "expires": __import__("time").time() + 600,
            }
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(server.PORT)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server.SESSIONS.clear()
        main.DB_PATH = self.old_main_db
        server.DB_PATH = self.old_server_db
        server.PORT = self.old_port
        server.EXPECTED_HOST = self.old_host
        server.EXPECTED_ORIGIN = self.old_origin
        passkey_auth.ORIGIN = self.old_auth_origin
        if self.old_admin_password is None:
            os.environ.pop("VBCCPAS_ADMIN_PASSWORD", None)
        else:
            os.environ["VBCCPAS_ADMIN_PASSWORD"] = self.old_admin_password
        self.temp_dir.cleanup()

    def request(self, path, method="GET", payload=None, csrf=True):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Host": server.EXPECTED_HOST,
            "Cookie": server.SESSION_COOKIE + "=" + self.session_token,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Origin"] = server.EXPECTED_ORIGIN
            if csrf:
                headers["X-CSRF-Token"] = self.csrf_token
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def db(self):
        return sqlite3.connect(self.db_path)

    def test_web_member_is_visible_to_desktop_dashboard(self):
        status, result = self.request("/api/members", "POST", {
            "name": "TEST Web Member", "class": "TEST-10A",
            "roll": "TEST-001", "email": "web-test@example.invalid",
        })
        self.assertEqual(status, 200, result)
        desktop = main.App.__new__(main.App)
        self.assertEqual(desktop.count_members(), 1)

    def test_desktop_database_write_is_visible_to_web_dashboard(self):
        with self.db() as conn:
            conn.execute(
                "INSERT INTO Members (Name, Class, Roll, Email) VALUES (?, ?, ?, ?)",
                ("TEST Desktop Member", "TEST-11B", "TEST-002", "desktop-test@example.invalid"),
            )
        status, members = self.request("/api/members")
        self.assertEqual(status, 200)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["name"], "TEST Desktop Member")

    def test_web_create_and_update_records_across_modules(self):
        records = [
            ("/api/events", {"title": "TEST Integration Event", "date": "2099-05-20", "location": "TEST Hall", "notes": "Disposable"}),
            ("/api/inventory", {"name": "TEST Projector", "quantity": 3, "notes": "Disposable"}),
        ]
        for path, payload in records:
            status, result = self.request(path, "POST", payload)
            self.assertEqual(status, 200, result)

        with self.db() as conn:
            member_id = conn.execute(
                "INSERT INTO Members (Name) VALUES (?)", ("TEST Attendance Member",)
            ).lastrowid
            event_id = conn.execute("SELECT EventID FROM Events").fetchone()[0]
        status, result = self.request("/api/attendance", "POST", {
            "member_id": member_id, "event_id": event_id,
            "present": True, "timestamp": "2099-05-20T09:00:00",
        })
        self.assertEqual(status, 200, result)

        for path, expected in [
            ("/api/events", "TEST Integration Event"),
            ("/api/inventory", "TEST Projector"),
        ]:
            status, rows = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(rows[0]["title"] if path.endswith("events") else rows[0]["name"], expected)
        status, attendance = self.request("/api/attendance")
        self.assertEqual(status, 200)
        self.assertEqual(attendance[0]["present"], 1)

    def test_web_member_update_is_reflected_in_shared_database(self):
        with self.db() as conn:
            member_id = conn.execute(
                "INSERT INTO Members (Name) VALUES (?)", ("TEST Before Edit",)
            ).lastrowid
        status, result = self.request("/api/members", "POST", {
            "id": member_id, "name": "TEST After Edit", "class": "TEST-12C",
            "roll": "TEST-003", "email": "edited@example.invalid",
        })
        self.assertEqual(status, 200, result)
        with self.db() as conn:
            row = conn.execute("SELECT Name, Class FROM Members WHERE MemberID=?", (member_id,)).fetchone()
        self.assertEqual(row, ("TEST After Edit", "TEST-12C"))

    def test_desktop_return_updates_data_seen_by_web_api(self):
        with self.db() as conn:
            member_id = conn.execute("INSERT INTO Members (Name) VALUES (?)", ("TEST Borrower",)).lastrowid
            item_id = conn.execute(
                "INSERT INTO Inventory (Name, Quantity) VALUES (?, ?)", ("TEST Camera", 0)
            ).lastrowid
            borrow_id = conn.execute(
                "INSERT INTO BorrowedItems (ItemID, MemberID, BorrowDate, Returned) VALUES (?, ?, ?, 0)",
                (item_id, member_id, "2099-01-01"),
            ).lastrowid

        desktop = main.App.__new__(main.App)
        desktop.populate_borrowed = lambda _tree: None
        desktop.return_selected_borrow(FakeTree(borrow_id))

        status, borrowed = self.request("/api/borrowed")
        self.assertEqual(status, 200)
        self.assertEqual(borrowed[0]["returned"], 1)
        with self.db() as conn:
            quantity = conn.execute("SELECT Quantity FROM Inventory WHERE ItemID=?", (item_id,)).fetchone()[0]
        self.assertEqual(quantity, 1)

    def test_negative_inventory_quantity_is_rejected(self):
        status, result = self.request("/api/inventory", "POST", {
            "name": "TEST Invalid Stock", "quantity": -1,
        })
        self.assertEqual(status, 400, result)

    def test_write_without_csrf_is_rejected(self):
        status, _ = self.request("/api/members", "POST", {"name": "TEST No CSRF"}, csrf=False)
        self.assertEqual(status, 403)
        with self.db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM Members").fetchone()[0]
        self.assertEqual(count, 0)

    def test_web_borrow_decrements_inventory_like_desktop(self):
        """A new outstanding web loan reserves one unit of inventory."""
        with self.db() as conn:
            member_id = conn.execute("INSERT INTO Members (Name) VALUES (?)", ("TEST Borrower",)).lastrowid
            item_id = conn.execute(
                "INSERT INTO Inventory (Name, Quantity) VALUES (?, ?)", ("TEST Loan Item", 2)
            ).lastrowid
        status, result = self.request("/api/borrowed", "POST", {
            "item_id": item_id, "member_id": member_id,
            "borrow_date": "2099-01-01", "returned": False,
        })
        self.assertEqual(status, 200, result)
        with self.db() as conn:
            quantity = conn.execute("SELECT Quantity FROM Inventory WHERE ItemID=?", (item_id,)).fetchone()[0]
        self.assertEqual(quantity, 1, "Web borrowing should reduce available stock by one")

    def test_web_summary_counts_only_outstanding_borrowings(self):
        """Returned loans are excluded from the currently borrowed summary."""
        with self.db() as conn:
            member_id = conn.execute("INSERT INTO Members (Name) VALUES (?)", ("TEST Borrower",)).lastrowid
            item_id = conn.execute("INSERT INTO Inventory (Name, Quantity) VALUES (?, 1)", ("TEST Returned Item",)).lastrowid
            conn.execute(
                "INSERT INTO BorrowedItems (ItemID, MemberID, BorrowDate, ReturnDate, Returned) VALUES (?, ?, ?, ?, 1)",
                (item_id, member_id, "2099-01-01", "2099-01-02"),
            )
        status, summary = self.request("/api/summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["borrowed"], 0, "Returned loans should not count as currently borrowed")


    def test_web_borrow_return_and_reopen_adjust_stock_once(self):
        with self.db() as conn:
            member_id = conn.execute(
                "INSERT INTO Members (Name) VALUES (?)", ("TEST Loan Lifecycle Member",)
            ).lastrowid
            item_id = conn.execute(
                "INSERT INTO Inventory (Name, Quantity) VALUES (?, ?)",
                ("TEST Loan Lifecycle Camera", 2),
            ).lastrowid

        status, result = self.request("/api/borrowed", "POST", {
            "item_id": item_id, "member_id": member_id,
            "borrow_date": "2099-01-01", "returned": False,
        })
        self.assertEqual(status, 200, result)
        with self.db() as conn:
            borrow_id = conn.execute(
                "SELECT BorrowID FROM BorrowedItems WHERE ItemID=?", (item_id,)
            ).fetchone()[0]
            quantity = conn.execute(
                "SELECT Quantity FROM Inventory WHERE ItemID=?", (item_id,)
            ).fetchone()[0]
        self.assertEqual(quantity, 1)

        for returned, expected_quantity in ((True, 2), (True, 2), (False, 1)):
            status, result = self.request("/api/borrowed", "POST", {
                "id": borrow_id, "item_id": item_id, "member_id": member_id,
                "borrow_date": "2099-01-01", "return_date": "2099-01-02",
                "returned": returned,
            })
            self.assertEqual(status, 200, result)
            with self.db() as conn:
                quantity = conn.execute(
                    "SELECT Quantity FROM Inventory WHERE ItemID=?", (item_id,)
                ).fetchone()[0]
            self.assertEqual(quantity, expected_quantity)

    def test_web_borrow_rejects_out_of_stock_without_partial_stock_change(self):
        with self.db() as conn:
            member_id = conn.execute(
                "INSERT INTO Members (Name) VALUES (?)", ("TEST Stock Transfer Member",)
            ).lastrowid
            old_item = conn.execute(
                "INSERT INTO Inventory (Name, Quantity) VALUES (?, 0)",
                ("TEST Existing Loan Item",),
            ).lastrowid
            unavailable_item = conn.execute(
                "INSERT INTO Inventory (Name, Quantity) VALUES (?, 0)",
                ("TEST Unavailable Transfer Item",),
            ).lastrowid
            borrow_id = conn.execute(
                "INSERT INTO BorrowedItems (ItemID, MemberID, BorrowDate, Returned) "
                "VALUES (?, ?, ?, 0)",
                (old_item, member_id, "2099-01-01"),
            ).lastrowid

        status, result = self.request("/api/borrowed", "POST", {
            "id": borrow_id, "item_id": unavailable_item,
            "member_id": member_id, "borrow_date": "2099-01-01",
            "returned": False,
        })
        self.assertEqual(status, 400, result)
        with self.db() as conn:
            old_quantity = conn.execute(
                "SELECT Quantity FROM Inventory WHERE ItemID=?", (old_item,)
            ).fetchone()[0]
            new_quantity = conn.execute(
                "SELECT Quantity FROM Inventory WHERE ItemID=?", (unavailable_item,)
            ).fetchone()[0]
            stored_item, stored_returned = conn.execute(
                "SELECT ItemID, Returned FROM BorrowedItems WHERE BorrowID=?",
                (borrow_id,),
            ).fetchone()
        self.assertEqual((old_quantity, new_quantity), (0, 0))
        self.assertEqual((stored_item, stored_returned), (old_item, 0))

    def test_web_create_already_returned_loan_does_not_change_stock(self):
        with self.db() as conn:
            member_id = conn.execute(
                "INSERT INTO Members (Name) VALUES (?)", ("TEST Returned Loan Member",)
            ).lastrowid
            item_id = conn.execute(
                "INSERT INTO Inventory (Name, Quantity) VALUES (?, ?)",
                ("TEST Already Returned Item", 2),
            ).lastrowid
        status, result = self.request("/api/borrowed", "POST", {
            "item_id": item_id, "member_id": member_id,
            "borrow_date": "2099-01-01", "returned": True,
        })
        self.assertEqual(status, 200, result)
        with self.db() as conn:
            quantity = conn.execute(
                "SELECT Quantity FROM Inventory WHERE ItemID=?", (item_id,)
            ).fetchone()[0]
        self.assertEqual(quantity, 2)



if __name__ == "__main__":
    unittest.main(verbosity=2)
