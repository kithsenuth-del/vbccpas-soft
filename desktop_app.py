"""Unified VBCCPAS desktop application with an integrated local web dashboard."""
import contextlib
import io
import os
import sqlite3
import sys
import threading
import webbrowser
import updater
try:
    from build_info import BUILD_COMMIT
except ImportError:
    BUILD_COMMIT = "development"
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import main as desktop
import problem_checker


class UnifiedApp(desktop.App):
    def __init__(self, root):
        super().__init__(root)
        self.httpd = None
        self.server_thread = None
        self.passkey_setup_token = None
        self.root.protocol("WM_DELETE_WINDOW", self.close_application)

    def add_unified_controls(self):
        for key, label in (
            ("web_dashboard", "Web Dashboard"),
            ("maintenance", "Maintenance & Backups"),
        ):
            button = ttk.Button(
                self.sidebar, text=label,
                command=lambda selected=key: self.show_view(selected),
                style="Sidebar.TButton",
            )
            button.pack(fill=tk.X, padx=12, pady=6)
            self.side_buttons[key] = button

    def try_login(self):
        super().try_login()
        if not hasattr(self, "user"):
            return
        self.start_local_server(show_errors=False)
        if self.passkey_setup_token:
            self.show_passkey_setup()

    def show_view(self, key):
        if key not in ("web_dashboard", "maintenance"):
            return super().show_view(key)
        self.clear_content()
        frame = tk.Frame(self.content, bg=self.colors["bg"])
        frame.pack(fill=tk.BOTH, expand=True)
        self.current_view = frame

        if key == "web_dashboard":
            tk.Label(frame, text="Web Dashboard", bg=self.colors["bg"],
                     fg="#f8fafc", font=("Segoe UI", 22, "bold")).pack(
                         anchor="w", padx=28, pady=(28, 8))
            tk.Label(
                frame,
                text="Your photography society dashboard runs locally and shares the same members, events and equipment records.",
                bg=self.colors["bg"], fg="#cbd5e1", font=("Segoe UI", 10),
                wraplength=760, justify="left",
            ).pack(anchor="w", padx=28, pady=(0, 20))
            self.server_status = tk.StringVar(
                value="Local web server: " + ("Running" if self.httpd else "Stopped")
            )
            tk.Label(
                frame, textvariable=self.server_status, bg=self.colors["panel"],
                fg="#e2e8f0", font=("Segoe UI", 12, "bold"), padx=18, pady=14,
            ).pack(fill=tk.X, padx=28, pady=8)
            ttk.Button(frame, text="Open Web Dashboard",
                       command=self.open_web_dashboard,
                       style="Rounded.TButton").pack(anchor="w", padx=28, pady=10)
            ttk.Button(frame, text="Start Local Server",
                       command=lambda: self.start_local_server(show_errors=True),
                       style="Rounded.TButton").pack(anchor="w", padx=28, pady=6)
            ttk.Button(frame, text="Stop Local Server",
                       command=self.stop_local_server,
                       style="Rounded.TButton").pack(anchor="w", padx=28, pady=6)
            tk.Label(
                frame, text="Security: local computer only • 127.0.0.1 • Port 8000",
                bg=self.colors["bg"], fg="#94a3b8", font=("Segoe UI", 9),
            ).pack(anchor="w", padx=28, pady=18)
            return

        tk.Label(frame, text="Maintenance & Backups", bg=self.colors["bg"],
                 fg="#f8fafc", font=("Segoe UI", 22, "bold")).pack(
                     anchor="w", padx=28, pady=(28, 8))
        tk.Label(
            frame, text="Keep the society studio running smoothly with safe checks and database backups.",
            bg=self.colors["bg"], fg="#cbd5e1", font=("Segoe UI", 10),
        ).pack(anchor="w", padx=28, pady=(0, 14))
        actions = tk.Frame(frame, bg=self.colors["bg"])
        actions.pack(fill=tk.X, padx=28, pady=8)
        ttk.Button(actions, text="Run Problem Checker",
                   command=self.run_problem_checker,
                   style="Rounded.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Back Up Database",
                   command=self.backup_database,
                   style="Rounded.TButton").pack(side=tk.LEFT, padx=8)
        ttk.Button(actions, text="Check for Software Updates",
                   command=self.check_for_updates,
                   style="Rounded.TButton").pack(side=tk.LEFT, padx=8)
        ttk.Button(actions, text="Recover Passkey Setup",
                   command=self.recover_passkey_setup,
                   style="Rounded.TButton").pack(side=tk.LEFT, padx=8)
        self.diagnostic_output = tk.Text(
            frame, height=22, wrap=tk.WORD, bg="#171719", fg="#f3eee6",
            insertbackground="white", font=("Consolas", 9), relief="flat",
        )
        self.diagnostic_output.pack(fill=tk.BOTH, expand=True, padx=28, pady=16)
        self.diagnostic_output.insert(
            "1.0",
            "Choose Run Problem Checker to inspect the application.\n"
            "The checker reads the database and does not repair or change records.\n",
        )
        self.diagnostic_output.configure(state=tk.DISABLED)

    def start_local_server(self, show_errors=True):
        if self.httpd:
            return True
        try:
            import server
            import passkey_auth
            passkey_auth.initialize(desktop.DB_PATH)
            configured = passkey_auth.configured(desktop.DB_PATH)
            httpd = server.ThreadingHTTPServer(
                ("127.0.0.1", server.PORT), server.Handler
            )
            httpd.daemon_threads = True
            self.httpd = httpd
            self.server_thread = threading.Thread(
                target=httpd.serve_forever, name="VBCCPAS-local-web-server",
                daemon=True,
            )
            self.server_thread.start()
            self.passkey_setup_token = (
                None if configured else passkey_auth.BOOTSTRAP_TOKEN
            )
            if hasattr(self, "server_status"):
                self.server_status.set("Local web server: Running")
            return True
        except OSError as exc:
            self.httpd = None
            if show_errors:
                messagebox.showerror(
                    "Web server could not start",
                    "Port 8000 may already be in use. Close another VBCCPAS "
                    "server window and try again.\n\n" + str(exc),
                    parent=self.root,
                )
            return False
        except Exception as exc:
            self.httpd = None
            if show_errors:
                messagebox.showerror("Web server error", str(exc), parent=self.root)
            return False

    def open_web_dashboard(self):
        if not self.start_local_server(show_errors=True):
            return
        import server
        webbrowser.open(server.EXPECTED_ORIGIN)
        if hasattr(self, "server_status"):
            self.server_status.set("Local web server: Running")

    def stop_local_server(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if hasattr(self, "server_status"):
            self.server_status.set("Local web server: Stopped")

    def show_passkey_setup(self):
        token = self.passkey_setup_token
        self.passkey_setup_token = None
        if not token:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("First-time Web Administrator Setup")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("590x250")
        dialog.resizable(False, False)
        ttk.Label(dialog, text="One-time passkey setup token",
                  font=("Segoe UI", 15, "bold")).pack(
                      anchor="w", padx=20, pady=(18, 8))
        ttk.Label(
            dialog,
            text="Keep this token private. Copy it into the setup screen in the "
                 "local web dashboard, then register your device passkey.",
            wraplength=540,
        ).pack(anchor="w", padx=20, pady=(0, 12))
        token_var = tk.StringVar(value=token)
        entry = ttk.Entry(dialog, textvariable=token_var, width=76)
        entry.pack(fill=tk.X, padx=20, pady=6)
        entry.configure(state="readonly")

        def copy_token():
            self.root.clipboard_clear()
            self.root.clipboard_append(token)
            messagebox.showinfo("Copied", "The one-time setup token was copied.",
                                parent=dialog)

        buttons = ttk.Frame(dialog)
        buttons.pack(anchor="e", padx=20, pady=16)
        ttk.Button(buttons, text="Copy Token", command=copy_token).pack(
            side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(
            side=tk.LEFT, padx=5)

    def recover_passkey_setup(self):
        """Re-open first-time passkey enrollment after a verified local admin login."""
        import passkey_auth

        if not self.start_local_server(show_errors=True):
            return
        if not messagebox.askyesno(
            "Recover administrator passkey setup",
            "This will remove the saved passkey registration from this computer "
            "so you can register it again. Any currently working passkey will "
            "stop working. Your society records will not be changed.\n\n"
            "A database backup is required before continuing. Proceed?",
            parent=self.root,
        ):
            return
        if not self.backup_database():
            return
        try:
            passkey_auth.reset_registration_state(desktop.DB_PATH)
            self.passkey_setup_token = passkey_auth.BOOTSTRAP_TOKEN
            messagebox.showinfo(
                "Passkey setup is ready",
                "The passkey registration state has been reset safely. "
                "Your society data was preserved and a backup was saved.\n\n"
                "Copy the new one-time token from the next window, then refresh "
                "the local web dashboard to display the registration form.",
                parent=self.root,
            )
            self.show_passkey_setup()
            import server
            webbrowser.open(server.EXPECTED_ORIGIN, new=1)
        except Exception as exc:
            messagebox.showerror(
                "Passkey recovery failed",
                "The passkey state could not be reset. Your database backup "
                "was retained.\n\n" + str(exc),
                parent=self.root,
            )

    def run_problem_checker(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report = problem_checker.Report()
            problem_checker.check_dependencies(report)
            if not getattr(sys, "frozen", False):
                problem_checker.check_python_files(report)
                problem_checker.check_frontend(report)
            else:
                report.add("WARN", "Packaged build: source syntax checks run during build.")
            problem_checker.read_only_database(Path(desktop.DB_PATH), report)
            print("")
            print(f"Results: {report.passed} passed | "
                  f"{report.warnings} warning(s) | {report.failed} problem(s)")
            print("Database records were not changed.")
        self.diagnostic_output.configure(state=tk.NORMAL)
        self.diagnostic_output.delete("1.0", tk.END)
        self.diagnostic_output.insert("1.0", output.getvalue())
        self.diagnostic_output.configure(state=tk.DISABLED)

    def check_for_updates(self):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Software updates", "The built-in updater is available in the packaged Windows version.", parent=self.root)
            return
        def check():
            try:
                release = updater.check_for_update(BUILD_COMMIT)
                self.root.after(0, lambda: self._show_update_result(release))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): messagebox.showerror("Update check failed", "Could not check GitHub for updates. Check your internet connection and try again.\n\n" + error, parent=self.root))
        threading.Thread(target=check, name="VBCCPAS-update-check", daemon=True).start()

    def _show_update_result(self, release):
        if release is None:
            messagebox.showinfo("Software is up to date", "This computer already has the latest published VBCCPAS Soft version.", parent=self.root)
            return
        name = release.get("name") or release["tag_name"]
        if not messagebox.askyesno("Update available", f"A newer version of VBCCPAS Soft is available:\n\n{name}\n\nInstall it now? The application will close and restart. Your society database and passkey information will be kept.", parent=self.root):
            return
        def install():
            try:
                updater.download_and_launch(release, os.path.abspath(sys.executable), os.getpid())
                self.root.after(0, self.close_application)
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): messagebox.showerror("Update could not be installed", "The current version has been left in place.\n\n" + error, parent=self.root))
        threading.Thread(target=install, name="VBCCPAS-update-install", daemon=True).start()

    def backup_database(self):
        if not os.path.isfile(desktop.DB_PATH):
            messagebox.showerror("Database not found",
                                 "The society database does not exist yet.",
                                 parent=self.root)
            return False
        filename = "VBCCPAS_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db"
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="Save secure database backup",
            initialfile=filename, defaultextension=".db",
            filetypes=[("SQLite database", "*.db"), ("All files", "*.*")],
        )
        if not destination:
            return False
        if os.path.abspath(destination) == os.path.abspath(desktop.DB_PATH):
            messagebox.showerror("Invalid destination",
                                 "Choose a different file from the active database.",
                                 parent=self.root)
            return False
        try:
            with sqlite3.connect(desktop.DB_PATH, timeout=10) as source:
                with sqlite3.connect(destination, timeout=10) as target:
                    source.backup(target)
            messagebox.showinfo(
                "Backup complete",
                "Database backup saved. Store it securely. It does not contain "
                "the device passkey's private key.",
                parent=self.root,
            )
            return True
        except (OSError, sqlite3.Error) as exc:
            messagebox.showerror("Backup failed", str(exc), parent=self.root)
            return False

    def close_application(self):
        self.stop_local_server()
        self.root.destroy()


def main():
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        desktop.setup_database()
    root = tk.Tk()
    app = UnifiedApp(root)
    app.add_unified_controls()
    startup = captured.getvalue()
    marker = "VBCCPAS initial admin password (save it securely): "
    if marker in startup:
        password = startup.split(marker, 1)[1].strip().splitlines()[0]
        messagebox.showinfo(
            "First-time administrator account",
            "A new administrator account was created.\n\n"
            "Username: admin\nTemporary password: " + password + "\n\n"
            "Save it securely. You can change it in the Security section.",
            parent=root,
        )
    root.mainloop()


if __name__ == "__main__":
    main()
