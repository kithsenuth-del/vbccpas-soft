"""Secure GitHub Releases updater for the packaged Windows application."""
import hashlib, json, subprocess, tempfile, urllib.request, zipfile
from pathlib import Path

REPOSITORY = "kithsenuth-del/vbccpas-soft"

RELEASE_API = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
UPDATE_ASSET = "VBCCPAS-Soft-Windows.zip"
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024

def _request(url):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "VBCCPAS-Soft-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return urllib.request.urlopen(request, timeout=20)

def check_for_update(current_commit):
    with _request(RELEASE_API) as response:
        release = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    if release.get("draft") or release.get("prerelease"): return None
    tag = release.get("tag_name", "")
    if not tag.startswith("build-"): raise ValueError("The latest GitHub release has an unrecognised build tag.")
    if not tag[6:] or tag[6:] == current_commit: return None
    asset = next((item for item in release.get("assets", []) if item.get("name") == UPDATE_ASSET), None)
    if not asset: raise ValueError("The latest release does not contain the Windows update package.")
    digest = asset.get("digest", "")
    if not digest.startswith("sha256:"): raise ValueError("GitHub did not provide a SHA-256 checksum for this update.")
    release["_update_asset"] = {"url": asset["browser_download_url"], "digest": digest.split(":", 1)[1].lower(), "size": int(asset.get("size", 0))}
    return release

def download_and_launch(release, target_exe, process_id):
    asset = release.get("_update_asset")
    if not asset: raise ValueError("Update details are missing. Check for updates again.")
    if asset["size"] <= 0 or asset["size"] > MAX_DOWNLOAD_BYTES: raise ValueError("The update package size is invalid or exceeds the safety limit.")
    with _request(asset["url"]) as response: package = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(package) > MAX_DOWNLOAD_BYTES: raise ValueError("The downloaded update is larger than the allowed size.")
    if hashlib.sha256(package).hexdigest() != asset["digest"]: raise ValueError("The update checksum does not match GitHub's release checksum.")
    target = Path(target_exe).resolve()
    if target.name.lower() != "vbccpas-soft.exe" or not target.is_file(): raise ValueError("The running application path could not be verified.")
    stage = Path(tempfile.mkdtemp(prefix="vbccpas-update-"))
    archive_path = stage / UPDATE_ASSET
    archive_path.write_bytes(package)
    with zipfile.ZipFile(archive_path) as archive:
        required = {"VBCCPAS-Soft.exe", "updater.ps1"}
        names = set(archive.namelist())
        if not required.issubset(names): raise ValueError("The update package is missing required application files.")
        allowed = required | {"Create-Desktop-Shortcut.ps1", "Setup-VBCCPAS-Shortcut.bat"}
        for name in names:
            if name not in allowed or Path(name).name != name: raise ValueError("The update package contains an unexpected or unsafe file path.")
        archive.extract("VBCCPAS-Soft.exe", stage)
        archive.extract("updater.ps1", stage)
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(stage / "updater.ps1"), "-ProcessId", str(int(process_id)), "-TargetExe", str(target), "-NewExe", str(stage / "VBCCPAS-Soft.exe")]
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0x00000008) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
    subprocess.Popen(command, close_fds=True, creationflags=flags)