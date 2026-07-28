import sqlite3

import tkinter as tk
from tkinter import messagebox
import hashlib
from tkcalendar import DateEntry
from tkinter import ttk

# Colour scheme
BG_COLOR      = "#1B2B34"   # dark background
FRAME_COLOR   = "#2C3E50"   # section frame
GREEN_COLOR   = "#27AE60"   # bright green
YELLOW_COLOR  = "#F1C40F"   # golden yellow
TEAL_COLOR    = "#1ABC9C"   # teal
TEXT_COLOR    = "white"
FONT_TITLE    = ("Arial", 14, "bold")
FONT_BTN      = ("Arial", 12, "bold")

# =========================
# SECTION 1: Database Setup
# =========================
conn = sqlite3.connect("society.db")
cursor = conn.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS Members (
    MemberID INTEGER PRIMARY KEY AUTOINCREMENT,
    Name TEXT NOT NULL,
    Contact TEXT,
    Role TEXT,
    Status TEXT
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS Events (
    EventID INTEGER PRIMARY KEY AUTOINCREMENT,
    Title TEXT NOT NULL,
    Date TEXT,
    Location TEXT,
    Description TEXT
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS Inventory (
    ItemID INTEGER PRIMARY KEY AUTOINCREMENT,
    ItemName TEXT NOT NULL,
    Quantity INTEGER,
    Location TEXT,
    Notes TEXT
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS BorrowedItems (
    BorrowID INTEGER PRIMARY KEY AUTOINCREMENT,
    ItemID INTEGER,
    MemberID INTEGER,
    EventID INTEGER,
    DateBorrowed TEXT,
    DateReturned TEXT,
    FOREIGN KEY(ItemID) REFERENCES Inventory(ItemID),
    FOREIGN KEY(MemberID) REFERENCES Members(MemberID),
    FOREIGN KEY(EventID) REFERENCES Events(EventID)
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS Users (
    UserID INTEGER PRIMARY KEY AUTOINCREMENT,
    MemberID INTEGER,
    Username TEXT UNIQUE NOT NULL,
    Password TEXT,
    Role TEXT CHECK(Role IN ('admin','member')) NOT NULL,
    FirstLogin BOOLEAN DEFAULT 1,
    FOREIGN KEY(MemberID) REFERENCES Members(MemberID)
)""")

# Seed default admins
cursor.execute("SELECT * FROM Users WHERE Role='admin'")
if not cursor.fetchone():
    cursor.execute("INSERT INTO Users (MemberID, Username, Password, Role, FirstLogin) VALUES (?, ?, ?, ?, ?)",
                   (None, "admin", hashlib.sha256("admin123".encode()).hexdigest(), "admin", 0))
    print("Default admin created: username=admin, password=admin123")

cursor.execute("SELECT * FROM Users WHERE Username='admin2'")
if not cursor.fetchone():
    cursor.execute("INSERT INTO Users (MemberID, Username, Password, Role, FirstLogin) VALUES (?, ?, ?, ?, ?)",
                   (None, "admin2", hashlib.sha256("admin456".encode()).hexdigest(), "admin", 0))
    print("Second admin created: username=admin2, password=admin456")

conn.commit()
conn.close()



# =========================
# SECTION 2: Helper Functions
# =========================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_login(username, password):
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("SELECT UserID, Password, Role, FirstLogin FROM Users WHERE Username=?", (username,))
    row = cursor.fetchone()
    conn.close()

    if row:
        user_id, stored_pw, role, first_login = row
        if first_login == 1:
            return ("first_login", user_id, role)
        elif stored_pw and stored_pw == hash_password(password):
            return ("success", user_id, role)
    return ("fail", None, None)

def reset_password(user_id, new_password):
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE Users SET Password=?, FirstLogin=0 WHERE UserID=?",
                   (hash_password(new_password), user_id))
    conn.commit()
    conn.close()

def force_reset(user_id):
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE Users SET Password=NULL, FirstLogin=1 WHERE UserID=?", (user_id,))
    conn.commit()
    conn.close()

# =========================
# SECTION 3: Database Functions
# =========================
def add_member(name, contact, role, status):
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO Members (Name, Contact, Role, Status) VALUES (?, ?, ?, ?)",
                   (name, contact, role, status))
    member_id = cursor.lastrowid
    cursor.execute("INSERT INTO Users (MemberID, Username, Role, Password, FirstLogin) VALUES (?, ?, ?, NULL, 1)",
                   (member_id, name.lower(), "member"))
    conn.commit()
    conn.close()
    messagebox.showinfo("Success", f"Member added! Username: {name.lower()}")

def borrow_item(item_id, member_id, event_id, date_borrowed):
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO BorrowedItems (ItemID, MemberID, EventID, DateBorrowed) VALUES (?, ?, ?, ?)",
                   (item_id, member_id, event_id, date_borrowed))
    conn.commit()
    conn.close()
    messagebox.showinfo("Success", "Item borrowed successfully!")

def return_item(borrow_id, date_returned):
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE BorrowedItems SET DateReturned=? WHERE BorrowID=?",
                   (date_returned, borrow_id))
    conn.commit()
    conn.close()
    messagebox.showinfo("Success", "Item return authorized!")
def logout(root):
    root.destroy()
    login_gui()


def borrow_items_gui(user_id):
    win = tk.Toplevel(root)
    win.title("Borrow Items")

    tree = ttk.Treeview(win, columns=("ID", "Name", "Qty", "Location"), show="headings", height=10)
    tree.pack(padx=10, pady=10)

    tree.heading("ID", text="Item ID")
    tree.heading("Name", text="Item Name")
    tree.heading("Qty", text="Quantity")
    tree.heading("Location", text="Location")

    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("SELECT ItemID, ItemName, Quantity, Location FROM Inventory")
    rows = cursor.fetchall()
    conn.close()

    for row in rows:
        tree.insert("", "end", values=row)

    tk.Label(win, text="Borrow Date").pack()
    borrow_date = DateEntry(win, width=12, background='darkblue',
                            foreground='white', borderwidth=2, date_pattern='yyyy-mm-dd')
    borrow_date.pack(pady=5)

    def save_borrow():
        selected = tree.selection()
        if not selected:
            messagebox.showerror("Error", "No items selected!")
            return
        for sel in selected:
            item_id = tree.item(sel, "values")[0]
            borrow_item(item_id, user_id, None, borrow_date.get())
        messagebox.showinfo("Success", "Borrow successful!")
        win.destroy()

    tk.Button(win, text="Borrow Selected", command=save_borrow,
              bg=GREEN_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(pady=10)

# Other GUI functions: add_member_gui, add_event_gui, add_item_gui, list_inventory, list_members, list_events, etc.
# (Keep your existing ones, they are fine — just ensure no duplicate borrow functions remain.)

# =========================
# SECTION 3: GUI Functions
# =========================

# Borrow Items (single or multiple via table)
def borrow_items_gui(user_id):
    win = tk.Toplevel(root)
    win.title("Borrow Items")

    tree = ttk.Treeview(win, columns=("ID", "Name", "Qty", "Location"), show="headings", height=10)
    tree.pack(padx=10, pady=10)

    tree.heading("ID", text="Item ID")
    tree.heading("Name", text="Item Name")
    tree.heading("Qty", text="Quantity")
    tree.heading("Location", text="Location")

    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("SELECT ItemID, ItemName, Quantity, Location FROM Inventory")
    rows = cursor.fetchall()
    conn.close()

    for row in rows:
        tree.insert("", "end", values=row)

    tk.Label(win, text="Borrow Date").pack()
    borrow_date = DateEntry(win, width=12, background='darkblue',
                            foreground='white', borderwidth=2, date_pattern='yyyy-mm-dd')
    borrow_date.pack(pady=5)

    def save_borrow():
        selected = tree.selection()
        if not selected:
            messagebox.showerror("Error", "No items selected!")
            return
        for sel in selected:
            item_id = tree.item(sel, "values")[0]
            borrow_item(item_id, user_id, None, borrow_date.get())
        messagebox.showinfo("Success", "Borrow successful!")
        win.destroy()

    tk.Button(win, text="Borrow Selected", command=save_borrow,
              bg=GREEN_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(pady=10)


# Member Management
def add_member_gui():
    win = tk.Toplevel(root)
    win.title("Add Member")

    tk.Label(win, text="Name").pack()
    entry_name = tk.Entry(win)
    entry_name.pack()

    tk.Label(win, text="Contact").pack()
    entry_contact = tk.Entry(win)
    entry_contact.pack()

    tk.Label(win, text="Role").pack()
    entry_role = tk.Entry(win)
    entry_role.pack()

    tk.Label(win, text="Status").pack()
    entry_status = tk.Entry(win)
    entry_status.pack()

    def save_member():
        add_member(entry_name.get(), entry_contact.get(), entry_role.get(), entry_status.get())
        win.destroy()

    tk.Button(win, text="Save", command=save_member).pack(pady=10)


def list_members():
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Members")
    rows = cursor.fetchall()
    conn.close()

    win = tk.Toplevel(root)
    win.title("Members List")
    for row in rows:
        tk.Label(win, text=str(row)).pack()


# Event Management
def add_event_gui():
    win = tk.Toplevel(root)
    win.title("Add Event")

    tk.Label(win, text="Title").pack()
    entry_title = tk.Entry(win)
    entry_title.pack()

    tk.Label(win, text="Date").pack()
    entry_date = tk.Entry(win)
    entry_date.pack()

    tk.Label(win, text="Location").pack()
    entry_location = tk.Entry(win)
    entry_location.pack()

    tk.Label(win, text="Description").pack()
    entry_desc = tk.Entry(win)
    entry_desc.pack()

    def save_event():
        conn = sqlite3.connect("society.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO Events (Title, Date, Location, Description) VALUES (?, ?, ?, ?)",
                       (entry_title.get(), entry_date.get(), entry_location.get(), entry_desc.get()))
        conn.commit()
        conn.close()
        messagebox.showinfo("Success", "Event added successfully!")
        win.destroy()

    tk.Button(win, text="Save", command=save_event).pack(pady=10)


def list_events():
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Events")
    rows = cursor.fetchall()
    conn.close()

    win = tk.Toplevel(root)
    win.title("Events List")
    for row in rows:
        tk.Label(win, text=str(row)).pack()


# Inventory Management
def add_item_gui():
    win = tk.Toplevel(root)
    win.title("Add Inventory Item")

    tk.Label(win, text="Item Name").pack()
    entry_name = tk.Entry(win)
    entry_name.pack()

    tk.Label(win, text="Quantity").pack()
    entry_qty = tk.Entry(win)
    entry_qty.pack()

    tk.Label(win, text="Location").pack()
    entry_loc = tk.Entry(win)
    entry_loc.pack()

    tk.Label(win, text="Notes").pack()
    entry_notes = tk.Entry(win)
    entry_notes.pack()

    def save_item():
        conn = sqlite3.connect("society.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO Inventory (ItemName, Quantity, Location, Notes) VALUES (?, ?, ?, ?)",
                       (entry_name.get(), entry_qty.get(), entry_loc.get(), entry_notes.get()))
        conn.commit()
        conn.close()
        messagebox.showinfo("Success", "Item added successfully!")
        win.destroy()

    tk.Button(win, text="Save", command=save_item).pack(pady=10)


def list_inventory():
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Inventory")
    rows = cursor.fetchall()
    conn.close()

    win = tk.Toplevel(root)
    win.title("Inventory List")
    for row in rows:
        tk.Label(win, text=str(row)).pack()


# Borrowed Items
def list_borrowed_items():
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT BorrowedItems.BorrowID, Inventory.ItemName, Members.Name, Events.Title, 
               BorrowedItems.DateBorrowed, BorrowedItems.DateReturned
        FROM BorrowedItems
        LEFT JOIN Inventory ON BorrowedItems.ItemID = Inventory.ItemID
        LEFT JOIN Members ON BorrowedItems.MemberID = Members.MemberID
        LEFT JOIN Events ON BorrowedItems.EventID = Events.EventID
    """)
    rows = cursor.fetchall()
    conn.close()

    win = tk.Toplevel(root)
    win.title("Borrowed Items List")
    for row in rows:
        tk.Label(win, text=str(row)).pack()


def list_currently_borrowed_items():
    conn = sqlite3.connect("society.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT BorrowedItems.BorrowID, Inventory.ItemName, Members.Name, Events.Title, 
               BorrowedItems.DateBorrowed
        FROM BorrowedItems
        LEFT JOIN Inventory ON BorrowedItems.ItemID = Inventory.ItemID
        LEFT JOIN Members ON BorrowedItems.MemberID = Members.MemberID
        LEFT JOIN Events ON BorrowedItems.EventID = Events.EventID
        WHERE BorrowedItems.DateReturned IS NULL
    """)
    rows = cursor.fetchall()
    conn.close()

    win = tk.Toplevel(root)
    win.title("Currently Borrowed Items")
    if rows:
        for row in rows:
            tk.Label(win, text=str(row)).pack()
    else:
        tk.Label(win, text="No items are currently borrowed.").pack()


def return_item_gui():
    win = tk.Toplevel(root)
    win.title("Authorize Return")

    tk.Label(win, text="Borrow ID").pack()
    entry_borrow = tk.Entry(win)
    entry_borrow.pack()

    tk.Label(win, text="Return Date").pack()
    entry_date = tk.Entry(win)
    entry_date.pack()

    def authorize_return():
        return_item(entry_borrow.get(), entry_date.get())
        win.destroy()

    tk.Button(win, text="Authorize", command=authorize_return).pack(pady=10)


# Security
def reset_admin_password_gui():
    win = tk.Toplevel(root)
    win.title("Reset Admin Password")

    tk.Label(win, text="New Password").pack()
    entry_new = tk.Entry(win, show="*")
    entry_new.pack()

    tk.Label(win, text="Confirm Password").pack()
    entry_confirm = tk.Entry(win, show="*")
    entry_confirm.pack()

    def save_password():
        if entry_new.get() != entry_confirm.get():
            messagebox.showerror("Error", "Passwords do not match!")
            return
        conn = sqlite3.connect("society.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE Users SET Password=? WHERE Role='admin'", 
                       (hash_password(entry_new.get()),))
        conn.commit()
        conn.close()
        messagebox.showinfo("Success", "Admin password reset successfully!")
        win.destroy()

    tk.Button(win, text="Save", command=save_password).pack(pady=10)


def master_override_gui():
    win = tk.Toplevel(root)
    win.title("Master Override Reset")

    tk.Label(win, text="Enter Master Override Code").pack()
    entry_code = tk.Entry(win, show="*")
    entry_code.pack()

    tk.Label(win, text="Username to Reset").pack()
    entry_user = tk.Entry(win)
    entry_user.pack()

    tk.Label(win, text="New Password").pack()
    entry_new = tk.Entry(win, show="*")


# =========================
# SECTION 5: Main Window
# =========================
def open_main_window(user_id, role):
    global root
    root = tk.Tk()
    root.title("Society Management System")
    root.state('zoomed')
    root.configure(bg=BG_COLOR)

    frame = tk.Frame(root)
    frame.pack(expand=True)

    if role == "member":
        member_frame = tk.LabelFrame(frame, text="Member Actions", bg=FRAME_COLOR, fg=TEXT_COLOR, font=FONT_TITLE)
        member_frame.grid(row=0, column=0, padx=20, pady=20)

        tk.Button(member_frame, text="List Members", command=list_members,
                  bg=GREEN_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(padx=10, pady=5)
        tk.Button(member_frame, text="View Inventory", command=list_inventory,
                  bg=TEAL_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(padx=10, pady=5)
        tk.Button(member_frame, text="Borrow Items", command=lambda: borrow_items_gui(user_id),
                  bg=YELLOW_COLOR, fg="black", font=FONT_BTN).pack(padx=10, pady=5)

    elif role == "admin":
        # Events Section
        event_frame = tk.LabelFrame(frame, text="Events", bg=FRAME_COLOR, fg=TEXT_COLOR, font=FONT_TITLE)
        event_frame.grid(row=0, column=0, padx=20, pady=20)

        tk.Button(event_frame, text="Add Event", command=add_event_gui,
                  bg=GREEN_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(padx=10, pady=5)
        tk.Button(event_frame, text="List Events", command=list_events,
                  bg=TEAL_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(padx=10, pady=5)

        # Inventory Section
        inventory_frame = tk.LabelFrame(frame, text="Inventory", bg=FRAME_COLOR, fg=TEXT_COLOR, font=FONT_TITLE)
        inventory_frame.grid(row=0, column=1, padx=20, pady=20)

        tk.Button(inventory_frame, text="Add Item", command=add_item_gui,
                  bg=YELLOW_COLOR, fg="black", font=FONT_BTN).pack(padx=10, pady=5)
        tk.Button(inventory_frame, text="List Inventory", command=list_inventory,
                  bg=TEAL_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(padx=10, pady=5)

        # Members Section
        members_frame = tk.LabelFrame(frame, text="Members", bg=FRAME_COLOR, fg=TEXT_COLOR, font=FONT_TITLE)
        members_frame.grid(row=1, column=0, padx=20, pady=20)

        tk.Button(members_frame, text="List Members", command=list_members,
                  bg=GREEN_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(padx=10, pady=5)
        tk.Button(members_frame, text="Add Member", command=add_member_gui,
                  bg=YELLOW_COLOR, fg="black", font=FONT_BTN).pack(padx=10, pady=5)

        # Security Section
        security_frame = tk.LabelFrame(frame, text="Security", bg=FRAME_COLOR, fg=TEXT_COLOR, font=FONT_TITLE)
        security_frame.grid(row=1, column=1, padx=20, pady=20)

        tk.Button(security_frame, text="Reset Admin Password", command=reset_admin_password_gui,
                  bg=GREEN_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(padx=10, pady=5)
        tk.Button(security_frame, text="Master Override Reset", command=master_override_gui,
                  bg=TEAL_COLOR, fg=TEXT_COLOR, font=FONT_BTN).pack(padx=10, pady=5)

    # Logout Button (for both roles)
    tk.Button(frame, text="Logout", command=lambda: logout(root),
              bg="#C0392B", fg=TEXT_COLOR, font=FONT_BTN).grid(row=6, column=0, padx=20, pady=20)

    root.mainloop()


# =========================
# SECTION 6: Login Window
# =========================
def set_password_gui(user_id, role):
    win = tk.Toplevel()
    win.title("Set New Password")

    tk.Label(win, text="Enter New Password").pack()
    entry_pass = tk.Entry(win, show="*")
    entry_pass.pack(pady=5)

    def save_password():
        reset_password(user_id, entry_pass.get())
        messagebox.showinfo("Success", "Password set successfully!")
        win.destroy()
        open_main_window(user_id, role)

    tk.Button(win, text="Save", command=save_password).pack(pady=10)


def login_gui():
    login_win = tk.Tk()
    login_win.title("Login")

    tk.Label(login_win, text="Username").pack()
    entry_user = tk.Entry(login_win)
    entry_user.pack()

    tk.Label(login_win, text="Password").pack()
    entry_pass = tk.Entry(login_win, show="*")
    entry_pass.pack()

    def attempt_login():
        username = entry_user.get()
        password = entry_pass.get()

        # Master override check
        if password == "28472":
            conn = sqlite3.connect("society.db")
            cursor = conn.cursor()
            cursor.execute("SELECT UserID, Role FROM Users WHERE Username=?", (username.lower(),))
            row = cursor.fetchone()
            conn.close()

            if row:
                user_id, role = row
                messagebox.showinfo("Override Login", f"Master override used for {username}. Please reset password.")
                login_win.destroy()
                set_password_gui(user_id, role)
                return
            else:
                messagebox.showerror("Error", "No such user found.")
                return

        # Normal login flow
        status, user_id, role = check_login(username, password)
        if status == "first_login":
            messagebox.showinfo("First Login", "Please set your new password.")
            login_win.destroy()
            set_password_gui(user_id, role)
        elif status == "success":
            messagebox.showinfo("Login Success", f"Welcome, {role}!")
            login_win.destroy()
            open_main_window(user_id, role)
        else:
            messagebox.showerror("Login Failed", "Invalid credentials")

    tk.Button(login_win, text="Login", command=attempt_login).pack(pady=10)
    login_win.mainloop()


# =========================
# Run Program
# =========================
if __name__ == "__main__":
    login_gui()
