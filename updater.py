"""Secure GitHub Releases downloader and full-package Windows installer launcher."""
import hashlib
import base64
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "kithsenuth-del/vbccpas-soft"
RELEASE_API = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
UPDATE_ASSET = "VBCCPAS-Soft-Windows.zip"
MANIFEST_NAME = "vbccpas-managed-files.json"
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
MAX_EXTRACTED_FILE_BYTES = 250 * 1024 * 1024
MAX_EXTRACTED_TOTAL_BYTES = 500 * 1024 * 1024
MAX_PACKAGE_FILES = 10000
PROTECTED_PATH_PARTS = {
    "society.db", "society.db-wal", "society.db-shm", "society.db-journal",
    "backups", "exports", "logs", "user-data", "user_data", ".vbccpas-data",
}


def _request(url):
    """Open only HTTPS requests to GitHub's API or release download host."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "api.github.com", "github.com", "release-assets.githubusercontent.com"
    }:
        raise ValueError("The update server address is not trusted.")
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "VBCCPAS-Soft-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return urllib.request.urlopen(request, timeout=30)


def check_for_update(current_commit, force=False):
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
    if not force and release_commit.lower() == str(current_commit).lower():
        return None

    asset = next((item for item in release.get("assets", [])
                  if item.get("name") == UPDATE_ASSET), None)
    if not asset:
        raise ValueError("The latest release does not contain the Windows update package.")
    digest = asset.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(
        r"sha256:[0-9a-fA-F]{64}", digest
    ):
        raise ValueError("GitHub has not published a valid SHA-256 checksum for this update yet.")
    download_url = asset.get("browser_download_url", "")
    parsed_url = urllib.parse.urlparse(download_url)
    if (parsed_url.scheme != "https" or parsed_url.hostname != "github.com"
            or not parsed_url.path.startswith(f"/{REPOSITORY}/releases/download/")):
        raise ValueError("GitHub returned an unexpected update download address.")
    size = int(asset.get("size", 0))
    if size <= 0 or size > MAX_DOWNLOAD_BYTES:
        raise ValueError("The published update package size is invalid.")
    release["_update_asset"] = {
        "url": download_url, "digest": digest.split(":", 1)[1].lower(),
        "size": size,
    }
    return release


def extract_package(package_bytes, destination):
    """Validate and extract every application file without touching user data."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    total_size = 0
    extracted = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(package_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("The downloaded update is not a valid ZIP package.") from exc

    with archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_PACKAGE_FILES:
            raise ValueError("The update package contains an invalid number of files.")
        names = set()
        for entry in entries:
            raw_name = entry.filename
            if not raw_name or "\\" in raw_name or ":" in raw_name:
                raise ValueError("The update package contains an unsafe file path.")
            directory_entry = entry.is_dir()
            path_text = raw_name[:-1] if directory_entry and raw_name.endswith("/") else raw_name
            raw_parts = path_text.split("/")
            if (not path_text or path_text.startswith("/")
                    or any(part in ("", ".", "..") for part in raw_parts)):
                raise ValueError("The update package contains a path outside the application folder.")
            relative = PurePosixPath(path_text)
            if relative.is_absolute():
                raise ValueError("The update package contains a path outside the application folder.")
            collision_key = relative.as_posix().casefold()
            if collision_key in names:
                raise ValueError("The update package contains duplicate or Windows-colliding entries.")
            names.add(collision_key)
            reserved_names = {"CON", "PRN", "AUX", "NUL"} | {
                f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
            }
            for part in relative.parts:
                if (part.rstrip(" .") != part or any(char in part for char in '<>:"|?*')
                        or part.split(".", 1)[0].upper() in reserved_names):
                    raise ValueError("The update package contains a Windows-invalid file name.")
            if any(part.lower() in PROTECTED_PATH_PARTS for part in relative.parts):
                raise ValueError("The update package attempts to include protected user data.")
            mode = (entry.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ValueError("The update package contains a symbolic link.")
            if directory_entry:
                continue
            if entry.file_size < 0 or entry.file_size > MAX_EXTRACTED_FILE_BYTES:
                raise ValueError("An application file exceeds the extraction size limit.")
            total_size += entry.file_size
            if total_size > MAX_EXTRACTED_TOTAL_BYTES:
                raise ValueError("The extracted application package is too large.")
            output = destination.joinpath(*relative.parts).resolve()
            if destination != output and destination not in output.parents:
                raise ValueError("The update package contains an unsafe destination.")
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry, "r") as source:
                data = source.read(MAX_EXTRACTED_FILE_BYTES + 1)
            if len(data) != entry.file_size or len(data) > MAX_EXTRACTED_FILE_BYTES:
                raise ValueError("An application file has an invalid uncompressed size.")
            output.write_bytes(data)
            extracted.append(relative.as_posix())

    required = {"VBCCPAS-Soft.exe", "VBCCPAS-Updater.exe", "updater.ps1", "Recover-VBCCPAS.bat", MANIFEST_NAME}
    if not required.issubset(set(extracted)):
        raise ValueError("The update package is missing the application, standalone updater, recovery launcher, or package manifest.")
    try:
        manifest = json.loads((destination / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The application package manifest is missing or invalid.") from exc
    manifest_files = manifest.get("files") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or not isinstance(manifest_files, list)):
        raise ValueError("The application package manifest has an unsupported format.")
    if (not all(isinstance(name, str) for name in manifest_files)
            or len(manifest_files) != len(set(manifest_files))):
        raise ValueError("The application package manifest contains invalid file entries.")
    actual_files = set(extracted) - {MANIFEST_NAME}
    if set(manifest_files) != actual_files:
        raise ValueError("The application package manifest does not match its contents.")
    return extracted


def recover_interrupted_update(target_exe, recovery_script=None):
    """Run the persistent recovery bootstrap before checking or applying updates."""
    target = Path(target_exe).resolve()
    if recovery_script is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return
        recovery_script = Path(local_app_data) / "VBCCPAS" / "Recovery" / "updater-recovery.ps1"
    script = Path(recovery_script)
    if not script.is_file():
        return
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-TargetExe", str(target), "-RecoverOnly", "-Quiet",
    ]
    # Older persistent recovery bootstraps may not know this optional switch.
    try:
        if "RestartAfterRecovery" in script.read_text(encoding="utf-8-sig"):
            command.append("-RestartAfterRecovery")
    except (OSError, UnicodeError):
        pass
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=180, check=False,
        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                       if os.name == "nt" else 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            "An interrupted VBCCPAS update needs recovery before another update"
            + (": " + detail if detail else ". Check %LOCALAPPDATA%\\VBCCPAS\\update.log.")
        )


def _log_handoff(message):
    try:
        import datetime
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VBCCPAS"
        root.mkdir(parents=True, exist_ok=True)
        with (root / "updater-launch.log").open("a", encoding="utf-8") as stream:
            stream.write("[" + datetime.datetime.now().astimezone().isoformat() + "] " + str(message) + "\n")
    except Exception:
        pass


def _schedule_powershell_update_task(powershell, script, process_id, updater_process_id,
                                      target, payload, repair_mode, stage, timeout=30):
    """Run the installer through Task Scheduler when Windows denies job breakaway."""
    task_name = "VBCCPAS-Update-" + uuid.uuid4().hex
    script_args = [
        "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-ProcessId", str(int(process_id)),
        "-UpdaterProcessId", str(int(updater_process_id or 0)),
        "-TargetExe", str(target),
        "-PackageDirectory", str(payload),
        "-ScheduledTaskName", task_name,
    ]
    if repair_mode:
        script_args.append("-RepairMode")

    task_payload = {
        "TaskName": task_name,
        "PowerShell": str(powershell),
        "Arguments": subprocess.list2cmdline(script_args),
        "WorkingDirectory": str(stage),
    }
    encoded_payload = base64.b64encode(
        json.dumps(task_payload).encode("utf-8")
    ).decode("ascii")
    helper_script = r"""
param([Parameter(Mandatory=$true)][string]$PayloadBase64)
$ErrorActionPreference = "Stop"
$data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PayloadBase64)) | ConvertFrom-Json
$service = New-Object -ComObject Schedule.Service
$service.Connect()
$folder = $service.GetFolder("\")
foreach ($existingTask in @($folder.GetTasks(0))) {
    if ($existingTask.Name -like "VBCCPAS-Update-*") {
        if ($existingTask.State -eq 4 -or $existingTask.State -eq 2) {
            throw ("Another VBCCPAS scheduled update is still active: " + $existingTask.Name)
        }
        try { $folder.DeleteTask($existingTask.Name, 0) } catch { }
    }
}
$definition = $service.NewTask(0)
$definition.RegistrationInfo.Description = "Temporary VBCCPAS update handoff"
$definition.Settings.Enabled = $true
$definition.Settings.Hidden = $true
$definition.Settings.AllowDemandStart = $true
$definition.Settings.StartWhenAvailable = $true
$definition.Settings.DisallowStartIfOnBatteries = $false
$definition.Settings.StopIfGoingOnBatteries = $false
$definition.Settings.ExecutionTimeLimit = "PT30M"
$definition.Principal.UserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$definition.Principal.LogonType = 3
$definition.Principal.RunLevel = 0
$trigger = $definition.Triggers.Create(1)
$trigger.StartBoundary = (Get-Date).AddDays(30).ToString("s")
$trigger.Enabled = $false
$action = $definition.Actions.Create(0)
$action.Path = $data.PowerShell
$action.Arguments = $data.Arguments
$action.WorkingDirectory = $data.WorkingDirectory
$registered = $folder.RegisterTaskDefinition($data.TaskName, $definition, 6, $null, $null, 3, $null)
$null = $registered.Run($null)
$deadline = (Get-Date).AddSeconds(15)
$state = 0
do {
    $registered = $folder.GetTask($data.TaskName, 0)
    $state = $registered.State
    if ($state -eq 4 -or $state -eq 2) { break }
    Start-Sleep -Milliseconds 200
} while ((Get-Date) -lt $deadline)
if ($state -ne 4 -and $state -ne 2) {
    try { $folder.DeleteTask($data.TaskName, 0) } catch { }
    throw "Scheduled updater task did not start. Task state: $state"
}
Write-Output ("TASK_STARTED:" + $data.TaskName)
"""
    helper_path = Path(stage) / "register-update-task.ps1"
    helper_path.write_text(helper_script, encoding="utf-8")
    command = [
        str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(helper_path),
        "-PayloadBase64", encoded_payload,
    ]
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    result = subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=float(timeout), creationflags=no_window, check=False,
    )
    expected = "TASK_STARTED:" + task_name
    if result.returncode != 0 or expected not in (result.stdout or ""):
        detail = (result.stdout or "") + "\\n" + (result.stderr or "")
        raise RuntimeError(
            "Windows denied process breakaway and the scheduled updater task could not be started. "
            "No application files were changed. " + detail.strip()
        )
    _log_handoff("Scheduled Task started PowerShell outside the updater job; task=" + task_name)
    return task_name


def download_and_launch(release, target_exe, process_id, updater_process_id=0, repair_mode=False, handoff_timeout=30):
    """Download a verified complete package and hand installation to PowerShell."""
    asset = release.get("_update_asset")
    if not asset:
        raise ValueError("Update details are missing. Check for updates again.")
    if asset["size"] <= 0 or asset["size"] > MAX_DOWNLOAD_BYTES:
        raise ValueError("The update package size is invalid or exceeds the safety limit.")

    _log_handoff("Downloading release package; target=" + str(target_exe))
    with _request(asset["url"]) as response:
        package = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(package) > MAX_DOWNLOAD_BYTES:
        raise ValueError("The downloaded update is larger than the allowed size.")
    if len(package) != asset["size"]:
        raise ValueError("The downloaded update is incomplete. Your current version was not changed.")
    if hashlib.sha256(package).hexdigest() != asset["digest"]:
        raise ValueError("The update checksum does not match GitHub's release checksum.")

    target = Path(target_exe).resolve()
    if target.name.lower() != "vbccpas-soft.exe" or (not repair_mode and not target.is_file()):
        raise ValueError("The application installation path could not be verified.")

    stage = Path(tempfile.mkdtemp(prefix="vbccpas-full-update-"))
    payload = stage / "payload"
    try:
        extracted = extract_package(package, payload)
        script = payload / "updater.ps1"
        _log_handoff("Package verified; extracted files=" + str(len(extracted)) + "; script exists=" + str(script.is_file()))
        if not script.is_file() or not payload.is_dir():
            raise RuntimeError("The extracted PowerShell installer or package folder is missing.")
        if os.name == "nt":
            windows_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
            powershell = windows_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            if not powershell.is_file():
                raise RuntimeError("Windows PowerShell could not be found at its standard Windows installation path.")
            powershell_command = str(powershell)
        else:
            # Non-Windows CI validates command construction with a mocked process.
            powershell_command = "powershell.exe"
            powershell = Path(powershell_command)
        command = [
            powershell_command, "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-ProcessId", str(int(process_id)),
            "-UpdaterProcessId", str(int(updater_process_id or 0)),
            "-TargetExe", str(target),
            "-PackageDirectory", str(payload),
        ]
        if repair_mode:
            command.append("-RepairMode")
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        # A packaged GUI updater may itself be hosted by a Windows Job Object
        # that terminates child processes when the parent exits. Ask Windows
        # to break the installer out of an inheritable job so it can finish
        # after the small updater window closes. If the host job disallows
        # breakaway, retry with the normal detached flags and log that fact.
        breakaway_flag = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        launch_flags = flags | breakaway_flag if os.name == "nt" else flags
        log_root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VBCCPAS"
        log_root.mkdir(parents=True, exist_ok=True)
        output_path = log_root / "updater-powershell.log"
        _log_handoff("Launching PowerShell; executable=" + str(powershell)
                     + "; script=" + str(script)
                     + "; target=" + str(target)
                     + "; package=" + str(payload)
                     + "; output=" + str(output_path))
        breakaway_rejected = False
        with output_path.open("ab") as output:
            try:
                child = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT, close_fds=True,
                    creationflags=launch_flags,
                )
                if launch_flags != flags:
                    _log_handoff("PowerShell launched with CREATE_BREAKAWAY_FROM_JOB.")
            except OSError as launch_error:
                if launch_flags == flags:
                    raise
                breakaway_rejected = True
                _log_handoff("Windows rejected breakaway launch; delegating through Task Scheduler: "
                             + repr(launch_error))
        if breakaway_rejected:
            # The PowerShell script waits for the GUI updater to exit before
            # replacing VBCCPAS-Updater.exe. It therefore cannot be kept as a
            # child while the updater waits. Task Scheduler starts the real
            # installer outside the updater's Windows Job Object; it can wait
            # for this process to exit and then safely replace the updater.
            _log_handoff("Breakaway is unavailable; scheduling the installer outside the updater job.")
            task_name = _schedule_powershell_update_task(
                powershell, script, process_id, updater_process_id,
                target, payload, repair_mode, stage, timeout=handoff_timeout,
            )
            _log_handoff("Update delegated to scheduled task; updater may now close; task=" + task_name)
        else:
            _log_handoff("PowerShell process created; PID=" + str(child.pid)
                         + "; command=" + subprocess.list2cmdline(command)
                         + "; stage=" + str(stage))
            # A created PID only proves CreateProcess succeeded. Detect
            # immediate startup/argument failures before the GUI updater exits.
            try:
                early_exit = child.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                _log_handoff("PowerShell remains running after startup check; PID=" + str(child.pid))
            else:
                if isinstance(early_exit, int):
                    _log_handoff("PowerShell exited during startup check; PID=" + str(child.pid)
                                 + "; exit_code=" + str(early_exit))
                    if early_exit != 0:
                        raise RuntimeError(
                            "The PowerShell installer exited immediately with code "
                            + str(early_exit) + ". Check %LOCALAPPDATA%\\VBCCPAS\\update.log "
                            "and updater-powershell.log."
                        )
                else:
                    _log_handoff("PowerShell startup check returned no numeric exit code; PID=" + str(child.pid))
    except Exception as exc:
        _log_handoff("PowerShell handoff failed: " + repr(exc))
        import shutil
        shutil.rmtree(stage, ignore_errors=True)
        raise
