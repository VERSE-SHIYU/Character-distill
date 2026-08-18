import sqlite3, asyncio, os
import asyncpg

uid = "3996bd7f23a34311"

# Check local SQLite in container
conn = sqlite3.connect("/app/data/character_sim.db")
cur = conn.execute("SELECT name FROM sqlite_master WHERE type=?;", ("table",))
tables = [r[0] for r in cur.fetchall()]
print("Container SQLite tables:", tables[:5])

cur = conn.execute("SELECT id, name FROM cards WHERE user_id = ?", (uid,))
cards_sqlite = [(r[0], r[1]) for r in cur.fetchall()]
print(f"\nShiyu cards in SQLite: {len(cards_sqlite)}")
for c in cards_sqlite:
    print(f"  {c[0]}: {c[1]}")
conn.close()

# Check PG
async def check_pg():
    pg = await asyncpg.connect(os.environ["DATABASE_URL"])
    pg_cards = await pg.fetch("SELECT id, name FROM cards WHERE user_id = $1", uid)
    pg_ids = [r["id"] for r in pg_cards]
    print(f"\nShiyu_ss cards in PG: {len(pg_cards)}")

    missing = [(c_id, c_name) for c_id, c_name in cards_sqlite if c_id not in pg_ids]
    if missing:
        print(f"\nMISSING cards: {missing}")
        for mid, mn in missing:
            row = await pg.fetchrow("SELECT id, user_id FROM cards WHERE id = $1", mid)
            if row:
                print(f"  Card {mid} ({mn}) found but owned by user {row['user_id']}")
            else:
                print(f"  Card {mid} ({mn}) not found at all in PG")
    else:
        print("\nAll 15 cards match!")

    await pg.close()

asyncio.run(check_pg())
