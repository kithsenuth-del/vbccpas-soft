
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

	# Additive equipment-register fields. Existing inventory rows and IDs remain intact.
	inventory_columns = {
		'Category': "TEXT DEFAULT 'General'",
		'AssetTag': 'TEXT',
		'Brand': 'TEXT',
		'Model': 'TEXT',
		'SerialNumber': 'TEXT',
		'ItemCondition': "TEXT DEFAULT 'Good'",
		'PurchaseDate': 'TEXT',
		'PurchaseCost': 'REAL',
		'StorageLocation': 'TEXT'
	}
	current_inventory_columns = {name.lower() for name in get_columns('Inventory')}
	for column_name, column_type in inventory_columns.items():
		if column_name.lower() not in current_inventory_columns:
			cur.execute(f'ALTER TABLE Inventory ADD COLUMN {column_name} {column_type}')
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


class App:
	def module_heading(self, parent, title, subtitle, actions=None):
		"""Consistent page heading and toolbar for the Windows workspace."""
		header = tk.Frame(parent, bg=self.colors['bg'])
		header.pack(fill=tk.X, padx=18, pady=(18, 12))
		tk.Label(header, text=title, font=('Segoe UI', 22, 'bold')).pack(anchor='w')
		tk.Label(header, text=subtitle, foreground='#aeb4c7').pack(anchor='w', pady=(3, 10))
		if actions:
			bar = tk.Frame(header, bg=self.colors['bg'])
			bar.pack(fill=tk.X, pady=(5, 0))
			for caption, command in actions:
				ttk.Button(bar, text=caption, command=command, style='Rounded.TButton').pack(side=tk.LEFT, padx=(0, 8))
		return header

	def module_table(self, parent, columns, widths=None):
		"""Create a consistent inset table surface for all record modules."""
		surface = tk.Frame(parent, bg=self.colors['card'], highlightthickness=1, highlightbackground='#30364a')
		surface.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
		inner = ttk.Frame(surface)
		inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
		tree = ttk.Treeview(inner, columns=columns, show='headings')
		for col in columns:
			tree.heading(col, text=col.replace('ID', ' ID').replace('Date', ' Date'))
			tree.column(col, width=(widths or {}).get(col, 145), minwidth=90, stretch=True, anchor='w')
		scroll = ttk.Scrollbar(inner, orient='vertical', command=tree.yview)
		tree.configure(yscrollcommand=scroll.set)
		tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
		scroll.pack(side=tk.RIGHT, fill=tk.Y)
		return tree

	def __init__(self, root):
		self.root = root
		root.title("VBCCPAS | Photographic Arts Society")
		root.geometry("1280x820")
		root.minsize(1000, 700)
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

		self.sidebar = tk.Frame(self.container, bg=self.colors['sidebar'], width=236)
		self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
		self.sidebar.pack_propagate(False)

		self.content = tk.Frame(self.container, bg=self.colors['bg'])
		self.content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

		# Login
		self.login_frame = tk.Frame(self.content, bg=self.colors['bg'])
		self.login_frame.pack(fill=tk.BOTH, expand=True)
		login_card = tk.Frame(self.login_frame, bg=self.colors['card'], bd=1, highlightthickness=1, highlightbackground='#334155')
		login_card.place(relx=0.5, rely=0.5, anchor=tk.CENTER, width=460, height=360)
		tk.Label(login_card, text="VBCCPAS Photographic Arts Society", bg=self.colors['card'], fg='#f3eee6', font=('Segoe UI', 20, 'bold')).place(x=24, y=24)
		tk.Label(login_card, text="The creative home for student photographers, photo events and camera equipment.", bg=self.colors['card'], fg='#d6cfc5', font=('Segoe UI', 10)).place(x=24, y=64)
		form = tk.Frame(login_card, bg=self.colors['card'])
		form.place(x=24, y=110, width=410)
		tk.Label(form, text="Username", bg=self.colors['card'], fg=self.colors['fg'], font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, sticky=tk.W, pady=(0, 6))
		self.username_var = tk.StringVar()
		tk.Entry(form, textvariable=self.username_var, bg='#171719', fg='#f8fafc', insertbackground='white', width=34).grid(row=1, column=0, sticky=tk.W, pady=(0, 10))
		tk.Label(form, text="Password", bg=self.colors['card'], fg=self.colors['fg'], font=('Segoe UI', 10, 'bold')).grid(row=2, column=0, sticky=tk.W, pady=(0, 6))
		self.password_var = tk.StringVar()
		tk.Entry(form, textvariable=self.password_var, show='*', bg='#0f172a', fg='#f8fafc', insertbackground='white', width=34).grid(row=3, column=0, sticky=tk.W, pady=(0, 12))
		btn_frame = tk.Frame(login_card, bg=self.colors['card'])
		btn_frame.place(x=24, y=290)
		ttk.Button(btn_frame, text="Sign in", command=self.try_login, style='Rounded.TButton').pack(side=tk.LEFT, padx=(0, 8))

		# sidebar buttons (hidden until login)
		self.side_buttons = {}
		self.sidebar_title = tk.Label(self.sidebar, text="Modules", bg=self.colors['sidebar'], fg='#cbd5e1', font=('Segoe UI', 11, 'bold'))
		self.sidebar_title.pack(anchor='w', padx=18, pady=(18, 10))
		for key, text in [('dashboard','Dashboard'), ('members','Members'), ('events','Events'), ('inventory','Inventory'), ('borrow','Borrowing'), ('attendance','Attendance'), ('reports','Reports'), ('security','Security')]:
			b = ttk.Button(self.sidebar, text=text, command=lambda k=key: self.show_view(k), style='Sidebar.TButton')
			b.pack(fill=tk.X, padx=12, pady=6)
			self.side_buttons[key] = b
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
			'bg': '#111319',
			'card': '#1c202a',
			'fg': '#f4f2f8',
			'accent1': '#8b7cf6',
			'accent2': '#c87548',
			'accent3': '#a99cff',
			'sidebar': '#171a22',
			'panel': '#242936'
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
		frame = tk.Frame(self.content, bg=self.colors['bg'])
		frame.pack(fill=tk.BOTH, expand=True)
		self.current_view = frame
		if key == 'dashboard':
			# Polished Windows workspace dashboard matching the approved VBCCPAS prototype.
			frame.configure(bg=self.colors['bg'])
			frame.grid_columnconfigure(0, weight=1)
			frame.grid_rowconfigure(2, weight=1)
			
			def panel(parent, bg=None, **kwargs):
				return tk.Frame(parent, bg=bg or self.colors['card'], bd=0,
				                highlightthickness=1, highlightbackground='#30364a', **kwargs)
			
			def label(parent, text, size=10, color='#f4f2f8', bold=False, **kwargs):
				return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
				                font=('Segoe UI', size, 'bold' if bold else 'normal'), **kwargs)
			
			# Top hero banner
			hero = panel(frame, '#20243a')
			hero.grid(row=0, column=0, sticky='ew', padx=18, pady=(16, 12))
			hero.grid_columnconfigure(0, weight=1)
			label(hero, 'VBCCPAS Soft', 25, '#ffffff', True).grid(row=0, column=0, sticky='w', padx=22, pady=(17, 2))
			label(hero, 'Together for Our Alma Mater', 12, '#d5d1ff').grid(row=1, column=0, sticky='w', padx=23, pady=(0, 16))
			label(hero, datetime.now().strftime('%A, %d %B %Y'), 9, '#aeb4ca').grid(row=0, column=1, rowspan=2, sticky='e', padx=22)
			
			# Live summary figures from the existing local database.
			conn = sqlite3.connect(DB_PATH)
			cur = conn.cursor()
			def safe_count(query):
				try:
					cur.execute(query)
					return int(cur.fetchone()[0] or 0)
				except sqlite3.Error:
					return 0
			mcount = safe_count('SELECT COUNT(*) FROM Members')
			ecount = safe_count('SELECT COUNT(*) FROM Events')
			icount = safe_count('SELECT COUNT(*) FROM Inventory')
			bcount = safe_count('SELECT COUNT(*) FROM BorrowedItems WHERE Returned=0')
			conn.close()
			
			stats = tk.Frame(frame, bg=self.colors['bg'])
			stats.grid(row=1, column=0, sticky='ew', padx=12, pady=4)
			for col in range(4): stats.grid_columnconfigure(col, weight=1, uniform='stat')
			stat_data = [
				('MEMBERS', f'{mcount:,}', 'Registered society members', '#3478e5'),
				('EVENTS', f'{ecount:,}', 'Events recorded', '#7454d6'),
				('EQUIPMENT', f'{icount:,}', 'Inventory items', '#159b78'),
				('ON LOAN', f'{bcount:,}', 'Items currently borrowed', '#c98732'),
			]
			for col, (title, value, hint, accent) in enumerate(stat_data):
				card = panel(stats, '#20263a')
				card.grid(row=0, column=col, sticky='nsew', padx=6, pady=5, ipady=7)
				label(card, title, 9, '#b9bfd2', True).pack(anchor='w', padx=14, pady=(11, 3))
				label(card, value, 23, accent, True).pack(anchor='w', padx=14)
				label(card, hint, 8, '#a5abc0').pack(anchor='w', padx=14, pady=(2, 9))
			
			# Main workspace: quick actions and upcoming event.
			workspace = tk.Frame(frame, bg=self.colors['bg'])
			workspace.grid(row=2, column=0, sticky='nsew', padx=18, pady=(12, 12))
			workspace.grid_columnconfigure(0, weight=3)
			workspace.grid_columnconfigure(1, weight=2)
			
			actions = panel(workspace, '#1c2233')
			actions.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
			label(actions, 'Quick Actions', 14, '#ffffff', True).pack(anchor='w', padx=18, pady=(16, 4))
			label(actions, 'Jump straight into the most-used society tools.', 9, '#aeb4c7').pack(anchor='w', padx=18, pady=(0, 12))
			action_grid = tk.Frame(actions, bg='#1c2233')
			action_grid.pack(fill=tk.X, padx=12, pady=(0, 14))
			for col in range(3): action_grid.grid_columnconfigure(col, weight=1, uniform='action')
			for i, (caption, destination) in enumerate([
				('＋  Add Member', 'members'), ('▦  Create Event', 'events'),
				('▤  View Reports', 'reports'), ('◈  Inventory', 'inventory'),
				('⇄  Borrowing', 'borrow'), ('✓  Attendance', 'attendance')
			]):
				btn = ttk.Button(action_grid, text=caption,
				                 command=lambda target=destination: self.show_view(target),
				                 style='Rounded.TButton')
				btn.grid(row=i//3, column=i%3, sticky='ew', padx=5, pady=5, ipady=8)
			
			upcoming = panel(workspace, '#1c2233')
			upcoming.grid(row=0, column=1, sticky='nsew', padx=(8, 0))
			label(upcoming, 'Upcoming Event', 14, '#ffffff', True).pack(anchor='w', padx=18, pady=(16, 10))
			next_event = self.get_next_event()
			if next_event:
				label(upcoming, next_event[0], 13, '#c6bcff', True, wraplength=300, justify='left').pack(anchor='w', padx=18, pady=(4, 4))
				label(upcoming, next_event[1].strftime('%A, %d %B %Y'), 10, '#d2d5e2').pack(anchor='w', padx=18)
			else:
				label(upcoming, 'No upcoming events scheduled', 11, '#d2d5e2').pack(anchor='w', padx=18, pady=(8, 4))
			label(upcoming, 'Manage your society calendar and event details.', 9, '#a5abc0', wraplength=300, justify='left').pack(anchor='w', padx=18, pady=(10, 12))
			ttk.Button(upcoming, text='Open Events', command=lambda: self.show_view('events'),
			           style='Rounded.TButton').pack(anchor='w', padx=18, pady=(0, 16))
			
			# Compact footer/status strip
			status = panel(frame, '#171c2a')
			status.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 16))
			label(status, '●  Local database ready', 9, '#8fd3ad', True).pack(side=tk.LEFT, padx=16, pady=10)
			label(status, 'VBCCPAS Soft  •  Windows desktop workspace', 9, '#9da5ba').pack(side=tk.RIGHT, padx=16, pady=10)
		elif key == 'members':
			self.view_members(frame)
		elif key == 'events':
			self.view_events(frame)
		elif key == 'inventory':
			self.view_inventory(frame)
		elif key == 'borrow':
			self.view_borrowing(frame)
		elif key == 'attendance':
			self.view_attendance(frame)
		elif key == 'reports':
			self.view_reports(frame)
		elif key == 'security':
			self.view_security(frame)

	# Members
	def view_members(self, parent):
		self.module_heading(parent, 'Members', 'Manage registered society members and their details.', [
			('＋  Add Member', self.add_member_dialog),
			('↓  Export CSV', lambda: self.export_table('Members', ['MemberID','Name','Class','Roll','Email']))
		])
		tree = self.module_table(parent, ('MemberID','Name','Class','Roll','Email'),
		                         {'MemberID':95, 'Name':240, 'Class':120, 'Roll':120, 'Email':240})
		self.populate_members(tree)
		def delete_selected():
			selection = tree.selection()
			if not selection:
				messagebox.showwarning('Select member', 'Choose a member first.')
				return
			member_id = tree.item(selection[0])['values'][0]
			if messagebox.askyesno('Delete member', 'Remove this member record?'):
				with sqlite3.connect(DB_PATH) as conn:
					conn.execute('DELETE FROM Members WHERE MemberID=?', (member_id,))
				self.populate_members(tree)
		ttk.Button(parent, text='Delete Selected', command=delete_selected).pack(anchor='e', padx=22, pady=(0, 14))
	def create_form_dialog(self, title, subtitle, fields, size='560x420'):
		"""Build a consistent, modal form window for data-entry workflows."""
		dialog = tk.Toplevel(self.root)
		dialog.title(title)
		dialog.configure(bg=self.colors['bg'])
		dialog.transient(self.root)
		dialog.resizable(False, False)
		dialog.geometry(size)
		outer = tk.Frame(dialog, bg=self.colors['bg'])
		outer.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
		card = tk.Frame(outer, bg=self.colors['card'], highlightthickness=1, highlightbackground='#30364a')
		card.pack(fill=tk.BOTH, expand=True)
		tk.Label(card, text=title, font=('Segoe UI', 18, 'bold')).pack(anchor='w', padx=20, pady=(18, 2))
		tk.Label(card, text=subtitle, foreground='#aeb4c7').pack(anchor='w', padx=20, pady=(0, 14))
		form = ttk.Frame(card, padding=(20, 4, 20, 10))
		form.pack(fill=tk.BOTH, expand=True)
		variables = {}
		for row, field in enumerate(fields):
			key, caption = field[0], field[1]
			values = field[2] if len(field) > 2 else None
			variables[key] = tk.StringVar()
			ttk.Label(form, text=caption).grid(row=row, column=0, sticky=tk.W, padx=(0, 14), pady=7)
			if values is not None:
				widget = ttk.Combobox(form, textvariable=variables[key], values=values, state='readonly', width=34)
			else:
				widget = ttk.Entry(form, textvariable=variables[key], width=37)
			widget.grid(row=row, column=1, sticky='ew', pady=7)
		form.grid_columnconfigure(1, weight=1)
		footer = ttk.Frame(card, padding=(20, 8, 20, 18))
		footer.pack(fill=tk.X)
		dialog.bind('<Escape>', lambda _event: dialog.destroy())
		dialog.update_idletasks()
		x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
		y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
		dialog.geometry(f'+{x}+{y}')
		dialog.grab_set()
		return dialog, variables, footer

	def add_member_dialog(self):
		d, fields, footer = self.create_form_dialog('Add Member', 'Enter the member details below.',
			[('name','Full name'),('class','Class / Grade'),('roll','Roll / Index'),('email','Email address')], '580x390')
		def save():
			name = fields['name'].get().strip()
			email = fields['email'].get().strip()
			if not name:
				messagebox.showwarning('Required field', 'Enter the member name.', parent=d); return
			if email and ('@' not in email or '.' not in email.rsplit('@', 1)[-1]):
				messagebox.showwarning('Invalid email', 'Enter a valid email address or leave it blank.', parent=d); return
			try:
				with sqlite3.connect(DB_PATH) as conn:
					conn.execute('INSERT INTO Members (Name,Class,Roll,Email) VALUES (?,?,?,?)',
						(name, fields['class'].get().strip(), fields['roll'].get().strip(), email))
			except sqlite3.Error as exc:
				messagebox.showerror('Could not save member', str(exc), parent=d); return
			d.destroy(); self.show_view('members')
		ttk.Button(footer, text='Cancel', command=d.destroy).pack(side=tk.RIGHT, padx=(8,0))
		ttk.Button(footer, text='Save Member', command=save, style='Rounded.TButton').pack(side=tk.RIGHT)

	# Events
	def view_events(self, parent):
		self.module_heading(parent, 'Events', 'Plan society activities, exhibitions and photo walks.', [
			('＋  Add Event', self.add_event_dialog)
		])
		tree = self.module_table(parent, ('EventID','Title','Date','Location'),
							 {'EventID':95, 'Title':260, 'Date':180, 'Location':220})
		with sqlite3.connect(DB_PATH) as conn:
			rows = conn.execute('SELECT EventID,Title,Date,Location FROM Events ORDER BY Date DESC').fetchall()
		for row in rows:
			tree.insert('', tk.END, values=row)

	def add_event_dialog(self):
		d, fields, footer = self.create_form_dialog('Create Event', 'Add an event to the society calendar. Use YYYY-MM-DD for the date.',
			[('title','Event title'),('date','Event date (YYYY-MM-DD)'),('location','Location'),('notes','Notes')], '600x430')
		def save():
			title = fields['title'].get().strip()
			date_text = fields['date'].get().strip()
			if not title:
				messagebox.showwarning('Required field', 'Enter an event title.', parent=d); return
			if date_text:
				try: datetime.strptime(date_text, '%Y-%m-%d')
				except ValueError:
					messagebox.showwarning('Invalid date', 'Enter the date in YYYY-MM-DD format.', parent=d); return
			try:
				with sqlite3.connect(DB_PATH) as conn:
					conn.execute('INSERT INTO Events (Title,Date,Location,Notes) VALUES (?,?,?,?)',
						(title, date_text or None, fields['location'].get().strip(), fields['notes'].get().strip()))
			except sqlite3.Error as exc:
				messagebox.showerror('Could not save event', str(exc), parent=d); return
			d.destroy(); self.show_view('events')
		ttk.Button(footer, text='Cancel', command=d.destroy).pack(side=tk.RIGHT, padx=(8,0))
		ttk.Button(footer, text='Save Event', command=save, style='Rounded.TButton').pack(side=tk.RIGHT)

	# Inventory
	def view_inventory(self, parent):
		self.module_heading(parent, 'Equipment & Inventory', 'Photographic, media-production and general society asset register.', [
			('＋  Register Equipment', self.add_item_dialog),
			('↓  Export Register', lambda: self.export_table('Inventory', ['ItemID','Category','Name','AssetTag','Brand','Model','SerialNumber','ItemCondition','Quantity','PurchaseDate','PurchaseCost','StorageLocation','Notes']))
		])
		with sqlite3.connect(DB_PATH) as conn:
			stats = conn.execute("""
				SELECT COUNT(*), COALESCE(SUM(Quantity),0),
				       SUM(CASE WHEN lower(Category) IN ('camera','lens','audio','lighting','video','media') THEN 1 ELSE 0 END)
				FROM Inventory
			""").fetchone()
		stats = stats or (0, 0, 0)
		cards = tk.Frame(parent, bg=self.colors['bg'])
		cards.pack(fill=tk.X, padx=18, pady=(0, 12))
		for title, value in [('Registered asset types', stats[0]), ('Total units in stock', stats[1]), ('Photo / media assets', stats[2] or 0)]:
			card = tk.Frame(cards, bg=self.colors['card'], highlightthickness=1, highlightbackground='#30364a')
			card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10), ipady=9)
			tk.Label(card, text=str(value), font=('Segoe UI', 19, 'bold')).pack(anchor='w', padx=14, pady=(7, 0))
			tk.Label(card, text=title, foreground='#aeb4c7').pack(anchor='w', padx=14)
		filters = ttk.Frame(parent, padding=(18, 0, 18, 10))
		filters.pack(fill=tk.X)
		search_var = tk.StringVar()
		ttk.Label(filters, text='Search assets').pack(side=tk.LEFT, padx=(0, 8))
		ttk.Entry(filters, textvariable=search_var, width=34).pack(side=tk.LEFT)
		tree = self.module_table(parent, ('ItemID','Category','Name','AssetTag','Brand / Model','SerialNumber','Condition','Quantity','Location'),
							 {'ItemID':65, 'Category':105, 'Name':170, 'AssetTag':110, 'Brand / Model':150, 'SerialNumber':145, 'Condition':100, 'Quantity':75, 'Location':130})
		def refresh(*_):
			for item in tree.get_children(): tree.delete(item)
			term = '%' + search_var.get().strip() + '%'
			with sqlite3.connect(DB_PATH) as conn:
				rows = conn.execute("""
					SELECT ItemID,Category,Name,AssetTag,
					       trim(COALESCE(Brand,'') || ' ' || COALESCE(Model,'')),
					       SerialNumber,ItemCondition,Quantity,StorageLocation
					FROM Inventory
					WHERE Category LIKE ? OR Name LIKE ? OR AssetTag LIKE ? OR Brand LIKE ?
					   OR Model LIKE ? OR SerialNumber LIKE ? OR StorageLocation LIKE ?
					ORDER BY Category,Name
				""", (term,term,term,term,term,term,term)).fetchall()
			for row in rows: tree.insert('', tk.END, values=row)
		search_var.trace_add('write', refresh)
		refresh()
		ttk.Button(parent, text='Refresh Register', command=refresh, style='Rounded.TButton').pack(anchor='e', padx=22, pady=(0, 14))

	def add_item_dialog(self):
		fields_spec = [
			('category','Equipment category',['Camera','Lens','Audio / Mixer','Microphone','Lighting','Video / Media','Tripod / Support','Computer / Storage','Accessories','Furniture','General']),
			('name','Item name / description'),
			('asset_tag','Society asset tag / ID'),
			('brand','Brand / manufacturer'),
			('model','Model'),
			('serial','Serial number'),
			('condition','Physical condition',['New','Excellent','Good','Fair','Needs repair','Unserviceable']),
			('quantity','Quantity / units'),
			('purchase_date','Purchase date (YYYY-MM-DD)'),
			('purchase_cost','Purchase cost (optional)'),
			('location','Storage / usual location'),
			('notes','Accessories, specifications or notes')
		]
		d, fields, footer = self.create_form_dialog('Register Society Equipment', 'Record cameras, lenses, mixers, microphones, lighting, media gear and other physical assets.', fields_spec, '700x690')
		fields['category'].set('Camera')
		fields['condition'].set('Good')
		fields['quantity'].set('1')
		def save():
			name = fields['name'].get().strip()
			quantity_text = fields['quantity'].get().strip()
			purchase_date = fields['purchase_date'].get().strip()
			cost_text = fields['purchase_cost'].get().strip().replace(',', '')
			if not name:
				messagebox.showwarning('Required field', 'Enter an item name or description.', parent=d); return
			try: quantity = int(quantity_text)
			except ValueError:
				messagebox.showwarning('Invalid quantity', 'Quantity must be a whole number of zero or greater.', parent=d); return
			if quantity < 0:
				messagebox.showwarning('Invalid quantity', 'Quantity cannot be negative.', parent=d); return
			if purchase_date:
				try: datetime.strptime(purchase_date, '%Y-%m-%d')
				except ValueError:
					messagebox.showwarning('Invalid date', 'Use YYYY-MM-DD for the purchase date.', parent=d); return
			try: purchase_cost = float(cost_text) if cost_text else None
			except ValueError:
				messagebox.showwarning('Invalid cost', 'Enter a valid purchase cost or leave it blank.', parent=d); return
			if purchase_cost is not None and purchase_cost < 0:
				messagebox.showwarning('Invalid cost', 'Purchase cost cannot be negative.', parent=d); return
			try:
				with sqlite3.connect(DB_PATH) as conn:
					conn.execute("""
						INSERT INTO Inventory
						(Name,Quantity,Notes,Category,AssetTag,Brand,Model,SerialNumber,ItemCondition,PurchaseDate,PurchaseCost,StorageLocation)
						VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
					""", (name,quantity,fields['notes'].get().strip(),fields['category'].get().strip(),
						fields['asset_tag'].get().strip(),fields['brand'].get().strip(),fields['model'].get().strip(),
						fields['serial'].get().strip(),fields['condition'].get().strip(),purchase_date or None,
						purchase_cost,fields['location'].get().strip()))
			except sqlite3.Error as exc:
				messagebox.showerror('Could not save equipment', str(exc), parent=d); return
			d.destroy()
			self.show_view('inventory')
		ttk.Button(footer, text='Cancel', command=d.destroy).pack(side=tk.RIGHT, padx=(8,0))
		ttk.Button(footer, text='Save to Register', command=save, style='Rounded.TButton').pack(side=tk.RIGHT)

	# Borrowing
	def view_borrowing(self, parent):
		self.module_heading(parent, 'Borrowing', 'Issue equipment to members and record returns.', [
			('＋  Borrow Item', self.borrow_dialog)
		])
		tree = self.module_table(parent, ('BorrowID','Item','Member','BorrowDate','ReturnDate','Returned'),
							 {'BorrowID':85, 'Item':180, 'Member':180, 'BorrowDate':170, 'ReturnDate':170, 'Returned':100})
		self.populate_borrowed(tree)
		ttk.Button(parent, text='Return Selected', command=lambda: self.return_selected_borrow(tree), style='Rounded.TButton').pack(anchor='e', padx=22, pady=(0, 14))

	def populate_borrowed(self, tree):
		for r in tree.get_children(): tree.delete(r)
		with sqlite3.connect(DB_PATH) as conn:
			rows = conn.execute('''
				SELECT b.BorrowID, i.Name, m.Name, b.BorrowDate, b.ReturnDate,
				       CASE WHEN b.Returned = 1 THEN 'Yes' ELSE 'No' END
				FROM BorrowedItems b
				LEFT JOIN Inventory i ON b.ItemID = i.ItemID
				LEFT JOIN Members m ON b.MemberID = m.MemberID
				ORDER BY b.BorrowID DESC
			''').fetchall()
		for row in rows: tree.insert('', tk.END, values=row)

	def return_selected_borrow(self, tree):
		selection = tree.selection()
		if not selection:
			messagebox.showwarning('Select item', 'Select a borrowed item to return.'); return
		borrow_id = tree.item(selection[0])['values'][0]
		try:
			with sqlite3.connect(DB_PATH) as conn:
				row = conn.execute('SELECT ItemID, Returned FROM BorrowedItems WHERE BorrowID=?', (borrow_id,)).fetchone()
				if not row or row[1] == 1:
					messagebox.showinfo('Already returned', 'This item is already marked as returned.'); return
				conn.execute('UPDATE BorrowedItems SET Returned=1, ReturnDate=? WHERE BorrowID=?',
					(datetime.now().isoformat(timespec='seconds'), borrow_id))
				conn.execute('UPDATE Inventory SET Quantity=Quantity+1 WHERE ItemID=?', (row[0],))
		except sqlite3.Error as exc:
			messagebox.showerror('Could not record return', str(exc)); return
		self.populate_borrowed(tree)

	def borrow_dialog(self):
		with sqlite3.connect(DB_PATH) as conn:
			items = conn.execute('SELECT ItemID,Name FROM Inventory WHERE Quantity>0 ORDER BY Name').fetchall()
			members = conn.execute('SELECT MemberID,Name FROM Members ORDER BY Name').fetchall()
		if not items:
			messagebox.showinfo('No equipment available', 'Add equipment with available quantity before recording a loan.'); return
		if not members:
			messagebox.showinfo('No members available', 'Register a member before recording a loan.'); return
		item_options = [f'{item_id}: {name}' for item_id, name in items]
		member_options = [f'{member_id}: {name}' for member_id, name in members]
		d, fields, footer = self.create_form_dialog('Borrow Equipment', 'Choose an available item and the member borrowing it.',
			[('item','Equipment',item_options),('member','Member',member_options),('return_date','Expected return (YYYY-MM-DD)')], '600x390')
		def save():
			try:
				item_id = int(fields['item'].get().split(':',1)[0])
				member_id = int(fields['member'].get().split(':',1)[0])
			except (ValueError, IndexError):
				messagebox.showwarning('Select details', 'Choose both an item and a member.', parent=d); return
			return_text = fields['return_date'].get().strip()
			if return_text:
				try: datetime.strptime(return_text, '%Y-%m-%d')
				except ValueError:
					messagebox.showwarning('Invalid date', 'Use YYYY-MM-DD for the expected return date.', parent=d); return
			try:
				with sqlite3.connect(DB_PATH) as conn:
					updated = conn.execute('UPDATE Inventory SET Quantity=Quantity-1 WHERE ItemID=? AND Quantity>0', (item_id,))
					if updated.rowcount != 1:
						messagebox.showwarning('Unavailable', 'This item is no longer available. Refresh and try again.', parent=d); return
					conn.execute('INSERT INTO BorrowedItems (ItemID,MemberID,BorrowDate,ReturnDate,Returned) VALUES (?,?,?,?,0)',
						(item_id, member_id, datetime.now().isoformat(timespec='seconds'), return_text or None))
			except sqlite3.Error as exc:
				messagebox.showerror('Could not record loan', str(exc), parent=d); return
			d.destroy(); self.show_view('borrow')
		ttk.Button(footer, text='Cancel', command=d.destroy).pack(side=tk.RIGHT, padx=(8,0))
		ttk.Button(footer, text='Confirm Loan', command=save, style='Rounded.TButton').pack(side=tk.RIGHT)

	# Attendance
	def view_attendance(self, parent):
		self.module_heading(parent, 'Attendance', 'Record and review member participation in society events.', [
			('＋  Mark Attendance', self.mark_attendance_dialog),
			('↓  Export CSV', lambda: self.export_table('Attendance', ['AttID','MemberID','EventID','Present','Timestamp']))
		])
		tree = self.module_table(parent, ('AttID','Member','Event','Present','Timestamp'),
							 {'AttID':85, 'Member':220, 'Event':240, 'Present':100, 'Timestamp':200})
		self.populate_attendance(tree)

	def populate_attendance(self, tree):
		for r in tree.get_children(): tree.delete(r)
		with sqlite3.connect(DB_PATH) as conn:
			rows = conn.execute("SELECT a.AttID,m.Name,e.Title,a.Present,a.Timestamp FROM Attendance a LEFT JOIN Members m ON a.MemberID=m.MemberID LEFT JOIN Events e ON a.EventID=e.EventID ORDER BY a.AttID DESC").fetchall()
		for row in rows: tree.insert('',tk.END,values=row)

	def mark_attendance_dialog(self):
		with sqlite3.connect(DB_PATH) as conn:
			members = conn.execute('SELECT MemberID,Name FROM Members ORDER BY Name').fetchall()
			events = conn.execute('SELECT EventID,Title FROM Events ORDER BY Date DESC').fetchall()
		if not members or not events:
			messagebox.showinfo('Records required', 'Add at least one member and one event before marking attendance.'); return
		d, fields, footer = self.create_form_dialog('Mark Attendance', 'Record a member’s participation in a society event.',
			[('member','Member',[f'{mid}: {name}' for mid,name in members]),('event','Event',[f'{eid}: {title}' for eid,title in events])], '600x340')
		present = tk.BooleanVar(value=True)
		ttk.Checkbutton(d, text='Present', variable=present).pack(anchor='w', padx=28, pady=(0,8))
		def save():
			try:
				member_id = int(fields['member'].get().split(':',1)[0])
				event_id = int(fields['event'].get().split(':',1)[0])
			except (ValueError, IndexError):
				messagebox.showwarning('Select details', 'Choose a member and an event.', parent=d); return
			try:
				with sqlite3.connect(DB_PATH) as conn:
					exists = conn.execute('SELECT 1 FROM Attendance WHERE MemberID=? AND EventID=?', (member_id,event_id)).fetchone()
					if exists:
						messagebox.showwarning('Already recorded', 'Attendance has already been recorded for this member and event.', parent=d); return
					conn.execute('INSERT INTO Attendance (MemberID,EventID,Present,Timestamp) VALUES (?,?,?,?)',
						(member_id,event_id,int(present.get()),datetime.now().isoformat(timespec='seconds')))
			except sqlite3.Error as exc:
				messagebox.showerror('Could not save attendance', str(exc), parent=d); return
			d.destroy(); self.show_view('attendance')
		ttk.Button(footer, text='Cancel', command=d.destroy).pack(side=tk.RIGHT, padx=(8,0))
		ttk.Button(footer, text='Save Attendance', command=save, style='Rounded.TButton').pack(side=tk.RIGHT)


	# Reports & Security
	def view_reports(self, parent):
		self.module_heading(parent, 'Reports & Exports', 'Download clear CSV reports from your society records.')
		items = [
			('Members', 'Registered member directory', lambda: self.export_table('Members', ['MemberID','Name','Class','Roll','Email'])),
			('Events', 'Society events and schedules', lambda: self.export_table('Events', ['EventID','Title','Date','Location','Notes'])),
			('Inventory', 'Equipment and stock quantities', lambda: self.export_table('Inventory', ['ItemID','Name','Quantity','Notes'])),
			('Borrowing', 'Equipment loans and returns', lambda: self.export_table('BorrowedItems', ['BorrowID','ItemID','MemberID','BorrowDate','ReturnDate','Returned'])),
			('Attendance', 'Member attendance records', lambda: self.export_table('Attendance', ['AttID','MemberID','EventID','Present','Timestamp']))
		]
		grid = tk.Frame(parent, bg=self.colors['bg'])
		grid.pack(fill=tk.BOTH, expand=True, padx=18, pady=8)
		for col in range(2): grid.grid_columnconfigure(col, weight=1, uniform='report')
		for index, (title, description, command) in enumerate(items):
			card = tk.Frame(grid, bg=self.colors['card'], highlightthickness=1, highlightbackground='#30364a')
			card.grid(row=index//2, column=index%2, sticky='ew', padx=7, pady=7, ipady=10)
			tk.Label(card, text=title, font=('Segoe UI', 13, 'bold')).pack(anchor='w', padx=16, pady=(12, 3))
			tk.Label(card, text=description, foreground='#aeb4c7').pack(anchor='w', padx=16, pady=(0, 10))
			ttk.Button(card, text='Export CSV', command=command, style='Rounded.TButton').pack(anchor='w', padx=16, pady=(0, 10))
	def view_security(self, parent):
		self.module_heading(parent, 'Security & Users', 'Manage application accounts and administrator access.')
		if self.user.get('role') != 'admin':
			ttk.Label(parent, text='Administrator access is required to manage users.', style='Warning.TLabel').pack(pady=18)
			return

		# Account editor card
		card = tk.Frame(parent, bg=self.colors['card'], highlightthickness=1, highlightbackground='#30364a')
		card.pack(fill=tk.X, padx=18, pady=(0, 12))
		tk.Label(card, text='Account details', font=('Segoe UI', 13, 'bold')).pack(anchor='w', padx=16, pady=(14, 4))
		ttk.Label(card, text='Select an existing account to edit it, or enter details to create a new account.',
			foreground='#aeb4c7').pack(anchor='w', padx=16, pady=(0, 8))
		form = ttk.Frame(card, padding=(16, 4, 16, 12))
		form.pack(fill=tk.X)
		selected_user_id = {'value': None}
		username_var = tk.StringVar()
		password_var = tk.StringVar()
		role_var = tk.StringVar(value='member')
		member_id_var = tk.StringVar()
		for col in (1, 3): form.grid_columnconfigure(col, weight=1)
		ttk.Label(form, text='Username').grid(row=0, column=0, sticky=tk.W, padx=(0,8), pady=5)
		ttk.Entry(form, textvariable=username_var).grid(row=0, column=1, sticky='ew', pady=5)
		ttk.Label(form, text='New password').grid(row=0, column=2, sticky=tk.W, padx=(16,8), pady=5)
		ttk.Entry(form, textvariable=password_var, show='*').grid(row=0, column=3, sticky='ew', pady=5)
		ttk.Label(form, text='Role').grid(row=1, column=0, sticky=tk.W, padx=(0,8), pady=5)
		tk.Combobox(form, textvariable=role_var, values=['admin','member'], state='readonly', width=18).grid(row=1, column=1, sticky='ew', pady=5)
		tk.Label(form, text='Linked member ID').grid(row=1, column=2, sticky=tk.W, padx=(16,8), pady=5)
		ttk.Entry(form, textvariable=member_id_var).grid(row=1, column=3, sticky='ew', pady=5)

		tree = self.module_table(parent, ('UserID','Username','Role','MemberID'),
							 {'UserID':100, 'Username':240, 'Role':140, 'MemberID':140})
		tree.bind('<<TreeviewSelect>>', lambda _event: self.load_security_selection(tree, username_var, role_var, member_id_var, selected_user_id))
		self.populate_users(tree)

		buttons = ttk.Frame(form)
		buttons.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(10, 0))
		ttk.Button(buttons, text='Save / Update', command=lambda: self.save_security_user(username_var, password_var, role_var, member_id_var, selected_user_id, tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=(0,6))
		ttk.Button(buttons, text='Clear', command=lambda: self.clear_security_form(username_var, password_var, role_var, member_id_var, selected_user_id), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		ttk.Button(buttons, text='Reset selected password', command=lambda: self.reset_selected_user(tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		ttk.Button(buttons, text='Delete selected', command=lambda: self.delete_selected_user(tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)

		toolbar = ttk.Frame(parent)
		toolbar.pack(fill=tk.X, padx=18, pady=(0, 10))
		ttk.Button(toolbar, text='Reset administrator password', command=self.reset_admin_password, style='Rounded.TButton').pack(side=tk.LEFT, padx=(0,8))
		ttk.Button(toolbar, text='Refresh accounts', command=lambda: self.populate_users(tree), style='Rounded.TButton').pack(side=tk.LEFT)


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

