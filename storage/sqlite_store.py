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

    async def save_text(self, id: str, filename: str, content: str, title: str = "", description: str = "") -> dict:
        """Save or update one text record."""
        try:
            char_count = len(content)
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO texts (id, filename, content, char_count, title, description)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        filename = excluded.filename,
                        content = excluded.content,
                        char_count = excluded.char_count,
                        title = excluded.title,
                        description = excluded.description
                    """,
                    (id, filename, content, char_count, title, description),
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
                    "SELECT id, filename, title, description, content, char_count, created_at FROM texts WHERE id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get text failed: {exc}")
            raise

    async def list_texts(self) -> list[dict]:
        """List all texts in descending created order."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, filename, title, description, content, char_count, created_at
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

    async def save_card(self, id: str, text_id: str, name: str, card_json: str) -> dict:
        """Save or update one card record. Upsert by text_id+name to avoid duplicates."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id FROM cards WHERE text_id = ? AND name = ?",
                    (text_id, name),
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
                        "INSERT INTO cards (id, text_id, name, card_json) VALUES (?, ?, ?, ?)",
                        (id, text_id, name, card_json),
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

    async def list_cards(self, text_id: str) -> list[dict]:
        """List all cards under one text id."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, text_id, name, card_json, created_at
                    FROM cards
                    WHERE text_id = ?
                    ORDER BY created_at DESC
                    """,
                    (text_id,),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SQLiteStore] List cards failed: {exc}")
            raise

    async def save_session(
        self, id: str, card_id: str, user_role: str, avatar_data: str
    ) -> dict:
        """Save or update one session record."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO sessions (id, card_id, user_role, avatar_data)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        card_id = excluded.card_id,
                        user_role = excluded.user_role,
                        avatar_data = excluded.avatar_data,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (id, card_id, user_role, avatar_data),
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
        self, keyword: str, character: str, text_id: str, page: int, page_size: int
    ) -> dict:
        """List sessions with filters, pagination and total."""
        safe_page = max(page, 1)
        safe_page_size = max(page_size, 1)
        offset = (safe_page - 1) * safe_page_size

        where_clauses: list[str] = []
        params: list[Any] = []
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
        """Delete one session."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute("DELETE FROM sessions WHERE id = ?", (id,))
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete session failed: {exc}")
            raise

    async def clear_all_sessions(self) -> int:
        """Delete all sessions and their messages. Returns count of deleted sessions."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute("DELETE FROM sessions")
                await conn.commit()
                return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Clear all sessions failed: {exc}")
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
