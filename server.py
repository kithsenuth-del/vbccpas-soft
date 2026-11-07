import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import main

if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = main.DB_PATH
MAX_BODY_BYTES = 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def get_db():
    return sqlite3.connect(DB_PATH, timeout=10)


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self.request_is_local_origin():
                self.send_json({"error": "Cross-origin requests are not allowed."}, 403)
                return
            self.handle_api(parsed.path)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_json({"error": "Not found."}, 404)
            return
        if not self.request_is_local_origin():
            self.send_json({"error": "Cross-origin requests are not allowed."}, 403)
            return
        self.handle_api(parsed.path)

    def request_is_local_origin(self):
        try:
            if self.client_address[0] not in LOOPBACK_HOSTS:
                return False
            fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
            if fetch_site and fetch_site not in {"same-origin", "none", "same-site"}:
                return False
            origin = self.headers.get("Origin")
            if not origin:
                return True
            parsed = urlparse(origin)
            return parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS and parsed.netloc.lower() == self.headers.get("Host", "").lower()
        except (ValueError, TypeError):
            return False

    def serve_static(self, path):
        if path in {"", "/"}:
            path = "/index.html"
        requested = unquote(path).lstrip("/")
        root = os.path.realpath(BASE_DIR)
        file_path = os.path.realpath(os.path.join(root, requested))
        if os.path.commonpath([root, file_path]) != root or not os.path.isfile(file_path):
            self.send_error(404)
            return
        ext = os.path.splitext(file_path)[1].lower()
        content_types = {
            ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        }
        with open(file_path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_api(self, path):
        try:
            if self.command == "GET":
                routes = {
                    "/api/summary": self.get_summary, "/api/members": self.list_members,
                    "/api/events": self.list_events, "/api/inventory": self.list_inventory,
                    "/api/borrowed": self.list_borrowed, "/api/attendance": self.list_attendance,
                    "/api/users": self.list_users,
                }
            else:
                routes = {
                    "/api/members": self.save_member, "/api/events": self.save_event,
                    "/api/inventory": self.save_inventory, "/api/borrowed": self.save_borrowed,
                    "/api/attendance": self.save_attendance, "/api/users": self.save_user,
                }
            handler = routes.get(path)
            if not handler:
                self.send_json({"error": "Not found."}, 404)
                return
            self.send_json(handler())
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc) or "Invalid request."}, 400)
        except sqlite3.IntegrityError:
            self.send_json({"error": "The record conflicts with existing data."}, 400)
        except Exception:
            self.send_json({"error": "The server could not complete the request."}, 500)

    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length.")
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("Request body must be between 1 byte and 1 MB.")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Request body must contain valid UTF-8 JSON.")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object.")
        return data

    def get_summary(self):
        with get_db() as conn:
            return {
                "members": conn.execute("SELECT COUNT(*) FROM Members").fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM Events").fetchone()[0],
                "inventory": conn.execute("SELECT COUNT(*) FROM Inventory").fetchone()[0],
                "borrowed": conn.execute("SELECT COUNT(*) FROM BorrowedItems").fetchone()[0],
                "attendance": conn.execute("SELECT COUNT(*) FROM Attendance").fetchone()[0],
                "users": conn.execute("SELECT COUNT(*) FROM Users").fetchone()[0],
            }

    def list_members(self):
        with get_db() as conn:
            return [dict(r) for r in conn.execute("SELECT MemberID AS id, Name AS name, Class AS class, Roll AS roll, Email AS email FROM Members ORDER BY MemberID DESC").fetchall()]

    def list_events(self):
        with get_db() as conn:
            return [dict(r) for r in conn.execute("SELECT EventID AS id, Title AS title, Date AS date, Location AS location, Notes AS notes FROM Events ORDER BY EventID DESC").fetchall()]

    def list_inventory(self):
        with get_db() as conn:
            return [dict(r) for r in conn.execute("SELECT ItemID AS id, Name AS name, Quantity AS quantity, Notes AS notes FROM Inventory ORDER BY ItemID DESC").fetchall()]

    def list_borrowed(self):
        with get_db() as conn:
            return [dict(r) for r in conn.execute("SELECT BorrowID AS id, ItemID AS item_id, MemberID AS member_id, BorrowDate AS borrow_date, ReturnDate AS return_date, Returned AS returned FROM BorrowedItems ORDER BY BorrowID DESC").fetchall()]

    def list_attendance(self):
        with get_db() as conn:
            return [dict(r) for r in conn.execute("SELECT AttID AS id, MemberID AS member_id, EventID AS event_id, Present AS present, Timestamp AS timestamp FROM Attendance ORDER BY AttID DESC").fetchall()]

    def list_users(self):
        with get_db() as conn:
            return [dict(r) for r in conn.execute("SELECT UserID AS id, Username AS username, Role AS role FROM Users ORDER BY UserID DESC").fetchall()]

    def save_member(self):
        data = self.read_json()
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Member name is required.")
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE Members SET Name=?, Class=?, Roll=?, Email=? WHERE MemberID=?", (name, data.get("class", ""), data.get("roll", ""), data.get("email", ""), int(data["id"])))
            else:
                conn.execute("INSERT INTO Members (Name, Class, Roll, Email) VALUES (?, ?, ?, ?)", (name, data.get("class", ""), data.get("roll", ""), data.get("email", "")))
        return {"ok": True}

    def save_event(self):
        data = self.read_json()
        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError("Event title is required.")
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE Events SET Title=?, Date=?, Location=?, Notes=? WHERE EventID=?", (title, data.get("date", ""), data.get("location", ""), data.get("notes", ""), int(data["id"])))
            else:
                conn.execute("INSERT INTO Events (Title, Date, Location, Notes) VALUES (?, ?, ?, ?)", (title, data.get("date", ""), data.get("location", ""), data.get("notes", "")))
        return {"ok": True}

    def save_inventory(self):
        data = self.read_json()
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Item name is required.")
        quantity = int(data.get("quantity", 0) or 0)
        if quantity < 0:
            raise ValueError("Quantity cannot be negative.")
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE Inventory SET Name=?, Quantity=?, Notes=? WHERE ItemID=?", (name, quantity, data.get("notes", ""), int(data["id"])))
            else:
                conn.execute("INSERT INTO Inventory (Name, Quantity, Notes) VALUES (?, ?, ?)", (name, quantity, data.get("notes", "")))
        return {"ok": True}

    def save_borrowed(self):
        data = self.read_json()
        returned = 1 if str(data.get("returned", "0")).lower() in {"1", "true", "yes", "on"} else 0
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE BorrowedItems SET ItemID=?, MemberID=?, BorrowDate=?, ReturnDate=?, Returned=? WHERE BorrowID=?", (data.get("item_id", ""), data.get("member_id", ""), data.get("borrow_date", ""), data.get("return_date", ""), returned, int(data["id"])))
            else:
                conn.execute("INSERT INTO BorrowedItems (ItemID, MemberID, BorrowDate, ReturnDate, Returned) VALUES (?, ?, ?, ?, ?)", (data.get("item_id", ""), data.get("member_id", ""), data.get("borrow_date", ""), data.get("return_date", ""), returned))
        return {"ok": True}

    def save_attendance(self):
        data = self.read_json()
        present = 1 if str(data.get("present", "0")).lower() in {"1", "true", "yes", "on"} else 0
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE Attendance SET MemberID=?, EventID=?, Present=?, Timestamp=? WHERE AttID=?", (data.get("member_id", ""), data.get("event_id", ""), present, data.get("timestamp", ""), int(data["id"])))
            else:
                conn.execute("INSERT INTO Attendance (MemberID, EventID, Present, Timestamp) VALUES (?, ?, ?, ?)", (data.get("member_id", ""), data.get("event_id", ""), present, data.get("timestamp", "")))
        return {"ok": True}

    def save_user(self):
        data = self.read_json()
        username = str(data.get("username", "")).strip()
        if not username:
            raise ValueError("Username is required.")
        password = main.hash_password(str(data["password"])) if data.get("password") else None
        role = data.get("role", "member")
        if role not in {"admin", "member"}:
            raise ValueError("Role must be admin or member.")
        with get_db() as conn:
            if data.get("id"):
                if password:
                    conn.execute("UPDATE Users SET Username=?, Password=?, Role=? WHERE UserID=?", (username, password, role, int(data["id"])))
                else:
                    conn.execute("UPDATE Users SET Username=?, Role=? WHERE UserID=?", (username, role, int(data["id"])))
            else:
                if not password:
                    raise ValueError("A password is required for a new user.")
                conn.execute("INSERT INTO Users (Username, Password, Role) VALUES (?, ?, ?)", (username, password, role))
        return {"ok": True}


if __name__ == "__main__":
    main.setup_database()
    port = int(os.environ.get("PORT", 8000))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Serving local VBCCPAS interface at http://127.0.0.1:" + str(port) + "/")
    httpd.serve_forever()
