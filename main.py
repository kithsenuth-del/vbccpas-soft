
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import sqlite3
import hashlib
import hmac
import secrets
import os
import csv
import sys
from datetime import datetime
from database_migrations import create_verified_backup

if getattr(sys, 'frozen', False):
	BASE_DIR = os.path.dirname(sys.executable)
else:
	BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "society.db")
APP_RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def hash_password(pw: str) -> str:
	"""Return a salted PBKDF2-SHA256 password hash."""
	salt = secrets.token_bytes(16)
	iterations = 310000
	digest = hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), salt, iterations)
	return 'pbkdf2_sha256$' + str(iterations) + '$' + salt.hex() + '$' + digest.hex()


def verify_password(pw: str, stored: str) -> bool:
	"""Verify PBKDF2 hashes and legacy SHA-256 hashes during migration."""
	if not stored:
		return False
	if stored.startswith('pbkdf2_sha256$'):
		try:
			_, iterations, salt_hex, digest_hex = stored.split('$', 3)
			digest = hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), bytes.fromhex(salt_hex), int(iterations))
			return hmac.compare_digest(digest.hex(), digest_hex)
		except (ValueError, TypeError):
			return False
	legacy_digest = hashlib.sha256(pw.encode('utf-8')).hexdigest()
	return hmac.compare_digest(legacy_digest, stored)


def setup_database():
	conn = sqlite3.connect(DB_PATH)
	cur = conn.cursor()

	def table_exists(table_name):
		cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
		return cur.fetchone() is not None

	def get_columns(table_name):
		cur.execute(f"PRAGMA table_info({table_name})")
		return [row[1] for row in cur.fetchall()]

	def migrate_members():
		if not table_exists('Members'):
			return
		cols = [c.lower() for c in get_columns('Members')]
		legacy_condition = 'memberid' in cols and 'name' in cols and any(x in cols for x in ('contact', 'role', 'status', 'itemname', 'description', 'location'))
		new_schema_ok = {'memberid', 'name', 'class', 'roll', 'email'}.issubset(set(cols))
		if legacy_condition and not new_schema_ok:
			cur.execute('DROP TABLE IF EXISTS Members_old')
			cur.execute('ALTER TABLE Members RENAME TO Members_old')
			cur.execute('''
			CREATE TABLE Members (
				MemberID INTEGER PRIMARY KEY AUTOINCREMENT,
				Name TEXT NOT NULL,
				Class TEXT,
				Roll TEXT,
				Email TEXT
			)
			''')
			email_field = 'Email' if 'email' in cols else ('contact' if 'contact' in cols else 'NULL')
			class_field = 'Class' if 'class' in cols else 'NULL'
			roll_field = 'Roll' if 'roll' in cols else 'NULL'
			cur.execute(f'''
			INSERT INTO Members (MemberID, Name, Class, Roll, Email)
			SELECT memberid, name, {class_field}, {roll_field}, {email_field} FROM Members_old
			''')
			cur.execute('DROP TABLE Members_old')

	def migrate_events():
		if not table_exists('Events'):
			return
		cols = [c.lower() for c in get_columns('Events')]
		if 'notes' not in cols and 'description' in cols:
			cur.execute('DROP TABLE IF EXISTS Events_old')
			cur.execute('ALTER TABLE Events RENAME TO Events_old')
			cur.execute('''
			CREATE TABLE Events (
				EventID INTEGER PRIMARY KEY AUTOINCREMENT,
				Title TEXT NOT NULL,
				Date TEXT,
				Location TEXT,
				Notes TEXT
			)
			''')
			cur.execute('''
			INSERT INTO Events (EventID, Title, Date, Location, Notes)
			SELECT EventID, Title, Date, Location, Description FROM Events_old
			''')
			cur.execute('DROP TABLE Events_old')

	def migrate_inventory():
		if not table_exists('Inventory'):
			return
		cols = [c.lower() for c in get_columns('Inventory')]
		if 'name' not in cols and 'itemname' in cols:
			cur.execute('DROP TABLE IF EXISTS Inventory_old')
			cur.execute('ALTER TABLE Inventory RENAME TO Inventory_old')
			cur.execute('''
			CREATE TABLE Inventory (
				ItemID INTEGER PRIMARY KEY AUTOINCREMENT,
				Name TEXT NOT NULL,
				Quantity INTEGER DEFAULT 0,
				Notes TEXT
			)
			''')
			cur.execute('''
			INSERT INTO Inventory (ItemID, Name, Quantity, Notes)
			SELECT ItemID, ItemName, Quantity, COALESCE(Notes, Location) FROM Inventory_old
			''')
			cur.execute('DROP TABLE Inventory_old')

	def migrate_borrowed():
		if not table_exists('BorrowedItems'):
			return
		cols = [c.lower() for c in get_columns('BorrowedItems')]
		if 'borrowdate' not in cols and 'dateborrowed' in cols:
			cur.execute('DROP TABLE IF EXISTS BorrowedItems_old')
			cur.execute('ALTER TABLE BorrowedItems RENAME TO BorrowedItems_old')
			cur.execute('''
			CREATE TABLE BorrowedItems (
				BorrowID INTEGER PRIMARY KEY AUTOINCREMENT,
				ItemID INTEGER,
				MemberID INTEGER,
				BorrowDate TEXT,
				ReturnDate TEXT,
				Returned BOOLEAN DEFAULT 0
			)
			''')
			cur.execute('''
			INSERT INTO BorrowedItems (BorrowID, ItemID, MemberID, BorrowDate, ReturnDate, Returned)
			SELECT BorrowID, ItemID, MemberID, DateBorrowed, DateReturned,
			       CASE WHEN DateReturned IS NOT NULL AND DateReturned != '' THEN 1 ELSE 0 END
			FROM BorrowedItems_old
			''')
			cur.execute('DROP TABLE BorrowedItems_old')

	def migrate_attendance():
		if not table_exists('Attendance'):
			return
		cols = [c.lower() for c in get_columns('Attendance')]
		if 'attid' not in cols and 'attendanceid' in cols:
			cur.execute('DROP TABLE IF EXISTS Attendance_old')
			cur.execute('ALTER TABLE Attendance RENAME TO Attendance_old')
			cur.execute('''
			CREATE TABLE Attendance (
				AttID INTEGER PRIMARY KEY AUTOINCREMENT,
				MemberID INTEGER,
				EventID INTEGER,
				Present BOOLEAN DEFAULT 0,
				Timestamp TEXT
			)
			''')
			cur.execute('''
			INSERT INTO Attendance (AttID, MemberID, EventID, Present, Timestamp)
			SELECT AttendanceID, MemberID, EventID, Present, datetime('now') FROM Attendance_old
			''')
			cur.execute('DROP TABLE Attendance_old')

	# Preserve a verified copy before any legacy conversion can rename/drop tables.
	legacy_migration_needed = (
		(table_exists('Members') and {'memberid', 'name'}.issubset(set(c.lower() for c in get_columns('Members'))) and
		 any(x in set(c.lower() for c in get_columns('Members')) for x in ('contact', 'role', 'status', 'itemname', 'description', 'location')) and
		 not {'memberid', 'name', 'class', 'roll', 'email'}.issubset(set(c.lower() for c in get_columns('Members')))) or
		(table_exists('Events') and 'notes' not in set(c.lower() for c in get_columns('Events')) and 'description' in set(c.lower() for c in get_columns('Events'))) or
		(table_exists('Inventory') and 'name' not in set(c.lower() for c in get_columns('Inventory')) and 'itemname' in set(c.lower() for c in get_columns('Inventory'))) or
		(table_exists('BorrowedItems') and 'borrowdate' not in set(c.lower() for c in get_columns('BorrowedItems')) and 'dateborrowed' in set(c.lower() for c in get_columns('BorrowedItems'))) or
		(table_exists('Attendance') and 'attid' not in set(c.lower() for c in get_columns('Attendance')) and 'attendanceid' in set(c.lower() for c in get_columns('Attendance')))
	)
	if legacy_migration_needed:
		# Fail closed: if backup or verification fails, do not run destructive conversions.
		create_verified_backup(DB_PATH)

	# Run migrations for any legacy schema before creating the expected tables.
	migrate_members()
	migrate_events()
	migrate_inventory()
	migrate_borrowed()
	migrate_attendance()

	# Users
	cur.execute('''
	CREATE TABLE IF NOT EXISTS Users (
		UserID INTEGER PRIMARY KEY AUTOINCREMENT,
		MemberID INTEGER,
		Username TEXT UNIQUE NOT NULL,
		Password TEXT,
		Role TEXT CHECK(Role IN ('admin','member')) NOT NULL DEFAULT 'member',
		FirstLogin BOOLEAN DEFAULT 1
	)
	''')
	# Members
	cur.execute('''
	CREATE TABLE IF NOT EXISTS Members (
		MemberID INTEGER PRIMARY KEY AUTOINCREMENT,
		Name TEXT NOT NULL,
		Class TEXT,
		Roll TEXT,
		Email TEXT
	)
	''')
	# Events
	cur.execute('''
	CREATE TABLE IF NOT EXISTS Events (
		EventID INTEGER PRIMARY KEY AUTOINCREMENT,
		Title TEXT NOT NULL,
		Date TEXT,
		Location TEXT,
		Notes TEXT
	)
	''')
	# Inventory
	cur.execute('''
	CREATE TABLE IF NOT EXISTS Inventory (
		ItemID INTEGER PRIMARY KEY AUTOINCREMENT,
		Name TEXT NOT NULL,
		Quantity INTEGER DEFAULT 0,
		Notes TEXT
	)
	''')

	# Add equipment-register fields without rebuilding or discarding existing records.
	inventory_fields = {
		'Category': "TEXT DEFAULT 'Other'",
		'Brand': 'TEXT',
		'Model': 'TEXT',
		'SerialNumber': 'TEXT',
		'Condition': "TEXT DEFAULT 'Good'",
		'Location': 'TEXT',
		'Custodian': 'TEXT',
		'PurchaseDate': 'TEXT',
		'Status': "TEXT DEFAULT 'Available'",
	}
	inventory_columns = {column.lower() for column in get_columns('Inventory')}
	for column, definition in inventory_fields.items():
		if column.lower() not in inventory_columns:
			cur.execute(f'ALTER TABLE Inventory ADD COLUMN {column} {definition}')

	# Borrowed
	cur.execute('''
	CREATE TABLE IF NOT EXISTS BorrowedItems (
		BorrowID INTEGER PRIMARY KEY AUTOINCREMENT,
		ItemID INTEGER,
		MemberID INTEGER,
		BorrowDate TEXT,
		ReturnDate TEXT,
		Returned BOOLEAN DEFAULT 0
	)
	''')
	# Attendance
	cur.execute('''
	CREATE TABLE IF NOT EXISTS Attendance (
		AttID INTEGER PRIMARY KEY AUTOINCREMENT,
		MemberID INTEGER,
		EventID INTEGER,
		Present BOOLEAN DEFAULT 0,
		Timestamp TEXT
	)
	''')

	# Bootstrap a fresh installation with the requested first-sign-in password.
	# An environment variable can override this default for managed deployments.
	cur.execute("SELECT 1 FROM Users WHERE Username=?", ("admin",))
	if not cur.fetchone():
		initial_password = os.environ.get("VBCCPAS_ADMIN_PASSWORD", "admin123")
		cur.execute("INSERT INTO Users (Username, Password, Role) VALUES (?, ?, 'admin')", ("admin", hash_password(initial_password)))

	conn.commit()
	conn.close()


def calculate_window_geometry(screen_width, screen_height):
	"""Choose a centred, resizable window that fits common Windows workspaces."""
	screen_width = max(1, int(screen_width))
	screen_height = max(1, int(screen_height))
	width = min(screen_width, max(640, min(1440, screen_width - 64)))
	height = min(screen_height, max(480, min(900, screen_height - 96)))
	min_width = min(1120, width)
	min_height = min(740, height)
	x = max(0, (screen_width - width) // 2)
	y = max(0, (screen_height - height) // 2)
	return {
		"width": width, "height": height,
		"min_width": min_width, "min_height": min_height,
		"x": x, "y": y,
	}


class App:
	def __init__(self, root):
		self.root = root
		root.title("VBCCPAS | Photographic Arts Society")
		layout = calculate_window_geometry(root.winfo_screenwidth(), root.winfo_screenheight())
		root.geometry(
			f"{layout['width']}x{layout['height']}+{layout['x']}+{layout['y']}"
		)
		root.minsize(layout["min_width"], layout["min_height"])
		self.style = ttk.Style(root)
		self.style.theme_use('clam')
		self.apply_theme()

		# Official society branding is bundled for offline use in the desktop build.
		assets_dir = os.path.join(APP_RESOURCE_DIR, "assets")
		try:
			self.brand_logo = tk.PhotoImage(file=os.path.join(assets_dir, "society-logo.gif"))
			self.root.iconphoto(True, self.brand_logo)
			self.brand_banner = tk.PhotoImage(file=os.path.join(assets_dir, "society-header.gif"))
			brand_bar = tk.Frame(root, bg="#000000", height=106)
			brand_bar.pack(side=tk.TOP, fill=tk.X)
			brand_bar.pack_propagate(False)
			ttk.Label(brand_bar, image=self.brand_banner, background="#000000").pack(expand=True)
		except (tk.TclError, OSError) as exc:
			print(f"VBCCPAS branding assets could not be loaded: {exc}")

		self.container = ttk.Frame(root, style='Main.TFrame')
		self.container.pack(fill=tk.BOTH, expand=True)

		self.sidebar = tk.Frame(self.container, bg=self.colors['sidebar'], width=252)
		self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
		self.sidebar.pack_propagate(False)

		self.content = tk.Frame(self.container, bg=self.colors['bg'])
		self.content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

		# Full-height split login workspace with the society brand and a focused sign-in panel.
		self.login_frame = tk.Frame(self.content, bg=self.colors['bg'])
		self.login_frame.pack(fill=tk.BOTH, expand=True)
		login_left = tk.Frame(self.login_frame, bg='#171923', width=420)
		login_left.pack(side=tk.LEFT, fill=tk.Y)
		login_left.pack_propagate(False)
		tk.Label(login_left, text='VBCCPAS', background='#171923',
		         foreground='#f7f6fb', font=('Segoe UI', 25, 'bold')).pack(anchor='w', padx=42, pady=(82, 8))
		tk.Label(login_left, text='PHOTOGRAPHIC ARTS SOCIETY',
		         background='#171923', foreground='#a99cff',
		         font=('Segoe UI', 9, 'bold')).pack(anchor='w', padx=44)
		tk.Frame(login_left, bg='#8b7cf6', height=3, width=58).pack(anchor='w', padx=44, pady=26)
		tk.Label(login_left, text='One workspace for\nyour society.',
		         background='#171923', foreground='#f4f2f8',
		         font=('Segoe UI', 23, 'bold'), justify='left').pack(anchor='w', padx=42, pady=(0, 12))
		tk.Label(login_left, text='Manage members, events, equipment and\nclub activity from one place.',
		         background='#171923', foreground='#a5a8b7',
		         font=('Segoe UI', 10), justify='left').pack(anchor='w', padx=44)
		tk.Label(login_left, text='BANDARANAYAKE CENTRAL COLLEGE  •  VEYANGODA',
		         background='#171923', foreground='#777f91',
		         font=('Segoe UI', 7, 'bold')).pack(side=tk.BOTTOM, anchor='w', padx=42, pady=30)
		login_stage = tk.Frame(self.login_frame, bg=self.colors['bg'])
		login_stage.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
		login_card = tk.Frame(login_stage, bg=self.colors['card'], bd=0,
		                      highlightthickness=1, highlightbackground='#34394a')
		login_card.place(relx=0.5, rely=0.5, anchor=tk.CENTER, width=480, height=470)
		tk.Label(login_card, text='WELCOME BACK', background=self.colors['card'],
		         foreground=self.colors['accent2'], font=('Segoe UI', 9, 'bold')).pack(anchor='w', padx=38, pady=(42, 8))
		tk.Label(login_card, text='Sign in to VBCCPAS', background=self.colors['card'],
		         foreground=self.colors['fg'], font=('Segoe UI', 23, 'bold')).pack(anchor='w', padx=38)
		tk.Label(login_card, text='Use your executive administrator account to continue.',
		         background=self.colors['card'], foreground='#a5a8b7',
		         font=('Segoe UI', 10)).pack(anchor='w', padx=38, pady=(8, 30))
		form = tk.Frame(login_card, bg=self.colors['card'])
		form.pack(fill=tk.X, padx=38)
		tk.Label(form, text='USERNAME', background=self.colors['card'],
		         foreground='#c8cbd7', font=('Segoe UI', 8, 'bold')).pack(anchor='w', pady=(0, 7))
		self.username_var = tk.StringVar()
		username_entry = ttk.Entry(form, textvariable=self.username_var, font=('Segoe UI', 11))
		username_entry.pack(fill=tk.X, ipady=7, pady=(0, 20))
		tk.Label(form, text='PASSWORD', background=self.colors['card'],
		         foreground='#c8cbd7', font=('Segoe UI', 8, 'bold')).pack(anchor='w', pady=(0, 7))
		self.password_var = tk.StringVar()
		password_entry = ttk.Entry(form, textvariable=self.password_var, show='•', font=('Segoe UI', 11))
		password_entry.pack(fill=tk.X, ipady=7)
		password_entry.bind('<Return>', lambda _event: self.try_login())
		btn_frame = tk.Frame(login_card, bg=self.colors['card'])
		btn_frame.pack(fill=tk.X, padx=38, pady=(28, 0))
		ttk.Button(btn_frame, text='Sign in  →', command=self.try_login,
		           style='Rounded.TButton').pack(fill=tk.X, ipady=5)
		tk.Label(login_card, text='Your records stay on this computer unless you configure sync.',
		         background=self.colors['card'], foreground='#777f91',
		         font=('Segoe UI', 8)).pack(anchor='w', padx=38, pady=(22, 0))
		username_entry.focus_set()

		# sidebar buttons (hidden until login)
		self.side_buttons = {}
		self.sidebar_brand = tk.Frame(self.sidebar, bg=self.colors['sidebar'])
		self.sidebar_brand.pack(fill=tk.X, padx=18, pady=(24, 24))
		tk.Label(self.sidebar_brand, text='VBCCPAS', background=self.colors['sidebar'],
		         foreground='#f4f2f8', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
		tk.Label(self.sidebar_brand, text='SOCIETY WORKSPACE',
		         background=self.colors['sidebar'], foreground='#a99cff',
		         font=('Segoe UI', 8, 'bold')).pack(anchor='w', pady=(5, 0))
		tk.Frame(self.sidebar, bg='#343846', height=1).pack(fill=tk.X, padx=18, pady=(0, 18))
		self.sidebar_title = tk.Label(self.sidebar, text="WORKSPACE MODULES", bg=self.colors['sidebar'], fg='#7f8799', font=('Segoe UI', 8, 'bold'))
		self.sidebar_title.pack(anchor='w', padx=18, pady=(6, 10))
		for key, label in [
			('dashboard', '⌂   Overview'), ('members', '♙   Members'),
			('events', '▦   Events & activities'), ('inventory', '▣   Equipment'),
			('borrow', '↔   Borrowing'), ('attendance', '✓   Attendance'),
			('reports', '▤   Reports & exports'), ('security', '⚙   Security & access')
		]:
			b = ttk.Button(self.sidebar, text=label, command=lambda k=key: self.show_view(k), style='Sidebar.TButton')
			b.pack(fill=tk.X, padx=12, pady=4, ipady=2)
			self.side_buttons[key] = b
		tk.Frame(self.sidebar, bg='#343846', height=1).pack(fill=tk.X, padx=18, pady=(22, 14))
		self.sidebar_account = tk.Frame(self.sidebar, bg=self.colors['sidebar'])
		self.sidebar_account.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=20)
		tk.Label(self.sidebar_account, text='SIGNED IN AS', background=self.colors['sidebar'],
		         foreground='#777f91', font=('Segoe UI', 7, 'bold')).pack(anchor='w')
		self.sidebar_user_label = ttk.Label(self.sidebar_account, text='Administrator',
		         background=self.colors['sidebar'], foreground='#f4f2f8',
		         font=('Segoe UI', 10, 'bold'))
		self.sidebar_user_label.pack(anchor='w', pady=(4, 0))
		self.sidebar.pack_forget()
		self.current_view = None

	def try_login(self):
		u = self.username_var.get().strip()
		p = self.password_var.get()
		if not u or not p:
			messagebox.showwarning("Missing", "Enter username and password")
			return
		conn = sqlite3.connect(DB_PATH)
		cur = conn.cursor()
		cur.execute("SELECT Password, Role FROM Users WHERE Username = ?", (u,))
		row = cur.fetchone()
		conn.close()
		if not row:
			messagebox.showerror("Login failed", "Unknown username")
			return
		if row[1] != 'admin':
			messagebox.showerror("Access denied", "Only the Executive Administrator can sign in to this desktop application.")
			return
		if not verify_password(p, row[0]):
			messagebox.showerror("Login failed", "Incorrect password")
			return
		# Upgrade legacy SHA-256 hashes after a successful login.
		if not row[0].startswith('pbkdf2_sha256$'):
			conn = sqlite3.connect(DB_PATH)
			conn.execute("UPDATE Users SET Password=? WHERE Username=?", (hash_password(p), u))
			conn.commit()
			conn.close()
		self.user = {'username': u, 'role': row[1]}
		self.sidebar_user_label.configure(text=u)
		self.login_frame.destroy()
		self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
		self.show_view('dashboard')

	def open_signup(self):
		d = tk.Toplevel(self.root)
		d.title('Sign up')
		frm = ttk.Frame(d, padding=16)
		frm.pack(fill=tk.BOTH, expand=True)
		tk.Label(frm, text='Register Member Account', font=('Segoe UI', 14)).grid(row=0, column=0, columnspan=2, pady=(0,12))
		member_var = tk.StringVar()
		username_var = tk.StringVar()
		password_var = tk.StringVar()
		tk.Label(frm, text='Existing Member ID:').grid(row=1, column=0, sticky=tk.E, padx=4, pady=4)
		ttk.Entry(frm, textvariable=member_var).grid(row=1, column=1, padx=4, pady=4)
		tk.Label(frm, text='Username:').grid(row=2, column=0, sticky=tk.E, padx=4, pady=4)
		tk.Entry(frm, textvariable=username_var).grid(row=2, column=1, padx=4, pady=4)
		tk.Label(frm, text='Password:').grid(row=3, column=0, sticky=tk.E, padx=4, pady=4)
		tk.Entry(frm, textvariable=password_var, show='*').grid(row=3, column=1, padx=4, pady=4)

		def save_signup():
			mid = member_var.get().strip()
			uname = username_var.get().strip()
			pw = password_var.get()
			if not (mid and uname and pw):
				messagebox.showwarning('Missing', 'Enter member id, username and password')
				return
			if len(pw) < 6:
				messagebox.showwarning('Weak password', 'Use at least 6 characters')
				return
			if not mid.isdigit():
				messagebox.showwarning('Invalid', 'Member ID must be numeric')
				return
			mid = int(mid)
			conn = sqlite3.connect(DB_PATH)
			cur = conn.cursor()
			cur.execute('SELECT MemberID FROM Members WHERE MemberID=?', (mid,))
			if not cur.fetchone():
				conn.close()
				messagebox.showerror('Not registered', 'Member ID not found in database')
				return
			cur.execute('SELECT 1 FROM Users WHERE Username=?', (uname,))
			if cur.fetchone():
				conn.close()
				messagebox.showerror('Taken', 'Username already exists')
				return
			cur.execute('INSERT INTO Users (MemberID, Username, Password, Role) VALUES (?,?,?,?)', (mid, uname, hash_password(pw), 'member'))
			conn.commit()
			conn.close()
			messagebox.showinfo('Created', 'Account created successfully. Please sign in.')
			d.destroy()

		btn = ttk.Button(frm, text='Register', command=save_signup, style='Rounded.TButton')
		btn.grid(row=4, column=0, columnspan=2, pady=12)

	def clear_content(self):
		if self.current_view:
			self.current_view.destroy()
			self.current_view = None

	def apply_theme(self):
		# Unified dark workspace palette shared with the browser dashboard.
		self.colors = {
			'bg': '#0f1117',
			'card': '#191d27',
			'fg': '#f4f2f8',
			'accent1': '#7967e8',
			'accent2': '#a99cff',
			'accent3': '#c87548',
			'sidebar': '#151821',
			'panel': '#202532'
		}
		bg = self.colors['bg']
		fg = self.colors['fg']
		accent = self.colors['accent1']
		accent2 = self.colors['accent2']
		accent3 = self.colors['accent3']
		self.style.configure('TFrame', background=bg)
		self.style.configure('Main.TFrame', background=bg)
		self.style.configure('TLabel', background=bg, foreground=fg,
			font=('Segoe UI', 10))
		self.style.configure('TNotebook', background=bg, borderwidth=0)
		self.style.configure('TNotebook.Tab', background=self.colors['sidebar'],
			foreground='#b9bdca', padding=(15, 9), borderwidth=0)
		self.style.map('TNotebook.Tab',
			background=[('selected', self.colors['card']), ('active', self.colors['panel'])],
			foreground=[('selected', '#ffffff'), ('active', fg)])
		self.style.configure('TButton', foreground='#ffffff', background=accent,
			padding=(13, 9), relief='flat', borderwidth=0,
			font=('Segoe UI', 10, 'bold'))
		self.style.map('TButton',
			background=[('disabled', '#454653'), ('pressed', accent2), ('active', accent3)],
			foreground=[('disabled', '#a1a1aa')])
		self.style.configure('Rounded.TButton', foreground='#ffffff',
			background=accent, padding=(14, 10), relief='flat', borderwidth=0,
			font=('Segoe UI', 10, 'bold'))
		self.style.map('Rounded.TButton',
			background=[('disabled', '#454653'), ('pressed', accent2), ('active', accent3)],
			foreground=[('disabled', '#a1a1aa')])
		self.style.configure('TCheckbutton', background=bg, foreground=fg,
			font=('Segoe UI', 10), padding=5)
		self.style.map('TCheckbutton', background=[('active', bg)],
			foreground=[('disabled', '#8b8d99')])
		self.style.configure('Warning.TLabel', background=bg,
			foreground='#e6b27e', font=('Segoe UI', 10, 'bold'))
		self.style.configure('Sidebar.TButton', foreground='#c8cbd7',
			background=self.colors['sidebar'], padding=(14, 12), anchor='w',
			relief='flat', borderwidth=0, font=('Segoe UI', 10, 'bold'))
		self.style.map('Sidebar.TButton',
			background=[('pressed', '#343044'), ('active', '#292b39')],
			foreground=[('active', '#ffffff')])
		self.style.configure('SidebarActive.TButton', foreground='#ffffff',
			background='#302c49', padding=(14, 12), anchor='w',
			relief='flat', borderwidth=0, font=('Segoe UI', 10, 'bold'))
		self.style.map('SidebarActive.TButton',
			background=[('active', '#393451'), ('pressed', '#423b62')],
			foreground=[('active', '#ffffff')])
		self.style.configure('Treeview', background=self.colors['card'],
			fieldbackground=self.colors['card'], foreground=fg,
			rowheight=32, borderwidth=0, font=('Segoe UI', 10))
		self.style.map('Treeview',
			background=[('selected', accent)],
			foreground=[('selected', '#ffffff')])
		self.style.configure('Treeview.Heading',
			background=self.colors['panel'], foreground='#d9dbe5',
			padding=(10, 9), relief='flat', font=('Segoe UI', 9, 'bold'))
		self.style.map('Treeview.Heading',
			background=[('active', '#303546')], foreground=[('active', '#ffffff')])
		self.style.configure('TEntry', fieldbackground='#171a22',
			foreground=fg, insertcolor=fg, padding=8, bordercolor='#373b4a')
		self.style.map('TEntry', fieldbackground=[('readonly', '#20232d')])
		self.style.configure('TCombobox', fieldbackground='#171a22',
			foreground=fg, padding=7, arrowcolor='#c8cbd7')
		self.style.map('TCombobox',
			fieldbackground=[('readonly', '#171a22')],
			foreground=[('readonly', fg), ('disabled', '#8b8d99')])
		self.style.configure('TSpinbox', fieldbackground='#171a22',
			foreground=fg, padding=7, arrowcolor='#c8cbd7')
		self.style.configure('TLabelframe', background=bg,
			bordercolor='#343846', relief='solid')
		self.style.configure('TLabelframe.Label', background=bg,
			foreground='#c8cbd7', font=('Segoe UI', 10, 'bold'))
		self.style.configure('TSeparator', background='#343846')
		try:
			self.root.configure(bg=bg)
		except Exception:
			pass

	def draw_gradient(self, canvas, color1, color2):
		# Simple vertical gradient
		w = canvas.winfo_reqwidth() or canvas.winfo_width() or 800
		h = int(canvas['height']) if canvas['height'] else 120
		# convert hex to rgb
		def hex_to_rgb(h):
			h = h.lstrip('#')
			return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
		r1, g1, b1 = hex_to_rgb(color1)
		r2, g2, b2 = hex_to_rgb(color2)
		canvas.delete('grad')
		canvas.update_idletasks()
		w = canvas.winfo_width() or w
		for i in range(h):
			r = int(r1 + (r2 - r1) * (i / h))
			g = int(g1 + (g2 - g1) * (i / h))
			b = int(b1 + (b2 - b1) * (i / h))
			color = f'#{r:02x}{g:02x}{b:02x}'
			canvas.create_line(0, i, w, i, fill=color, tags='grad')

	def count_members(self):
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT COUNT(*) FROM Members');n=cur.fetchone()[0];conn.close();return n

	def count_borrowed(self):
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT COUNT(*) FROM BorrowedItems WHERE Returned=0');n=cur.fetchone()[0];conn.close();return n

	def get_next_event(self):
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT Title, Date FROM Events WHERE Date IS NOT NULL');rows=cur.fetchall();conn.close()
		now = datetime.now()
		candidates = []
		for title, datestr in rows:
			if not datestr: continue
			# try ISO first, then common formats
			_dt = None
			try:
				_dt = datetime.fromisoformat(datestr)
			except Exception:
				try:
					_dt = datetime.strptime(datestr, '%Y-%m-%d')
				except Exception:
					try:
						_dt = datetime.strptime(datestr, '%d/%m/%Y')
					except Exception:
						_dt = None
			if _dt and _dt >= now:
				candidates.append((_dt, title, datestr))
		if not candidates:
			return None
		candidates.sort()
		_dt, title, datestr = candidates[0]
		return title, _dt

	def show_view(self, key):
		self.clear_content()
		self.active_view = key
		for nav_key, button in self.side_buttons.items():
			button.configure(style='SidebarActive.TButton' if nav_key == key else 'Sidebar.TButton')
		frame = tk.Frame(self.content, bg=self.colors['bg'])
		frame.pack(fill=tk.BOTH, expand=True)
		self.current_view = frame
		page_titles = {
			'dashboard': ('Your workspace', 'A clear overview of society activity and upcoming work.'),
			'members': ('Members', 'Manage the society member register and contact details.'),
			'events': ('Events & activities', 'Plan photo walks, workshops, exhibitions and meetings.'),
			'inventory': ('Equipment', 'Keep a complete record of cameras, mixers and studio equipment.'),
			'borrow': ('Borrowing', 'Track equipment loans, due dates and returns.'),
			'attendance': ('Attendance', 'Record participation in society events and activities.'),
			'reports': ('Reports & exports', 'Export clean records for society administration.'),
			'security': ('Security & access', 'Manage administrator access and user accounts.'),
			'web_dashboard': ('Web dashboard', 'Open the local browser workspace.'),
			'maintenance': ('Maintenance', 'Backups, diagnostics and software updates.'),
		}
		title, subtitle = page_titles.get(key, ('VBCCPAS workspace', 'Society management'))
		page_head = tk.Frame(frame, bg=self.colors['bg'])
		page_head.pack(fill=tk.X, padx=24, pady=(20, 12))
		heading_row = tk.Frame(page_head, bg=self.colors['bg'])
		heading_row.pack(fill=tk.X)
		tk.Label(heading_row, text='VBCCPAS  /  SOCIETY WORKSPACE',
		         foreground=self.colors['accent2'], font=('Segoe UI', 8, 'bold')).pack(side=tk.LEFT, anchor='w')
		tk.Label(heading_row, text=datetime.now().strftime('%A, %d %B %Y'),
		         foreground='#777f91', font=('Segoe UI', 8)).pack(side=tk.RIGHT, anchor='e')
		tk.Label(page_head, text=title, font=('Segoe UI', 25, 'bold'),
		         foreground=self.colors['fg']).pack(anchor='w', pady=(7, 2))
		tk.Label(page_head, text=subtitle, foreground='#a5a8b7',
		         font=('Segoe UI', 10)).pack(anchor='w')
		body = tk.Frame(frame, bg=self.colors['bg'])
		body.pack(fill=tk.BOTH, expand=True)
		if key == 'dashboard':
			stats = tk.Frame(body, bg=self.colors['bg'])
			stats.pack(fill=tk.X, padx=16, pady=6)
			mcount = self.count_members()
			bcount = self.count_borrowed()
			next_event = self.get_next_event()
			for card_title, value, color, subtitle in [
				('Society members', str(mcount), self.colors['accent1'], 'Registered members'),
				('Equipment on loan', str(bcount), self.colors['accent2'], 'Outstanding items'),
				('Next event', f"{next_event[0]} ({next_event[1].strftime('%Y-%m-%d')})" if next_event else 'No upcoming events', self.colors['accent3'], 'Upcoming schedule')
			]:
				card = tk.Frame(stats, bg=self.colors['card'], bd=0,
				                highlightthickness=1, highlightbackground='#34394a',
				                width=280, height=126)
				card.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
				card.pack_propagate(False)
				tk.Label(card, text=card_title.upper(), bg=self.colors['card'],
				         fg='#a5a8b7', font=('Segoe UI', 8, 'bold')).place(x=18, y=16)
				tk.Label(card, text=value, bg=self.colors['card'], fg=color,
				         font=('Segoe UI', 19, 'bold'), wraplength=245).place(x=18, y=42)
				tk.Label(card, text=subtitle, bg=self.colors['card'], fg='#777f91',
				         font=('Segoe UI', 9)).place(x=18, y=96)
			panel = tk.Frame(body, bg=self.colors['panel'], highlightthickness=1,
			                 highlightbackground='#34394a')
			panel.pack(fill=tk.BOTH, expand=True, padx=24, pady=(12, 18))
			tk.Label(panel, text='Quick actions', bg=self.colors['panel'],
			         fg=self.colors['fg'], font=('Segoe UI', 14, 'bold')).pack(
			             anchor='w', padx=20, pady=(18, 4))
			tk.Label(panel, text='Jump straight to the tasks you use most.',
			         bg=self.colors['panel'], fg='#a5a8b7').pack(anchor='w', padx=20, pady=(0, 14))
			buttons = tk.Frame(panel, bg=self.colors['panel'])
			buttons.pack(anchor='w', padx=16, pady=(0, 18))
			for label, destination in [('Manage members','members'),('Plan an event','events'),
			                           ('Equipment register','inventory'),('Record attendance','attendance')]:
				ttk.Button(buttons, text=label,
				           command=lambda selected=destination: self.show_view(selected),
				           style='Rounded.TButton').pack(side=tk.LEFT, padx=5)
		elif key == 'members':
			self.view_members(body)
		elif key == 'events':
			self.view_events(body)
		elif key == 'inventory':
			self.view_inventory(body)
		elif key == 'borrow':
			self.view_borrowing(body)
		elif key == 'attendance':
			self.view_attendance(body)
		elif key == 'reports':
			self.view_reports(body)
		elif key == 'security':
			self.view_security(body)
	
	# Members
	def view_members(self, parent):
		top = parent
		tb = ttk.Frame(top)
		tb.pack(fill=tk.X, pady=(0,6))
		left = ttk.Frame(tb)
		left.pack(side=tk.LEFT)
		right = ttk.Frame(tb)
		right.pack(side=tk.RIGHT)
		ttk.Button(left, text='Add Member', command=self.add_member_dialog, style='Rounded.TButton').pack(side=tk.LEFT)
		ttk.Button(left, text='Export CSV', command=lambda: self.export_table('Members', ['MemberID','Name','Class','Roll','Email'])).pack(side=tk.LEFT, padx=6)
		cols = ('MemberID','Name','Class','Roll','Email')
		tree = ttk.Treeview(top, columns=cols, show='headings')
		for c in cols:
			tree.heading(c, text=c)
			tree.column(c, width=140)
		tree.pack(fill=tk.BOTH, expand=True)
		self.populate_members(tree)
		def on_delete():
			sel = tree.selection()
			if not sel: return
			mid = tree.item(sel[0])['values'][0]
			if messagebox.askyesno('Delete','Remove member?'):
				conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('DELETE FROM Members WHERE MemberID=?',(mid,));conn.commit();conn.close();self.populate_members(tree)
		ttk.Button(right, text='Delete Selected', command=on_delete).pack()
	def add_member_dialog(self):
		d=tk.Toplevel(self.root);d.title('Add Member')
		f=ttk.Frame(d, padding=12);f.pack()
		name=tk.StringVar();cls=tk.StringVar();roll=tk.StringVar();email=tk.StringVar()
		ttk.Label(f,text='Name').grid(row=0,column=0);ttk.Entry(f,textvariable=name).grid(row=0,column=1)
		ttk.Label(f,text='Class').grid(row=1,column=0);ttk.Entry(f,textvariable=cls).grid(row=1,column=1)
		ttk.Label(f,text='Roll').grid(row=2,column=0);ttk.Entry(f,textvariable=roll).grid(row=2,column=1)
		ttk.Label(f,text='Email').grid(row=3,column=0);ttk.Entry(f,textvariable=email).grid(row=3,column=1)
		def save():
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO Members (Name,Class,Roll,Email) VALUES (?,?,?,?)', (name.get(),cls.get(),roll.get(),email.get()));conn.commit();conn.close();d.destroy(); self.show_view('members')
		ttk.Button(f,text='Save',command=save).grid(row=4,column=0,columnspan=2,pady=8)

	# Events
	def view_events(self, parent):
		top=parent
		tb=ttk.Frame(top);tb.pack(fill=tk.X)
		left = ttk.Frame(tb); left.pack(side=tk.LEFT)
		right = ttk.Frame(tb); right.pack(side=tk.RIGHT)
		ttk.Button(left,text='Add Event',command=self.add_event_dialog, style='Rounded.TButton').pack(side=tk.LEFT)
		cols=('EventID','Title','Date','Location')
		tree=ttk.Treeview(top,columns=cols,show='headings')
		for c in cols: tree.heading(c,text=c); tree.column(c,width=160)
		tree.pack(fill=tk.BOTH,expand=True)
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT EventID,Title,Date,Location FROM Events');rows=cur.fetchall();conn.close()
		for r in rows: tree.insert('',tk.END,values=r)
	def add_event_dialog(self):
		d=tk.Toplevel(self.root);d.title('Add Event')
		f=ttk.Frame(d, padding=12);f.pack()
		title=tk.StringVar(); date=tk.StringVar(); loc=tk.StringVar(); notes=tk.StringVar()
		ttk.Label(f,text='Title').grid(row=0,column=0);ttk.Entry(f,textvariable=title).grid(row=0,column=1)
		ttk.Label(f,text='Date').grid(row=1,column=0);ttk.Entry(f,textvariable=date).grid(row=1,column=1)
		ttk.Label(f,text='Location').grid(row=2,column=0);ttk.Entry(f,textvariable=loc).grid(row=2,column=1)
		ttk.Label(f,text='Notes').grid(row=3,column=0);ttk.Entry(f,textvariable=notes).grid(row=3,column=1)
		def save():
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO Events (Title,Date,Location,Notes) VALUES (?,?,?,?)', (title.get(),date.get(),loc.get(),notes.get()));conn.commit();conn.close();d.destroy();self.show_view('events')
		ttk.Button(f,text='Save',command=save).grid(row=4,column=0,columnspan=2,pady=8)

	# Equipment inventory
	def view_inventory(self, parent):
		panel = tk.Frame(parent, bg=self.colors['panel'], highlightthickness=1,
		                 highlightbackground='#303747')
		panel.pack(fill=tk.BOTH, expand=True, padx=22, pady=(4, 20))
		header = tk.Frame(panel, bg=self.colors['panel'])
		header.pack(fill=tk.X, padx=20, pady=(18, 10))
		tk.Label(header, text='Equipment register', background=self.colors['panel'],
		         foreground=self.colors['fg'], font=('Segoe UI', 16, 'bold')).pack(side=tk.LEFT)
		tk.Label(header, text='Track cameras, lenses, mixers and studio equipment',
		         background=self.colors['panel'], foreground='#a5a8b7').pack(side=tk.LEFT, padx=16, pady=(4, 0))
		toolbar = tk.Frame(panel, bg=self.colors['panel'])
		toolbar.pack(fill=tk.X, padx=20, pady=(4, 12))
		search_var = tk.StringVar()
		tk.Entry(toolbar, textvariable=search_var, width=34).pack(side=tk.LEFT, padx=(0, 10))
		ttk.Button(toolbar, text='Add equipment', command=self.add_item_dialog,
		           style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		ttk.Button(toolbar, text='Edit selected', command=lambda: self.edit_item_dialog(tree),
		           style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		ttk.Button(toolbar, text='Delete selected', command=lambda: self.delete_inventory_item(tree),
		           style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		ttk.Button(toolbar, text='Export CSV',
		           command=lambda: self.export_table('Inventory',
		               ['ItemID','Name','Category','Brand','Model','SerialNumber','Quantity',
		                'Condition','Location','Custodian','PurchaseDate','Status','Notes'])
		           ).pack(side=tk.RIGHT, padx=4)
		search_var.set('')
		cols = ('ItemID','Name','Category','Brand','Model','Serial','Qty','Condition','Location','Custodian','Status')
		wrap = tk.Frame(panel, bg=self.colors['panel'])
		wrap.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 18))
		tree = ttk.Treeview(wrap, columns=cols, show='headings')
		widths = {'ItemID':58,'Name':170,'Category':105,'Brand':100,'Model':105,
		          'Serial':125,'Qty':55,'Condition':100,'Location':110,'Custodian':120,'Status':100}
		for col in cols:
			tree.heading(col, text=col)
			tree.column(col, width=widths.get(col, 110), minwidth=55, stretch=True)
		ybar = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=tree.yview)
		xbar = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=tree.xview)
		tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
		tree.grid(row=0, column=0, sticky='nsew')
		ybar.grid(row=0, column=1, sticky='ns')
		xbar.grid(row=1, column=0, sticky='ew')
		wrap.grid_rowconfigure(0, weight=1)
		wrap.grid_columnconfigure(0, weight=1)
		def load_rows(*_):
			for row in tree.get_children():
				tree.delete(row)
			term = search_var.get().strip().lower()
			conn = sqlite3.connect(DB_PATH)
			cur = conn.cursor()
			cur.execute('''SELECT ItemID, Name, Category, Brand, Model, SerialNumber,
				Quantity, Condition, Location, Custodian, Status FROM Inventory
				ORDER BY Category, Name''')
			rows = cur.fetchall()
			conn.close()
			for row in rows:
				if not term or term in ' '.join(str(value or '') for value in row).lower():
					tree.insert('', tk.END, values=row)
		search_var.trace_add('write', load_rows)
		tree.bind('<Double-1>', lambda _event: self.edit_item_dialog(tree))
		load_rows()
		self.inventory_tree = tree

	def add_item_dialog(self):
		self.inventory_form_dialog()

	def edit_item_dialog(self, tree):
		selection = tree.selection()
		if not selection:
			messagebox.showinfo('Select equipment', 'Choose an equipment record to edit.')
			return
		item_id = tree.item(selection[0])['values'][0]
		conn = sqlite3.connect(DB_PATH)
		conn.row_factory = sqlite3.Row
		row = conn.execute('SELECT * FROM Inventory WHERE ItemID=?', (item_id,)).fetchone()
		conn.close()
		if row:
			self.inventory_form_dialog(dict(row))

	def inventory_form_dialog(self, record=None):
		record = record or {}
		dialog = tk.Toplevel(self.root)
		dialog.title('Edit equipment' if record else 'Register equipment')
		dialog.configure(bg=self.colors['bg'])
		dialog.transient(self.root)
		dialog.grab_set()
		dialog.geometry('700x650')
		dialog.minsize(620, 560)
		outer = tk.Frame(dialog, bg=self.colors['bg'])
		outer.pack(fill=tk.BOTH, expand=True, padx=22, pady=20)
		tk.Label(outer, text='Equipment details', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
		tk.Label(outer, text='Record identifying details and where the item is kept.',
		         foreground='#a5a8b7').pack(anchor='w', pady=(3, 16))
		form = tk.Frame(outer, bg=self.colors['panel'], padx=18, pady=18)
		form.pack(fill=tk.BOTH, expand=True)
		fields = [
			('Equipment name', 'Name', 'entry'),
			('Category', 'Category', 'category'),
			('Brand', 'Brand', 'entry'),
			('Model', 'Model', 'entry'),
			('Serial number', 'SerialNumber', 'entry'),
			('Quantity', 'Quantity', 'quantity'),
			('Condition', 'Condition', 'condition'),
			('Location / storage', 'Location', 'entry'),
			('Assigned custodian', 'Custodian', 'entry'),
			('Purchase / acquired date', 'PurchaseDate', 'entry'),
			('Availability status', 'Status', 'status'),
			('Notes', 'Notes', 'entry'),
		]
		variables = {}
		for index, (label, key, kind) in enumerate(fields):
			row, col = divmod(index, 2)
			cell = tk.Frame(form, bg=self.colors['panel'])
			cell.grid(row=row, column=col, sticky='ew', padx=(0 if col == 0 else 12, 0),
			          pady=7)
			tk.Label(cell, text=label, background=self.colors['panel'],
			         foreground='#d9dbe5', font=('Segoe UI', 9, 'bold')).pack(anchor='w', pady=(0, 5))
			default = record.get(key, '')
			if key == 'Category' and not default: default = 'Other'
			if key == 'Quantity' and default == '': default = 1
			if key == 'Condition' and not default: default = 'Good'
			if key == 'Status' and not default: default = 'Available'
			var = tk.StringVar(value=str(default))
			variables[key] = var
			if kind == 'category':
				widget = ttk.Combobox(cell, textvariable=var, state='readonly',
				                      values=['Camera','Lens','Mixer','Microphone','Lighting',
				                              'Tripod','Computer','Storage','Accessory','Other'])
			elif kind == 'condition':
				widget = ttk.Combobox(cell, textvariable=var, state='readonly',
				                      values=['New','Good','Fair','Needs service','Damaged'])
			elif kind == 'status':
				widget = ttk.Combobox(cell, textvariable=var, state='readonly',
				                      values=['Available','In use','Under maintenance','Retired','Missing'])
			else:
				widget = ttk.Entry(cell, textvariable=var)
			widget.pack(fill=tk.X)
			if kind == 'quantity':
				widget.configure(width=12)
			form.grid_columnconfigure(0, weight=1)
			form.grid_columnconfigure(1, weight=1)
		buttons = tk.Frame(outer, bg=self.colors['bg'])
		buttons.pack(fill=tk.X, pady=(14, 0))
		def save():
			name = variables['Name'].get().strip()
			try:
				quantity = int(variables['Quantity'].get())
			except ValueError:
				messagebox.showerror('Invalid quantity', 'Quantity must be a whole number.', parent=dialog)
				return
			if not name or quantity < 0:
				messagebox.showerror('Check details', 'Enter an equipment name and a quantity of zero or more.', parent=dialog)
				return
			values = [name, variables['Category'].get(), variables['Brand'].get().strip(),
			          variables['Model'].get().strip(), variables['SerialNumber'].get().strip(),
			          quantity, variables['Condition'].get(), variables['Location'].get().strip(),
			          variables['Custodian'].get().strip(), variables['PurchaseDate'].get().strip(),
			          variables['Status'].get(), variables['Notes'].get().strip()]
			conn = sqlite3.connect(DB_PATH)
			if record:
				conn.execute('''UPDATE Inventory SET Name=?, Category=?, Brand=?, Model=?,
					SerialNumber=?, Quantity=?, Condition=?, Location=?, Custodian=?,
					PurchaseDate=?, Status=?, Notes=? WHERE ItemID=?''', values + [record['ItemID']])
			else:
				conn.execute('''INSERT INTO Inventory
					(Name, Category, Brand, Model, SerialNumber, Quantity, Condition,
					 Location, Custodian, PurchaseDate, Status, Notes)
					VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', values)
			conn.commit()
			conn.close()
			dialog.destroy()
			self.show_view('inventory')
		ttk.Button(buttons, text='Save equipment', command=save,
		           style='Rounded.TButton').pack(side=tk.RIGHT)
		ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side=tk.RIGHT, padx=8)

	def delete_inventory_item(self, tree):
		selection = tree.selection()
		if not selection:
			messagebox.showinfo('Select equipment', 'Choose an equipment record to delete.')
			return
		item_id = tree.item(selection[0])['values'][0]
		if not messagebox.askyesno('Delete equipment',
				'Permanently remove this equipment record? Existing borrowing history will remain.'):
			return
		conn = sqlite3.connect(DB_PATH)
		conn.execute('DELETE FROM Inventory WHERE ItemID=?', (item_id,))
		conn.commit()
		conn.close()
		self.show_view('inventory')

	# Borrowing
	def view_borrowing(self, parent):
		top=parent
		tb=ttk.Frame(top);tb.pack(fill=tk.X)
		left=ttk.Frame(tb); left.pack(side=tk.LEFT)
		ttk.Button(left,text='Borrow Item',command=self.borrow_dialog, style='Rounded.TButton').pack(side=tk.LEFT)
		ttk.Button(left,text='Return Selected',command=lambda: self.return_selected_borrow(tree)).pack(side=tk.LEFT, padx=6)
		ttk.Button(left,text='Refresh',command=lambda: self.populate_borrowed(tree)).pack(side=tk.LEFT, padx=6)
		cols=('BorrowID','Item','Member','BorrowDate','ReturnDate','Returned')
		tree=ttk.Treeview(top,columns=cols,show='headings')
		for c in cols: tree.heading(c,text=c); tree.column(c,width=140)
		tree.pack(fill=tk.BOTH,expand=True)
		self.populate_borrowed(tree)

	def populate_borrowed(self, tree):
		for r in tree.get_children(): tree.delete(r)
		conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
		cur.execute('''
			SELECT b.BorrowID,
			       i.Name,
			       m.Name,
			       b.BorrowDate,
			       b.ReturnDate,
			       CASE WHEN b.Returned = 1 THEN 'Yes' ELSE 'No' END
			FROM BorrowedItems b
			LEFT JOIN Inventory i ON b.ItemID = i.ItemID
			LEFT JOIN Members m ON b.MemberID = m.MemberID
			ORDER BY b.BorrowID DESC
		''')
		rows=cur.fetchall(); conn.close()
		for r in rows: tree.insert('',tk.END,values=r)

	def return_selected_borrow(self, tree):
		sel = tree.selection()
		if not sel:
			messagebox.showwarning('Select','Select a borrowed item to return')
			return
		borrow_id = tree.item(sel[0])['values'][0]
		conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
		cur.execute('SELECT ItemID, Returned FROM BorrowedItems WHERE BorrowID=?', (borrow_id,))
		row = cur.fetchone()
		if row and row[1] == 1:
			conn.close()
			messagebox.showinfo('Already returned', 'This item was already marked as returned.')
			return
		if row:
			cur.execute('UPDATE BorrowedItems SET Returned=1, ReturnDate=? WHERE BorrowID=?', (datetime.now().isoformat(), borrow_id))
			cur.execute('UPDATE Inventory SET Quantity=Quantity+1 WHERE ItemID=? AND Quantity >= 0', (row[0],))
		conn.commit(); conn.close(); self.populate_borrowed(tree)

	def borrow_dialog(self):
		d=tk.Toplevel(self.root);d.title('Borrow Item')
		f=ttk.Frame(d, padding=12);f.pack()
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT ItemID,Name FROM Inventory WHERE Quantity>0');items=cur.fetchall();cur.execute('SELECT MemberID,Name FROM Members');members=cur.fetchall();conn.close()
		item_var=tk.IntVar(); member_var=tk.IntVar(); return_on=tk.StringVar()
		ttk.Label(f,text='Item').grid(row=0,column=0);
		item_cb=ttk.Combobox(f, values=[f"{i[0]}: {i[1]}" for i in items])
		item_cb.grid(row=0,column=1)
		ttk.Label(f,text='Member').grid(row=1,column=0);
		mem_cb=ttk.Combobox(f, values=[f"{m[0]}: {m[1]}" for m in members])
		mem_cb.grid(row=1,column=1)
		ttk.Label(f,text='Return Date').grid(row=2,column=0);ttk.Entry(f,textvariable=return_on).grid(row=2,column=1)
		def save():
			try:
				iid=int(item_cb.get().split(':')[0]); mid=int(mem_cb.get().split(':')[0])
			except Exception:
				messagebox.showerror('Error','Select item and member')
				return
			rd=return_on.get() or None
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO BorrowedItems (ItemID,MemberID,BorrowDate,ReturnDate,Returned) VALUES (?,?,?,?,0)', (iid,mid,datetime.now().isoformat(), rd));cur.execute('UPDATE Inventory SET Quantity=Quantity-1 WHERE ItemID=?',(iid,));conn.commit();conn.close();d.destroy();self.show_view('borrow')
		ttk.Button(f,text='Save',command=save).grid(row=3,column=0,columnspan=2,pady=8)

	# Attendance
	def view_attendance(self, parent):
		top=parent
		tb=ttk.Frame(top);tb.pack(fill=tk.X)
		left=ttk.Frame(tb); left.pack(side=tk.LEFT)
		ttk.Button(left,text='Mark Attendance',command=self.mark_attendance_dialog).pack(side=tk.LEFT)
		ttk.Button(left,text='Refresh',command=lambda: self.populate_attendance(tree)).pack(side=tk.LEFT, padx=6)
		ttk.Button(left,text='Export Attendance CSV',command=lambda: self.export_table('Attendance', ['AttID','MemberID','EventID','Present','Timestamp'])).pack(side=tk.LEFT, padx=6)
		cols=('AttID','Member','Event','Present','Timestamp')
		tree=ttk.Treeview(top,columns=cols,show='headings')
		for c in cols: tree.heading(c,text=c); tree.column(c,width=140)
		tree.pack(fill=tk.BOTH,expand=True)
		self.populate_attendance(tree)

	def populate_attendance(self, tree):
		for r in tree.get_children(): tree.delete(r)
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute("SELECT a.AttID,m.Name,e.Title,a.Present,a.Timestamp FROM Attendance a LEFT JOIN Members m ON a.MemberID=m.MemberID LEFT JOIN Events e ON a.EventID=e.EventID");rows=cur.fetchall();conn.close()
		for r in rows: tree.insert('',tk.END,values=r)

	def mark_attendance_dialog(self):
		d=tk.Toplevel(self.root);d.title('Mark Attendance')
		f=ttk.Frame(d, padding=12);f.pack()
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT MemberID,Name FROM Members');members=cur.fetchall();cur.execute('SELECT EventID,Title FROM Events');events=cur.fetchall();conn.close()
		mem_cb=ttk.Combobox(f, values=[f"{m[0]}: {m[1]}" for m in members])
		mem_cb.grid(row=0,column=1); ttk.Label(f,text='Member').grid(row=0,column=0)
		evt_cb=ttk.Combobox(f, values=[f"{e[0]}: {e[1]}" for e in events])
		evt_cb.grid(row=1,column=1); ttk.Label(f,text='Event').grid(row=1,column=0)
		present_var = tk.IntVar(value=1)
		ttk.Checkbutton(f, text='Present', variable=present_var).grid(row=2,column=0,columnspan=2)
		def save():
			try:
				mid=int(mem_cb.get().split(':')[0]); eid=int(evt_cb.get().split(':')[0])
			except Exception:
				messagebox.showerror('Error','Select member and event');return
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO Attendance (MemberID,EventID,Present,Timestamp) VALUES (?,?,?,?)',(mid,eid,present_var.get(),datetime.now().isoformat()));conn.commit();conn.close();d.destroy();self.show_view('attendance')
		ttk.Button(f,text='Save',command=save).grid(row=3,column=0,columnspan=2,pady=8)

	# Reports & Security
	def view_reports(self, parent):
		top=parent
		ttk.Label(top, text='Reports', font=('Segoe UI', 14, 'bold')).pack(pady=8)
		btns = ttk.Frame(top)
		btns.pack(pady=8)
		ttk.Button(btns, text='Export Members CSV', command=lambda: self.export_table('Members', ['MemberID','Name','Class','Roll','Email']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		ttk.Button(btns, text='Export Inventory CSV', command=lambda: self.export_table('Inventory', ['ItemID','Name','Category','Brand','Model','SerialNumber','Quantity','Condition','Location','Custodian','PurchaseDate','Status','Notes']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		ttk.Button(btns, text='Export Events CSV', command=lambda: self.export_table('Events', ['EventID','Title','Date','Location','Notes']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		ttk.Button(btns, text='Export Borrowing CSV', command=lambda: self.export_table('BorrowedItems', ['BorrowID','ItemID','MemberID','BorrowDate','ReturnDate','Returned']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		ttk.Button(btns, text='Export Attendance CSV', command=lambda: self.export_table('Attendance', ['AttID','MemberID','EventID','Present','Timestamp']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)

	def view_security(self, parent):
		top=parent
		ttk.Label(top, text='Security / Users', font=('Segoe UI', 14, 'bold')).pack(pady=8)
		if self.user.get('role') != 'admin':
			ttk.Label(top, text='Admin access required to manage users.', style='Warning.TLabel').pack(pady=12)
			return
		toolbar=ttk.Frame(top); toolbar.pack(fill=tk.X, pady=(0,8))
		ttk.Button(toolbar, text='Reset admin password', command=self.reset_admin_password, style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		ttk.Button(toolbar, text='Refresh', command=lambda: self.populate_users(tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		form = ttk.Frame(top, padding=10)
		form.pack(fill=tk.X, pady=(0,8))
		selected_user_id = {'value': None}
		username_var = tk.StringVar()
		password_var = tk.StringVar()
		role_var = tk.StringVar(value='member')
		member_id_var = tk.StringVar()
		ttk.Label(form, text='Username').grid(row=0, column=0, sticky=tk.W, padx=(0,8), pady=4)
		ttk.Entry(form, textvariable=username_var, width=24).grid(row=0, column=1, pady=4)
		ttk.Label(form, text='Password').grid(row=0, column=2, sticky=tk.W, padx=(12,8), pady=4)
		ttk.Entry(form, textvariable=password_var, show='*', width=24).grid(row=0, column=3, pady=4)
		ttk.Label(form, text='Role').grid(row=1, column=0, sticky=tk.W, padx=(0,8), pady=4)
		role_cb = ttk.Combobox(form, textvariable=role_var, values=['admin','member'], state='readonly', width=20)
		role_cb.grid(row=1, column=1, pady=4)
		ttk.Label(form, text='Member ID').grid(row=1, column=2, sticky=tk.W, padx=(12,8), pady=4)
		ttk.Entry(form, textvariable=member_id_var, width=18).grid(row=1, column=3, pady=4)
		btns = ttk.Frame(form)
		btns.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(8,0))
		ttk.Button(btns, text='Save / Update', command=lambda: self.save_security_user(username_var, password_var, role_var, member_id_var, selected_user_id, tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		ttk.Button(btns, text='Clear', command=lambda: self.clear_security_form(username_var, password_var, role_var, member_id_var, selected_user_id), style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		ttk.Button(btns, text='Reset selected password', command=lambda: self.reset_selected_user(tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		ttk.Button(btns, text='Delete selected', command=lambda: self.delete_selected_user(tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		cols=('UserID','Username','Role','MemberID')
		tree=ttk.Treeview(top,columns=cols,show='headings')
		for c in cols: tree.heading(c,text=c); tree.column(c,width=140)
		tree.pack(fill=tk.BOTH,expand=True, pady=8)
		tree.bind('<<TreeviewSelect>>', lambda event: self.load_security_selection(tree, username_var, role_var, member_id_var, selected_user_id))
		self.populate_users(tree)

	def populate_users(self, tree):
		for r in tree.get_children(): tree.delete(r)
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT UserID, Username, Role, MemberID FROM Users ORDER BY UserID');rows=cur.fetchall();conn.close()
		for r in rows: tree.insert('',tk.END,values=r)

	def load_security_selection(self, tree, username_var, role_var, member_id_var, selected_user_id):
		sel = tree.selection()
		if not sel:
			return
		values = tree.item(sel[0])['values']
		selected_user_id['value'] = values[0]
		username_var.set(values[1] or '')
		role_var.set(values[2] or 'member')
		member_id_var.set(values[3] or '')

	def clear_security_form(self, username_var, password_var, role_var, member_id_var, selected_user_id):
		selected_user_id['value'] = None
		username_var.set('')
		password_var.set('')
		role_var.set('member')
		member_id_var.set('')

	def save_security_user(self, username_var, password_var, role_var, member_id_var, selected_user_id, tree):
		username = username_var.get().strip()
		password = password_var.get().strip()
		role = role_var.get().strip() or 'member'
		member_id = member_id_var.get().strip()
		if not username:
			messagebox.showwarning('Missing', 'Username is required')
			return
		if member_id:
			try:
				member_id_int = int(member_id)
			except ValueError:
				messagebox.showerror('Invalid', 'Member ID must be numeric')
				return
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT 1 FROM Members WHERE MemberID=?', (member_id_int,));exists=cur.fetchone();conn.close()
			if not exists:
				messagebox.showerror('Invalid', 'Member ID does not exist')
				return
		else:
			member_id_int = None
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor()
		try:
			if selected_user_id['value']:
				if password:
					cur.execute('UPDATE Users SET Username=?, Password=?, Role=?, MemberID=? WHERE UserID=?', (username, hash_password(password), role, member_id_int, selected_user_id['value']))
				else:
					cur.execute('UPDATE Users SET Username=?, Role=?, MemberID=? WHERE UserID=?', (username, role, member_id_int, selected_user_id['value']))
			else:
				if len(password) < 6:
					conn.close(); messagebox.showwarning('Weak password', 'Use at least 6 characters'); return
				cur.execute('SELECT 1 FROM Users WHERE Username=?', (username,))
				if cur.fetchone():
					conn.close(); messagebox.showerror('Taken','Username already exists'); return
				cur.execute('INSERT INTO Users (Username, Password, Role, MemberID) VALUES (?,?,?,?)', (username, hash_password(password), role, member_id_int))
			conn.commit(); conn.close();
			self.populate_users(tree)
			self.clear_security_form(username_var, password_var, role_var, member_id_var, selected_user_id)
			messagebox.showinfo('Saved', 'User account saved successfully')
		except Exception as exc:
			conn.close(); messagebox.showerror('Error', str(exc))

	def reset_selected_user(self, tree):
		sel = tree.selection()
		if not sel:
			messagebox.showwarning('Select', 'Select a user to reset')
			return
		username = tree.item(sel[0])['values'][1]
		self.set_user_password(username)

	def set_user_password(self, username):
		password = simpledialog.askstring(
			'Set password',
			'Enter a new password for ' + username + ' (at least 6 characters):',
			parent=self.root,
			show='*'
		)
		if password is None:
			return
		if len(password) < 6:
			messagebox.showwarning('Weak password', 'Use at least 6 characters.')
			return
		conn = sqlite3.connect(DB_PATH)
		conn.execute('UPDATE Users SET Password=? WHERE Username=?', (hash_password(password), username))
		conn.commit()
		conn.close()
		messagebox.showinfo('Reset', 'Password updated for ' + username)

	def delete_selected_user(self, tree):
		sel = tree.selection()
		if not sel:
			messagebox.showwarning('Select','Select a user to delete')
			return
		username = tree.item(sel[0])['values'][1]
		if username == 'admin':
			messagebox.showerror('Forbidden','Cannot delete admin user')
			return
		if not messagebox.askyesno('Confirm', f"Delete user {username}?"):
			return
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('DELETE FROM Users WHERE Username=?', (username,));conn.commit();conn.close();self.populate_users(tree)

	def reset_admin_password(self):
		self.set_user_password('admin')

	def export_table(self, table, columns):
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute(f"SELECT {', '.join(columns)} FROM {table}");rows=cur.fetchall();conn.close()
		fn = f"{table}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
		with open(fn,'w',newline='',encoding='utf-8') as f:
			w=csv.writer(f); w.writerow(columns); w.writerows(rows)
		messagebox.showinfo('Export','Wrote '+fn)


def main():
	setup_database()
	root = tk.Tk()
	app = App(root)
	root.mainloop()


if __name__ == '__main__':
	main()

