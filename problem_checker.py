"""Read-only, beginner-friendly health checks for VBCCPAS Soft.

Run: python problem_checker.py
Optional: python problem_checker.py --database path/to/society.db

This checker never creates, edits, repairs, or deletes application data.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
REQUIRED_TABLES = {
    "Users": {"UserID", "Username", "Password", "Role"},
    "Members": {"MemberID", "Name", "Class", "Roll", "Email"},
    "Events": {"EventID", "Title", "Date", "Location", "Notes"},
    "Inventory": {"ItemID", "Name", "Quantity", "Notes"},
    "BorrowedItems": {"BorrowID", "ItemID", "MemberID", "BorrowDate", "ReturnDate", "Returned"},
    "Attendance": {"AttID", "MemberID", "EventID", "Present", "Timestamp"},
}


class Report:
    def __init__(self):
        self.passed = 0
        self.warnings = 0
        self.failed = 0

    def add(self, level: str, message: str) -> None:
        symbols = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
        print(f"{symbols[level]} {message}")
        if level == "PASS":
            self.passed += 1
        elif level == "WARN":
            self.warnings += 1
        else:
            self.failed += 1


def check_python_files(report: Report) -> None:
    files = sorted(ROOT.glob("*.py"))
    errors = []
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{path.name}: {exc}")
    if errors:
        for error in errors:
            report.add("FAIL", f"Python syntax problem — {error}")
    else:
        report.add("PASS", f"Python syntax checked ({len(files)} files)")


def check_dependencies(report: Report) -> None:
    if importlib.util.find_spec("webauthn") is None:
        report.add("FAIL", "Missing dependency 'webauthn'. Run: python -m pip install -r requirements.txt")
    else:
        report.add("PASS", "Required passkey package is installed")


def check_frontend(report: Report) -> None:
    checker = ROOT / "check_frontend.py"
    if not checker.exists():
        report.add("WARN", "Frontend checker not found; dashboard JavaScript was not checked")
        return
    if not shutil.which("node"):
        report.add("WARN", "Node.js is not installed; skipped dashboard JavaScript syntax check")
        return
    try:
        result = subprocess.run(
            [sys.executable, str(checker)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add("FAIL", f"Could not run frontend checker: {exc}")
        return
    if result.returncode == 0:
        report.add("PASS", "Dashboard JavaScript syntax is valid")
    else:
        detail = (result.stderr or result.stdout).strip().splitlines()
        report.add("FAIL", "Dashboard JavaScript check failed: " + (detail[-1] if detail else "unknown error"))


def read_only_database(path: Path, report: Report) -> None:
    if not path.exists():
        report.add("WARN", f"Database not found at {path}; start the app once to create it")
        return
    if not path.is_file():
        report.add("FAIL", f"Database path is not a file: {path}")
        return

    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity == "ok":
            report.add("PASS", "SQLite database integrity check passed")
        else:
            report.add("FAIL", f"SQLite integrity check reported: {integrity}")

        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, expected_columns in REQUIRED_TABLES.items():
            if table not in tables:
                report.add("FAIL", f"Database table missing: {table}")
                continue
            actual_columns = {
                row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')
            }
            missing = expected_columns - actual_columns
            if missing:
                report.add("FAIL", f"{table} is missing columns: {', '.join(sorted(missing))}")
            else:
                report.add("PASS", f"{table} table structure is present")

        if "Inventory" in tables:
            negative = conn.execute("SELECT COUNT(*) FROM Inventory WHERE Quantity < 0").fetchone()[0]
            if negative:
                report.add("FAIL", f"{negative} inventory record(s) have negative quantities")
            else:
                report.add("PASS", "No negative inventory quantities")

        checks = [
            ("BorrowedItems", "ItemID", "Inventory", "ItemID", "borrowing record(s) reference missing inventory items"),
            ("BorrowedItems", "MemberID", "Members", "MemberID", "borrowing record(s) reference missing members"),
            ("Attendance", "MemberID", "Members", "MemberID", "attendance record(s) reference missing members"),
            ("Attendance", "EventID", "Events", "EventID", "attendance record(s) reference missing events"),
        ]
        for source, source_key, target, target_key, description in checks:
            if source in tables and target in tables:
                sql = (
                    f'SELECT COUNT(*) FROM "{source}" s '
                    f'LEFT JOIN "{target}" t ON s."{source_key}"=t."{target_key}" '
                    f'WHERE s."{source_key}" IS NOT NULL AND t."{target_key}" IS NULL'
                )
                count = conn.execute(sql).fetchone()[0]
                if count:
                    report.add("WARN", f"{count} {description}")
                else:
                    report.add("PASS", f"No {description}")

        counts = []
        for table in ("Members", "Events", "Inventory", "BorrowedItems", "Attendance"):
            if table in tables:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                counts.append(f"{table}: {count}")
        if counts:
            report.add("PASS", "Record totals (counts only): " + " | ".join(counts))
        conn.close()
    except sqlite3.Error as exc:
        report.add("FAIL", f"Could not safely inspect database: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run safe, read-only VBCCPAS Soft problem checks.")
    parser.add_argument(
        "--database",
        default=str(ROOT / "society.db"),
        help="Database file to inspect (read-only; defaults to society.db beside this script)",
    )
    args = parser.parse_args()

    print("\nVBCCPAS SOFT — SIMPLE PROBLEM CHECKER")
    print("=" * 44)
    report = Report()
    check_python_files(report)
    check_dependencies(report)
    check_frontend(report)
    read_only_database(Path(args.database).expanduser(), report)
    print("\n" + "=" * 44)
    print(f"Results: {report.passed} passed | {report.warnings} warning(s) | {report.failed} problem(s)")
    if report.failed:
        print("Action needed: review the FAIL items above before using the affected feature.")
        return 1
    if report.warnings:
        print("Review the WARN items when convenient. No data was changed.")
    else:
        print("All available checks passed. No data was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
