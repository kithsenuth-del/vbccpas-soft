
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import hashlib
import os
import csv
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "society.db")


def hash_password(pw: str) -> str:
	return hashlib.sha256(pw.encode('utf-8')).hexdigest()


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

	# seed admin
	cur.execute("SELECT 1 FROM Users WHERE Username=?", ("admin",))
	if not cur.fetchone():
		cur.execute("INSERT INTO Users (Username, Password, Role) VALUES (?, ?, 'admin')", ("admin", hash_password("admin123")))

	conn.commit()
	conn.close()


class App:
	def __init__(self, root):
		self.root = root
		root.title("School Society Manager")
		root.geometry("1200x740")
		root.minsize(1080, 680)
		self.style = ttk.Style(root)
		self.style.theme_use('clam')
		self.apply_theme()

		self.container = ttk.Frame(root, style='Main.TFrame')
		self.container.pack(fill=tk.BOTH, expand=True)

		self.sidebar = tk.Frame(self.container, bg=self.colors['sidebar'], width=250)
		self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
		self.sidebar.pack_propagate(False)

		self.content = tk.Frame(self.container, bg=self.colors['bg'])
		self.content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

		# Login
		self.login_frame = tk.Frame(self.content, bg=self.colors['bg'])
		self.login_frame.pack(fill=tk.BOTH, expand=True)
		login_card = tk.Frame(self.login_frame, bg=self.colors['card'], bd=1, highlightthickness=1, highlightbackground='#334155')
		login_card.place(relx=0.5, rely=0.5, anchor=tk.CENTER, width=460, height=360)
		tk.Label(login_card, text="VBCCPAS Society Manager", bg=self.colors['card'], fg='#f8fafc', font=('Segoe UI', 20, 'bold')).place(x=24, y=24)
		tk.Label(login_card, text="Sign in to manage members, events, inventory and reports.", bg=self.colors['card'], fg='#cbd5e1', font=('Segoe UI', 10)).place(x=24, y=64)
		form = tk.Frame(login_card, bg=self.colors['card'])
		form.place(x=24, y=110, width=410)
		tk.Label(form, text="Username", bg=self.colors['card'], fg=self.colors['fg'], font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, sticky=tk.W, pady=(0, 6))
		self.username_var = tk.StringVar()
		tk.Entry(form, textvariable=self.username_var, bg='#0f172a', fg='#f8fafc', insertbackground='white', width=34).grid(row=1, column=0, sticky=tk.W, pady=(0, 10))
		tk.Label(form, text="Password", bg=self.colors['card'], fg=self.colors['fg'], font=('Segoe UI', 10, 'bold')).grid(row=2, column=0, sticky=tk.W, pady=(0, 6))
		self.password_var = tk.StringVar()
		tk.Entry(form, textvariable=self.password_var, show='*', bg='#0f172a', fg='#f8fafc', insertbackground='white', width=34).grid(row=3, column=0, sticky=tk.W, pady=(0, 12))
		btn_frame = tk.Frame(login_card, bg=self.colors['card'])
		btn_frame.place(x=24, y=290)
		ttk.Button(btn_frame, text="Sign in", command=self.try_login, style='Rounded.TButton').pack(side=tk.LEFT, padx=(0, 8))
		ttk.Button(btn_frame, text="Sign up", command=self.open_signup, style='Rounded.TButton').pack(side=tk.LEFT)

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
		if hash_password(p) != row[0]:
			messagebox.showerror("Login failed", "Incorrect password")
			return
		self.user = {'username': u, 'role': row[1] if len(row) > 1 else 'member'}
		self.login_frame.destroy()
		self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
		self.show_view('dashboard')

	def open_signup(self):
		d = tk.Toplevel(self.root)
		d.title('Sign up')
		frm = ttk.Frame(d, padding=16)
		frm.pack(fill=tk.BOTH, expand=True)
		tk.Label(frm, text='Register Member Account', font=(None, 14)).grid(row=0, column=0, columnspan=2, pady=(0,12))
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
		self.colors = {
			'bg': '#07131f',
			'card': '#0f172a',
			'fg': '#e2e8f0',
			'accent1': '#10b981',
			'accent2': '#fbbf24',
			'accent3': '#38bdf8',
			'sidebar': '#020617',
			'panel': '#111827'
		}
		bg = self.colors['bg']
		fg = self.colors['fg']
		accent = self.colors['accent1']
		accent2 = self.colors['accent2']
		accent3 = self.colors['accent3']
		self.style.configure('TFrame', background=bg)
		self.style.configure('Main.TFrame', background=bg)
		self.style.configure('TLabel', background=bg, foreground=fg, font=('Segoe UI', 10))
		self.style.configure('TNotebook', background=bg)
		self.style.configure('Rounded.TButton', foreground='#f8fafc', background=accent, padding=8, relief='flat', font=('Segoe UI', 10, 'bold'))
		self.style.map('Rounded.TButton', background=[('active', accent3), ('pressed', accent2)])
		self.style.configure('Sidebar.TButton', foreground='#e2e8f0', background=self.colors['sidebar'], padding=10, relief='flat', font=('Segoe UI', 10, 'bold'))
		self.style.map('Sidebar.TButton', background=[('active', accent), ('pressed', accent3)])
		self.style.configure('Treeview', background=self.colors['card'], fieldbackground=self.colors['card'], foreground=fg, rowheight=28)
		self.style.map('Treeview', background=[('selected', accent)])
		self.style.configure('TEntry', fieldbackground='#0f172a', foreground=fg)
		self.style.configure('TCombobox', fieldbackground='#0f172a', foreground=fg)
		self.style.configure('TSpinbox', fieldbackground='#0f172a', foreground=fg)
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
			header = tk.Frame(frame, bg=self.colors['panel'], bd=1, highlightthickness=1, highlightbackground='#334155')
			header.pack(fill=tk.X, padx=16, pady=(16, 12))
			tk.Label(header, text=f"Welcome back, {self.user['username']}", bg=self.colors['panel'], fg='#f8fafc', font=('Segoe UI', 18, 'bold')).pack(anchor='w', padx=20, pady=(16, 4))
			tk.Label(header, text="A bright, modern desktop workspace for society management.", bg=self.colors['panel'], fg='#cbd5e1', font=('Segoe UI', 10)).pack(anchor='w', padx=20, pady=(0, 16))
			stats = tk.Frame(frame, bg=self.colors['bg'])
			stats.pack(fill=tk.X, padx=16, pady=6)
			mcount = self.count_members()
			bcount = self.count_borrowed()
			next_event = self.get_next_event()
			for title, value, color, subtitle in [
				('Society Members', str(mcount), self.colors['accent1'], 'Registered members'),
				('Currently Borrowed', str(bcount), self.colors['accent2'], 'Outstanding items'),
				('Next Event', f"{next_event[0]} ({next_event[1].strftime('%Y-%m-%d')})" if next_event else 'No upcoming events', self.colors['accent3'], 'Upcoming schedule')
			]:
				card = tk.Frame(stats, bg=self.colors['card'], bd=1, highlightthickness=1, highlightbackground='#334155', width=260, height=120)
				card.pack(side=tk.LEFT, padx=8)
				card.pack_propagate(False)
				tk.Label(card, text=title, bg=self.colors['card'], fg='#cbd5e1', font=('Segoe UI', 10)).place(x=16, y=16)
				tk.Label(card, text=value, bg=self.colors['card'], fg=color, font=('Segoe UI', 18, 'bold')).place(x=16, y=42)
				tk.Label(card, text=subtitle, bg=self.colors['card'], fg='#64748b', font=('Segoe UI', 9)).place(x=16, y=88)
			panel = tk.Frame(frame, bg=self.colors['panel'], bd=1, highlightthickness=1, highlightbackground='#334155')
			panel.pack(fill=tk.BOTH, expand=True, padx=16, pady=(12, 16))
			tk.Label(panel, text="Quick actions", bg=self.colors['panel'], fg='#f8fafc', font=('Segoe UI', 12, 'bold')).pack(anchor='w', padx=18, pady=(16, 8))
			tk.Label(panel, text="Use the sidebar to open members, events, inventory, borrowing, attendance, reports, and security modules.", bg=self.colors['panel'], fg='#cbd5e1', justify=tk.LEFT).pack(anchor='w', padx=18, pady=(0, 10))
			buttons = tk.Frame(panel, bg=self.colors['panel'])
			buttons.pack(anchor='w', padx=18, pady=6)
			ttk.Button(buttons, text='Open Members', command=lambda: self.show_view('members'), style='Rounded.TButton').pack(side=tk.LEFT, padx=(0, 8))
			ttk.Button(buttons, text='Open Events', command=lambda: self.show_view('events'), style='Rounded.TButton').pack(side=tk.LEFT)
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
		top = parent
		tb = ttk.Frame(top)
		tb.pack(fill=tk.X, pady=(0,6))
		left = ttk.Frame(tb)
		left.pack(side=tk.LEFT)
		right = ttk.Frame(tb)
		right.pack(side=tk.RIGHT)
		ttk.Button(left, text='Add Member', command=self.add_member_dialog, style='Rounded.TButton').pack(side=tk.LEFT)
		tk.Button(left, text='Export CSV', command=lambda: self.export_table('Members', ['MemberID','Name','Class','Roll','Email'])).pack(side=tk.LEFT, padx=6)
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
		# Inline add form
		form = ttk.Frame(top, padding=6)
		form.pack(fill=tk.X, pady=(8,0))
		name_var = tk.StringVar(); class_var = tk.StringVar(); roll_var = tk.StringVar(); email_var = tk.StringVar()
		ttk.Label(form, text='Name').grid(row=0, column=0); ttk.Entry(form, textvariable=name_var, width=24).grid(row=0, column=1, padx=6)
		tk.Label(form, text='Class').grid(row=0, column=2); ttk.Entry(form, textvariable=class_var, width=12).grid(row=0, column=3, padx=6)
		tk.Label(form, text='Roll').grid(row=0, column=4); ttk.Entry(form, textvariable=roll_var, width=10).grid(row=0, column=5, padx=6)
		tk.Label(form, text='Email').grid(row=1, column=0); ttk.Entry(form, textvariable=email_var, width=36).grid(row=1, column=1, columnspan=3, padx=6)
		def do_inline_add():
			if not name_var.get().strip(): messagebox.showwarning('Missing','Enter name'); return
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO Members (Name,Class,Roll,Email) VALUES (?,?,?,?)', (name_var.get(),class_var.get(),roll_var.get(),email_var.get()));conn.commit();conn.close(); self.populate_members(tree); name_var.set(''); class_var.set(''); roll_var.set(''); email_var.set('')
		ttk.Button(form, text='Add Inline', command=do_inline_add, style='Rounded.TButton').grid(row=1, column=4, columnspan=2, padx=6)

	def populate_members(self, tree):
		for r in tree.get_children(): tree.delete(r)
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT MemberID, Name, Class, Roll, Email FROM Members');rows=cur.fetchall();conn.close()
		for r in rows: tree.insert('',tk.END,values=r)

	def add_member_dialog(self):
		d=tk.Toplevel(self.root);d.title('Add Member')
		f=ttk.Frame(d,padx=12,pady=12);f.pack()
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
		# inline add
		form = ttk.Frame(top,padding=6); form.pack(fill=tk.X, pady=(8,0))
		title_var = tk.StringVar(); date_var = tk.StringVar(); loc_var = tk.StringVar()
		tk.Label(form,text='Title').grid(row=0,column=0); ttk.Entry(form,textvariable=title_var, width=36).grid(row=0,column=1, padx=6)
		tk.Label(form,text='Date').grid(row=1,column=0); ttk.Entry(form,textvariable=date_var, width=20).grid(row=1,column=1, padx=6)
		tk.Label(form,text='Location').grid(row=2,column=0); ttk.Entry(form,textvariable=loc_var, width=28).grid(row=2,column=1, padx=6)
		def do_add_event():
			if not title_var.get().strip(): messagebox.showwarning('Missing','Enter title'); return
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO Events (Title,Date,Location) VALUES (?,?,?)', (title_var.get(),date_var.get(),loc_var.get()));conn.commit();conn.close(); title_var.set(''); date_var.set(''); loc_var.set(''); self.show_view('events')
		ttk.Button(form,text='Add Inline', command=do_add_event, style='Rounded.TButton').grid(row=0,column=2,rowspan=3,padx=8)

	def add_event_dialog(self):
		d=tk.Toplevel(self.root);d.title('Add Event')
		f=ttk.Frame(d,padx=12,pady=12);f.pack()
		title=tk.StringVar(); date=tk.StringVar(); loc=tk.StringVar(); notes=tk.StringVar()
		ttk.Label(f,text='Title').grid(row=0,column=0);ttk.Entry(f,textvariable=title).grid(row=0,column=1)
		ttk.Label(f,text='Date').grid(row=1,column=0);ttk.Entry(f,textvariable=date).grid(row=1,column=1)
		ttk.Label(f,text='Location').grid(row=2,column=0);ttk.Entry(f,textvariable=loc).grid(row=2,column=1)
		ttk.Label(f,text='Notes').grid(row=3,column=0);ttk.Entry(f,textvariable=notes).grid(row=3,column=1)
		def save():
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO Events (Title,Date,Location,Notes) VALUES (?,?,?,?)', (title.get(),date.get(),loc.get(),notes.get()));conn.commit();conn.close();d.destroy();self.show_view('events')
		ttk.Button(f,text='Save',command=save).grid(row=4,column=0,columnspan=2,pady=8)

	# Inventory
	def view_inventory(self, parent):
		top=parent
		tb=ttk.Frame(top);tb.pack(fill=tk.X)
		left = ttk.Frame(tb); left.pack(side=tk.LEFT)
		right = ttk.Frame(tb); right.pack(side=tk.RIGHT)
		ttk.Button(left,text='Add Item',command=self.add_item_dialog, style='Rounded.TButton').pack(side=tk.LEFT)
		cols=('ItemID','Name','Quantity','Notes')
		tree=ttk.Treeview(top,columns=cols,show='headings')
		for c in cols: tree.heading(c,text=c); tree.column(c,width=160)
		tree.pack(fill=tk.BOTH,expand=True)
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT ItemID,Name,Quantity,Notes FROM Inventory');rows=cur.fetchall();conn.close()
		for r in rows: tree.insert('',tk.END,values=r)
		# inline add
		form = ttk.Frame(top,padding=6); form.pack(fill=tk.X, pady=(8,0))
		name_var = tk.StringVar(); qty_var = tk.IntVar(value=1); notes_var = tk.StringVar()
		tk.Label(form,text='Name').grid(row=0,column=0); ttk.Entry(form,textvariable=name_var, width=36).grid(row=0,column=1, padx=6)
		tk.Label(form,text='Quantity').grid(row=1,column=0); ttk.Entry(form,textvariable=qty_var, width=12).grid(row=1,column=1, padx=6)
		tk.Label(form,text='Notes').grid(row=2,column=0); ttk.Entry(form,textvariable=notes_var, width=36).grid(row=2,column=1, padx=6)
		def do_add_item():
			if not name_var.get().strip(): messagebox.showwarning('Missing','Enter name'); return
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO Inventory (Name,Quantity,Notes) VALUES (?,?,?)', (name_var.get(), qty_var.get(), notes_var.get()));conn.commit();conn.close(); name_var.set(''); qty_var.set(1); notes_var.set(''); self.show_view('inventory')
		ttk.Button(form, text='Add Inline', command=do_add_item, style='Rounded.TButton').grid(row=0,column=2,rowspan=3,padx=8)

	def add_item_dialog(self):
		d=tk.Toplevel(self.root);d.title('Add Item')
		f=ttk.Frame(d,padx=12,pady=12);f.pack()
		name=tk.StringVar(); qty=tk.IntVar(value=1); notes=tk.StringVar()
		ttk.Label(f,text='Name').grid(row=0,column=0);ttk.Entry(f,textvariable=name).grid(row=0,column=1)
		ttk.Label(f,text='Quantity').grid(row=1,column=0);ttk.Entry(f,textvariable=qty).grid(row=1,column=1)
		ttk.Label(f,text='Notes').grid(row=2,column=0);ttk.Entry(f,textvariable=notes).grid(row=2,column=1)
		def save():
			conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('INSERT INTO Inventory (Name,Quantity,Notes) VALUES (?,?,?)', (name.get(), qty.get(), notes.get()));conn.commit();conn.close();d.destroy();self.show_view('inventory')
		ttk.Button(f,text='Save',command=save).grid(row=3,column=0,columnspan=2,pady=8)

	# Borrowing
	def view_borrowing(self, parent):
		top=parent
		tb=ttk.Frame(top);tb.pack(fill=tk.X)
		left=ttk.Frame(tb); left.pack(side=tk.LEFT)
		tk.Button(left,text='Borrow Item',command=self.borrow_dialog).pack(side=tk.LEFT)
		tk.Button(left,text='Return Selected',command=lambda: self.return_selected_borrow(tree)).pack(side=tk.LEFT, padx=6)
		tk.Button(left,text='Refresh',command=lambda: self.populate_borrowed(tree)).pack(side=tk.LEFT, padx=6)
		cols=('BorrowID','Item','Member','BorrowDate','ReturnDate','Returned')
		tree=ttk.Treeview(top,columns=cols,show='headings')
		for c in cols: tree.heading(c,text=c); tree.column(c,width=140)
		tree.pack(fill=tk.BOTH,expand=True)
		self.populate_borrowed(tree)

	def borrow_dialog(self):
		d=tk.Toplevel(self.root);d.title('Borrow Item')
		f=ttk.Frame(d,padx=12,pady=12);f.pack()
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
		tk.Button(left,text='Mark Attendance',command=self.mark_attendance_dialog).pack(side=tk.LEFT)
		tk.Button(left,text='Refresh',command=lambda: self.populate_attendance(tree)).pack(side=tk.LEFT, padx=6)
		tk.Button(left,text='Export Attendance CSV',command=lambda: self.export_table('Attendance', ['AttID','MemberID','EventID','Present','Timestamp'])).pack(side=tk.LEFT, padx=6)
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
		f=ttk.Frame(d,padx=12,pady=12);f.pack()
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT MemberID,Name FROM Members');members=cur.fetchall();cur.execute('SELECT EventID,Title FROM Events');events=cur.fetchall();conn.close()
		mem_cb=ttk.Combobox(f, values=[f"{m[0]}: {m[1]}" for m in members])
		mem_cb.grid(row=0,column=1); ttk.Label(f,text='Member').grid(row=0,column=0)
		evt_cb=ttk.Combobox(f, values=[f"{e[0]}: {e[1]}" for e in events])
		evt_cb.grid(row=1,column=1); ttk.Label(f,text='Event').grid(row=1,column=0)
		present_var = tk.IntVar(value=1)
		tk.Checkbutton(f, text='Present', variable=present_var).grid(row=2,column=0,columnspan=2)
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
		tk.Label(top, text='Reports').pack(pady=8)
		btns = ttk.Frame(top)
		btns.pack(pady=8)
		tk.Button(btns, text='Export Members CSV', command=lambda: self.export_table('Members', ['MemberID','Name','Class','Roll','Email']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		tk.Button(btns, text='Export Inventory CSV', command=lambda: self.export_table('Inventory', ['ItemID','Name','Quantity','Notes']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		tk.Button(btns, text='Export Events CSV', command=lambda: self.export_table('Events', ['EventID','Title','Date','Location','Notes']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		tk.Button(btns, text='Export Borrowing CSV', command=lambda: self.export_table('BorrowedItems', ['BorrowID','ItemID','MemberID','BorrowDate','ReturnDate','Returned']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)
		tk.Button(btns, text='Export Attendance CSV', command=lambda: self.export_table('Attendance', ['AttID','MemberID','EventID','Present','Timestamp']), style='Rounded.TButton').pack(side=tk.LEFT, padx=6)

	def view_security(self, parent):
		top=parent
		tk.Label(top, text='Security / Users').pack(pady=8)
		if self.user.get('role') != 'admin':
			tk.Label(top, text='Admin access required to manage users.', foreground='orange').pack(pady=12)
			return
		tb=ttk.Frame(top);tb.pack(fill=tk.X)
		left=ttk.Frame(tb); left.pack(side=tk.LEFT)
		tk.Button(left, text='Reset admin password', command=self.reset_admin_password, style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		tk.Button(left, text='Reset selected', command=lambda: self.reset_selected_user(tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		tk.Button(left, text='Delete selected', command=lambda: self.delete_selected_user(tree), style='Rounded.TButton').pack(side=tk.LEFT, padx=4)
		cols=('UserID','Username','Role','MemberID')
		tree=ttk.Treeview(top,columns=cols,show='headings')
		for c in cols: tree.heading(c,text=c); tree.column(c,width=140)
		tree.pack(fill=tk.BOTH,expand=True, pady=8)
		self.populate_users(tree)

	def populate_users(self, tree):
		for r in tree.get_children(): tree.delete(r)
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('SELECT UserID, Username, Role, MemberID FROM Users');rows=cur.fetchall();conn.close()
		for r in rows: tree.insert('',tk.END,values=r)

	def reset_selected_user(self, tree):
		sel = tree.selection()
		if not sel:
			messagebox.showwarning('Select','Select a user to reset')
			return
		username = tree.item(sel[0])['values'][1]
		if username == 'admin':
			self.reset_admin_password()
			return
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute('UPDATE Users SET Password=? WHERE Username=?', (hash_password('password123'), username));conn.commit();conn.close();messagebox.showinfo('Reset', f"Password reset for {username}")

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
		conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute("UPDATE Users SET Password=? WHERE Username='admin'", (hash_password('admin123'),));conn.commit();conn.close();messagebox.showinfo('Reset','Admin password set to admin123')

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

