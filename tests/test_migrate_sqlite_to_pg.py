#!/usr/bin/env python3
"""Integration test for migrate_sqlite_to_pg.py.

Creates a temporary SQLite DB with known data (including a user with
password_hash), runs the migration into a real PG, and verifies:
  - PG user_secrets has the password_hash for that user
  - PG users does NOT have a password_hash column
  - Row counts match expected
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import asyncpg

# 将项目根加入 path 以便导入迁移脚本
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.migrate_sqlite_to_pg import run_migration  # noqa: E402

PG_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://charsim:REDACTED@localhost:5432/charsim",
)


def make_test_sqlite() -> str:
    """Create a temporary SQLite DB with a user that has password_hash.

    Returns the path to the temporary DB file.
    """
    db_path = os.path.join(
        tempfile.gettempdir(),
        f"test_migrate_{uuid.uuid4().hex[:8]}.db",
    )
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")

    # ── users 表（旧 schema：password_hash 在 users 里） ──
    conn.execute("""
        CREATE TABLE users (
            id              TEXT PRIMARY KEY,
            username        TEXT NOT NULL,
            password_hash   TEXT NOT NULL,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            is_admin        INTEGER DEFAULT 0,
            is_disabled     INTEGER DEFAULT 0,
            api_key         TEXT DEFAULT '',
            base_url        TEXT DEFAULT 'https://api.deepseek.com',
            model           TEXT DEFAULT 'deepseek-v4-pro',
            avatar_data     TEXT DEFAULT '',
            email           TEXT DEFAULT '',
            email_verified  INTEGER DEFAULT 0,
            profile_stats_visible INTEGER DEFAULT 1,
            cards_visible   INTEGER NOT NULL DEFAULT 1,
            books_visible   INTEGER NOT NULL DEFAULT 1,
            banner_data     TEXT DEFAULT '',
            last_login_at   TEXT DEFAULT '',
            last_active_at  TEXT DEFAULT '',
            bio             TEXT DEFAULT '',
            following_visible INTEGER NOT NULL DEFAULT 1,
            presence_visibility TEXT NOT NULL DEFAULT 'friends',
            embedding_key   TEXT DEFAULT '',
            embedding_region TEXT DEFAULT 'cn'
        )
    """)

    # ── texts 表（cards 的父表） ──
    conn.execute("""
        CREATE TABLE texts (
            id              TEXT PRIMARY KEY,
            filename        TEXT NOT NULL,
            title           TEXT DEFAULT '',
            description     TEXT DEFAULT '',
            content         TEXT NOT NULL,
            char_count      INTEGER NOT NULL,
            text_type       TEXT DEFAULT 'story',
            original_char_count INTEGER DEFAULT NULL,
            characters_json TEXT DEFAULT NULL,
            user_id         TEXT DEFAULT NULL,
            visibility      TEXT DEFAULT 'private',
            content_resolved TEXT DEFAULT '',
            coref_resolved  INTEGER DEFAULT 0,
            cover_data      TEXT DEFAULT '',
            deleted_at      TEXT DEFAULT '',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── cards 表 ──
    conn.execute("""
        CREATE TABLE cards (
            id              TEXT PRIMARY KEY,
            text_id         TEXT,
            name            TEXT NOT NULL,
            card_json       TEXT NOT NULL,
            user_id         TEXT DEFAULT NULL,
            avatar_data     TEXT DEFAULT '',
            voice_ref_json  TEXT DEFAULT '',
            visibility      TEXT DEFAULT 'private',
            forked_from     TEXT DEFAULT '',
            likes           INTEGER DEFAULT 0,
            market_description TEXT DEFAULT '',
            market_tags     TEXT DEFAULT '',
            publish_message TEXT DEFAULT '',
            updated_at      TEXT DEFAULT '',
            deleted_at      TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ts = "2026-06-01 00:00:00"

    # 插入一个用户（带 password_hash）
    conn.execute(
        """INSERT INTO users (id, username, password_hash, api_key, base_url, model,
                              avatar_data, email, created_at, cards_visible, books_visible,
                              following_visible, presence_visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 'friends')""",
        (
            "test_user_1", "testuser",
            "$2b$12$TEST_HASH_1234567890123456789012345678901234567890",
            "sk-test-api-key-12345",
            "https://api.deepseek.com",
            "deepseek-v4-pro",
            "", "test@example.com", ts,
        ),
    )
    conn.commit()

    # 插入一个公开卡片
    conn.execute(
        """INSERT INTO cards (id, text_id, name, card_json, user_id, visibility, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("test_card_1", "test_text_1", "Test Card", '{"name": "Test"}',
         "test_user_1", "public", ts),
    )

    # 插入一个文本记录（cards 依赖 texts）
    conn.execute(
        """INSERT INTO texts (id, filename, content, char_count, user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("test_text_1", "test.txt", "Hello world", 11, "test_user_1", ts),
    )

    # 插入更多用户来测试批量迁移
    conn.execute(
        """INSERT INTO users (id, username, password_hash, api_key, cards_visible,
                              books_visible, following_visible, presence_visibility, created_at)
           VALUES (?, ?, ?, ?, 1, 1, 1, 'friends', ?)""",
        ("test_user_2", "user2", "hash2", "key2", ts),
    )
    conn.execute(
        """INSERT INTO users (id, username, password_hash, api_key, cards_visible,
                              books_visible, following_visible, presence_visibility, created_at)
           VALUES (?, ?, ?, ?, 1, 1, 1, 'friends', ?)""",
        ("test_user_3", "user3", "hash3", "key3", ts),
    )

    conn.commit()
    conn.close()
    return db_path


async def apply_pg_migrations(pg: asyncpg.Connection) -> None:
    """Apply all PG migration SQL files so the target schema is up to date."""
    migrations_dir = Path(__file__).resolve().parent.parent / "storage" / "migrations_pg"
    for sql_path in sorted(migrations_dir.glob("*.sql")):
        sql = sql_path.read_text(encoding="utf-8")
        try:
            await pg.execute(sql)
        except Exception as exc:
            print(f"  Migration {sql_path.name}: {exc}")


async def clean_pg_tables(pg: asyncpg.Connection) -> None:
    """清空迁移目标表以便重复测试."""
    tables = [
        "user_secrets", "users", "cards", "texts",
    ]
    for t in tables:
        try:
            await pg.execute(f"DELETE FROM {t}")
        except asyncpg.UndefinedTableError:
            pass  # table doesn't exist yet — skip


async def verify_migration(pg: asyncpg.Connection) -> None:
    """验证迁移结果."""
    errors = []

    # 1. PG users 应该有 3 行
    user_count = await pg.fetchval("SELECT COUNT(*) FROM users")
    if user_count != 3:
        errors.append(f"users count: expected 3, got {user_count}")

    # 2. PG user_secrets 应该有 3 行
    secrets_count = await pg.fetchval("SELECT COUNT(*) FROM user_secrets")
    if secrets_count != 3:
        errors.append(f"user_secrets count: expected 3, got {secrets_count}")

    # 3. pg_user 的 password_hash 必须可在 user_secrets 查到
    pw_hash = await pg.fetchval(
        "SELECT password_hash FROM user_secrets WHERE user_id = 'test_user_1'",
    )
    if pw_hash != "$2b$12$TEST_HASH_1234567890123456789012345678901234567890":
        errors.append(
            f"user_secrets.password_hash mismatch: got {pw_hash!r}"
        )

    # 4. PG users 表没有 password_hash 列（已被 data_residency 迁移删掉）
    cols = await pg.fetch(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name = 'users' AND column_name = 'password_hash'""",
    )
    if cols:
        errors.append("users table still has password_hash column - migration 005 not applied?")

    # 5. user_secrets 的其他字段
    api_key = await pg.fetchval(
        "SELECT api_key FROM user_secrets WHERE user_id = 'test_user_1'",
    )
    if api_key != "sk-test-api-key-12345":
        errors.append(f"user_secrets.api_key mismatch: got {api_key!r}")

    # 6. cards 迁移
    card_count = await pg.fetchval("SELECT COUNT(*) FROM cards")
    if card_count != 1:
        errors.append(f"cards count: expected 1, got {card_count}")

    # 7. home_region 默认值
    region = await pg.fetchval(
        "SELECT home_region FROM users WHERE id = 'test_user_1'",
    )
    if region != "cn-shenzhen":
        errors.append(f"home_region: expected 'cn-shenzhen', got {region!r}")

    if errors:
        print("FAILED assertions:")
        for e in errors:
            print(f"   FAILED {e}")
        return False
    return True


async def main() -> int:
    print("=" * 60)
    print("  Integration test: SQLite → PG migration")
    print("=" * 60)

    # ── 1. 创建临时 SQLite ──
    sqlite_path = make_test_sqlite()
    print(f"\n[1/5] Created test SQLite: {sqlite_path}")

    # ── 2. 连接 PG 并应用迁移 ──
    print(f"[2/5] Connecting to PG: {PG_URL}")
    pg = await asyncpg.connect(PG_URL)
    print("  Applying PG migrations...")
    await apply_pg_migrations(pg)
    await clean_pg_tables(pg)
    print("  Cleaned target tables")

    # ── 3. Dry-run ──
    print("[3/5] Dry-run (should report counts, write nothing)")
    code = await run_migration(
        sqlite_path=sqlite_path,
        pg_url=PG_URL,
        dry_run=True,
    )
    if code != 0:
        print("   FAILED Dry-run failed")
        return 1
    # Verify dry-run wrote nothing
    after_dry = await pg.fetchval("SELECT COUNT(*) FROM users")
    assert after_dry == 0, f"Dry-run wrote {after_dry} users!"
    print("  OK Dry-run wrote nothing (users=0)")

    # ── 4. Actual migration ──
    print("[4/5] Running actual migration")
    code = await run_migration(
        sqlite_path=sqlite_path,
        pg_url=PG_URL,
        dry_run=False,
    )
    if code != 0:
        print("   FAILED Migration failed")
        return 1

    # ── 5. Verify ──
    print("[5/5] Verifying migration results")
    ok = await verify_migration(pg)
    if ok:
        print("\n  OK All assertions passed!")
        print("  OK password_hash correctly in user_secrets")
        print("  OK users table has no password_hash column")
        print("  OK home_region defaults to 'cn-shenzhen'")
        print("  OK All rows migrated with correct counts")
    else:
        print("\n   FAILED Some assertions failed")
        await pg.close()
        os.unlink(sqlite_path)
        return 1

    await pg.close()
    os.unlink(sqlite_path)
    return 0


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
