"""SQLite implementation for StorageBase."""

from __future__ import annotations

import asyncio
import json
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

                    # Auto-deduplicate: keep only the newest card per text_id+name
                    try:
                        await conn.execute("""
                            DELETE FROM cards
                            WHERE id NOT IN (
                                SELECT id FROM (
                                    SELECT id, ROW_NUMBER() OVER (
                                        PARTITION BY text_id, name
                                        ORDER BY rowid DESC
                                    ) AS rn
                                    FROM cards
                                ) WHERE rn = 1
                            )
                        """)
                        await conn.commit()
                    except Exception as exc:
                        if "no such window function" not in str(exc).lower():
                            print(f"[SQLiteStore] Dedup cards migration: {exc}")

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
                    "SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count FROM texts WHERE id = ?",
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
                        SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count
                        FROM texts WHERE user_id = ?
                        ORDER BY created_at DESC
                        """, (user_id,),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count
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
                    "UPDATE cards SET card_json = ? WHERE id = ?",
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
                    "SELECT id, text_id, name, card_json, created_at FROM cards WHERE id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get card failed: {exc}")
            raise

    async def list_cards(self, text_id: str, user_id: str = "") -> list[dict]:
        """List all cards under one text id, optionally filtered by user."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        "SELECT id, text_id, name, card_json, created_at FROM cards WHERE text_id = ? AND user_id = ? ORDER BY created_at DESC",
                        (text_id, user_id),
                    )
                else:
                    cursor = await conn.execute(
                        "SELECT id, text_id, name, card_json, created_at FROM cards WHERE text_id = ? ORDER BY created_at DESC",
                        (text_id,),
                    )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List cards failed: {exc}")
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
                    SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, c.text_id, c.name AS character_name
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
        """Update voice_ref_json for all sessions of a card."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE sessions SET voice_ref_json = ?, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
                    (voice_ref_json, card_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update session voice_ref failed: {exc}")
            raise

    async def get_session_voice_ref(self, card_id: str) -> str | None:
        """Get voice_ref_json from the newest session of a card."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT voice_ref_json FROM sessions WHERE card_id = ? ORDER BY updated_at DESC LIMIT 1",
                    (card_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get session voice_ref failed: {exc}")
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

    async def create_user(self, id: str, username: str, password_hash: str) -> dict:
        """Create a new user. Raises on duplicate username."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
                    (id, username, password_hash),
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

    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Get a user by ID."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, password_hash, is_admin, is_disabled, created_at FROM users WHERE id = ?",
                    (user_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get user by id failed: {exc}")
            raise

    # ---- Admin ----

    async def get_all_users(self) -> list[dict]:
        """List all users (without password_hash)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, is_admin, is_disabled, created_at FROM users ORDER BY created_at DESC"
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

    # ---- Usage stats ----

    async def record_usage(self, user_id: str, action: str, prompt_tokens: int, completion_tokens: int) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO usage_stats (user_id, action, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?)",
                    (user_id, action, prompt_tokens, completion_tokens),
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

            return {"total_calls": total["calls"], "total_prompt_tokens": total["prompt_tokens"], "total_completion_tokens": total["completion_tokens"], "by_day": by_day, "by_action": by_action}
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
