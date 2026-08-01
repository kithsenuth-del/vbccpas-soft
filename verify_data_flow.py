import json
import os
import sqlite3
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import main
import server as web_server

BASE_DIR = os.path.dirname(__file__)
RESULT_PATH = os.path.join(BASE_DIR, "verification_result.json")


def post_json(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, response.read().decode("utf-8")


if __name__ == "__main__":
    main.setup_database()
    httpd = ThreadingHTTPServer(("127.0.0.1", 8001), web_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    conn = sqlite3.connect(main.DB_PATH)
    cur = conn.cursor()
    for table, name in [("Members", "Verify Member"), ("Events", "Verify Event"), ("Inventory", "Verify Item")]:
        if table == "Members":
            cur.execute("DELETE FROM Members WHERE Name=?", (name,))
        elif table == "Events":
            cur.execute("DELETE FROM Events WHERE Title=?", (name,))
        elif table == "Inventory":
            cur.execute("DELETE FROM Inventory WHERE Name=?", (name,))
    conn.commit()
    conn.close()

    results = {}
    try:
        status, body = post_json("http://127.0.0.1:8001/api/members", {"name": "Verify Member", "class": "12-C", "roll": "88", "email": "verify@example.com"})
        results["members_api"] = {"status": status, "body": body}
    except Exception as exc:
        results["members_api"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        status, body = post_json("http://127.0.0.1:8001/api/events", {"title": "Verify Event", "date": "2026-08-12", "location": "Hall", "notes": "verify"})
        results["events_api"] = {"status": status, "body": body}
    except Exception as exc:
        results["events_api"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        status, body = post_json("http://127.0.0.1:8001/api/inventory", {"name": "Verify Item", "quantity": "7", "notes": "verify stock"})
        results["inventory_api"] = {"status": status, "body": body}
    except Exception as exc:
        results["inventory_api"] = {"error": f"{type(exc).__name__}: {exc}"}

    conn = sqlite3.connect(main.DB_PATH)
    cur = conn.cursor()
    results.update(
        {
            "members_count": cur.execute("SELECT COUNT(*) FROM Members WHERE Name=?", ("Verify Member",)).fetchone()[0],
            "events_count": cur.execute("SELECT COUNT(*) FROM Events WHERE Title=?", ("Verify Event",)).fetchone()[0],
            "inventory_count": cur.execute("SELECT COUNT(*) FROM Inventory WHERE Name=?", ("Verify Item",)).fetchone()[0],
        }
    )
    conn.close()

    conn = sqlite3.connect(main.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO Members (Name, Class, Roll, Email) VALUES (?, ?, ?, ?)", ("Desktop Verify", "12-D", "99", "desktop@example.com"))
    cur.execute("INSERT INTO Events (Title, Date, Location, Notes) VALUES (?, ?, ?, ?)", ("Desktop Verify Event", "2026-08-13", "Room 2", "desktop check"))
    cur.execute("INSERT INTO Inventory (Name, Quantity, Notes) VALUES (?, ?, ?)", ("Desktop Verify Item", 4, "desktop stock"))
    conn.commit()
    conn.close()

    conn = sqlite3.connect(main.DB_PATH)
    cur = conn.cursor()
    results.update(
        {
            "desktop_members_count": cur.execute("SELECT COUNT(*) FROM Members WHERE Name=?", ("Desktop Verify",)).fetchone()[0],
            "desktop_events_count": cur.execute("SELECT COUNT(*) FROM Events WHERE Title=?", ("Desktop Verify Event",)).fetchone()[0],
            "desktop_inventory_count": cur.execute("SELECT COUNT(*) FROM Inventory WHERE Name=?", ("Desktop Verify Item",)).fetchone()[0],
        }
    )
    conn.close()

    httpd.shutdown()
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    print(json.dumps(results, indent=2))
