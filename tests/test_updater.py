"""Regression tests for the GitHub Releases updater."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Running a test file directly puts tests/ rather than the repository root
# on sys.path. Add the project root so the updater module is importable in CI.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import updater
import updater_app


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return json.dumps(self.payload).encode("utf-8")


def release_payload(tag="build-" + "a" * 40, digest="sha256:" + "b" * 64):
    return {
        "tag_name": tag,
        "name": "VBCCPAS Soft test build",
        "draft": False,
        "prerelease": False,
        "assets": [{
            "name": updater.UPDATE_ASSET,
            "browser_download_url": (
                "https://github.com/" + updater.REPOSITORY
                + "/releases/download/" + tag + "/" + updater.UPDATE_ASSET
            ),
            "digest": digest,
            "size": 1234,
        }],
    }


class FakeBinaryResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.payload


def complete_package_bytes():
    import io
    import zipfile
    files = {
        "VBCCPAS-Soft.exe": b"application",
        "VBCCPAS-Updater.exe": b"updater",
        "updater.ps1": b"installer",
        "Recover-VBCCPAS.bat": b"recovery",
    }
    manifest = json.dumps({"schema_version": 1, "files": sorted(files)})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(updater.MANIFEST_NAME, manifest)
    return buffer.getvalue()


class UpdaterTests(unittest.TestCase):
    def test_updater_window_cannot_close_during_update(self):
        from unittest.mock import patch

        class FakeRoot:
            def __init__(self):
                self.destroyed = False
            def destroy(self):
                self.destroyed = True

        window = object.__new__(updater_app.UpdaterWindow)
        window.root = FakeRoot()
        window.busy = True
        with patch.object(updater_app.messagebox, "showwarning") as warning:
            window.close_window()
        warning.assert_called_once()
        self.assertFalse(window.root.destroyed)

        window.busy = False
        window.close_window()
        self.assertTrue(window.root.destroyed)

    def test_updater_window_log_writes_one_record_per_line(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(updater_app.os.environ, {"LOCALAPPDATA": folder}):
                updater_app._log("first window diagnostic")
                updater_app._log("second window diagnostic")
            log_path = Path(folder) / "VBCCPAS" / "updater-launch.log"
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("first window diagnostic", lines[0])
            self.assertIn("second window diagnostic", lines[1])

    def test_handoff_log_writes_one_record_per_line(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(updater.os.environ, {"LOCALAPPDATA": folder}):
                updater._log_handoff("first diagnostic")
                updater._log_handoff("second diagnostic")
            log_path = Path(folder) / "VBCCPAS" / "updater-launch.log"
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("first diagnostic", lines[0])
            self.assertIn("second diagnostic", lines[1])
    def test_returns_none_when_latest_release_is_current_build(self):
        commit = "a" * 40
        with patch.object(updater, "_request", return_value=FakeResponse(release_payload())):
            self.assertIsNone(updater.check_for_update(commit))

    def test_force_mode_returns_current_release_for_repair(self):
        payload = release_payload()
        with patch.object(updater, "_request", return_value=FakeResponse(payload)):
            result = updater.check_for_update("a" * 40, force=True)
        self.assertEqual(result["tag_name"], "build-" + "a" * 40)
        self.assertEqual(result["_update_asset"]["digest"], "b" * 64)

    def test_recovery_bootstrap_is_invoked_when_present(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "updater-recovery.ps1"
            script.write_text("param([switch]$RestartAfterRecovery)", encoding="utf-8")
            completed = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            with patch.object(updater.subprocess, "run", return_value=completed) as run:
                updater.recover_interrupted_update(Path(folder) / "VBCCPAS-Soft.exe", script)
            self.assertIn("-RecoverOnly", run.call_args.args[0])
            self.assertIn("-RestartAfterRecovery", run.call_args.args[0])

    def test_older_recovery_bootstrap_does_not_receive_new_switch(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "updater-recovery.ps1"
            script.write_text("# older recovery bootstrap", encoding="utf-8")
            completed = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            with patch.object(updater.subprocess, "run", return_value=completed) as run:
                updater.recover_interrupted_update(Path(folder) / "VBCCPAS-Soft.exe", script)
            self.assertIn("-RecoverOnly", run.call_args.args[0])
            self.assertNotIn("-RestartAfterRecovery", run.call_args.args[0])

    def test_recovery_bootstrap_failure_blocks_new_update(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "updater-recovery.ps1"
            script.write_text("# recovery bootstrap", encoding="utf-8")
            completed = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "rollback failed"})()
            with patch.object(updater.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(RuntimeError, "rollback failed"):
                    updater.recover_interrupted_update(Path(folder) / "VBCCPAS-Soft.exe", script)

    def test_returns_verified_asset_details_for_new_build(self):
        payload = release_payload()
        with patch.object(updater, "_request", return_value=FakeResponse(payload)):
            result = updater.check_for_update("c" * 40)
        self.assertEqual(result["_update_asset"]["digest"], "b" * 64)
        self.assertEqual(result["_update_asset"]["size"], 1234)

    def test_rejects_release_without_sha256_digest(self):
        payload = release_payload(digest="")
        with patch.object(updater, "_request", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(ValueError, "SHA-256 checksum"):
                updater.check_for_update("c" * 40)

    def test_rejects_malformed_build_tag(self):
        payload = release_payload(tag="latest")
        with patch.object(updater, "_request", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(ValueError, "build tag"):
                updater.check_for_update("c" * 40)

    def test_rejects_untrusted_download_url(self):
        payload = release_payload()
        payload["assets"][0]["browser_download_url"] = "https://example.com/update.zip"
        with patch.object(updater, "_request", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(ValueError, "download address"):
                updater.check_for_update("c" * 40)

    def test_download_rejects_incomplete_package_before_launch(self):
        import tempfile
        package = complete_package_bytes()
        release = {"_update_asset": {
            "url": "https://github.com/" + updater.REPOSITORY + "/releases/download/build-" + "a" * 40 + "/" + updater.UPDATE_ASSET,
            "size": len(package) + 1,
            "digest": __import__("hashlib").sha256(package).hexdigest(),
        }}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "VBCCPAS-Soft.exe"
            target.write_bytes(b"old application")
            with patch.object(updater, "_request", return_value=FakeBinaryResponse(package)), \
                 patch.object(updater.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    updater.download_and_launch(release, target, 0)
            launch.assert_not_called()
            self.assertEqual(target.read_bytes(), b"old application")

    def test_download_rejects_bad_sha256_before_launch(self):
        import tempfile
        package = complete_package_bytes()
        release = {"_update_asset": {
            "url": "https://github.com/" + updater.REPOSITORY + "/releases/download/build-" + "a" * 40 + "/" + updater.UPDATE_ASSET,
            "size": len(package),
            "digest": "0" * 64,
        }}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "VBCCPAS-Soft.exe"
            target.write_bytes(b"old application")
            with patch.object(updater, "_request", return_value=FakeBinaryResponse(package)), \
                 patch.object(updater.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(ValueError, "checksum"):
                    updater.download_and_launch(release, target, 0)
            launch.assert_not_called()
            self.assertEqual(target.read_bytes(), b"old application")

    def test_download_verifies_and_launches_a_complete_package(self):
        import hashlib
        import shutil
        import tempfile
        package = complete_package_bytes()
        release = {"_update_asset": {
            "url": "https://github.com/" + updater.REPOSITORY + "/releases/download/build-" + "a" * 40 + "/" + updater.UPDATE_ASSET,
            "size": len(package),
            "digest": hashlib.sha256(package).hexdigest(),
        }}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "VBCCPAS-Soft.exe"
            target.write_bytes(b"old application")
            with patch.object(updater, "_request", return_value=FakeBinaryResponse(package)), \
                 patch.object(updater.subprocess, "Popen") as launch:
                updater.download_and_launch(release, target, 0)
            launch.assert_called_once()
            command = launch.call_args.args[0]
            script_path = Path(command[command.index("-File") + 1])
            self.assertEqual(script_path.name, "updater.ps1")
            self.assertTrue((script_path.parent / updater.MANIFEST_NAME).is_file())
            self.assertEqual(target.read_bytes(), b"old application")
            shutil.rmtree(script_path.parent.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
