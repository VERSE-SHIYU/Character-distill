"""Fix card ID collision — reassign a new UUID to the card that conflicted with another user's card."""
import asyncio, os, uuid, json
from datetime import datetime
import asyncpg

OLD_ID = "5de273219075"
NEW_ID = uuid.uuid4().hex[:12]
UID = "3996bd7f23a34311"

async def fix():
    pg = await asyncpg.connect(os.environ["DATABASE_URL"])

    print(f"Reassigning card {OLD_ID} -> {NEW_ID}")

    with open("/tmp/shiyu_export.json") as f:
        export = json.load(f)

    card = None
    for c in export["cards"]:
        if c["id"] == OLD_ID:
            card = dict(c)
            break

    if not card:
        print("Card not found in export!")
        return

    card["id"] = NEW_ID
    card["user_id"] = UID

    # Build insert
    cols = [
        "id", "text_id", "name", "card_json", "user_id", "avatar_data",
        "voice_ref_json", "visibility", "forked_from", "likes",
        "market_description", "market_tags", "publish_message",
        "updated_at", "deleted_at", "created_at", "cross_border_synced",
    ]
    vals = [card.get(c) for c in cols]

    # Parse timestamps
    for i, v in enumerate(vals):
        if isinstance(v, str) and cols[i] in ("created_at", "updated_at"):
            try:
                vals[i] = datetime.fromisoformat(v)
            except ValueError:
                pass

    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    sql = f"INSERT INTO cards ({', '.join(cols)}) OVERRIDING SYSTEM VALUE VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"
    await pg.execute(sql, *vals)
    print(f"Card inserted: {NEW_ID}")

    # Update child references
    await pg.execute("UPDATE card_versions SET card_id = $1 WHERE card_id = $2 AND user_id = $3", NEW_ID, OLD_ID, UID)
    print("card_versions updated")

    await pg.execute("UPDATE sessions SET card_id = $1 WHERE card_id = $2 AND user_id = $3", NEW_ID, OLD_ID, UID)
    print("sessions updated")

    await pg.execute("UPDATE card_comments SET card_id = $1 WHERE card_id = $2", NEW_ID, OLD_ID)
    print("card_comments updated")

    await pg.execute("UPDATE card_likes SET card_id = $1 WHERE card_id = $2", NEW_ID, OLD_ID)
    print("card_likes updated")

    # Final count
    cnt = await pg.fetchval("SELECT COUNT(*) FROM cards WHERE user_id = $1", UID)
    print(f"\nShiyu_ss cards now: {cnt}")

    await pg.close()
    print("\nOK")

asyncio.run(fix())
