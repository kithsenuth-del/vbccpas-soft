"""Secure GitHub Releases updater for the packaged Windows application."""
import hashlib
import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPOSITORY = "kithsenuth-del/vbccpas-soft"
RELEASE_API = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
UPDATE_ASSET = "VBCCPAS-Soft-Windows.zip"
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
MAX_EXTRACTED_FILE_BYTES = 250 * 1024 * 1024
ALLOWED_FILES = {
    "VBCCPAS-Soft.exe",
    "updater.ps1",
    "Create-Desktop-Shortcut.ps1",
    "Setup-VBCCPAS-Shortcut.bat",
}


def _request(url):
    """Open only HTTPS requests to GitHub's API or release download host."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "github.com", "release-assets.githubusercontent.com"}:
        raise ValueError("The update server address is not trusted.")
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "VBCCPAS-Soft-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return urllib.request.urlopen(request, timeout=30)


def check_for_update(current_commit):
    with _request(RELEASE_API) as response:
        release = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))

    if release.get("draft") or release.get("prerelease"):
        return None

    tag = release.get("tag_name", "")
    if not tag.startswith("build-") or len(tag) != len("build-") + 40:
        raise ValueError("The latest GitHub release has an unrecognised build tag.")

    release_commit = tag[len("build-"):]
    if not re.fullmatch(r"[0-9a-fA-F]{40}", release_commit):
        raise ValueError("The latest GitHub release has an invalid build identifier.")
    if release_commit.lower() == str(current_commit).lower():
        return None

    asset = next(
        (item for item in release.get("assets", []) if item.get("name") == UPDATE_ASSET),
        None,
    )
    if not asset:
        raise ValueError("The latest release does not contain the Windows update package.")

    digest = asset.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError(
            "GitHub has not published a valid SHA-256 checksum for this update yet. "
            "Please try again shortly."
        )

    download_url = asset.get("browser_download_url", "")
    parsed_url = urllib.parse.urlparse(download_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "github.com"
        or not parsed_url.path.startswith(f"/{REPOSITORY}/releases/download/")
    ):
        raise ValueError("GitHub returned an unexpected update download address.")

    size = int(asset.get("size", 0))
    if size <= 0 or size > MAX_DOWNLOAD_BYTES:
        raise ValueError("The published update package size is invalid.")

    release["_update_asset"] = {
        "url": download_url,
        "digest": digest.split(":", 1)[1].lower(),
        "size": size,
    }
    return release


def download_and_launch(release, target_exe, process_id):
    asset = release.get("_update_asset")
    if not asset:
        raise ValueError("Update details are missing. Check for updates again.")
    if asset["size"] <= 0 or asset["size"] > MAX_DOWNLOAD_BYTES:
        raise ValueError("The update package size is invalid or exceeds the safety limit.")

    with _request(asset["url"]) as response:
        package = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(package) > MAX_DOWNLOAD_BYTES:
        raise ValueError("The downloaded update is larger than the allowed size.")
    if len(package) != asset["size"]:
        raise ValueError("The downloaded update is incomplete. Your current version was not changed.")
    if hashlib.sha256(package).hexdigest() != asset["digest"]:
        raise ValueError("The update checksum does not match GitHub's release checksum.")

    target = Path(target_exe).resolve()
    if target.name.lower() != "vbccpas-soft.exe" or not target.is_file():
        raise ValueError("The running application path could not be verified.")

    stage = Path(tempfile.mkdtemp(prefix="vbccpas-update-"))
    archive_path = stage / UPDATE_ASSET
    archive_path.write_bytes(package)

    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("The update package contains duplicate file entries.")
        if not {"VBCCPAS-Soft.exe", "updater.ps1"}.issubset(set(names)):
            raise ValueError("The update package is missing required application files.")
        for entry in entries:
            if (
                entry.filename not in ALLOWED_FILES
                or Path(entry.filename).name != entry.filename
                or entry.is_dir()
                or entry.file_size > MAX_EXTRACTED_FILE_BYTES
            ):
                raise ValueError("The update package contains an unexpected or unsafe file.")
        bad_file = archive.testzip()
        if bad_file:
            raise ValueError("The update package is damaged: " + bad_file)
        archive.extract("VBCCPAS-Soft.exe", stage)
        archive.extract("updater.ps1", stage)

    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(stage / "updater.ps1"),
        "-ProcessId", str(int(process_id)),
        "-TargetExe", str(target),
        "-NewExe", str(stage / "VBCCPAS-Soft.exe"),
    ]
    flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    )
    subprocess.Popen(command, close_fds=True, creationflags=flags)
