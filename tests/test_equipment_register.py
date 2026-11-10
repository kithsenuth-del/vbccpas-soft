"""Equipment-register schema and API regression tests using disposable databases."""
import os
import sqlite3
import tempfile
import unittest

import main
import server


class EquipmentRegisterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vbccpas-equipment-")
        self.db_path = os.path.join(self.temp.name, "equipment-test.sqlite3")
        self.old_main_path = main.DB_PATH
        self.old_server_path = server.DB_PATH
        main.DB_PATH = self.db_path
        server.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE Inventory (
                    ItemID INTEGER PRIMARY KEY AUTOINCREMENT,
                    Name TEXT NOT NULL,
                    Quantity INTEGER DEFAULT 0,
                    Notes TEXT
                )
            """)
            conn.execute(
                "INSERT INTO Inventory (Name, Quantity, Notes) VALUES (?, ?, ?)",
                ("Legacy camera", 2, "Existing record must remain"),
            )
        main.setup_database()

    def tearDown(self):
        main.DB_PATH = self.old_main_path
        server.DB_PATH = self.old_server_path
        self.temp.cleanup()

    def test_existing_inventory_records_survive_schema_extension(self):
        with sqlite3.connect(self.db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(Inventory)")}
            row = conn.execute(
                "SELECT Name, Quantity, Notes, Category, Condition, Status FROM Inventory"
            ).fetchone()
        self.assertTrue({
            "Category", "Brand", "Model", "SerialNumber", "Condition",
            "Location", "Custodian", "PurchaseDate", "Status"
        }.issubset(columns))
        self.assertEqual(row, (
            "Legacy camera", 2, "Existing record must remain",
            "Other", "Good", "Available"
        ))

    def test_equipment_fields_round_trip_through_inventory_api(self):
        handler = server.Handler.__new__(server.Handler)
        payload = {
            "name": "Studio video mixer",
            "category": "Mixer",
            "brand": "Test brand",
            "model": "VM-200",
            "serial_number": "TEST-SERIAL-001",
            "quantity": "1",
            "condition": "Good",
            "location": "Media cupboard",
            "custodian": "Society equipment officer",
            "purchase_date": "2026-11-10",
            "status": "Available",
            "notes": "Disposable automated test record",
        }
        result = handler.save_inventory(payload)
        self.assertTrue(result["ok"])
        rows = handler.list_inventory()
        saved = next(row for row in rows if row["name"] == payload["name"])
        for key in (
            "category", "brand", "model", "serial_number", "condition",
            "location", "custodian", "purchase_date", "status", "notes"
        ):
            self.assertEqual(saved[key], payload[key])


if __name__ == "__main__":
    unittest.main()
