import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import main
import passkey_auth

if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = main.DB_PATH
PORT = int(os.environ.get("PORT", "8000"))
EXPECTED_HOST = "localhost:" + str(PORT)
EXPECTED_ORIGIN = "http://localhost:" + str(PORT)
passkey_auth.ORIGIN = EXPECTED_ORIGIN
MAX_BODY_BYTES = 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_COOKIE = "__Host-vbccpas_session"
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def startup_security():
    passkey_auth.initialize(DB_PATH)
    print("VBCCPAS Soft local-only server: " + EXPECTED_ORIGIN)
    if not passkey_auth.configured(DB_PATH):
        print("")
        print("FIRST-TIME EXECUTIVE ADMINISTRATOR PASSKEY SETUP")
        print("One-time local setup token: " + passkey_auth.BOOTSTRAP_TOKEN)
        print("Keep this token private. Enter it only in the local browser setup screen.")
        print("")


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if not self.request_is_local_origin():
            self.send_json({"error": "This application is available only through its local browser address."}, 403)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/status":
            session = self.get_session()
            summary = passkey_auth.passkey_summary(DB_PATH)
            summary["authenticated"] = session is not None
            if session:
                summary["csrf_token"] = session["csrf"]
                summary["administrator"] = "Executive Administrator"
            self.send_json(summary)
            return
        if parsed.path.startswith("/api/"):
            if not self.require_session():
                return
            self.handle_api_get(parsed.path)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        if not self.request_is_local_origin():
            self.send_json({"error": "This application is available only through its local browser address."}, 403)
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_json({"error": "Not found."}, 404)
            return
        try:
            data = self.read_json()
            if parsed.path == "/api/auth/register/options":
                result = passkey_auth.begin_registration(DB_PATH, data.get("bootstrap_token"))
                self.send_json(result)
                return
            if parsed.path == "/api/auth/register/verify":
                result = passkey_auth.complete_registration(
                    DB_PATH, data.get("bootstrap_token"), data.get("transaction"), data.get("credential")
                )
                self.create_session()
                return
            if parsed.path == "/api/auth/login/options":
                self.send_json(passkey_auth.begin_login(DB_PATH))
                return
            if parsed.path == "/api/auth/login/verify":
                passkey_auth.complete_login(DB_PATH, data.get("transaction"), data.get("credential"))
                self.create_session()
                return
            if parsed.path == "/api/auth/logout":
                if not self.require_session():
                    return
                if not self.valid_csrf():
                    self.send_json({"error": "Your security token expired. Please sign in again."}, 403)
                    return
                self.destroy_session()
                self.send_json({"ok": True}, headers=[
                    ("Set-Cookie", SESSION_COOKIE + "=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict")
                ])
                return
            if not self.require_session():
                return
            if not self.valid_csrf():
                self.send_json({"error": "Your security token expired. Please sign in again."}, 403)
                return
            self.handle_api_post(parsed.path, data)
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, 403)
        except ValueError as exc:
            self.send_json({"error": str(exc) or "Invalid request."}, 400)
        except sqlite3.IntegrityError:
            self.send_json({"error": "The record conflicts with existing data."}, 400)
        except Exception:
            self.send_json({"error": "The request could not be completed. Check the local server log."}, 500)

    def request_is_local_origin(self):
        try:
            if self.client_address[0] not in LOOPBACK_HOSTS:
                return False
            if self.headers.get("Host", "").lower() != EXPECTED_HOST.lower():
                return False
            fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                return False
            origin = self.headers.get("Origin")
            if origin and origin.rstrip("/") != EXPECTED_ORIGIN:
                return False
            return True
        except (ValueError, TypeError):
            return False

    def get_session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get(SESSION_COOKIE)
            token = morsel.value if morsel else ""
        except Exception:
            return None
        if not token:
            return None
        now = time.time()
        with SESSIONS_LOCK:
            session = SESSIONS.get(token)
            if not session:
                return None
            if session["expires"] < now:
                SESSIONS.pop(token, None)
                return None
            return session

    def require_session(self):
        if self.get_session():
            return True
        self.send_json({"error": "Sign in with the executive administrator passkey to continue.", "authenticated": False}, 401)
        return False

    def valid_csrf(self):
        session = self.get_session()
        supplied = self.headers.get("X-CSRF-Token", "")
        return bool(session and supplied and secrets.compare_digest(session["csrf"], supplied))

    def create_session(self):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with SESSIONS_LOCK:
            now = time.time()
            expired = [key for key, value in SESSIONS.items() if value["expires"] < now]
            for key in expired:
                SESSIONS.pop(key, None)
            SESSIONS[token] = {"csrf": csrf, "expires": now + SESSION_TTL_SECONDS}
        self.send_json(
            {"ok": True, "csrf_token": csrf, "administrator": "Executive Administrator"},
            headers=[(
                "Set-Cookie",
                SESSION_COOKIE + "=" + token + "; Path=/; Max-Age=" + str(SESSION_TTL_SECONDS)
                + "; HttpOnly; Secure; SameSite=Strict",
            )],
        )

    def destroy_session(self):
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        if morsel:
            with SESSIONS_LOCK:
                SESSIONS.pop(morsel.value, None)

    def serve_static(self, path):
        if path in {"", "/"}:
            path = "/index.html"
        requested = unquote(path).lstrip("/")
        root = os.path.realpath(BASE_DIR)
        file_path = os.path.realpath(os.path.join(root, requested))
        try:
            is_inside_root = os.path.commonpath([root, file_path]) == root
        except ValueError:
            is_inside_root = False
        if not is_inside_root or not os.path.isfile(file_path):
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

    def send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for name, value in headers:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def handle_api_get(self, path):
        routes = {
            "/api/summary": self.get_summary,
            "/api/members": self.list_members,
            "/api/events": self.list_events,
            "/api/inventory": self.list_inventory,
            "/api/borrowed": self.list_borrowed,
            "/api/attendance": self.list_attendance,
            "/api/auth/passkey": lambda: passkey_auth.passkey_summary(DB_PATH),
        }
        handler = routes.get(path)
        if not handler:
            self.send_json({"error": "Not found."}, 404)
            return
        try:
            self.send_json(handler())
        except Exception:
            self.send_json({"error": "The server could not complete the request."}, 500)

    def handle_api_post(self, path, data):
        routes = {
            "/api/members": self.save_member,
            "/api/events": self.save_event,
            "/api/inventory": self.save_inventory,
            "/api/borrowed": self.save_borrowed,
            "/api/attendance": self.save_attendance,
        }
        handler = routes.get(path)
        if not handler:
            self.send_json({"error": "Not found."}, 404)
            return
        self.send_json(handler(data))

    def get_summary(self):
        with get_db() as conn:
            return {
                "members": conn.execute("SELECT COUNT(*) FROM Members").fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM Events").fetchone()[0],
                "inventory": conn.execute("SELECT COUNT(*) FROM Inventory").fetchone()[0],
                "borrowed": conn.execute("SELECT COUNT(*) FROM BorrowedItems").fetchone()[0],
                "attendance": conn.execute("SELECT COUNT(*) FROM Attendance").fetchone()[0],
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

    def save_member(self, data):
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Member name is required.")
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE Members SET Name=?, Class=?, Roll=?, Email=? WHERE MemberID=?", (name, data.get("class", ""), data.get("roll", ""), data.get("email", ""), int(data["id"])))
            else:
                conn.execute("INSERT INTO Members (Name, Class, Roll, Email) VALUES (?, ?, ?, ?)", (name, data.get("class", ""), data.get("roll", ""), data.get("email", "")))
        return {"ok": True}

    def save_event(self, data):
        title = str(data.get("title", "")).strip()
        if not title:
            raise ValueError("Event title is required.")
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE Events SET Title=?, Date=?, Location=?, Notes=? WHERE EventID=?", (title, data.get("date", ""), data.get("location", ""), data.get("notes", ""), int(data["id"])))
            else:
                conn.execute("INSERT INTO Events (Title, Date, Location, Notes) VALUES (?, ?, ?, ?)", (title, data.get("date", ""), data.get("location", ""), data.get("notes", "")))
        return {"ok": True}

    def save_inventory(self, data):
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

    def save_borrowed(self, data):
        returned = 1 if str(data.get("returned", "0")).lower() in {"1", "true", "yes", "on"} else 0
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE BorrowedItems SET ItemID=?, MemberID=?, BorrowDate=?, ReturnDate=?, Returned=? WHERE BorrowID=?", (data.get("item_id", ""), data.get("member_id", ""), data.get("borrow_date", ""), data.get("return_date", ""), returned, int(data["id"])))
            else:
                conn.execute("INSERT INTO BorrowedItems (ItemID, MemberID, BorrowDate, ReturnDate, Returned) VALUES (?, ?, ?, ?, ?)", (data.get("item_id", ""), data.get("member_id", ""), data.get("borrow_date", ""), data.get("return_date", ""), returned))
        return {"ok": True}

    def save_attendance(self, data):
        present = 1 if str(data.get("present", "0")).lower() in {"1", "true", "yes", "on"} else 0
        with get_db() as conn:
            if data.get("id"):
                conn.execute("UPDATE Attendance SET MemberID=?, EventID=?, Present=?, Timestamp=? WHERE AttID=?", (data.get("member_id", ""), data.get("event_id", ""), present, data.get("timestamp", ""), int(data["id"])))
            else:
                conn.execute("INSERT INTO Attendance (MemberID, EventID, Present, Timestamp) VALUES (?, ?, ?, ?)", (data.get("member_id", ""), data.get("event_id", ""), present, data.get("timestamp", "")))
        return {"ok": True}

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    main.setup_database()
    startup_security()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    httpd.serve_forever()
