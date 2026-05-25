"""SQLite implementation for StorageBase."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

try:
    import aiosqlite  # type: ignore[import-not-found]
except ModuleNotFoundError:
    aiosqlite = None  # type: ignore[assignment]

from .base import StorageBase


class _ConnectionContext:
    """Wrap an opened aiosqlite connection for `async with await ...` usage."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    async def __aenter__(self) -> Any:
        """Return already-opened connection."""
        return self.conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Always close connection on scope exit."""
        try:
            await self.conn.close()
        except Exception as close_exc:
            print(f"[SQLiteStore] Close connection failed: {close_exc}")


class SQLiteStore(StorageBase):
    """Asynchronous storage implementation based on sqlite."""

    def __init__(self, db_path: str) -> None:
        """Set database path and lazy-init state."""
        self.db_path = Path(db_path)
        self._init_lock = asyncio.Lock()
        self._initialized = False

    @staticmethod
    def _ensure_driver() -> None:
        """Validate that aiosqlite is available at runtime."""
        if aiosqlite is None:
            print("[SQLiteStore] Missing dependency: aiosqlite")
            raise ModuleNotFoundError(
                "aiosqlite is required. Please install dependencies from requirements.txt"
            )

    async def _ensure_initialized(self) -> None:
        """Create database file and run migration once."""
        self._ensure_driver()
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                migrations_dir = Path(__file__).with_name("migrations")
                migration_path = migrations_dir / "001_init.sql"
                migration_sql = migration_path.read_text(encoding="utf-8")
                voice_migration_path = migrations_dir / "002_voice.sql"
                wechat_migration_path = migrations_dir / "003_wechat.sql"
                title_desc_migration_path = migrations_dir / "004_title_desc.sql"
                characters_cache_path = migrations_dir / "005_characters_cache.sql"
                card_avatar_path = migrations_dir / "006_card_avatar.sql"
                text_type_path = migrations_dir / "007_text_type.sql"
            except OSError as exc:
                print(f"[SQLiteStore] Read migration file failed: {exc}")
                raise

            try:
                async with aiosqlite.connect(self.db_path) as conn:  # type: ignore[union-attr]
                    await conn.execute("PRAGMA foreign_keys = ON;")
                    await conn.executescript(migration_sql)
                    await conn.commit()

                    # Run 002_voice migration (ALTER TABLE may fail if column exists)
                    if voice_migration_path.exists():
                        try:
                            voice_sql = voice_migration_path.read_text(encoding="utf-8")
                            await conn.executescript(voice_sql)
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Voice migration failed: {exc}")

                    # Run 003_wechat migration (CREATE TABLE IF NOT EXISTS)
                    if wechat_migration_path.exists():
                        wechat_sql = wechat_migration_path.read_text(encoding="utf-8")
                        await conn.executescript(wechat_sql)
                        await conn.commit()

                    # Run 004_title_desc migration (ALTER TABLE may fail if column exists)
                    if title_desc_migration_path.exists():
                        try:
                            title_desc_sql = title_desc_migration_path.read_text(encoding="utf-8")
                            await conn.executescript(title_desc_sql)
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Title/desc migration failed: {exc}")

                    # Run 005_characters_cache migration (ALTER TABLE may fail if column exists)
                    if characters_cache_path.exists():
                        try:
                            characters_cache_sql = characters_cache_path.read_text(encoding="utf-8")
                            await conn.executescript(characters_cache_sql)
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Characters cache migration failed: {exc}")

                    # Run 006_card_avatar migration (ALTER TABLE may fail if column exists)
                    if card_avatar_path.exists():
                        try:
                            card_avatar_sql = card_avatar_path.read_text(encoding="utf-8")
                            await conn.executescript(card_avatar_sql)
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Card avatar migration failed: {exc}")

                    # Run 007_text_type migration (ALTER TABLE may fail if column exists)
                    if text_type_path.exists():
                        try:
                            text_type_sql = text_type_path.read_text(encoding="utf-8")
                            await conn.executescript(text_type_sql)
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Text type migration failed: {exc}")

                    # Run 008_original_char_count migration (ALTER TABLE may fail if column exists)
                    original_char_count_path = migrations_dir / "008_original_char_count.sql"
                    if original_char_count_path.exists():
                        try:
                            occ_sql = original_char_count_path.read_text(encoding="utf-8")
                            await conn.executescript(occ_sql)
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Original char count migration failed: {exc}")

                    # Run 009_users migration (CREATE TABLE IF NOT EXISTS — idempotent)
                    users_migration_path = migrations_dir / "009_users.sql"
                    if users_migration_path.exists():
                        await conn.executescript(users_migration_path.read_text(encoding="utf-8"))
                        await conn.commit()

                    # Run user_id column migrations (ALTER TABLE — may fail if column exists)
                    for mig_name in ("010_user_id_texts.sql", "011_user_id_cards.sql", "012_user_id_sessions.sql"):
                        mig_path = migrations_dir / mig_name
                        if mig_path.exists():
                            try:
                                await conn.executescript(mig_path.read_text(encoding="utf-8"))
                                await conn.commit()
                            except Exception as exc:
                                if "duplicate column" not in str(exc).lower():
                                    print(f"[SQLiteStore] Migration {mig_name} failed: {exc}")

                    # Run 013_admin migration (ALTER TABLE may fail if columns exist)
                    admin_migration_path = migrations_dir / "013_admin.sql"
                    if admin_migration_path.exists():
                        try:
                            admin_sql = admin_migration_path.read_text(encoding="utf-8")
                            await conn.executescript(admin_sql)
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Admin migration failed: {exc}")

                    # Run 014_sessions_deleted_at migration (ALTER TABLE may fail if column exists)
                    deleted_at_migration_path = migrations_dir / "014_sessions_deleted_at.sql"
                    if deleted_at_migration_path.exists():
                        try:
                            deleted_at_sql = deleted_at_migration_path.read_text(encoding="utf-8")
                            await conn.executescript(deleted_at_sql)
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Deleted_at migration failed: {exc}")

                    # Run 015_refresh_tokens migration (CREATE TABLE IF NOT EXISTS — idempotent)
                    refresh_tokens_path = migrations_dir / "015_refresh_tokens.sql"
                    if refresh_tokens_path.exists():
                        await conn.executescript(refresh_tokens_path.read_text(encoding="utf-8"))
                        await conn.commit()

                    # Run 016_usage_stats migration (CREATE TABLE IF NOT EXISTS — idempotent)
                    usage_stats_path = migrations_dir / "016_usage_stats.sql"
                    if usage_stats_path.exists():
                        await conn.executescript(usage_stats_path.read_text(encoding="utf-8"))
                        await conn.commit()

                    # Run 017_affinity migration (ALTER TABLE — may fail if columns exist)
                    affinity_migration_path = migrations_dir / "017_affinity.sql"
                    if affinity_migration_path.exists():
                        try:
                            await conn.executescript(affinity_migration_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Affinity migration failed: {exc}")

                    # Run 018_user_api_config migration (ALTER TABLE — may fail if columns exist)
                    api_config_path = migrations_dir / "018_user_api_config.sql"
                    if api_config_path.exists():
                        try:
                            await conn.executescript(api_config_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] User API config migration failed: {exc}")

                    # Run 019_usage_stats_model migration (ALTER TABLE — may fail if column exists)
                    usage_model_path = migrations_dir / "019_usage_stats_model.sql"
                    if usage_model_path.exists():
                        try:
                            await conn.executescript(usage_model_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Usage stats model migration failed: {exc}")

                    # Run 020_affinity_reason migration
                    reason_path = migrations_dir / "020_affinity_reason.sql"
                    if reason_path.exists():
                        try:
                            await conn.executescript(reason_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Affinity reason migration failed: {exc}")

                    # Run 021_user_avatar migration (ALTER TABLE may fail if column exists)
                    avatar_path = migrations_dir / "021_user_avatar.sql"
                    if avatar_path.exists():
                        try:
                            await conn.executescript(avatar_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] User avatar migration failed: {exc}")

                    # Run 022_message_retracted migration (ALTER TABLE may fail if column exists)
                    retracted_path = migrations_dir / "022_message_retracted.sql"
                    if retracted_path.exists():
                        try:
                            await conn.executescript(retracted_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Message retracted migration failed: {exc}")

                    # Run 023_user_email migration (ALTER TABLE may fail if column exists)
                    email_path = migrations_dir / "023_user_email.sql"
                    if email_path.exists():
                        try:
                            await conn.executescript(email_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] User email migration failed: {exc}")

                    # Run 024_verification_codes migration (CREATE TABLE IF NOT EXISTS)
                    vc_path = migrations_dir / "024_verification_codes.sql"
                    if vc_path.exists():
                        await conn.executescript(vc_path.read_text(encoding="utf-8"))
                        await conn.commit()

                    # Run 025_market migration (ALTER TABLE may fail if column exists)
                    market_path = migrations_dir / "025_market.sql"
                    if market_path.exists():
                        try:
                            await conn.executescript(market_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Market migration failed: {exc}")

                    # Run 026_group_sessions migration (CREATE TABLE IF NOT EXISTS + ALTER TABLE)
                    group_path = migrations_dir / "026_group_sessions.sql"
                    if group_path.exists():
                        try:
                            await conn.executescript(group_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Group sessions migration failed: {exc}")

                    # Run 027_voice_to_cards migration (ALTER TABLE cards + data migration)
                    voice_to_cards_path = migrations_dir / "027_voice_to_cards.sql"
                    if voice_to_cards_path.exists():
                        try:
                            await conn.executescript(voice_to_cards_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Voice to cards migration failed: {exc}")

                    # Run 028_comments_follows migration (CREATE TABLE IF NOT EXISTS)
                    cf_path = migrations_dir / "028_comments_follows.sql"
                    if cf_path.exists():
                        try:
                            await conn.executescript(cf_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            print(f"[SQLiteStore] Comments/follows migration failed: {exc}")

                    # Run 029_soft_delete migration (ALTER TABLE cards ADD COLUMN deleted_at)
                    sd_path = migrations_dir / "029_soft_delete_cards.sql"
                    if sd_path.exists():
                        try:
                            await conn.executescript(sd_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Soft delete migration failed: {exc}")

                    # Run 030_user_posts migration (CREATE TABLE IF NOT EXISTS)
                    up_path = migrations_dir / "030_user_posts.sql"
                    if up_path.exists():
                        try:
                            await conn.executescript(up_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            print(f"[SQLiteStore] User posts migration failed: {exc}")

                    # Run 031_text_comments migration (CREATE TABLE IF NOT EXISTS)
                    tc_path = migrations_dir / "031_text_comments.sql"
                    if tc_path.exists():
                        try:
                            await conn.executescript(tc_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            print(f"[SQLiteStore] Text comments migration failed: {exc}")

                    # Run 032_direct_messages migration (CREATE TABLE IF NOT EXISTS)
                    dm_path = migrations_dir / "032_direct_messages.sql"
                    if dm_path.exists():
                        try:
                            await conn.executescript(dm_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            print(f"[SQLiteStore] Direct messages migration failed: {exc}")

                    # Run 033_text_visibility migration (ALTER TABLE may fail if column exists)
                    tv_path = migrations_dir / "033_text_visibility.sql"
                    if tv_path.exists():
                        try:
                            await conn.executescript(tv_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Text visibility migration failed: {exc}")

                    # Run 034_post_enhancements migration (ALTER TABLE + CREATE TABLE)
                    pe_path = migrations_dir / "034_post_enhancements.sql"
                    if pe_path.exists():
                        try:
                            await conn.executescript(pe_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            print(f"[SQLiteStore] Post enhancements migration failed: {exc}")

                    # Run 035_card_updated_at migration (ALTER TABLE ADD COLUMN)
                    card_ua_path = migrations_dir / "035_card_updated_at.sql"
                    if card_ua_path.exists():
                        try:
                            await conn.executescript(card_ua_path.read_text(encoding="utf-8"))
                            await conn.commit()
                        except Exception as exc:
                            if "duplicate column" not in str(exc).lower():
                                print(f"[SQLiteStore] Card updated_at migration failed: {exc}")

                    # Auto-deduplicate: keep only the newest card per text_id+name
                    # Exclude forked cards (forked_from != '') to preserve independent copies
                    try:
                        await conn.execute("""
                            DELETE FROM cards
                            WHERE forked_from = '' AND id NOT IN (
                                SELECT id FROM (
                                    SELECT id, ROW_NUMBER() OVER (
                                        PARTITION BY text_id, name
                                        ORDER BY rowid DESC
                                    ) AS rn
                                    FROM cards
                                    WHERE forked_from = ''
                                ) WHERE rn = 1
                            )
                        """)
                        await conn.commit()
                    except Exception as exc:
                        if "no such window function" not in str(exc).lower():
                            print(f"[SQLiteStore] Dedup cards migration: {exc}")

                    # Auto-deduplicate forked cards: same forked_from+user_id+text_id, keep newest
                    try:
                        await conn.execute("""
                            DELETE FROM cards
                            WHERE forked_from != '' AND deleted_at IS NULL AND id NOT IN (
                                SELECT id FROM (
                                    SELECT id, ROW_NUMBER() OVER (
                                        PARTITION BY forked_from, user_id, text_id
                                        ORDER BY rowid DESC
                                    ) AS rn
                                    FROM cards
                                    WHERE forked_from != '' AND deleted_at IS NULL
                                ) WHERE rn = 1
                            )
                        """)
                        await conn.commit()
                    except Exception as exc:
                        if "no such window function" not in str(exc).lower():
                            print(f"[SQLiteStore] Dedup forked cards: {exc}")

                self._initialized = True
            except Exception as exc:
                print(f"[SQLiteStore] Initialize database failed: {exc}")
                raise

    async def _connect(self):
        """Open a sqlite connection and return a managed context wrapper."""
        await self._ensure_initialized()
        conn = await aiosqlite.connect(self.db_path)  # type: ignore[union-attr]
        conn.row_factory = aiosqlite.Row  # type: ignore[union-attr]
        await conn.execute("PRAGMA foreign_keys = ON;")
        return _ConnectionContext(conn)

    @staticmethod
    def _row_to_dict(row: Any) -> dict | None:
        """Convert sqlite row to dict."""
        return dict(row) if row is not None else None

    async def save_text(self, id: str, filename: str, content: str, title: str = "", description: str = "", text_type: str = "story", original_char_count: int | None = None, user_id: str = "") -> dict:
        """Save or update one text record."""
        try:
            char_count = len(content)
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO texts (id, filename, content, char_count, title, description, text_type, original_char_count, user_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        filename = excluded.filename,
                        content = excluded.content,
                        char_count = excluded.char_count,
                        title = excluded.title,
                        description = excluded.description,
                        text_type = excluded.text_type,
                        original_char_count = excluded.original_char_count,
                        user_id = excluded.user_id
                    """,
                    (id, filename, content, char_count, title, description, text_type, original_char_count, user_id),
                )
                await conn.commit()
            return await self.get_text(id) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Save text failed: {exc}")
            raise

    async def get_text(self, id: str) -> dict | None:
        """Get one text record by id."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count, user_id FROM texts WHERE id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get text failed: {exc}")
            raise

    async def list_texts(self, user_id: str = "") -> list[dict]:
        """List texts for a user in descending created order."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        """
                        SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count, visibility
                        FROM texts WHERE user_id = ?
                        ORDER BY created_at DESC
                        """, (user_id,),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count, visibility
                        FROM texts
                        ORDER BY created_at DESC
                        """
                    )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List texts failed: {exc}")
            raise

    async def save_characters(self, text_id: str, characters: list) -> None:
        """Cache identified characters for a text."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE texts SET characters_json = ? WHERE id = ?",
                    (json.dumps(characters, ensure_ascii=False), text_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save characters failed: {exc}")
            raise

    async def get_characters(self, text_id: str) -> list | None:
        """Get cached identified characters for a text, or None."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT characters_json FROM texts WHERE id = ?", (text_id,)
                )
                row = await cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return None
        except Exception as exc:
            print(f"[SQLiteStore] Get characters failed: {exc}")
            raise

    async def delete_text(self, id: str) -> bool:
        """Delete one text record."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute("DELETE FROM texts WHERE id = ?", (id,))
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete text failed: {exc}")
            raise

    async def save_card(self, id: str, text_id: str, name: str, card_json: str, user_id: str = "") -> dict:
        """Save or update one card record. Upsert by text_id+name+user_id to avoid duplicates."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id FROM cards WHERE text_id = ? AND name = ? AND user_id = ?",
                    (text_id, name, user_id),
                )
                existing = await cursor.fetchone()
                if existing:
                    existing_id = existing[0]
                    await conn.execute(
                        "UPDATE cards SET card_json = ? WHERE id = ?",
                        (card_json, existing_id),
                    )
                    await conn.commit()
                    return await self.get_card(existing_id) or {}
                else:
                    await conn.execute(
                        "INSERT INTO cards (id, text_id, name, card_json, user_id) VALUES (?, ?, ?, ?, ?)",
                        (id, text_id, name, card_json, user_id),
                    )
                    await conn.commit()
                    return await self.get_card(id) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Save card failed: {exc}")
            raise

    async def update_card(self, card_id: str, card_json: dict) -> dict:
        """Update a card's JSON content by ID."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET card_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(card_json, ensure_ascii=False), card_id),
                )
                await conn.commit()
                return await self.get_card(card_id) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Update card failed: {exc}")
            raise

    async def get_card(self, id: str) -> dict | None:
        """Get one card record by id."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, text_id, name, card_json, created_at, user_id, visibility, forked_from FROM cards WHERE id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get card failed: {exc}")
            raise

    async def get_market_card_detail(self, card_id: str, user_id: str) -> dict | None:
        """Get a single public card with author info and like status."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT c.id, c.name, c.card_json, c.user_id, c.likes, c.created_at,
                              c.avatar_data,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(t.title, '') AS text_title,
                              (SELECT COUNT(*) FROM card_comments cc WHERE cc.card_id = c.id) AS comment_count,
                              COALESCE(u.avatar_data, '') AS author_avatar
                        FROM cards c
                        LEFT JOIN users u ON u.id = c.user_id
                        LEFT JOIN texts t ON t.id = c.text_id
                        WHERE c.id = ? AND c.visibility = 'public' AND c.deleted_at IS NULL""",
                    (card_id,),
                )
                row = await cursor.fetchone()
                card = dict(row) if row else None
                if card:
                    like_cursor = await conn.execute(
                        "SELECT 1 FROM card_likes WHERE card_id = ? AND user_id = ?",
                        (card_id, user_id),
                    )
                    card["liked_by_me"] = await like_cursor.fetchone() is not None
            return card
        except Exception as exc:
            print(f"[SQLiteStore] Get market card detail failed: {exc}")
            return None

    async def list_cards(self, text_id: str, user_id: str = "") -> list[dict]:
        """List all cards under one text id, optionally filtered by user."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        "SELECT id, text_id, name, card_json, created_at, visibility, forked_from FROM cards WHERE text_id = ? AND user_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
                        (text_id, user_id),
                    )
                else:
                    cursor = await conn.execute(
                        "SELECT id, text_id, name, card_json, created_at, visibility, forked_from FROM cards WHERE text_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
                        (text_id,),
                    )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List cards failed: {exc}")
            raise

    async def list_standalone_cards(self, user_id: str) -> list[dict]:
        """List cards with no text_id attachment (standalone/market-forked)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, text_id, name, card_json, created_at, visibility, forked_from FROM cards WHERE (text_id IS NULL OR text_id = '') AND user_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List standalone cards failed: {exc}")
            raise

    async def save_card_avatar(self, card_id: str, avatar_data: str) -> None:
        """Save base64 avatar image for a card."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET avatar_data = ? WHERE id = ?",
                    (avatar_data, card_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save card avatar failed: {exc}")
            raise

    async def get_card_avatar(self, card_id: str) -> str | None:
        """Get base64 avatar image for a card, or None."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT avatar_data FROM cards WHERE id = ?",
                    (card_id,),
                )
                row = await cursor.fetchone()
                if row and row[0]:
                    return row[0]
                return None
        except Exception as exc:
            print(f"[SQLiteStore] Get card avatar failed: {exc}")
            return None

    # ── Market / public card methods ──────────────────────────

    async def list_public_cards(self, page: int = 1, page_size: int = 20, sort: str = "new") -> list[dict]:
        """List public cards with pagination and sorting (hot=likes, new=created_at)."""
        try:
            order = "c.likes DESC, c.created_at DESC" if sort == "hot" else "c.created_at DESC"
            offset = (page - 1) * page_size
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    f"""SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data,
                              c.forked_from, c.likes, c.created_at,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(t.title, '') AS text_title,
                              (SELECT COUNT(*) FROM card_comments cc WHERE cc.card_id = c.id) AS comment_count
                        FROM cards c
                        LEFT JOIN users u ON u.id = c.user_id
                        LEFT JOIN texts t ON t.id = c.text_id
                        WHERE c.visibility = 'public' AND c.deleted_at IS NULL
                        ORDER BY {order}
                        LIMIT ? OFFSET ?""",
                    (page_size, offset),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List public cards failed: {exc}")
            raise

    async def list_public_cards_total(self) -> int:
        """Return total count of public cards (for pagination)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM cards WHERE visibility = 'public' AND deleted_at IS NULL"
                )
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] List public cards total failed: {exc}")
            return 0

    async def search_public_cards(self, keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
        """Search public cards by name match (case-insensitive)."""
        try:
            offset = (page - 1) * page_size
            pattern = f"%{keyword}%"
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data,
                              c.forked_from, c.likes, c.created_at,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(t.title, '') AS text_title
                        FROM cards c
                        LEFT JOIN users u ON u.id = c.user_id
                        LEFT JOIN texts t ON t.id = c.text_id
                        WHERE c.visibility = 'public' AND c.name LIKE ? AND c.deleted_at IS NULL
                        ORDER BY c.likes DESC, c.created_at DESC
                        LIMIT ? OFFSET ?""",
                    (pattern, page_size, offset),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Search public cards failed: {exc}")
            raise

    async def search_public_cards_total(self, keyword: str) -> int:
        """Return total count of matching public cards."""
        try:
            pattern = f"%{keyword}%"
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM cards WHERE visibility = 'public' AND name LIKE ? AND deleted_at IS NULL",
                    (pattern,),
                )
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] Search public cards total failed: {exc}")
            return 0

    async def fork_card(self, card_id: str, new_id: str, new_user_id: str, new_text_id: str = "") -> dict | None:
        """Deep copy a public card for a new user. Returns the new card dict."""
        original = await self.get_card(card_id)
        if not original:
            return None
        # Verify the original is public
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT visibility FROM cards WHERE id = ? AND deleted_at IS NULL", (card_id,)
                )
                row = await cursor.fetchone()
                if not row or row[0] != "public":
                    return None
        except Exception as exc:
            print(f"[SQLiteStore] Fork card visibility check failed: {exc}")
            return None

        try:
            text_id = new_text_id if new_text_id is not None else original.get("text_id", "")
            async with await self._connect() as conn:
                # Check for existing fork to avoid duplicates
                cursor = await conn.execute(
                    "SELECT id FROM cards WHERE forked_from = ? AND user_id = ? AND text_id = ? AND deleted_at IS NULL",
                    (card_id, new_user_id, text_id),
                )
                existing = await cursor.fetchone()
                if existing:
                    return await self.get_card(existing[0])
                await conn.execute(
                    """INSERT INTO cards (id, text_id, name, card_json, user_id, avatar_data, forked_from, visibility)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'private')""",
                    (new_id, text_id, original["name"],
                     original.get("card_json", "{}"), new_user_id,
                     await self.get_card_avatar(card_id) or "", card_id),
                )
                await conn.commit()
            return await self.get_card(new_id)
        except Exception as exc:
            print(f"[SQLiteStore] Fork card failed: {exc}")
            raise

    async def toggle_like(self, card_id: str, user_id: str) -> dict:
        """Toggle like status. Returns {'liked': bool, 'likes': int}."""
        try:
            async with await self._connect() as conn:
                # Check if already liked
                cursor = await conn.execute(
                    "SELECT 1 FROM card_likes WHERE user_id = ? AND card_id = ?",
                    (user_id, card_id),
                )
                liked = await cursor.fetchone() is not None

                if liked:
                    await conn.execute(
                        "DELETE FROM card_likes WHERE user_id = ? AND card_id = ?",
                        (user_id, card_id),
                    )
                    await conn.execute(
                        "UPDATE cards SET likes = max(0, likes - 1) WHERE id = ?",
                        (card_id,),
                    )
                else:
                    await conn.execute(
                        "INSERT INTO card_likes (user_id, card_id) VALUES (?, ?)",
                        (user_id, card_id),
                    )
                    await conn.execute(
                        "UPDATE cards SET likes = likes + 1 WHERE id = ?",
                        (card_id,),
                    )
                await conn.commit()

                # Read updated likes count
                cursor = await conn.execute(
                    "SELECT likes FROM cards WHERE id = ?", (card_id,)
                )
                row = await cursor.fetchone()
                new_count = row[0] if row else 0
            return {"liked": not liked, "likes": new_count}
        except Exception as exc:
            print(f"[SQLiteStore] Toggle like failed: {exc}")
            raise

    async def delete_card(self, card_id: str) -> bool:
        """Soft delete: set deleted_at timestamp."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET deleted_at = datetime('now') WHERE id = ? AND deleted_at IS NULL",
                    (card_id,),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Delete card failed: {exc}")
            return False

    async def restore_card(self, card_id: str) -> bool:
        """Restore a soft-deleted card."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET deleted_at = NULL WHERE id = ?",
                    (card_id,),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Restore card failed: {exc}")
            return False

    async def purge_card(self, card_id: str) -> bool:
        """Permanently delete a card (hard delete)."""
        try:
            async with await self._connect() as conn:
                await conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Purge card failed: {exc}")
            return False

    async def list_deleted_cards(self, user_id: str) -> list[dict]:
        """List soft-deleted cards for a user (recycle bin)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, text_id, name, card_json, created_at, visibility, forked_from, deleted_at FROM cards WHERE deleted_at IS NOT NULL AND user_id = ? ORDER BY deleted_at DESC",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List deleted cards failed: {exc}")
            raise

    async def update_card_visibility(self, card_id: str, visibility: str) -> bool:
        """Set card visibility to 'public' or 'private'."""
        if visibility not in ("public", "private"):
            return False
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET visibility = ? WHERE id = ?",
                    (visibility, card_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Update card visibility failed: {exc}")
            return False

    async def get_liked_card_ids(self, user_id: str) -> list[str]:
        """Return all card IDs the user has liked (for frontend highlight)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT card_id FROM card_likes WHERE user_id = ?", (user_id,)
                )
                rows = await cursor.fetchall()
            return [r[0] for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get liked card ids failed: {exc}")
            return []

    async def get_recent_card_session(self, card_id: str, exclude_id: str = "") -> dict | None:
        """Get the most recent session for a card (excluding a given session id)."""
        try:
            async with await self._connect() as conn:
                if exclude_id:
                    cursor = await conn.execute(
                        """
                        SELECT id FROM sessions
                        WHERE card_id = ? AND id != ?
                        ORDER BY updated_at DESC LIMIT 1
                        """,
                        (card_id, exclude_id),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT id FROM sessions
                        WHERE card_id = ?
                        ORDER BY updated_at DESC LIMIT 1
                        """,
                        (card_id,),
                    )
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return None
        except Exception as exc:
            print(f"[SQLiteStore] Get recent card session failed: {exc}")
            return None

    async def save_session(
        self, id: str, card_id: str, user_role: str, avatar_data: str, user_id: str = ""
    ) -> dict:
        """Save or update one session record."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO sessions (id, card_id, user_role, avatar_data, user_id)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        card_id = excluded.card_id,
                        user_role = excluded.user_role,
                        avatar_data = excluded.avatar_data,
                        user_id = excluded.user_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (id, card_id, user_role, avatar_data, user_id),
                )
                await conn.commit()
            return await self.get_session(id) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Save session failed: {exc}")
            raise

    async def get_session(self, id: str) -> dict | None:
        """Get one session with character name."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, s.user_id, c.text_id, c.name AS character_name
                    FROM sessions s
                    JOIN cards c ON s.card_id = c.id
                    WHERE s.id = ?
                    """,
                    (id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get session failed: {exc}")
            raise

    async def list_sessions(
        self, keyword: str, character: str, text_id: str, page: int, page_size: int, user_id: str = ""
    ) -> dict:
        """List sessions with filters, pagination and total."""
        safe_page = max(page, 1)
        safe_page_size = max(page_size, 1)
        offset = (safe_page - 1) * safe_page_size

        where_clauses: list[str] = ["s.deleted_at IS NULL"]
        params: list[Any] = []
        if user_id:
            where_clauses.append("s.user_id = ?")
            params.append(user_id)
        if character:
            where_clauses.append("c.name = ?")
            params.append(character)
        if text_id:
            where_clauses.append("c.text_id = ?")
            params.append(text_id)
        if keyword:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM messages m2
                    WHERE m2.session_id = s.id
                      AND m2.content LIKE ?
                )
                """
            )
            params.append(f"%{keyword}%")
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        try:
            async with await self._connect() as conn:
                count_cursor = await conn.execute(
                    f"""
                    SELECT COUNT(*) AS total
                    FROM sessions s
                    JOIN cards c ON s.card_id = c.id
                    {where_sql}
                    """,
                    tuple(params),
                )
                total_row = await count_cursor.fetchone()
                total = int(total_row["total"]) if total_row else 0

                list_cursor = await conn.execute(
                    f"""
                    SELECT
                        s.id,
                        s.card_id,
                        s.user_role,
                        s.avatar_data,
                        s.created_at,
                        s.updated_at,
                        c.text_id,
                        c.name AS character_name,
                        (
                            SELECT content
                            FROM messages m3
                            WHERE m3.session_id = s.id
                            ORDER BY m3.id DESC
                            LIMIT 1
                        ) AS last_message,
                        (
                            SELECT created_at
                            FROM messages m4
                            WHERE m4.session_id = s.id
                            ORDER BY m4.id DESC
                            LIMIT 1
                        ) AS last_message_at
                    FROM sessions s
                    JOIN cards c ON s.card_id = c.id
                    {where_sql}
                    ORDER BY COALESCE(last_message_at, s.updated_at, s.created_at) DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple([*params, safe_page_size, offset]),
                )
                rows = await list_cursor.fetchall()

            return {
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
                "items": [dict(row) for row in rows],
            }
        except Exception as exc:
            print(f"[SQLiteStore] List sessions failed: {exc}")
            raise

    async def delete_session(self, id: str) -> bool:
        """Soft-delete one session (set deleted_at timestamp)."""
        try:
            from datetime import datetime
            now = datetime.now().isoformat()
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE sessions SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                    (now, id),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete session failed: {exc}")
            raise

    async def clear_all_sessions(self, user_id: str = "") -> int:
        """Soft-delete all non-deleted sessions. Returns count of affected sessions."""
        try:
            from datetime import datetime
            now = datetime.now().isoformat()
            async with await self._connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        "UPDATE sessions SET deleted_at = ? WHERE deleted_at IS NULL AND user_id = ?",
                        (now, user_id),
                    )
                else:
                    cursor = await conn.execute(
                        "UPDATE sessions SET deleted_at = ? WHERE deleted_at IS NULL",
                        (now,),
                    )
                await conn.commit()
                return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Clear all sessions failed: {exc}")
            raise

    async def list_trash_sessions(self, user_id: str = "") -> list[dict]:
        """List soft-deleted sessions (in trash)."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        """
                        SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, s.deleted_at,
                               c.text_id, c.name AS character_name
                        FROM sessions s
                        JOIN cards c ON s.card_id = c.id
                        WHERE s.deleted_at IS NOT NULL AND s.user_id = ?
                        ORDER BY s.deleted_at DESC
                        """,
                        (user_id,),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, s.deleted_at,
                               c.text_id, c.name AS character_name
                        FROM sessions s
                        JOIN cards c ON s.card_id = c.id
                        WHERE s.deleted_at IS NOT NULL
                        ORDER BY s.deleted_at DESC
                        """
                    )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List trash sessions failed: {exc}")
            raise

    async def restore_session(self, id: str) -> bool:
        """Restore a soft-deleted session (clear deleted_at)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE sessions SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
                    (id,),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Restore session failed: {exc}")
            raise

    async def purge_trash(self, user_id: str = "") -> int:
        """Permanently delete all soft-deleted sessions. Returns count."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        "DELETE FROM sessions WHERE deleted_at IS NOT NULL AND user_id = ?",
                        (user_id,),
                    )
                else:
                    cursor = await conn.execute(
                        "DELETE FROM sessions WHERE deleted_at IS NOT NULL",
                    )
                await conn.commit()
                return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Purge trash failed: {exc}")
            raise

    async def hard_delete_session(self, id: str) -> bool:
        """Permanently delete one session (hard delete, for trash purge of single item)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute("DELETE FROM sessions WHERE id = ?", (id,))
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Hard delete session failed: {exc}")
            raise

    async def update_session_voice_ref(self, card_id: str, voice_ref_json: str) -> None:
        """Update voice_ref_json on the card (not session) for voice cloning reference audio."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET voice_ref_json = ? WHERE id = ?",
                    (voice_ref_json, card_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update card voice_ref failed: {exc}")
            raise

    async def get_session_voice_ref(self, card_id: str) -> str | None:
        """Get voice_ref_json from the card (not session)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT voice_ref_json FROM cards WHERE id = ?",
                    (card_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception as exc:
            print(f"[SQLiteStore] Get card voice_ref failed: {exc}")
            raise

    # ---- WeChat user mapping ----

    async def get_wechat_user(self, openid: str) -> dict | None:
        """Get stored wechat user mapping."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT openid, session_id, card_id, created_at FROM wechat_users WHERE openid = ?",
                    (openid,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get wechat user failed: {exc}")
            raise

    async def save_wechat_user(self, openid: str, session_id: str, card_id: str) -> None:
        """Upsert wechat user mapping."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO wechat_users (openid, session_id, card_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(openid) DO UPDATE SET
                        session_id = excluded.session_id,
                        card_id = excluded.card_id
                    """,
                    (openid, session_id, card_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save wechat user failed: {exc}")
            raise

    async def save_message(
        self, session_id: str, role: str, content: str, rag_context: str
    ) -> dict:
        """Save one message and touch session updated_at."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    INSERT INTO messages (session_id, role, content, rag_context)
                    VALUES (?, ?, ?, ?)
                    """,
                    (session_id, role, content, rag_context),
                )
                await conn.execute(
                    "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (session_id,),
                )
                await conn.commit()
                message_id = int(cursor.lastrowid)

                row_cursor = await conn.execute(
                    """
                    SELECT id, session_id, role, content, rag_context, created_at
                    FROM messages
                    WHERE id = ?
                    """,
                    (message_id,),
                )
                row = await row_cursor.fetchone()
            return self._row_to_dict(row) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Save message failed: {exc}")
            raise

    async def get_messages(self, session_id: str) -> list[dict]:
        """List all messages in one session."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, session_id, role, content, rag_context, created_at
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id ASC
                    """,
                    (session_id,),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get messages failed: {exc}")
            raise

    # ── group sessions ────────────────────────────────────────────────

    async def create_group_session(
        self, id: str, name: str, card_ids: list[str], user_id: str
    ) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO group_sessions (id, name, card_ids, user_id)
                       VALUES (?, ?, ?, ?)""",
                    (id, name, json.dumps(card_ids), user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Create group session failed: {exc}")
            raise

    async def get_group_session(self, id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, name, card_ids, user_id, created_at FROM group_sessions WHERE id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            if row is None:
                return None
            d = dict(row)
            d["card_ids"] = json.loads(d["card_ids"])
            return d
        except Exception as exc:
            print(f"[SQLiteStore] Get group session failed: {exc}")
            raise

    async def list_group_sessions(self, user_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, name, card_ids, user_id, created_at
                       FROM group_sessions
                       WHERE user_id = ?
                       ORDER BY created_at DESC""",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["card_ids"] = json.loads(d["card_ids"])
                results.append(d)
            return results
        except Exception as exc:
            print(f"[SQLiteStore] List group sessions failed: {exc}")
            raise

    async def save_group_message(
        self, group_id: str, speaker: str, role: str, content: str, speaker_card_id: str = ""
    ) -> int:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """INSERT INTO group_messages (group_id, speaker, role, content, speaker_card_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (group_id, speaker, role, content, speaker_card_id),
                )
                await conn.commit()
                return cursor.lastrowid
        except Exception as exc:
            print(f"[SQLiteStore] Save group message failed: {exc}")
            raise

    async def get_group_messages(self, group_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, group_id, speaker, role, content, speaker_card_id, created_at
                       FROM group_messages
                       WHERE group_id = ?
                       ORDER BY id ASC""",
                    (group_id,),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get group messages failed: {exc}")
            raise

    async def delete_group_session(self, id: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute("DELETE FROM group_messages WHERE group_id = ?", (id,))
                await conn.execute("DELETE FROM group_sessions WHERE id = ?", (id,))
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Delete group session failed: {exc}")
            raise

    async def delete_messages_after(self, session_id: str, message_id: int) -> int:
        """Delete messages after and including one message id."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    DELETE FROM messages
                    WHERE session_id = ?
                      AND id >= ?
                    """,
                    (session_id, message_id),
                )
                await conn.execute(
                    "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (session_id,),
                )
                await conn.commit()
                return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Delete messages after failed: {exc}")
            raise

    async def export_session(self, session_id: str, format: str) -> str:
        """Export a session to json or txt content."""
        fmt = format.lower().strip()
        if fmt not in {"json", "txt"}:
            raise ValueError("format only supports json or txt")

        session = await self.get_session(session_id)
        if session is None:
            raise ValueError("session not found")
        messages = await self.get_messages(session_id)
        card = await self.get_card(session["card_id"])

        card_parsed: dict[str, Any] = {}
        if card and card.get("card_json"):
            try:
                card_parsed = json.loads(card["card_json"])
            except json.JSONDecodeError as exc:
                print(f"[SQLiteStore] Parse card_json failed: {exc}")
                card_parsed = {"raw": card["card_json"]}

        if fmt == "json":
            payload = {
                "session": session,
                "card": card_parsed,
                "messages": messages,
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        lines = [
            f"Session ID: {session['id']}",
            f"Character: {session.get('character_name', '')}",
            f"User Role: {session.get('user_role', '')}",
            "",
            "==== Messages ====",
        ]
        for msg in messages:
            lines.append(f"[{msg['role']}] {msg['content']}")
        return "\n".join(lines)

    async def create_user(self, id: str, username: str, password_hash: str, email: str = "") -> dict:
        """Create a new user. Raises on duplicate username."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO users (id, username, password_hash, email, email_verified) VALUES (?, ?, ?, ?, ?)",
                    (id, username, password_hash, email, 1 if email else 0),
                )
                await conn.commit()
                return await self.get_user_by_username(username) or {}
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise ValueError("用户名已存在") from exc
            print(f"[SQLiteStore] Create user failed: {exc}")
            raise

    async def get_user_by_username(self, username: str) -> dict | None:
        """Get a user by username."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, password_hash, is_admin, is_disabled, created_at FROM users WHERE username = ?",
                    (username,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get user failed: {exc}")
            raise

    async def get_user_by_email(self, email: str) -> dict | None:
        """Get a user by email."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, password_hash, email, email_verified, is_admin, is_disabled, created_at FROM users WHERE email = ? AND email != ''",
                    (email,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get user by email failed: {exc}")
            raise

    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Get a user by ID."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, password_hash, is_admin, is_disabled, created_at, avatar_data FROM users WHERE id = ?",
                    (user_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get user by id failed: {exc}")
            raise

    # ---- Email & verification codes ----

    async def get_user_email(self, user_id: str) -> str:
        """Get a user's verified email, empty string if none."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT email FROM users WHERE id = ?", (user_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row else ""
        except Exception as exc:
            print(f"[SQLiteStore] Get user email failed: {exc}")
            raise

    async def update_user_email(self, user_id: str, email: str) -> None:
        """Set a user's email and mark verified."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET email = ?, email_verified = 1 WHERE id = ?",
                    (email, user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update user email failed: {exc}")
            raise

    async def save_verification_code(self, email: str, code: str, purpose: str) -> None:
        """Save a verification code with 5-minute expiry."""
        import uuid as _uuid
        from datetime import datetime, timedelta, timezone
        cid = _uuid.uuid4().hex[:16]
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO verification_codes (id, email, code, purpose, expires_at) VALUES (?, ?, ?, ?, ?)",
                    (cid, email, code, purpose, expires_at),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save verification code failed: {exc}")
            raise

    async def verify_code(self, email: str, code: str, purpose: str) -> bool:
        """Verify a code. Returns True if valid, consumes it. False otherwise."""
        from datetime import datetime, timezone
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, expires_at FROM verification_codes WHERE email = ? AND code = ? AND purpose = ? AND used = 0 ORDER BY created_at DESC LIMIT 1",
                    (email, code, purpose),
                )
                row = await cursor.fetchone()
                if not row:
                    return False
                if row["expires_at"] < datetime.now(timezone.utc).isoformat():
                    return False
                # Mark as used
                await conn.execute(
                    "UPDATE verification_codes SET used = 1 WHERE id = ?",
                    (row["id"],),
                )
                await conn.commit()
                return True
        except Exception as exc:
            print(f"[SQLiteStore] Verify code failed: {exc}")
            raise

    async def cleanup_expired_codes(self) -> int:
        """Delete expired verification codes. Returns count deleted."""
        from datetime import datetime, timezone
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM verification_codes WHERE expires_at < ?",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                await conn.commit()
                return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Cleanup expired codes failed: {exc}")
            raise

    # ---- Admin ----

    async def get_all_users(self) -> list[dict]:
        """List all users (without password_hash)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, email, email_verified, is_admin, is_disabled, created_at FROM users ORDER BY created_at DESC"
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get all users failed: {exc}")
            raise

    async def set_user_admin(self, user_id: str, is_admin: bool) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Set user admin failed: {exc}")
            raise

    async def set_user_disabled(self, user_id: str, is_disabled: bool) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute("UPDATE users SET is_disabled = ? WHERE id = ?", (int(is_disabled), user_id))
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Set user disabled failed: {exc}")
            raise

    async def reset_user_password(self, user_id: str, password_hash: str) -> bool:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (password_hash, user_id),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Reset password failed: {exc}")
            raise

    # ---- User API config ----

    @staticmethod
    def _get_fernet():
        from cryptography.fernet import Fernet
        import base64
        from hashlib import sha256
        key = os.getenv("FERNET_KEY")
        if not key:
            raw = os.getenv("JWT_SECRET", "character-distill-dev-secret-key-change-in-prod").encode()
            key = base64.urlsafe_b64encode(sha256(raw).digest())
        return Fernet(key)

    async def get_user_api_config(self, user_id: str) -> dict:
        """Get a user's API config. api_key is returned decrypted."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT api_key, base_url, model FROM users WHERE id = ?",
                    (user_id,),
                )
                row = await cursor.fetchone()
            if not row:
                return {"api_key": "", "base_url": "", "model": ""}
            encrypted = row[0] or ""
            api_key = ""
            if encrypted:
                try:
                    api_key = self._get_fernet().decrypt(encrypted.encode()).decode()
                except Exception:
                    pass
            return {
                "api_key": api_key,
                "base_url": row[1] or "https://api.deepseek.com",
                "model": row[2] or "deepseek-v4-pro",
            }
        except Exception as exc:
            print(f"[SQLiteStore] Get user API config failed: {exc}")
            raise

    async def update_user_api_config(self, user_id: str, api_key: str, base_url: str, model: str) -> None:
        """Update a user's API config. api_key is encrypted before storage.

        Only updates api_key when a non-empty value is provided, so a blank
        api_key in the request does not overwrite an existing encrypted key.
        """
        try:
            async with await self._connect() as conn:
                if api_key:
                    encrypted = self._get_fernet().encrypt(api_key.encode()).decode()
                    await conn.execute(
                        "UPDATE users SET api_key = ?, base_url = ?, model = ? WHERE id = ?",
                        (encrypted, base_url, model, user_id),
                    )
                else:
                    await conn.execute(
                        "UPDATE users SET base_url = ?, model = ? WHERE id = ?",
                        (base_url, model, user_id),
                    )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update user API config failed: {exc}")
            raise

    async def update_user_avatar(self, user_id: str, avatar_data: str) -> None:
        """Store base64 avatar for a user."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET avatar_data = ? WHERE id = ?",
                    (avatar_data, user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update user avatar failed: {exc}")
            raise

    async def update_user_password(self, user_id: str, password_hash: str) -> None:
        """Update a user's password hash."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (password_hash, user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update user password failed: {exc}")
            raise

    async def get_user_avatar(self, user_id: str) -> str:
        """Get base64 avatar for a user, empty string if none."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT avatar_data FROM users WHERE id = ?", (user_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row and row[0] else ""
        except Exception as exc:
            print(f"[SQLiteStore] Get user avatar failed: {exc}")
            raise

    async def create_invite_code(self, code: str, created_by: str) -> dict:
        import uuid as _uuid
        cid = _uuid.uuid4().hex[:16]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO invite_codes (id, code, created_by) VALUES (?, ?, ?)",
                    (cid, code, created_by),
                )
                await conn.commit()
            return await self.get_invite_code(code) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Create invite code failed: {exc}")
            raise

    async def get_invite_code(self, code: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, code, created_by, used_by, used_at, created_at FROM invite_codes WHERE code = ?",
                    (code,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get invite code failed: {exc}")
            raise

    async def use_invite_code(self, code: str, used_by: str) -> None:
        from datetime import datetime
        now = datetime.now().isoformat()
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE invite_codes SET used_by = ?, used_at = ? WHERE code = ?",
                    (used_by, now, code),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Use invite code failed: {exc}")
            raise

    async def list_invite_codes(self) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, code, created_by, used_by, used_at, created_at FROM invite_codes ORDER BY created_at DESC"
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List invite codes failed: {exc}")
            raise

    async def delete_invite_code(self, code: str) -> bool:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM invite_codes WHERE code = ?", (code,)
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete invite code failed: {exc}")
            raise

    async def delete_used_invites(self) -> int:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM invite_codes WHERE used_by IS NOT NULL"
                )
                await conn.commit()
                return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Delete used invites failed: {exc}")
            raise

    # ---- Refresh tokens ----

    async def save_refresh_token(self, token_hash: str, user_id: str, expires_at: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO refresh_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                    (token_hash, user_id, expires_at),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save refresh token failed: {exc}")
            raise

    async def get_refresh_token(self, token_hash: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT token_hash, user_id, expires_at, used FROM refresh_tokens WHERE token_hash = ?",
                    (token_hash,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get refresh token failed: {exc}")
            raise

    async def mark_refresh_token_used(self, token_hash: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE refresh_tokens SET used = 1 WHERE token_hash = ?",
                    (token_hash,),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Mark refresh token used failed: {exc}")
            raise

    async def delete_user_refresh_tokens(self, user_id: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "DELETE FROM refresh_tokens WHERE user_id = ?",
                    (user_id,),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Delete user refresh tokens failed: {exc}")
            raise

    async def get_user_card_ids(self, user_id: str) -> list[str]:
        """Get all card IDs owned by a user (for Mem0 cleanup)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id FROM cards WHERE user_id = ?", (user_id,)
                )
                rows = await cursor.fetchall()
            return [row[0] for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get user card ids failed: {exc}")
            raise

    async def delete_user(self, user_id: str) -> dict:
        """Cascade-delete a user: texts → cards → sessions → messages → stats → tokens → user."""
        counts = {}
        try:
            async with await self._connect() as conn:
                # 1. Delete messages belonging to user's sessions
                cursor = await conn.execute(
                    "DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE user_id = ?)",
                    (user_id,),
                )
                counts["messages"] = cursor.rowcount

                # 2. Delete sessions
                cursor = await conn.execute(
                    "DELETE FROM sessions WHERE user_id = ?", (user_id,)
                )
                counts["sessions"] = cursor.rowcount

                # 3. Delete cards (avatar_data stored inline in cards table, deleted with row)
                cursor = await conn.execute(
                    "DELETE FROM cards WHERE user_id = ?", (user_id,)
                )
                counts["cards"] = cursor.rowcount

                # 4. Delete texts
                cursor = await conn.execute(
                    "DELETE FROM texts WHERE user_id = ?", (user_id,)
                )
                counts["texts"] = cursor.rowcount

                # 5. Delete usage stats
                cursor = await conn.execute(
                    "DELETE FROM usage_stats WHERE user_id = ?", (user_id,)
                )
                counts["usage_stats"] = cursor.rowcount

                # 6. Delete refresh tokens
                cursor = await conn.execute(
                    "DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,)
                )
                counts["refresh_tokens"] = cursor.rowcount

                # 7. Nullify invite codes created by this user
                cursor = await conn.execute(
                    "UPDATE invite_codes SET created_by = '[deleted]' WHERE created_by = ?",
                    (user_id,),
                )
                counts["invite_codes"] = cursor.rowcount

                # 8. Delete the user
                cursor = await conn.execute(
                    "DELETE FROM users WHERE id = ?", (user_id,)
                )
                if cursor.rowcount == 0:
                    raise ValueError("用户不存在")
                counts["user"] = 1

                await conn.commit()
            return counts
        except ValueError:
            raise
        except Exception as exc:
            print(f"[SQLiteStore] Delete user failed: {exc}")
            raise

    # ---- Usage stats ----

    async def record_usage(self, user_id: str, action: str, prompt_tokens: int, completion_tokens: int, model: str = "") -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO usage_stats (user_id, action, prompt_tokens, completion_tokens, model) VALUES (?, ?, ?, ?, ?)",
                    (user_id, action, prompt_tokens, completion_tokens, model),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Record usage failed: {exc}")

    async def get_usage_stats(self, user_id: str) -> dict:
        try:
            async with await self._connect() as conn:
                # Totals
                cursor = await conn.execute(
                    "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = ?",
                    (user_id,),
                )
                row = await cursor.fetchone()
                total = dict(row) if row else {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

                # By day
                cursor = await conn.execute(
                    "SELECT date(created_at) AS date, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = ? GROUP BY date(created_at) ORDER BY date DESC LIMIT 30",
                    (user_id,),
                )
                by_day = [dict(r) for r in await cursor.fetchall()]

                # By action
                cursor = await conn.execute(
                    "SELECT action, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = ? GROUP BY action",
                    (user_id,),
                )
                by_action = {}
                for r in await cursor.fetchall():
                    d = dict(r)
                    by_action[d["action"]] = {"calls": d["calls"], "prompt_tokens": d["prompt_tokens"], "completion_tokens": d["completion_tokens"]}

                # By model
                cursor = await conn.execute(
                    "SELECT model, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = ? AND model != '' GROUP BY model",
                    (user_id,),
                )
                by_model = {}
                for r in await cursor.fetchall():
                    d = dict(r)
                    by_model[d["model"]] = {"calls": d["calls"], "prompt_tokens": d["prompt_tokens"], "completion_tokens": d["completion_tokens"]}

            return {"total_calls": total["calls"], "total_prompt_tokens": total["prompt_tokens"], "total_completion_tokens": total["completion_tokens"], "by_day": by_day, "by_action": by_action, "by_model": by_model}
        except Exception as exc:
            print(f"[SQLiteStore] Get usage stats failed: {exc}")
            raise

    async def get_all_usage_summary(self) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT u.id AS user_id, u.username,
                       COUNT(s.id) AS total_calls,
                       COALESCE(SUM(s.prompt_tokens), 0) AS total_prompt_tokens,
                       COALESCE(SUM(s.completion_tokens), 0) AS total_completion_tokens,
                       MAX(s.created_at) AS last_active
                    FROM users u
                    LEFT JOIN usage_stats s ON u.id = s.user_id
                    GROUP BY u.id
                    ORDER BY last_active DESC NULLS LAST"""
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get all usage summary failed: {exc}")
            raise

    # ---- Affinity ----

    async def update_session_affinity(
        self, session_id: str, affinity: int, trust: int, mood: str, guard: int, reason: str = ""
    ) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE sessions
                       SET affinity = ?, trust = ?, mood = ?, guard = ?,
                           affinity_reason = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (affinity, trust, mood, guard, reason, session_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update session affinity failed: {exc}")

    async def get_session_affinity(self, session_id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT affinity, trust, mood, guard, affinity_reason as reason FROM sessions WHERE id = ?",
                    (session_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get session affinity failed: {exc}")
            return None

    # ── Comments ──

    async def get_comments(self, card_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, user_id, username, content, created_at FROM card_comments WHERE card_id = ? ORDER BY created_at DESC",
                    (card_id,),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get comments failed: {exc}")
            return []

    async def add_comment(self, card_id: str, user_id: str, username: str, content: str) -> dict:
        import uuid
        cid = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO card_comments (id, card_id, user_id, username, content) VALUES (?, ?, ?, ?, ?)",
                    (cid, card_id, user_id, username, content),
                )
                await conn.commit()
            return {"id": cid, "card_id": card_id, "user_id": user_id, "username": username, "content": content}
        except Exception as exc:
            print(f"[SQLiteStore] Add comment failed: {exc}")
            raise

    # ── Follows ──

    async def get_followers(self, user_id: str) -> list[str]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT follower_id FROM user_follows WHERE following_id = ?", (user_id,)
                )
                rows = await cursor.fetchall()
            return [r[0] for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get followers failed: {exc}")
            return []

    async def get_following(self, user_id: str) -> list[str]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT following_id FROM user_follows WHERE follower_id = ?", (user_id,)
                )
                rows = await cursor.fetchall()
            return [r[0] for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get following failed: {exc}")
            return []

    async def get_following_details(self, user_id: str) -> list[dict]:
        """Get followed users with id and username."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT u.id, u.username FROM user_follows f JOIN users u ON u.id = f.following_id WHERE f.follower_id = ?",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get following details failed: {exc}")
            return []

    async def toggle_follow(self, follower_id: str, following_id: str) -> dict:
        try:
            async with await self._connect() as conn:
                # Check if already following
                cursor = await conn.execute(
                    "SELECT 1 FROM user_follows WHERE follower_id = ? AND following_id = ?",
                    (follower_id, following_id),
                )
                exists = await cursor.fetchone()
                if exists:
                    await conn.execute(
                        "DELETE FROM user_follows WHERE follower_id = ? AND following_id = ?",
                        (follower_id, following_id),
                    )
                    await conn.commit()
                    return {"following": False}
                else:
                    await conn.execute(
                        "INSERT INTO user_follows (follower_id, following_id) VALUES (?, ?)",
                        (follower_id, following_id),
                    )
                    await conn.commit()
                    return {"following": True}
        except Exception as exc:
            print(f"[SQLiteStore] Toggle follow failed: {exc}")
            return {"following": False}

    # ── Author ──

    async def get_author_cards(self, user_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, name, card_json, forked_from, likes, created_at, avatar_data
                       FROM cards WHERE user_id = ? AND visibility = 'public' AND deleted_at IS NULL
                       ORDER BY created_at DESC""",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get author cards failed: {exc}")
            return []

    # ── User Posts ──

    async def add_post(self, user_id: str, content: str, visibility: str, images: str = "", card_id: str = "") -> dict:
        """Add a new post. Returns the created post dict."""
        import uuid
        post_id = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO user_posts (id, user_id, content, visibility, images, card_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (post_id, user_id, content, visibility, images, card_id),
                )
                await conn.commit()
                cursor = await conn.execute(
                    "SELECT id, user_id, content, visibility, images, card_id, likes, created_at FROM user_posts WHERE id = ?",
                    (post_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else {"id": post_id, "user_id": user_id, "content": content, "visibility": visibility, "images": images, "card_id": card_id, "likes": 0}
        except Exception as exc:
            print(f"[SQLiteStore] Add post failed: {exc}")
            raise

    async def get_user_posts(self, user_id: str, viewer_id: str) -> list[dict]:
        """Get posts for a user. viewer_id==user_id sees all, others see only public."""
        try:
            async with await self._connect() as conn:
                base = """SELECT p.id, p.user_id, p.content, p.visibility, p.images, p.card_id, p.likes, p.created_at,
                                 COALESCE(u.username, '') AS author_name,
                                 COALESCE(u.avatar_data, '') AS author_avatar,
                                 (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id = p.id) AS comment_count,
                                 c.name AS card_name,
                                 c.card_json AS card_json,
                                 c.avatar_data AS card_avatar_data,
                                 c.updated_at AS card_updated_at
                          FROM user_posts p
                          JOIN users u ON u.id = p.user_id
                          LEFT JOIN cards c ON c.id = p.card_id
                          WHERE p.user_id = ?"""
                if viewer_id == user_id:
                    cursor = await conn.execute(base + " ORDER BY p.created_at DESC", (user_id,))
                else:
                    cursor = await conn.execute(base + " AND p.visibility = 'public' ORDER BY p.created_at DESC", (user_id,))
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get user posts failed: {exc}")
            return []

    async def delete_post(self, post_id: str, user_id: str) -> bool:
        """Delete a post by id, only if owned by user_id. Returns True if deleted."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM user_posts WHERE id = ? AND user_id = ?",
                    (post_id, user_id),
                )
                await conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete post failed: {exc}")
            return False

    async def get_feed_posts(self, user_id: str, page: int = 1, page_size: int = 20) -> list[dict]:
        """Get public posts from followed users, newest first."""
        try:
            async with await self._connect() as conn:
                offset = (page - 1) * page_size
                cursor = await conn.execute(
                    """SELECT p.id, p.user_id, p.content, p.visibility, p.images, p.card_id,
                              p.likes, p.created_at,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(u.avatar_data, '') AS author_avatar,
                              (SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = ?) AS liked_by_me,
                              (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id = p.id) AS comment_count,
                              c.name AS card_name,
                              c.card_json AS card_json,
                              c.avatar_data AS card_avatar_data
                        FROM user_posts p
                        JOIN users u ON u.id = p.user_id
                        LEFT JOIN cards c ON c.id = p.card_id
                        WHERE p.user_id IN (SELECT following_id FROM user_follows WHERE follower_id = ?)
                          AND p.visibility = 'public'
                        ORDER BY p.created_at DESC
                        LIMIT ? OFFSET ?""",
                    (user_id, user_id, page_size, offset),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get feed posts failed: {exc}")
            return []

    async def admin_delete_post(self, post_id: str) -> bool:
        """Admin: delete any post by id. Returns True if deleted."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM user_posts WHERE id = ?",
                    (post_id,),
                )
                await conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Admin delete post failed: {exc}")
            return False

    async def toggle_post_like(self, post_id: str, user_id: str) -> dict:
        """Toggle like on a post. Returns {'liked': bool, 'likes': int}."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT 1 FROM post_likes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                liked = await cursor.fetchone() is not None

                if liked:
                    await conn.execute(
                        "DELETE FROM post_likes WHERE user_id = ? AND post_id = ?",
                        (user_id, post_id),
                    )
                    await conn.execute(
                        "UPDATE user_posts SET likes = max(0, likes - 1) WHERE id = ?",
                        (post_id,),
                    )
                else:
                    await conn.execute(
                        "INSERT INTO post_likes (user_id, post_id) VALUES (?, ?)",
                        (user_id, post_id),
                    )
                    await conn.execute(
                        "UPDATE user_posts SET likes = likes + 1 WHERE id = ?",
                        (post_id,),
                    )
                await conn.commit()

                cursor = await conn.execute(
                    "SELECT likes FROM user_posts WHERE id = ?", (post_id,)
                )
                row = await cursor.fetchone()
                new_count = row[0] if row else 0
            return {"liked": not liked, "likes": new_count}
        except Exception as exc:
            print(f"[SQLiteStore] Toggle post like failed: {exc}")
            raise

    async def get_post_comments(self, post_id: str) -> list[dict]:
        """Get all comments for a post."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, user_id, username, content, created_at FROM post_comments WHERE post_id = ? ORDER BY created_at DESC",
                    (post_id,),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get post comments failed: {exc}")
            return []

    async def add_post_comment(self, post_id: str, user_id: str, username: str, content: str) -> dict:
        """Add a comment to a post."""
        import uuid
        from datetime import datetime, timezone
        cid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO post_comments (id, post_id, user_id, username, content) VALUES (?, ?, ?, ?, ?)",
                    (cid, post_id, user_id, username, content),
                )
                await conn.commit()
            return {"id": cid, "post_id": post_id, "user_id": user_id, "username": username, "content": content, "created_at": now}
        except Exception as exc:
            print(f"[SQLiteStore] Add post comment failed: {exc}")
            raise

    async def get_liked_post_ids(self, user_id: str) -> list[str]:
        """Return all post IDs the user has liked."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT post_id FROM post_likes WHERE user_id = ?", (user_id,)
                )
                rows = await cursor.fetchall()
            return [r[0] for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get liked post ids failed: {exc}")
            return []

    # ── Text Comments ──

    async def get_text_comments(self, text_id: str, page: int = 1, page_size: int = 20) -> dict:
        """Get paginated top-level comments with nested replies."""
        try:
            offset = (page - 1) * page_size
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM text_comments WHERE text_id = ? AND parent_id = ''",
                    (text_id,),
                )
                row = await cursor.fetchone()
                total = row[0] if row else 0

                cursor = await conn.execute(
                    """SELECT id, text_id, user_id, username, content, parent_id, likes, created_at
                       FROM text_comments
                       WHERE text_id = ? AND parent_id = ''
                       ORDER BY created_at DESC
                       LIMIT ? OFFSET ?""",
                    (text_id, page_size, offset),
                )
                comments = [dict(r) for r in await cursor.fetchall()]

                comment_ids = [c["id"] for c in comments]
                if comment_ids:
                    placeholders = ",".join("?" for _ in comment_ids)
                    cursor = await conn.execute(
                        f"""SELECT id, text_id, user_id, username, content, parent_id, likes, created_at
                            FROM text_comments
                            WHERE parent_id IN ({placeholders})
                            ORDER BY created_at ASC""",
                        comment_ids,
                    )
                    replies = [dict(r) for r in await cursor.fetchall()]
                    replies_by_parent: dict[str, list[dict]] = {}
                    for r in replies:
                        replies_by_parent.setdefault(r["parent_id"], []).append(r)
                    for c in comments:
                        c["replies"] = replies_by_parent.get(c["id"], [])
                else:
                    for c in comments:
                        c["replies"] = []

            return {"comments": comments, "total": total}
        except Exception as exc:
            print(f"[SQLiteStore] Get text comments failed: {exc}")
            raise

    async def add_text_comment(self, text_id: str, user_id: str, username: str, content: str, parent_id: str = "") -> dict:
        """Add a comment. Returns the created comment dict."""
        import uuid
        comment_id = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO text_comments (id, text_id, user_id, username, content, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (comment_id, text_id, user_id, username, content, parent_id),
                )
                await conn.commit()
                cursor = await conn.execute(
                    "SELECT id, text_id, user_id, username, content, parent_id, likes, created_at FROM text_comments WHERE id = ?",
                    (comment_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else {"id": comment_id, "text_id": text_id, "user_id": user_id, "username": username, "content": content, "parent_id": parent_id, "likes": 0}
        except Exception as exc:
            print(f"[SQLiteStore] Add text comment failed: {exc}")
            raise

    async def toggle_text_comment_like(self, comment_id: str, user_id: str) -> dict:
        """Toggle like on a comment. Returns {'liked': bool, 'likes': int}."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT 1 FROM text_comment_likes WHERE comment_id = ? AND user_id = ?",
                    (comment_id, user_id),
                )
                exists = await cursor.fetchone()
                if exists:
                    await conn.execute(
                        "DELETE FROM text_comment_likes WHERE comment_id = ? AND user_id = ?",
                        (comment_id, user_id),
                    )
                    await conn.execute(
                        "UPDATE text_comments SET likes = likes - 1 WHERE id = ?",
                        (comment_id,),
                    )
                    liked = False
                else:
                    await conn.execute(
                        "INSERT INTO text_comment_likes (comment_id, user_id) VALUES (?, ?)",
                        (comment_id, user_id),
                    )
                    await conn.execute(
                        "UPDATE text_comments SET likes = likes + 1 WHERE id = ?",
                        (comment_id,),
                    )
                    liked = True
                await conn.commit()
                cursor = await conn.execute(
                    "SELECT likes FROM text_comments WHERE id = ?",
                    (comment_id,),
                )
                row = await cursor.fetchone()
            return {"liked": liked, "likes": row[0] if row else 0}
        except Exception as exc:
            print(f"[SQLiteStore] Toggle text comment like failed: {exc}")
            raise

    async def delete_text_comment(self, comment_id: str, user_id: str) -> bool:
        """Delete a comment (and its replies) by id, only if owned by user_id."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM text_comments WHERE id = ? AND user_id = ?",
                    (comment_id, user_id),
                )
                await conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete text comment failed: {exc}")
            return False

    async def get_liked_comment_ids(self, comment_ids: list[str], user_id: str) -> set[str]:
        """Return set of comment_ids that the user has liked."""
        if not comment_ids:
            return set()
        try:
            placeholders = ",".join("?" for _ in comment_ids)
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    f"SELECT comment_id FROM text_comment_likes WHERE comment_id IN ({placeholders}) AND user_id = ?",
                    (*comment_ids, user_id),
                )
                rows = await cursor.fetchall()
            return {r[0] for r in rows}
        except Exception as exc:
            print(f"[SQLiteStore] Get liked comment ids failed: {exc}")
            return set()

    # ── Direct Messages ──

    async def send_message(self, sender_id: str, receiver_id: str, content: str) -> dict:
        """Send a direct message. Returns the created message dict."""
        import uuid
        msg_id = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO direct_messages (id, sender_id, receiver_id, content) VALUES (?, ?, ?, ?)",
                    (msg_id, sender_id, receiver_id, content),
                )
                await conn.commit()
                cursor = await conn.execute(
                    "SELECT id, sender_id, receiver_id, content, is_read, created_at FROM direct_messages WHERE id = ?",
                    (msg_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else {"id": msg_id, "sender_id": sender_id, "receiver_id": receiver_id, "content": content, "is_read": 0}
        except Exception as exc:
            print(f"[SQLiteStore] Send message failed: {exc}")
            raise

    async def get_conversations(self, user_id: str) -> list[dict]:
        """Get conversation list grouped by the other participant."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT
                         sub.other_id,
                         u.username,
                         (SELECT dm2.content FROM direct_messages dm2
                          WHERE (dm2.sender_id = ? AND dm2.receiver_id = sub.other_id)
                             OR (dm2.sender_id = sub.other_id AND dm2.receiver_id = ?)
                          ORDER BY dm2.created_at DESC LIMIT 1
                         ) AS last_message,
                         (SELECT dm2.created_at FROM direct_messages dm2
                          WHERE (dm2.sender_id = ? AND dm2.receiver_id = sub.other_id)
                             OR (dm2.sender_id = sub.other_id AND dm2.receiver_id = ?)
                          ORDER BY dm2.created_at DESC LIMIT 1
                         ) AS last_time,
                         (SELECT COUNT(*) FROM direct_messages dm2
                          WHERE dm2.sender_id = sub.other_id AND dm2.receiver_id = ? AND dm2.is_read = 0
                         ) AS unread
                       FROM (
                         SELECT DISTINCT
                           CASE WHEN sender_id = ? THEN receiver_id ELSE sender_id END AS other_id
                         FROM direct_messages
                         WHERE sender_id = ? OR receiver_id = ?
                       ) sub
                       JOIN users u ON u.id = sub.other_id
                       ORDER BY last_time DESC""",
                    (user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get conversations failed: {exc}")
            return []

    async def get_conversation_messages(self, user_id: str, other_id: str, page: int = 1, page_size: int = 30) -> list[dict]:
        """Get paginated messages between two users."""
        try:
            offset = (page - 1) * page_size
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, sender_id, receiver_id, content, is_read, created_at
                       FROM direct_messages
                       WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
                       ORDER BY created_at DESC
                       LIMIT ? OFFSET ?""",
                    (user_id, other_id, other_id, user_id, page_size, offset),
                )
                rows = await cursor.fetchall()
            messages = [dict(r) for r in rows]
            messages.reverse()  # chronological order
            return messages
        except Exception as exc:
            print(f"[SQLiteStore] Get conversation messages failed: {exc}")
            return []

    async def mark_read(self, user_id: str, other_id: str) -> int:
        """Mark all messages from other_id to user_id as read. Returns count updated."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE direct_messages SET is_read = 1 WHERE sender_id = ? AND receiver_id = ? AND is_read = 0",
                    (other_id, user_id),
                )
                await conn.commit()
            return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Mark read failed: {exc}")
            return 0

    async def get_unread_count(self, user_id: str) -> int:
        """Get total unread message count."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM direct_messages WHERE receiver_id = ? AND is_read = 0",
                    (user_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] Get unread count failed: {exc}")
            return 0

    # ── Text Visibility & Author Public Data ──

    async def update_text_visibility(self, text_id: str, user_id: str, visibility: str) -> bool:
        """Set a text's visibility to 'public' or 'private'. Returns True if updated."""
        if visibility not in ("public", "private"):
            print(f"[SQLiteStore] Invalid visibility value: {visibility}")
            return False
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE texts SET visibility = ? WHERE id = ? AND user_id = ?",
                    (visibility, text_id, user_id),
                )
                await conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Update text visibility failed: {exc}")
            return False

    async def get_author_texts(self, user_id: str) -> list[dict]:
        """Get public texts for an author profile. Returns metadata only, no content."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, title, description, text_type, char_count, created_at
                       FROM texts WHERE user_id = ? AND visibility = 'public'
                       ORDER BY created_at DESC""",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get author texts failed: {exc}")
            return []

    async def get_followers_count(self, user_id: str) -> int:
        """Count of users following this user."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM user_follows WHERE following_id = ?",
                    (user_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] Get followers count failed: {exc}")
            return 0

    async def get_following_count(self, user_id: str) -> int:
        """Count of users this user is following."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM user_follows WHERE follower_id = ?",
                    (user_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] Get following count failed: {exc}")
            return 0
