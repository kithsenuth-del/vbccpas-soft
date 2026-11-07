"""Non-destructive tests for the local-only executive administrator API."""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import main
import passkey_auth
import server


def call(base, path, method="GET", payload=None, origin=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Host": "localhost:" + str(server.PORT)}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def run_checks():
    old_main_path = main.DB_PATH
    old_server_path = server.DB_PATH
    old_port = server.PORT
    old_host = server.EXPECTED_HOST
    old_origin = server.EXPECTED_ORIGIN
    old_webauthn_origin = passkey_auth.ORIGIN
    old_admin_password = os.environ.get("VBCCPAS_ADMIN_PASSWORD")
    httpd = None
    thread = None
    results = {}
    try:
        with tempfile.TemporaryDirectory(prefix="vbccpas-passkey-test-") as temp_dir:
            db_path = os.path.join(temp_dir, "isolated.db")
            main.DB_PATH = db_path
            server.DB_PATH = db_path
            os.environ["VBCCPAS_ADMIN_PASSWORD"] = "temporary-test-password-only"
            main.setup_database()
            passkey_auth.initialize(db_path)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            server.PORT = httpd.server_port
            server.EXPECTED_HOST = "localhost:" + str(server.PORT)
            server.EXPECTED_ORIGIN = "http://localhost:" + str(server.PORT)
            passkey_auth.ORIGIN = server.EXPECTED_ORIGIN
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:" + str(server.PORT)
            same_origin = server.EXPECTED_ORIGIN

            status, body = call(base, "/api/auth/status")
            state = json.loads(body)
            results["first_run_reports_setup_required"] = status == 200 and state.get("configured") is False

            status, body = call(base, "/api/summary")
            results["summary_requires_passkey_session"] = status == 401

            status, body = call(
                base, "/api/members", "POST",
                {"name": "Should not be saved"}, same_origin
            )
            results["member_write_requires_passkey_session"] = status == 401

            status, body = call(
                base, "/api/auth/register/options", "POST",
                {"bootstrap_token": "not-the-console-token"}, same_origin
            )
            results["invalid_bootstrap_token_rejected"] = status == 403

            status, body = call(base, "/api/auth/status", origin="https://attacker.invalid")
            results["cross_origin_rejected"] = status == 403

            status, body = call(base, "/")
            results["local_login_page_served"] = status == 200 and "Executive administrator login" in body

            with sqlite3_connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM Members").fetchone()[0]
            results["unauthorized_write_did_not_change_database"] = count == 0
            results["all_passed"] = all(results.values())
    finally:
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        if thread:
            thread.join(timeout=2)
        main.DB_PATH = old_main_path
        server.DB_PATH = old_server_path
        server.PORT = old_port
        server.EXPECTED_HOST = old_host
        server.EXPECTED_ORIGIN = old_origin
        passkey_auth.ORIGIN = old_webauthn_origin
        if old_admin_password is None:
            os.environ.pop("VBCCPAS_ADMIN_PASSWORD", None)
        else:
            os.environ["VBCCPAS_ADMIN_PASSWORD"] = old_admin_password
    return results


def sqlite3_connect(path):
    import sqlite3
    return sqlite3.connect(path)


if __name__ == "__main__":
    print(json.dumps(run_checks(), indent=2))
