"""Standalone graphical updater for the complete VBCCPAS Windows package."""
import argparse
import os
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import updater

try:
    from build_info import BUILD_COMMIT
except ImportError:
    BUILD_COMMIT = "development"


def _log(message):
    try:
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VBCCPAS"
        root.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        with (root / "updater-launch.log").open("a", encoding="utf-8") as stream:
            stream.write(f"[{datetime.now().astimezone().isoformat()}] {message}\\n")
    except Exception:
        pass


class UpdaterWindow:
    def __init__(self, root, target_exe, process_id, repair_mode=False):
        self.root = root
        self.target_exe = Path(target_exe).resolve()
        self.process_id = int(process_id or 0)
        self.repair_mode = bool(repair_mode)
        self.release = None
        self.busy = False
        root.title("VBCCPAS Soft — Software Updater")
        root.geometry("570x455")
        root.minsize(520, 420)
        root.configure(bg="#111214")
        root.protocol("WM_DELETE_WINDOW", self.close_window)
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self.style.configure(
            "TButton", background="#ad603d", foreground="#fff8f1",
            padding=(16, 9), borderwidth=0, focusthickness=2,
            focuscolor="#d8b779", font=("Segoe UI", 10, "bold"),
        )
        self.style.map(
            "TButton",
            background=[("active", "#c87548"), ("pressed", "#8f4e32"), ("disabled", "#51443d")],
            foreground=[("disabled", "#b7aaa0")],
        )
        self.style.configure(
            "Horizontal.TProgressbar", troughcolor="#171719",
            background="#c87548", bordercolor="#3a302b", lightcolor="#c87548",
            darkcolor="#ad603d",
        )

        assets_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets"
        try:
            self.brand_logo = tk.PhotoImage(file=str(assets_dir / "society-logo.gif"))
            root.iconphoto(True, self.brand_logo)
            self.brand_banner = tk.PhotoImage(file=str(assets_dir / "society-header.gif"))
            tk.Label(root, image=self.brand_banner, bg="#000000").pack(fill=tk.X, padx=18, pady=(14, 0))
        except (tk.TclError, OSError) as exc:
            print(f"VBCCPAS branding assets could not be loaded: {exc}")

        panel = tk.Frame(root, bg="#242528", padx=26, pady=24,
                         highlightthickness=1, highlightbackground="#554336")
        panel.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        tk.Label(panel, text="VBCCPAS SOFT", bg="#292a2d", fg="#f3eee6",
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(panel, text="Independent application updater",
                 bg="#292a2d", fg="#d69b54",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(2, 16))
        self.status = tk.StringVar(value="Ready to check GitHub for updates.")
        tk.Label(panel, textvariable=self.status, bg="#292a2d", fg="#e5e7eb",
                 font=("Segoe UI", 10), wraplength=480, justify="left").pack(
                     anchor="w", fill=tk.X, pady=(0, 14))
        self.progress = ttk.Progressbar(panel, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.pack(fill=tk.X, pady=(0, 16))
        actions = tk.Frame(panel, bg="#242528")
        actions.pack(anchor="e")
        self.repair_button = ttk.Button(actions, text="Repair Installation",
                                         command=self.start_repair)
        self.repair_button.pack(side=tk.LEFT, padx=(0, 8))
        self.check_button = ttk.Button(actions, text="Check for Updates",
                                       command=self.check_updates)
        self.check_button.pack(side=tk.LEFT)
        tk.Label(panel,
                 text="Your society database and personal data are excluded from application replacement.",
                 bg="#292a2d", fg="#aeb4bd", wraplength=480,
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(18, 0))

        if self.target_exe.name.lower() != "vbccpas-soft.exe" or (not self.target_exe.is_file() and not self.repair_mode):
            self.check_button.configure(state=tk.DISABLED)
            self.status.set("The application is missing. Use Repair Installation to restore it from GitHub.")
        elif self.process_id or self.repair_mode:
            if self.repair_mode:
                self.status.set("Repair mode: the latest complete release will be reinstalled.")
            self.root.after(350, self.check_updates)

    def close_window(self):
        if self.busy:
            messagebox.showwarning(
                "Update in progress",
                "VBCCPAS Soft is still preparing or applying the update. "
                "Keep this window open until the operation finishes.",
                parent=self.root,
            )
            return
        self.root.destroy()

    def set_busy(self, busy, message):
        self.busy = busy
        self.status.set(message)
        self.progress.start(10) if busy else self.progress.stop()
        check_disabled = busy or (not self.target_exe.is_file() and not self.repair_mode)
        self.check_button.configure(state=tk.DISABLED if check_disabled else tk.NORMAL)
        self.repair_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def check_updates(self):
        if self.busy:
            return
        self.set_busy(True, "Connecting securely to GitHub and checking the latest release…")
        _log(f"Update check started; target={self.target_exe}; build={BUILD_COMMIT}")

        def worker():
            try:
                updater.recover_interrupted_update(self.target_exe)
                _log("Recovery preflight completed; requesting latest release metadata.")
                release = updater.check_for_update(BUILD_COMMIT, force=self.repair_mode)
                _log("Release available." if release else "Already up to date.")
                self.root.after(0, lambda: self.show_release(release))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): self.show_error(
                    "Update check failed", error))
        threading.Thread(target=worker, name="VBCCPAS-release-check", daemon=True).start()

    def show_release(self, release):
        self.set_busy(False, "No update is currently available." if release is None
                      else "A complete application update is available.")
        self.release = release
        if release is None:
            messagebox.showinfo("VBCCPAS Soft", ("No published release is currently available for repair." if self.repair_mode else "This installation is already up to date."),
                                parent=self.root)
            return
        release_name = release.get("name") or release.get("tag_name", "Latest release")
        proceed = messagebox.askyesno(
            "Install complete update",
            "A new VBCCPAS Soft release is available:\n\n" + release_name
            + "\n\nThe updater will close the application, replace application files, "
              "preserve society.db and user-data folders, and keep a rollback copy. "
              "Continue?",
            parent=self.root,
        )
        if proceed:
            self.install_update()

    def start_repair(self):
        if self.busy:
            return
        self.repair_mode = True
        self.check_updates()

    def install_update(self):
        if not self.release or self.busy:
            return
        self.set_busy(True, "Downloading, verifying and preparing the complete application package…")

        def worker():
            try:
                _log(f"Install accepted; updater PID={os.getpid()}; app PID={self.process_id}")
                updater.download_and_launch(
                    self.release, str(self.target_exe), self.process_id,
                    updater_process_id=os.getpid(),
                    repair_mode=self.repair_mode,
                )
                _log("PowerShell handoff returned; closing updater for installer handover.")
                self.root.after(0, self.root.destroy)
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): self.show_error(
                    "Update was not installed", error))
        threading.Thread(target=worker, name="VBCCPAS-package-install", daemon=True).start()

    def show_error(self, title, error):
        self.set_busy(False, "The update could not be completed. Your installed application was not intentionally changed.")
        messagebox.showerror(title, error, parent=self.root)


def main():
    parser = argparse.ArgumentParser(description="VBCCPAS Soft standalone updater")
    parser.add_argument("--target-exe", default=None)
    parser.add_argument("--process-id", type=int, default=0)
    parser.add_argument("--repair", action="store_true", help="Reinstall the latest release if the main application is missing or damaged.")
    args = parser.parse_args()
    _log("Standalone updater launched; argv=" + repr(sys.argv))
    updater_exe_dir = Path(sys.executable if getattr(sys, "frozen", False)
                           else __file__).resolve().parent
    target = Path(args.target_exe).resolve() if args.target_exe else updater_exe_dir / "VBCCPAS-Soft.exe"
    root = tk.Tk()
    try:
        UpdaterWindow(root, target, args.process_id, repair_mode=args.repair)
        _log(f"Updater window initialized; target={target}; app_pid={args.process_id}")
        root.mainloop()
        _log("Updater window closed normally.")
    except Exception:
        _log("Fatal updater GUI error: " + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
