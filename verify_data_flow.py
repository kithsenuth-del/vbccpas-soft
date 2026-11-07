"""Non-destructive API smoke test using an isolated temporary SQLite database."""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import main
import server as web_server


def request_json(url, payload=None):
    headers = {}
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def run_checks():
    original_main_path = main.DB_PATH
    original_server_path = web_server.DB_PATH
    results = {}
    httpd = None
    thread = None
    try:
        with tempfile.TemporaryDirectory(prefix="vbccpas-test-") as temp_dir:
            test_db = os.path.join(temp_dir, "isolated-test.db")
            main.DB_PATH = test_db
            web_server.DB_PATH = test_db
            main.setup_database()
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:" + str(httpd.server_port)

            status, summary = request_json(base + "/api/summary")
            results["summary_get"] = status == 200 and "members" in summary
            request_json(base + "/api/members", {"name": "Isolated Test Member", "class": "12-C", "roll": "88", "email": "test@example.invalid"})
            _, members = request_json(base + "/api/members")
            results["members_create_and_list"] = any(r["name"] == "Isolated Test Member" for r in members)

            request_json(base + "/api/events", {"title": "Isolated Test Event", "date": "2026-08-12", "location": "Test Hall", "notes": "temporary"})
            _, events = request_json(base + "/api/events")
            results["events_create_and_list"] = any(r["title"] == "Isolated Test Event" for r in events)

            request_json(base + "/api/inventory", {"name": "Isolated Test Item", "quantity": 7, "notes": "temporary"})
            _, inventory = request_json(base + "/api/inventory")
            results["inventory_create_and_list"] = any(r["name"] == "Isolated Test Item" for r in inventory)

            bad_origin = urllib.request.Request(base + "/api/members", data=b'{"name":"blocked"}', headers={"Content-Type": "application/json", "Origin": "https://attacker.invalid"}, method="POST")
            try:
                urllib.request.urlopen(bad_origin, timeout=5)
                results["cross_origin_blocked"] = False
            except urllib.error.HTTPError as exc:
                results["cross_origin_blocked"] = exc.code == 403

            bad_type = urllib.request.Request(base + "/api/members", data=b'name=blocked', headers={"Content-Type": "text/plain"}, method="POST")
            try:
                urllib.request.urlopen(bad_type, timeout=5)
                results["invalid_content_type_rejected"] = False
            except urllib.error.HTTPError as exc:
                results["invalid_content_type_rejected"] = exc.code == 400

            results["all_passed"] = all(results.values())
    finally:
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        if thread:
            thread.join(timeout=2)
        main.DB_PATH = original_main_path
        web_server.DB_PATH = original_server_path
    return results


if __name__ == "__main__":
    print(json.dumps(run_checks(), indent=2))
