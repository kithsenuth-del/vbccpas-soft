import sqlite3
conn = sqlite3.connect('society.db')
cur = conn.cursor()
try:
    cur.execute("PRAGMA table_info(Users)")
    rows = cur.fetchall()
    print('PRAGMA table_info(Users):')
    for r in rows:
        print(r)
    cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name='Users'")
    print('\nsqlite_master entry:')
    print(cur.fetchall())
except Exception as e:
    print('ERROR:', e)
finally:
    conn.close()
