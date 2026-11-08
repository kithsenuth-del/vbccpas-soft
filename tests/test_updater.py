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


class UpdaterTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
