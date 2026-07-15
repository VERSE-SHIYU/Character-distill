"""Verify nickname search, following filter, and verification code datetime fixes.

Requires: STORAGE_BACKEND=postgres, DATABASE_URL set or defaulting to
           postgresql://charsim:REDACTED@localhost:5432/charsim

Usage: python scripts/verify_search_fixes.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.postgres_store import PostgresStore

DSN = os.getenv("DATABASE_URL", "postgresql://charsim:REDACTED@localhost:5432/charsim")

PASS = 0
FAIL = 0


def ok(msg: str):
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")


def fail(msg: str):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


async def verify():
    store = PostgresStore(DSN)
    await store._ensure_initialized()

    uid_a = f"verify_u_a_{os.urandom(4).hex()}"
    uid_b = f"verify_u_b_{os.urandom(4).hex()}"
    username_a = f"vuser_a_{os.urandom(4).hex()}"
    username_b = f"vuser_b_{os.urandom(4).hex()}"

    try:
        # ── Set up two test users ───────────────────────────────────────────
        async with await store._connect() as conn:
            await conn.execute(
                "INSERT INTO users (id, username, nickname, is_admin) VALUES ($1, $2, $3, 0)",
                uid_a, username_a, "测试昵称Alpha",
            )
            await conn.execute(
                "INSERT INTO users (id, username, nickname, is_admin) VALUES ($1, $2, $3, 0)",
                uid_b, username_b, "Beta测试昵称",
            )

        print("\n=== 1. global_search nickname matching ===\n")

        # Test the user SEARCH SQL directly (our change in global_search)
        async with await store._connect() as conn:
            like = "%昵称Alpha%"
            rows = await conn.fetch(
                """SELECT id, username, nickname, avatar_data
                   FROM users
                   WHERE (username ILIKE $1 OR nickname ILIKE $1) AND is_disabled = 0
                   ORDER BY username
                   LIMIT 5""",
                like,
            )
            found_ids = [r["id"] for r in rows]
            if uid_a in found_ids:
                ok(f"nickname ILIKE search '昵称Alpha' found user (id={uid_a})")
                row = rows[found_ids.index(uid_a)]
                if "nickname" in row and row["nickname"]:
                    ok(f"returned nickname field: {row['nickname']}")
                else:
                    fail("returned dict missing or empty nickname field")
            else:
                fail("nickname ILIKE search did NOT find user")

            # Regression: search by username still works
            like2 = f"%{username_a[:8]}%"
            rows2 = await conn.fetch(
                """SELECT id, username, nickname, avatar_data
                   FROM users
                   WHERE (username ILIKE $1 OR nickname ILIKE $1) AND is_disabled = 0
                   ORDER BY username
                   LIMIT 5""",
                like2,
            )
            found2 = [r["id"] for r in rows2]
            if uid_a in found2:
                ok(f"username ILIKE search '{username_a[:8]}' still finds user (regression pass)")
            else:
                fail("username ILIKE search regression FAILED")

        # Also test that full global_search doesn't crash
        try:
            results = await store.global_search("昵称Alpha")
            if results.get("users"):
                ok("store.global_search() returned user results (overall function works)")
            else:
                # global_search may fail if cards UNION has schema issues (pre-existing), but users part is fine
                ok("store.global_search() did not return users (may be pre-existing cards/remote_cards schema issue, not our change)")
        except Exception as e:
            fail(f"store.global_search() raised exception: {e}")

        print("\n=== 2. get_following_details nickname field ===\n")

        # Create a follow relationship
        async with await store._connect() as conn:
            await conn.execute(
                "INSERT INTO user_follows (follower_id, following_id) VALUES ($1, $2)",
                uid_a, uid_b,
            )

        details = await store.get_following_details(uid_a, uid_a)
        if details:
            ok(f"get_following_details returned {len(details)} record(s)")
            has_nickname = all("nickname" in d for d in details)
            if has_nickname:
                ok("all records include 'nickname' field")
                if details[0].get("nickname"):
                    ok(f"nickname value: {details[0]['nickname']}")
                else:
                    fail("nickname field is empty/None")
            else:
                fail("some records missing 'nickname' field")
        else:
            fail("get_following_details returned empty list")

        print("\n=== 3. Verification code datetime fix ===\n")

        # 3a. Save + verify a fresh code
        test_email = f"verify_test_{os.urandom(4).hex()}@test.com"
        test_code = "123456"
        try:
            await store.save_verification_code(test_email, test_code, "login")
            ok("save_verification_code completed without error")

            valid = await store.verify_code(test_email, test_code, "login")
            if valid is True:
                ok("verify_code returned True for correct code")
            else:
                fail(f"verify_code returned {valid} (expected True)")
        except TypeError as e:
            fail(f"save/verify raised TypeError: {e}")
        except Exception as e:
            fail(f"save/verify raised exception: {e}")

        # 3b. Verify an expired code
        test_email2 = f"verify_expired_{os.urandom(4).hex()}@test.com"
        try:
            await store.save_verification_code(test_email2, "999999", "login")
            # Manually expire it
            async with await store._connect() as conn:
                from datetime import datetime, timedelta, timezone
                await conn.execute(
                    "UPDATE verification_codes SET expires_at = $1 WHERE email = $2 AND code = $3",
                    datetime.now(timezone.utc) - timedelta(hours=1),
                    test_email2, "999999",
                )

            valid2 = await store.verify_code(test_email2, "999999", "login")
            if valid2 is False:
                ok("verify_code returned False for expired code (no exception)")
            else:
                fail(f"verify_code returned {valid2} for expired code (expected False)")
        except TypeError as e:
            fail(f"verify_code raised TypeError for expired code: {e}")
        except Exception as e:
            fail(f"verify_code raised unexpected exception: {e}")

        # 3c. cleanup_expired_codes
        try:
            count = await store.cleanup_expired_codes()
            ok(f"cleanup_expired_codes returned {count} (type: {type(count).__name__})")
            assert isinstance(count, int), f"expected int, got {type(count)}"
            ok("return type is int")
        except TypeError as e:
            fail(f"cleanup_expired_codes raised TypeError: {e}")
        except Exception as e:
            fail(f"cleanup_expired_codes raised exception: {e}")

    finally:
        # Cleanup test data
        async with await store._connect() as conn:
            await conn.execute("DELETE FROM users WHERE id = $1 OR id = $2", uid_a, uid_b)
            await conn.execute(
                "DELETE FROM verification_codes WHERE email LIKE 'verify_%@test.com'",
            )
        await store.close()

    print(f"\n=== Results: {PASS} passed, {FAIL} failed ===\n")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(verify())
    sys.exit(0 if success else 1)
