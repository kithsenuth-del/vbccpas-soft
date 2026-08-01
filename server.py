import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import main

BASE_DIR = os.path.dirname(__file__)
DB_PATH = main.DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api(parsed.path)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api(parsed.path)
            return
        self.send_error(404)

    def serve_static(self, path):
        if path in {"", "/"}:
            path = "/index.html"
        file_path = os.path.join(BASE_DIR, path.lstrip("/"))
        if not os.path.exists(file_path):
            self.send_error(404)
            return
        content_type = "text/html; charset=utf-8" if file_path.endswith(".html") else "application/octet-stream"
        with open(file_path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api(self, path):
        if self.command == "GET":
            if path == "/api/summary":
                self.send_json(self.get_summary())
                return
            if path == "/api/members":
                self.send_json(self.list_members())
                return
            if path == "/api/events":
                self.send_json(self.list_events())
                return
            if path == "/api/inventory":
                self.send_json(self.list_inventory())
                return
            if path == "/api/borrowed":
                self.send_json(self.list_borrowed())
                return
            if path == "/api/attendance":
                self.send_json(self.list_attendance())
                return
            if path == "/api/users":
                self.send_json(self.list_users())
                return

        if self.command == "POST":
            if path == "/api/members":
                self.send_json(self.save_member())
                return
            if path == "/api/events":
                self.send_json(self.save_event())
                return
            if path == "/api/inventory":
                self.send_json(self.save_inventory())
                return
            if path == "/api/borrowed":
                self.send_json(self.save_borrowed())
                return
            if path == "/api/attendance":
                self.send_json(self.save_attendance())
                return
            if path == "/api/users":
                self.send_json(self.save_user())
                return

        self.send_error(404)

    def log_message(self, format, *args):
        return

    def send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body) if body else {}

    def get_summary(self):
        conn = get_db()
        counts = {
            "members": conn.execute("SELECT COUNT(*) FROM Members").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM Events").fetchone()[0],
            "inventory": conn.execute("SELECT COUNT(*) FROM Inventory").fetchone()[0],
            "borrowed": conn.execute("SELECT COUNT(*) FROM BorrowedItems").fetchone()[0],
            "attendance": conn.execute("SELECT COUNT(*) FROM Attendance").fetchone()[0],
            "users": conn.execute("SELECT COUNT(*) FROM Users").fetchone()[0],
        }
        conn.close()
        return counts

    def list_members(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT MemberID AS id, Name AS name, Class AS class, Roll AS roll, Email AS email FROM Members ORDER BY MemberID DESC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_events(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT EventID AS id, Title AS title, Date AS date, Location AS location, Notes AS notes FROM Events ORDER BY EventID DESC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_inventory(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT ItemID AS id, Name AS name, Quantity AS quantity, Notes AS notes FROM Inventory ORDER BY ItemID DESC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_borrowed(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT BorrowID AS id, ItemID AS item_id, MemberID AS member_id, BorrowDate AS borrow_date, ReturnDate AS return_date, Returned AS returned FROM BorrowedItems ORDER BY BorrowID DESC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_attendance(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT AttID AS id, MemberID AS member_id, EventID AS event_id, Present AS present, Timestamp AS timestamp FROM Attendance ORDER BY AttID DESC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_users(self):
        conn = get_db()
        rows = conn.execute(
            "SELECT UserID AS id, Username AS username, Role AS role FROM Users ORDER BY UserID DESC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def save_member(self):
        data = self.read_json()
        conn = get_db()
        if data.get("id"):
            conn.execute(
                "UPDATE Members SET Name = ?, Class = ?, Roll = ?, Email = ? WHERE MemberID = ?",
                (data.get("name", ""), data.get("class", ""), data.get("roll", ""), data.get("email", ""), data["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO Members (Name, Class, Roll, Email) VALUES (?, ?, ?, ?)",
                (data.get("name", ""), data.get("class", ""), data.get("roll", ""), data.get("email", "")),
            )
        conn.commit()
        conn.close()
        return {"ok": True}

    def save_event(self):
        data = self.read_json()
        conn = get_db()
        if data.get("id"):
            conn.execute(
                "UPDATE Events SET Title = ?, Date = ?, Location = ?, Notes = ? WHERE EventID = ?",
                (data.get("title", ""), data.get("date", ""), data.get("location", ""), data.get("notes", ""), data["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO Events (Title, Date, Location, Notes) VALUES (?, ?, ?, ?)",
                (data.get("title", ""), data.get("date", ""), data.get("location", ""), data.get("notes", "")),
            )
        conn.commit()
        conn.close()
        return {"ok": True}

    def save_inventory(self):
        data = self.read_json()
        conn = get_db()
        quantity = int(data.get("quantity", 0) or 0)
        if data.get("id"):
            conn.execute(
                "UPDATE Inventory SET Name = ?, Quantity = ?, Notes = ? WHERE ItemID = ?",
                (data.get("name", ""), quantity, data.get("notes", ""), data["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO Inventory (Name, Quantity, Notes) VALUES (?, ?, ?)",
                (data.get("name", ""), quantity, data.get("notes", "")),
            )
        conn.commit()
        conn.close()
        return {"ok": True}

    def save_borrowed(self):
        data = self.read_json()
        conn = get_db()
        returned = 1 if str(data.get("returned", "0")).lower() in {"1", "true", "yes", "on"} else 0
        if data.get("id"):
            conn.execute(
                "UPDATE BorrowedItems SET ItemID = ?, MemberID = ?, BorrowDate = ?, ReturnDate = ?, Returned = ? WHERE BorrowID = ?",
                (data.get("item_id", ""), data.get("member_id", ""), data.get("borrow_date", ""), data.get("return_date", ""), returned, data["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO BorrowedItems (ItemID, MemberID, BorrowDate, ReturnDate, Returned) VALUES (?, ?, ?, ?, ?)",
                (data.get("item_id", ""), data.get("member_id", ""), data.get("borrow_date", ""), data.get("return_date", ""), returned),
            )
        conn.commit()
        conn.close()
        return {"ok": True}

    def save_attendance(self):
        data = self.read_json()
        conn = get_db()
        present = 1 if str(data.get("present", "0")).lower() in {"1", "true", "yes", "on"} else 0
        if data.get("id"):
            conn.execute(
                "UPDATE Attendance SET MemberID = ?, EventID = ?, Present = ?, Timestamp = ? WHERE AttID = ?",
                (data.get("member_id", ""), data.get("event_id", ""), present, data.get("timestamp", ""), data["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO Attendance (MemberID, EventID, Present, Timestamp) VALUES (?, ?, ?, ?)",
                (data.get("member_id", ""), data.get("event_id", ""), present, data.get("timestamp", "")),
            )
        conn.commit()
        conn.close()
        return {"ok": True}

    def save_user(self):
        data = self.read_json()
        conn = get_db()
        password = main.hash_password(data.get("password", "")) if data.get("password") else None
        if data.get("id"):
            if password:
                conn.execute(
                    "UPDATE Users SET Username = ?, Password = ?, Role = ? WHERE UserID = ?",
                    (data.get("username", ""), password, data.get("role", "member"), data["id"]),
                )
            else:
                conn.execute(
                    "UPDATE Users SET Username = ?, Role = ? WHERE UserID = ?",
                    (data.get("username", ""), data.get("role", "member"), data["id"]),
                )
        else:
            conn.execute(
                "INSERT INTO Users (Username, Password, Role) VALUES (?, ?, ?)",
                (data.get("username", ""), password or main.hash_password("welcome"), data.get("role", "member")),
            )
        conn.commit()
        conn.close()
        return {"ok": True}


if __name__ == "__main__":
    main.setup_database()
    port = int(os.environ.get("PORT", 8000))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving Tailwind UI at http://127.0.0.1:{port}/")
    server.serve_forever()
