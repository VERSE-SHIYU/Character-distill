import asyncio, os, json
import asyncpg
from pwdlib import PasswordHash

async def verify():
    pg = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid = "3996bd7f23a34311"

    # 1. User exists
    user = await pg.fetchrow("SELECT id, username, is_admin FROM users WHERE id = $1", uid)
    print(f"User: {dict(user)}")

    # 2. Password correct
    secret = await pg.fetchrow("SELECT password_hash FROM user_secrets WHERE user_id = $1", uid)
    ph = PasswordHash.recommended()
    print(f"Password verify ('test1234'): {ph.verify('test1234', secret['password_hash'])}")

    # 3. Count data by table
    tables = [
        "texts", "cards", "sessions", "messages", "direct_messages",
        "card_comments", "card_versions", "user_posts", "post_comments",
        "user_follows", "group_sessions", "group_messages", "usage_stats",
        "reading_progress", "invite_codes", "refresh_tokens",
    ]
    total = 0
    for t in tables:
        if t == "direct_messages":
            sql = "SELECT COUNT(*) FROM {} WHERE sender_id=$1 OR receiver_id=$1".format(t)
        elif t == "user_follows":
            sql = "SELECT COUNT(*) FROM {} WHERE follower_id=$1 OR following_id=$1".format(t)
        elif t == "messages":
            sql = "SELECT COUNT(*) FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE user_id=$1)"
        elif t == "group_messages":
            sql = "SELECT COUNT(*) FROM group_messages WHERE group_id IN (SELECT id FROM group_sessions WHERE user_id=$1)"
        elif t == "invite_codes":
            sql = "SELECT COUNT(*) FROM invite_codes WHERE created_by=$1"
        else:
            sql = "SELECT COUNT(*) FROM {} WHERE user_id=$1".format(t)
        cnt = await pg.fetchval(sql, uid)
        if cnt and cnt > 0:
            print(f"  {t}: {cnt}")
            total += cnt

    print(f"\n  TOTAL: {total} rows")
    await pg.close()

asyncio.run(verify())
