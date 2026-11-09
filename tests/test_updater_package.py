"""Tests for safe extraction of complete application update packages."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from io import BytesIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import updater


def make_zip(entries, add_manifest=True):
    entries = list(entries)
    if add_manifest and not any(name == updater.MANIFEST_NAME for name, _ in entries):
        manifest_files = [name for name, _ in entries if not name.endswith("/")]
        entries.append((updater.MANIFEST_NAME, __import__("json").dumps({
            "schema_version": 1, "files": manifest_files,
        })))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


class FullPackageExtractionTests(unittest.TestCase):
    def test_extracts_complete_application_and_nested_resources(self):
        package = make_zip([
            ("VBCCPAS-Soft.exe", b"application"),
            ("VBCCPAS-Updater.exe", b"updater"),
            ("updater.ps1", b"installer"),
            ("Recover-VBCCPAS.bat", b"recovery"),
            ("assets/theme.json", b"{}"),
        ])
        with tempfile.TemporaryDirectory() as folder:
            files = updater.extract_package(package, folder)
            self.assertIn("assets/theme.json", files)
            self.assertEqual((Path(folder) / "assets/theme.json").read_bytes(), b"{}")
            self.assertTrue((Path(folder) / "VBCCPAS-Soft.exe").is_file())

    def test_rejects_windows_case_insensitive_path_collisions(self):
        package = make_zip([
            ("VBCCPAS-Soft.exe", b"application"),
            ("VBCCPAS-Updater.exe", b"updater"),
            ("updater.ps1", b"installer"),
            ("Recover-VBCCPAS.bat", b"recovery"),
            ("Assets/Logo.png", b"one"),
            ("assets/logo.PNG", b"two"),
        ])
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "Windows-colliding"):
                updater.extract_package(package, folder)

    def test_rejects_empty_path_segments(self):
        package = make_zip([
            ("VBCCPAS-Soft.exe", b"application"),
            ("VBCCPAS-Updater.exe", b"updater"),
            ("updater.ps1", b"installer"),
            ("Recover-VBCCPAS.bat", b"recovery"),
            ("assets//theme.json", b"{}"),
        ])
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "path outside"):
                updater.extract_package(package, folder)

    def test_rejects_path_traversal(self):
        package = make_zip([
            ("VBCCPAS-Soft.exe", b"app"),
            ("updater.ps1", b"script"),
            ("Recover-VBCCPAS.bat", b"recovery"),
            ("../outside.txt", b"bad"),
        ])
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "path outside"):
                updater.extract_package(package, folder)
            self.assertFalse((Path(folder).parent / "outside.txt").exists())

    def test_rejects_database_or_backup_payloads(self):
        for unsafe_name in ("society.db", "society.db-wal", "society.db-shm", "society.db-journal", "backups/old.db", "exports/members.csv"):
            package = make_zip([
                ("VBCCPAS-Soft.exe", b"app"),
                ("updater.ps1", b"script"),
                ("Recover-VBCCPAS.bat", b"recovery"),
                (unsafe_name, b"user data"),
            ])
            with tempfile.TemporaryDirectory() as folder:
                with self.subTest(name=unsafe_name):
                    with self.assertRaisesRegex(ValueError, "protected user data"):
                        updater.extract_package(package, folder)

    def test_rejects_manifest_that_does_not_match_package_contents(self):
        package = make_zip([
            ("VBCCPAS-Soft.exe", b"application"),
            ("VBCCPAS-Updater.exe", b"updater"),
            ("updater.ps1", b"installer"),
            ("Recover-VBCCPAS.bat", b"recovery"),
            ("assets/new-module.py", b"module"),
            (updater.MANIFEST_NAME, __import__("json").dumps({
                "schema_version": 1,
                "files": ["VBCCPAS-Soft.exe", "VBCCPAS-Updater.exe", "updater.ps1"],
            })),
        ], add_manifest=False)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "manifest does not match"):
                updater.extract_package(package, folder)

    def test_requires_main_application_and_installer(self):
        package = make_zip([
            ("notes.txt", b"not an application"),
            ("updater.ps1", b"installer"),
        ])
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "missing the application"):
                updater.extract_package(package, folder)


if __name__ == "__main__":
    unittest.main()
