"""Assign all orphan data (no user_id) to the Shiyu user."""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "character_sim.db")

if not os.path.exists(DB_PATH):
    print(f"Database not found: {DB_PATH}")
    exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 1. Find Shiyu's user_id
cur = conn.execute("SELECT id FROM users WHERE username = ?", ("Shiyu",))
row = cur.fetchone()
if not row:
    print("User 'Shiyu' not found in users table")
    conn.close()
    exit(1)

user_id = row["id"]
print(f"Target user: Shiyu (id={user_id})")

# 2. Texts
cur = conn.execute("SELECT COUNT(*) AS cnt FROM texts WHERE user_id IS NULL OR user_id = ''")
texts_null = cur.fetchone()["cnt"]
conn.execute("UPDATE texts SET user_id = ? WHERE user_id IS NULL OR user_id = ''", (user_id,))
print(f"texts: {texts_null} rows migrated")

# 3. Cards
cur = conn.execute("SELECT COUNT(*) AS cnt FROM cards WHERE user_id IS NULL OR user_id = ''")
cards_null = cur.fetchone()["cnt"]
conn.execute("UPDATE cards SET user_id = ? WHERE user_id IS NULL OR user_id = ''", (user_id,))
print(f"cards: {cards_null} rows migrated")

# 4. Sessions
cur = conn.execute("SELECT COUNT(*) AS cnt FROM sessions WHERE user_id IS NULL OR user_id = ''")
sessions_null = cur.fetchone()["cnt"]
conn.execute("UPDATE sessions SET user_id = ? WHERE user_id IS NULL OR user_id = ''", (user_id,))
print(f"sessions: {sessions_null} rows migrated")

conn.commit()
conn.close()
print("Done.")
