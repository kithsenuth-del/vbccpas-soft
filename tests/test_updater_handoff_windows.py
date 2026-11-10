"""Windows integration test for the real Python-to-PowerShell updater handoff.

The launcher runs in a short-lived helper process. The PowerShell child must
remain alive and finish its work after that helper exits, just as it must when
the standalone GUI updater closes.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_package(marker_path):
    escaped_marker = str(marker_path).replace("'", "''")
    script = (
        "param([int]$ProcessId = 0, "
        "[Parameter(Mandatory=$true)][string]$TargetExe, "
        "[string]$PackageDirectory = '', [int]$UpdaterProcessId = 0, "
        "[switch]$RepairMode)\n"
        "Start-Sleep -Seconds 5\n"
        "Set-Content -LiteralPath '" + escaped_marker
        + "' -Value 'HANDOFF_SURVIVED_PARENT_EXIT' -Encoding ASCII\n"
    )
    files = {
        "VBCCPAS-Soft.exe": b"placeholder target application",
        "VBCCPAS-Updater.exe": b"placeholder graphical updater",
        "updater.ps1": script.encode("utf-8"),
        "Recover-VBCCPAS.bat": b"@echo off\r\n",
    }
    manifest = {
        "schema_version": 1,
        "files": sorted(files),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("vbccpas-managed-files.json", json.dumps(manifest))
    return buffer.getvalue()


def write_parent_harness(path):
    source = r"""
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import updater

class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self, _limit=-1):
        return self.payload

package = Path(sys.argv[2]).read_bytes()
target = Path(sys.argv[3])
updater._request = lambda _url: FakeResponse(package)
release = {"_update_asset": {
    "url": "https://github.com/kithsenuth-del/vbccpas-soft/releases/download/test/VBCCPAS-Soft-Windows.zip",
    "size": len(package),
    "digest": hashlib.sha256(package).hexdigest(),
}}
updater.download_and_launch(release, target, 0)
"""
    path.write_text(source, encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory(prefix="VBCCPAS Handoff Test ") as folder:
        root = Path(folder)
        local_app_data = root / "local"
        local_app_data.mkdir()
        marker = root / "handoff-completed.txt"
        package_path = root / "test-package.zip"
        target = root / "VBCCPAS Install With Spaces" / "VBCCPAS-Soft.exe"
        target.parent.mkdir()
        target.write_bytes(b"old application placeholder")
        package_bytes = make_package(marker)
        package_path.write_bytes(package_bytes)
        harness = root / "launcher-parent.py"
        write_parent_harness(harness)

        env = os.environ.copy()
        env["LOCALAPPDATA"] = str(local_app_data)
        env["TEMP"] = str(root)
        env["TMP"] = str(root)
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

        # First prove the extracted PowerShell payload works when launched
        # directly. This separates script/argument problems from process
        # creation and Windows Job Object behaviour in the real handoff.
        direct_marker = root / "direct-powershell-completed.txt"
        with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
            direct_script_text = archive.read("updater.ps1").decode("utf-8")
        direct_script_text = direct_script_text.replace(
            str(marker).replace("'", "''"),
            str(direct_marker).replace("'", "''"),
        )
        direct_script = root / "direct-smoke.ps1"
        direct_script.write_text(direct_script_text, encoding="utf-8")
        powershell = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        direct = subprocess.run(
            [
                str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(direct_script),
                "-ProcessId", "0", "-UpdaterProcessId", "0",
                "-TargetExe", str(target), "-PackageDirectory", str(root),
            ],
            env=env, capture_output=True, text=True, timeout=15, check=False,
        )
        if direct.returncode != 0 or not direct_marker.is_file():
            raise AssertionError(
                "The PowerShell payload failed when run directly; this is not a handoff problem. "
                "Exit=" + str(direct.returncode) + "\\n" + direct.stdout + "\\n" + direct.stderr
            )

        result = subprocess.run(
            [sys.executable, str(harness), str(PROJECT_ROOT), str(package_path), str(target)],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "The updater launcher helper failed.\n"
                + result.stdout + "\n" + result.stderr
            )

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not marker.is_file():
            time.sleep(0.25)

        launch_log = local_app_data / "VBCCPAS" / "updater-launch.log"
        launch_text = launch_log.read_text(encoding="utf-8") if launch_log.exists() else ""
        if not marker.is_file():
            powershell_log = local_app_data / "VBCCPAS" / "updater-powershell.log"
            powershell_text = powershell_log.read_text(encoding="utf-8", errors="replace") if powershell_log.exists() else ""
            raise AssertionError(
                "PowerShell did not finish after its Python launcher exited. "
                "This indicates the detached/breakaway handoff did not survive.\n"
                "Launcher log:\n" + launch_text + "\nPowerShell log:\n" + powershell_text
            )
        if marker.read_text(encoding="ascii").strip() != "HANDOFF_SURVIVED_PARENT_EXIT":
            raise AssertionError("PowerShell handoff marker contents were incorrect.")
        if "Windows rejected breakaway launch" in launch_text:
            if "Breakaway is unavailable; keeping the updater alive until PowerShell completes." not in launch_text:
                raise AssertionError("The launcher did not keep itself alive after Windows rejected breakaway.")
            if "PowerShell completed while updater remained alive; exit_code=0" not in launch_text:
                raise AssertionError("The fallback did not wait for successful PowerShell completion.")
            outcome = "safe wait fallback"
        else:
            if "PowerShell launched with CREATE_BREAKAWAY_FROM_JOB." not in launch_text:
                raise AssertionError("The launcher neither broke away from the Windows job nor logged a rejection.")
            if "remains running after startup check" not in launch_text:
                raise AssertionError("The launcher did not record the expected two-second running-child check.")
            outcome = "CREATE_BREAKAWAY_FROM_JOB"
        time.sleep(1)
        print("PASS: PowerShell completed its staged work after launcher handoff (" + outcome + ").")


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("This integration test must run on Windows.")
    main()
