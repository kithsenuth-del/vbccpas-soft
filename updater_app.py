"""Standalone graphical updater for the complete VBCCPAS Windows package."""
import argparse
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import updater

try:
    from build_info import BUILD_COMMIT
except ImportError:
    BUILD_COMMIT = "development"


class UpdaterWindow:
    def __init__(self, root, target_exe, process_id):
        self.root = root
        self.target_exe = Path(target_exe).resolve()
        self.process_id = int(process_id or 0)
        self.release = None
        self.busy = False
        root.title("VBCCPAS Soft — Software Updater")
        root.geometry("570x455")
        root.minsize(520, 420)
        root.configure(bg="#111214")
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
        self.check_button = ttk.Button(panel, text="Check for Updates",
                                       command=self.check_updates)
        self.check_button.pack(anchor="e")
        tk.Label(panel,
                 text="Your society database and personal data are excluded from application replacement.",
                 bg="#292a2d", fg="#aeb4bd", wraplength=480,
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(18, 0))

        if not self.target_exe.is_file() or self.target_exe.name.lower() != "vbccpas-soft.exe":
            self.check_button.configure(state=tk.DISABLED)
            self.status.set("The VBCCPAS application could not be located. Keep this updater beside VBCCPAS-Soft.exe.")
        elif self.process_id:
            self.root.after(350, self.check_updates)

    def set_busy(self, busy, message):
        self.busy = busy
        self.status.set(message)
        self.progress.start(10) if busy else self.progress.stop()
        self.check_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def check_updates(self):
        if self.busy:
            return
        self.set_busy(True, "Connecting securely to GitHub and checking the latest release…")

        def worker():
            try:
                release = updater.check_for_update(BUILD_COMMIT)
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
            messagebox.showinfo("VBCCPAS Soft", "This installation is already up to date.",
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

    def install_update(self):
        if not self.release or self.busy:
            return
        self.set_busy(True, "Downloading, verifying and preparing the complete application package…")

        def worker():
            try:
                updater.download_and_launch(
                    self.release, str(self.target_exe), self.process_id,
                    updater_process_id=os.getpid(),
                )
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
    args = parser.parse_args()
    updater_exe_dir = Path(sys.executable if getattr(sys, "frozen", False)
                           else __file__).resolve().parent
    target = Path(args.target_exe).resolve() if args.target_exe else updater_exe_dir / "VBCCPAS-Soft.exe"
    root = tk.Tk()
    UpdaterWindow(root, target, args.process_id)
    root.mainloop()


if __name__ == "__main__":
    main()
