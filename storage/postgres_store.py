"""PostgreSQL implementation for StorageBase using asyncpg."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-not-found]

from .base import StorageBase


class _PoolContext:
    """Wrap an asyncpg pool connection for `async with ...` usage."""

    def __init__(self, pool: asyncpg.Pool[asyncpg.Connection]) -> None:
        self.pool = pool
        self.conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        self.conn = await self.pool.acquire()
        return self.conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.conn is not None:
            try:
                await self.pool.release(self.conn)
            except Exception as release_exc:
                print(f"[PostgresStore] Release connection failed: {release_exc}")
            self.conn = None


class PostgresStore(StorageBase):
    """Asynchronous storage implementation based on PostgreSQL via asyncpg."""

    def __init__(self, dsn: str) -> None:
        """Set DSN and lazy-init state."""
        self.dsn = dsn
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._pool: asyncpg.Pool[asyncpg.Connection] | None = None

    async def close(self) -> None:
        """Close the connection pool."""
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()
        self._initialized = False

    @staticmethod
    def _parse_rowcount(tag: str) -> int:
        """Parse asyncpg status tag like 'INSERT 0 1' to row count."""
        try:
            return int(tag.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def _ensure_initialized(self) -> None:
        """Create pool and run migration once."""
        if self._initialized and self._pool is not None:
            return

        async with self._init_lock:
            if self._initialized and self._pool is not None:
                return

            try:
                self._pool = await asyncpg.create_pool(
                    self.dsn,
                    min_size=2,
                    max_size=30,
                )
                # Run migrations
                migrations_dir = Path(__file__).with_name("migrations_pg")
                async with self._pool.acquire() as conn:
                    for migration_path in sorted(migrations_dir.glob("*.sql")):
                        sql = migration_path.read_text(encoding="utf-8")
                        await conn.execute(sql)

                self._initialized = True
            except Exception as exc:
                print(f"[PostgresStore] Initialize database failed: {exc}")
                raise

    async def _connect(self):
        """Acquire a pool connection and return a managed context wrapper."""
        await self._ensure_initialized()
        return _PoolContext(self._pool)  # type: ignore[arg-type]

    @staticmethod
    def _normalize_value(val):
        """Normalize non-JSON-serializable types to safe equivalents."""
        from datetime import date, datetime
        from decimal import Decimal
        from uuid import UUID
        if val is None:
            return None
        if isinstance(val, (datetime, date)):
            return val.isoformat()
        if isinstance(val, Decimal):
            return float(val)
        if isinstance(val, UUID):
            return str(val)
        if isinstance(val, bytes):
            return val  # no BYTEA in this schema; keep as-is defensively
        return val

    @staticmethod
    def _normalize_record(rec: dict) -> dict:
        """Normalize all values in a record dict for JSON safety."""
        return {k: PostgresStore._normalize_value(v) for k, v in rec.items()}

    @staticmethod
    def _row_to_dict(row: asyncpg.Record | None) -> dict | None:
        """Convert asyncpg Record to dict with type normalization."""
        if row is None:
            return None
        return PostgresStore._normalize_record(dict(row))

    @staticmethod
    def _list_rows(rows) -> list[dict]:
        """Convert a list of asyncpg rows to a list of normalized dicts."""
        return [PostgresStore._row_to_dict(r) for r in rows]

    async def execute(self, sql: str, params=()) -> None:
        """Execute a single SQL statement (INSERT/UPDATE/DELETE)."""
        async with await self._connect() as conn:
            await conn.execute(sql, *params)

    async def fetch_one(self, sql: str, params=()) -> dict | None:
        """Query a single row, returns dict or None."""
        async with await self._connect() as conn:
            row = await conn.fetchrow(sql, *params)
            return self._row_to_dict(row)

    # ── Texts ────────────────────────────────────────────────────

    async def save_text(self, id: str, filename: str, content: str, title: str = "", description: str = "", text_type: str = "story", original_char_count: int | None = None, user_id: str = "", content_resolved: str = "", coref_resolved: int = 0) -> dict:
        """Save or update one text record."""
        try:
            char_count = len(content)
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO texts (id, filename, content, char_count, title, description, text_type, original_char_count, user_id, content_resolved, coref_resolved)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT(id) DO UPDATE SET
                        filename = EXCLUDED.filename,
                        content = EXCLUDED.content,
                        char_count = EXCLUDED.char_count,
                        title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        text_type = EXCLUDED.text_type,
                        original_char_count = EXCLUDED.original_char_count,
                        user_id = EXCLUDED.user_id,
                        content_resolved = EXCLUDED.content_resolved,
                        coref_resolved = EXCLUDED.coref_resolved
                    """,
                    id, filename, content, char_count, title, description, text_type, original_char_count, user_id, content_resolved, coref_resolved,
                )
            return await self.get_text(id) or {}
        except Exception as exc:
            print(f"[PostgresStore] Save text failed: {exc}")
            raise

    async def update_text_resolved(self, text_id: str, content_resolved: str) -> None:
        """Write back coref-resolved content and mark coref_resolved=1."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE texts SET content_resolved=$1, coref_resolved=1 WHERE id=$2",
                    content_resolved, text_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] update_text_resolved failed: {exc}")
            raise

    async def update_text_cover(self, text_id: str, cover_data: str) -> None:
        """Update cover_data for a text."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE texts SET cover_data = $1 WHERE id = $2",
                    cover_data, text_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] update_text_cover failed: {exc}")
            raise

    async def get_text(self, id: str) -> dict | None:
        """Get one text record by id."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count, user_id, deleted_at, content_resolved, coref_resolved FROM texts WHERE id = $1",
                    id,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get text failed: {exc}")
            raise

    async def list_texts(self, user_id: str = "") -> list[dict]:
        """List texts for a user in descending created order (excludes soft-deleted)."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    rows = await conn.fetch(
                        """
                        SELECT id, filename, title, description, char_count, created_at, text_type, original_char_count, visibility, cover_data
                        FROM texts WHERE user_id = $1 AND (deleted_at IS NULL OR deleted_at = '')
                        ORDER BY created_at DESC
                        """, user_id,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, filename, title, description, char_count, created_at, text_type, original_char_count, visibility, cover_data
                        FROM texts WHERE (deleted_at IS NULL OR deleted_at = '')
                        ORDER BY created_at DESC
                        """
                    )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List texts failed: {exc}")
            raise

    async def save_characters(self, text_id: str, characters: list) -> None:
        """Cache identified characters for a text."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE texts SET characters_json = $1 WHERE id = $2",
                    json.dumps(characters, ensure_ascii=False), text_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Save characters failed: {exc}")
            raise

    async def get_characters(self, text_id: str) -> list | None:
        """Get cached identified characters for a text, or None."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT characters_json FROM texts WHERE id = $1", text_id,
                )
            if row and row[0]:
                return json.loads(row[0])
            return None
        except Exception as exc:
            print(f"[PostgresStore] Get characters failed: {exc}")
            raise

    async def delete_text(self, id: str) -> bool:
        """Soft-delete one text record."""
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE texts SET deleted_at = CURRENT_TIMESTAMP::text WHERE id = $1 AND (deleted_at IS NULL OR deleted_at = '')",
                    id,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Delete text failed: {exc}")
            raise

    async def get_deleted_texts(self, user_id: str) -> list[dict]:
        """List soft-deleted texts for a user."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, filename, title, description, char_count, created_at, text_type, original_char_count, deleted_at
                    FROM texts WHERE user_id = $1 AND deleted_at != ''
                    ORDER BY deleted_at DESC
                    """, user_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get deleted texts failed: {exc}")
            raise

    async def restore_text(self, id: str) -> bool:
        """Restore a soft-deleted text."""
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE texts SET deleted_at = '' WHERE id = $1 AND deleted_at != ''",
                    id,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Restore text failed: {exc}")
            raise

    async def hard_delete_text(self, id: str) -> bool:
        """Permanently delete a text and all associated data."""
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM reading_progress WHERE text_id = $1", id)
                    await conn.execute(
                        "DELETE FROM sessions WHERE card_id IN (SELECT id FROM cards WHERE text_id = $1)",
                        id,
                    )
                    await conn.execute("DELETE FROM cards WHERE text_id = $1", id)
                    tag = await conn.execute("DELETE FROM texts WHERE id = $1", id)
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Hard delete text failed: {exc}")
            raise

    async def save_card(self, id: str, text_id: str, name: str, card_json: str, user_id: str = "") -> dict:
        """Save or update one card record. Upsert by text_id+name+user_id to avoid duplicates."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id FROM cards WHERE text_id = $1 AND name = $2 AND user_id = $3",
                    text_id, name, user_id,
                )
                if row:
                    existing_id = row[0]
                    await conn.execute(
                        "UPDATE cards SET card_json = $1, deleted_at = NULL WHERE id = $2",
                        card_json, existing_id,
                    )
                    return await self.get_card(existing_id) or {}
                else:
                    await conn.execute(
                        "INSERT INTO cards (id, text_id, name, card_json, user_id) VALUES ($1, $2, $3, $4, $5)",
                        id, text_id, name, card_json, user_id,
                    )
                    return await self.get_card(id) or {}
        except Exception as exc:
            print(f"[PostgresStore] Save card failed: {exc}")
            raise

    async def update_card(self, card_id: str, card_json: dict) -> dict:
        """Update a card's JSON content by ID."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET card_json = $1, updated_at = CURRENT_TIMESTAMP::text WHERE id = $2",
                    json.dumps(card_json, ensure_ascii=False), card_id,
                )
                return await self.get_card(card_id) or {}
        except Exception as exc:
            print(f"[PostgresStore] Update card failed: {exc}")
            raise

    async def get_card(self, id: str) -> dict | None:
        """Get one card record by id."""
        try:
            pub_sub = ("SELECT c2.id FROM cards c2 WHERE c2.forked_from = c.id"
                       " AND c2.visibility = 'public' AND c2.deleted_at IS NULL LIMIT 1")
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    f"SELECT c.id AS id, c.text_id AS text_id, c.name AS name, c.card_json AS card_json, c.created_at AS created_at, c.user_id AS user_id, c.visibility AS visibility, c.forked_from AS forked_from, c.deleted_at AS deleted_at, c.avatar_data AS avatar_data, c.market_description AS market_description, c.market_tags AS market_tags, c.publish_message AS publish_message, ({pub_sub}) AS published_id, COALESCE(u.username, '') AS author_username FROM cards c LEFT JOIN users u ON c.user_id = u.id WHERE c.id = $1",
                    id,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get card failed: {exc}")
            raise

    async def get_card_detail(self, card_id: str, user_id: str) -> dict | None:
        """Get card detail with author info — works for both market and non-market cards."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    """SELECT c.id, c.name, c.card_json, c.user_id, c.likes, c.created_at,
                              c.avatar_data, c.visibility,
                              c.market_description, c.market_tags, c.publish_message,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(t.title, '') AS text_title,
                              (SELECT COUNT(*) FROM card_comments cc WHERE cc.card_id = c.id) AS comment_count,
                              COALESCE(u.avatar_data, '') AS author_avatar
                        FROM cards c
                        LEFT JOIN users u ON u.id = c.user_id
                        LEFT JOIN texts t ON t.id = c.text_id
                        WHERE c.id = $1 AND c.deleted_at IS NULL""",
                    card_id,
                )
                card = self._row_to_dict(row)
                if card:
                    like_row = await conn.fetchrow(
                        "SELECT 1 FROM card_likes WHERE card_id = $1 AND user_id = $2",
                        card_id, user_id,
                    )
                    card["liked_by_me"] = like_row is not None
                    card["is_market_card"] = card["visibility"] == "public"
                return card
        except Exception as exc:
            print(f"[PostgresStore] Get card detail failed: {exc}")
            return None

    async def get_market_card_detail(self, card_id: str, user_id: str) -> dict | None:
        """Get a single public card with author info and like status."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    """SELECT c.id, c.name, c.card_json, c.user_id, c.likes, c.created_at,
                              c.avatar_data,
                              c.market_description, c.market_tags, c.publish_message,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(t.title, '') AS text_title,
                              (SELECT COUNT(*) FROM card_comments cc WHERE cc.card_id = c.id) AS comment_count,
                              COALESCE(u.avatar_data, '') AS author_avatar
                        FROM cards c
                        LEFT JOIN users u ON u.id = c.user_id
                        LEFT JOIN texts t ON t.id = c.text_id
                        WHERE c.id = $1 AND c.visibility = 'public' AND c.deleted_at IS NULL""",
                    card_id,
                )
                card = self._row_to_dict(row)
                if card:
                    like_row = await conn.fetchrow(
                        "SELECT 1 FROM card_likes WHERE card_id = $1 AND user_id = $2",
                        card_id, user_id,
                    )
                    card["liked_by_me"] = like_row is not None
            return card
        except Exception as exc:
            print(f"[PostgresStore] Get market card detail failed: {exc}")
            return None

    async def list_cards(self, text_id: str, user_id: str = "") -> list[dict]:
        """List all cards under one text id, optionally filtered by user."""
        try:
            pub_sub = ("SELECT c2.id FROM cards c2 WHERE c2.forked_from = cards.id"
                       " AND c2.visibility = 'public' AND c2.deleted_at IS NULL LIMIT 1")
            async with await self._connect() as conn:
                if user_id:
                    rows = await conn.fetch(
                        f"SELECT id, text_id, name, card_json, created_at, visibility, forked_from, market_description, market_tags, ({pub_sub}) AS published_id FROM cards WHERE text_id = $1 AND user_id = $2 AND deleted_at IS NULL ORDER BY created_at DESC",
                        text_id, user_id,
                    )
                else:
                    rows = await conn.fetch(
                        f"SELECT id, text_id, name, card_json, created_at, visibility, forked_from, market_description, market_tags, ({pub_sub}) AS published_id FROM cards WHERE text_id = $1 AND deleted_at IS NULL ORDER BY created_at DESC",
                        text_id,
                    )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List cards failed: {exc}")
            raise

    async def list_standalone_cards(self, user_id: str) -> list[dict]:
        """List cards with no text_id attachment (standalone/market-forked)."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT id, text_id, name, card_json, created_at, visibility, forked_from, market_description, market_tags FROM cards WHERE (text_id IS NULL OR text_id = '') AND user_id = $1 AND deleted_at IS NULL ORDER BY created_at DESC",
                    user_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List standalone cards failed: {exc}")
            raise

    async def save_card_avatar(self, card_id: str, avatar_data: str) -> None:
        """Save base64 avatar image for a card, and sync to published copy."""
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE cards SET avatar_data = $1 WHERE id = $2",
                        avatar_data, card_id,
                    )
                    await conn.execute(
                        "UPDATE cards SET avatar_data = $1 WHERE forked_from = $2 AND visibility = 'public' AND deleted_at IS NULL",
                        avatar_data, card_id,
                    )
                    row = await conn.fetchrow(
                        "SELECT forked_from FROM cards WHERE id = $1 AND forked_from IS NOT NULL AND forked_from != ''",
                        card_id,
                    )
                    if row:
                        await conn.execute(
                            "UPDATE cards SET avatar_data = $1 WHERE id = $2",
                            avatar_data, row[0],
                        )
        except Exception as exc:
            print(f"[PostgresStore] Save card avatar failed: {exc}")
            raise

    async def get_card_avatar(self, card_id: str) -> str | None:
        """Get base64 avatar image for a card, or None."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT avatar_data FROM cards WHERE id = $1",
                    card_id,
                )
                if row and row[0]:
                    return row[0]
                return None
        except Exception as exc:
            print(f"[PostgresStore] Get card avatar failed: {exc}")
            return None

    # ── Market / public card methods ──────────────────────────

    async def list_public_cards(self, page: int = 1, page_size: int = 20, sort: str = "new", tag: str = "") -> list[dict]:
        """List public cards with pagination and sorting (hot=likes, new=created_at)."""
        try:
            order = "c.likes DESC, c.created_at DESC" if sort == "hot" else "c.created_at DESC"
            offset = (page - 1) * page_size
            tag_clause = " AND c.market_tags LIKE $3" if tag else ""
            params: list[Any] = []
            if tag:
                params = [f"%{tag}%", page_size, offset]
            else:
                params = [page_size, offset]
            async with await self._connect() as conn:
                if tag:
                    rows = await conn.fetch(
                        f"""SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data,
                                  c.forked_from, c.likes, c.created_at,
                                  c.market_description, c.market_tags,
                                  COALESCE(u.username, '') AS author_name,
                                  COALESCE(u.avatar_data, '') AS author_avatar,
                                  COALESCE(t.title, '') AS text_title,
                                  (SELECT COUNT(*) FROM card_comments cc WHERE cc.card_id = c.id) AS comment_count
                            FROM cards c
                            LEFT JOIN users u ON u.id = c.user_id
                            LEFT JOIN texts t ON t.id = c.text_id
                            WHERE c.visibility = 'public' AND c.deleted_at IS NULL AND c.market_tags LIKE $3
                            ORDER BY {order}
                            LIMIT $1 OFFSET $2""",
                        f"%{tag}%", page_size, offset,
                    )
                else:
                    rows = await conn.fetch(
                        f"""SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data,
                                  c.forked_from, c.likes, c.created_at,
                                  c.market_description, c.market_tags,
                                  COALESCE(u.username, '') AS author_name,
                                  COALESCE(u.avatar_data, '') AS author_avatar,
                                  COALESCE(t.title, '') AS text_title,
                                  (SELECT COUNT(*) FROM card_comments cc WHERE cc.card_id = c.id) AS comment_count
                            FROM cards c
                            LEFT JOIN users u ON u.id = c.user_id
                            LEFT JOIN texts t ON t.id = c.text_id
                            WHERE c.visibility = 'public' AND c.deleted_at IS NULL
                            ORDER BY {order}
                            LIMIT $1 OFFSET $2""",
                        page_size, offset,
                    )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List public cards failed: {exc}")
            raise

    async def list_public_cards_total(self, tag: str = "") -> int:
        """Return total count of public cards (for pagination)."""
        try:
            async with await self._connect() as conn:
                if tag:
                    row = await conn.fetchrow(
                        "SELECT COUNT(*) FROM cards WHERE visibility = 'public' AND deleted_at IS NULL AND market_tags LIKE $1",
                        f"%{tag}%",
                    )
                else:
                    row = await conn.fetchrow(
                        "SELECT COUNT(*) FROM cards WHERE visibility = 'public' AND deleted_at IS NULL"
                    )
            return row[0] if row else 0
        except Exception as exc:
            print(f"[PostgresStore] List public cards total failed: {exc}")
            return 0

    async def search_public_cards(self, keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
        """Search public cards by name match (case-insensitive)."""
        try:
            offset = (page - 1) * page_size
            pattern = f"%{keyword}%"
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data,
                              c.forked_from, c.likes, c.created_at,
                              c.market_description, c.market_tags,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(u.avatar_data, '') AS author_avatar,
                              COALESCE(t.title, '') AS text_title
                        FROM cards c
                        LEFT JOIN users u ON u.id = c.user_id
                        LEFT JOIN texts t ON t.id = c.text_id
                        WHERE c.visibility = 'public' AND c.name LIKE $1 AND c.deleted_at IS NULL
                        ORDER BY c.likes DESC, c.created_at DESC
                        LIMIT $2 OFFSET $3""",
                    pattern, page_size, offset,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Search public cards failed: {exc}")
            raise

    async def search_public_cards_total(self, keyword: str) -> int:
        """Return total count of matching public cards."""
        try:
            pattern = f"%{keyword}%"
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) FROM cards WHERE visibility = 'public' AND name LIKE $1 AND deleted_at IS NULL",
                    pattern,
                )
            return row[0] if row else 0
        except Exception as exc:
            print(f"[PostgresStore] Search public cards total failed: {exc}")
            return 0

    async def global_search(self, keyword: str, user_id: str = "") -> dict:
        """Search cards, texts, users by keyword. Returns max 5 per type."""
        like = f"%{keyword}%"
        try:
            async with await self._connect() as conn:
                cards_rows = await conn.fetch(
                    """SELECT c.id, c.name, c.card_json, c.avatar_data,
                              COALESCE(u.username, '') AS author_name
                       FROM cards c
                       LEFT JOIN users u ON u.id = c.user_id
                       WHERE c.visibility = 'public' AND c.deleted_at IS NULL
                         AND c.name LIKE $1
                       ORDER BY c.likes DESC
                       LIMIT 5""",
                    like,
                )
                cards = self._list_rows(cards_rows)
                texts_rows = await conn.fetch(
                    """SELECT id, title, filename, char_count
                       FROM texts
                       WHERE user_id = $1 AND (title LIKE $2 OR filename LIKE $2)
                       ORDER BY created_at DESC
                       LIMIT 5""",
                    user_id, like,
                )
                texts = self._list_rows(texts_rows)

                users_rows = await conn.fetch(
                    """SELECT id, username, avatar_data
                       FROM users
                       WHERE username LIKE $1 AND is_disabled = 0
                       ORDER BY username
                       LIMIT 5""",
                    like,
                )
                users = self._list_rows(users_rows)

            return {"cards": cards, "texts": texts, "users": users}
        except Exception as exc:
            print(f"[PostgresStore] Global search failed: {exc}")
            return {"cards": [], "texts": [], "users": []}

    async def fork_card(self, card_id: str, new_id: str, new_user_id: str, new_text_id: str = "") -> dict | None:
        """Deep copy a public card for a new user. Returns the new card dict."""
        original = await self.get_card(card_id)
        if not original:
            return None
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT visibility FROM cards WHERE id = $1 AND deleted_at IS NULL", card_id,
                )
                if not row or row[0] != "public":
                    return None
        except Exception as exc:
            print(f"[PostgresStore] Fork card visibility check failed: {exc}")
            return None

        try:
            text_id = new_text_id if new_text_id is not None else original.get("text_id", "")
            async with await self._connect() as conn:
                existing = await conn.fetchrow(
                    "SELECT id FROM cards WHERE forked_from = $1 AND user_id = $2 AND text_id = $3 AND deleted_at IS NULL",
                    card_id, new_user_id, text_id,
                )
                if existing:
                    return await self.get_card(existing[0])
                await conn.execute(
                    """INSERT INTO cards (id, text_id, name, card_json, user_id, avatar_data, forked_from, visibility)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, 'private')""",
                    new_id, text_id, original["name"],
                    original.get("card_json", "{}"), new_user_id,
                    await self.get_card_avatar(card_id) or "", card_id,
                )
            return await self.get_card(new_id)
        except Exception as exc:
            print(f"[PostgresStore] Fork card failed: {exc}")
            raise

    async def toggle_like(self, card_id: str, user_id: str) -> dict:
        """Toggle like status. Returns {'liked': bool, 'likes': int}."""
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    liked_row = await conn.fetchrow(
                        "SELECT 1 FROM card_likes WHERE user_id = $1 AND card_id = $2",
                        user_id, card_id,
                    )
                    liked = liked_row is not None
                    if liked:
                        await conn.execute(
                            "DELETE FROM card_likes WHERE user_id = $1 AND card_id = $2",
                            user_id, card_id,
                        )
                        await conn.execute(
                            "UPDATE cards SET likes = GREATEST(0, likes - 1) WHERE id = $1",
                            card_id,
                        )
                    else:
                        await conn.execute(
                            "INSERT INTO card_likes (user_id, card_id) VALUES ($1, $2)",
                            user_id, card_id,
                        )
                        await conn.execute(
                            "UPDATE cards SET likes = likes + 1 WHERE id = $1",
                            card_id,
                        )
                count_row = await conn.fetchrow(
                    "SELECT likes FROM cards WHERE id = $1", card_id,
                )
                new_count = count_row[0] if count_row else 0
            return {"liked": not liked, "likes": new_count}
        except Exception as exc:
            print(f"[PostgresStore] Toggle like failed: {exc}")
            raise

    async def delete_card(self, card_id: str) -> bool:
        """Soft delete: set deleted_at timestamp."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET deleted_at = CURRENT_TIMESTAMP::text WHERE id = $1 AND deleted_at IS NULL",
                    card_id,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Delete card failed: {exc}")
            return False

    async def restore_card(self, card_id: str) -> bool:
        """Restore a soft-deleted card."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET deleted_at = NULL WHERE id = $1",
                    card_id,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Restore card failed: {exc}")
            return False

    async def purge_card(self, card_id: str) -> bool:
        """Permanently delete a card (hard delete)."""
        try:
            async with await self._connect() as conn:
                await conn.execute("DELETE FROM cards WHERE id = $1", card_id)
            return True
        except Exception as exc:
            print(f"[PostgresStore] Purge card failed: {exc}")
            return False

    async def list_deleted_cards(self, user_id: str) -> list[dict]:
        """List soft-deleted cards for a user (recycle bin)."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT id, text_id, name, card_json, created_at, visibility, forked_from, deleted_at FROM cards WHERE deleted_at IS NOT NULL AND user_id = $1 ORDER BY deleted_at DESC",
                    user_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List deleted cards failed: {exc}")
            raise

    async def update_card_visibility(self, card_id: str, visibility: str) -> bool:
        """Set card visibility to 'public' or 'private'."""
        if visibility not in ("public", "private"):
            return False
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET visibility = $1 WHERE id = $2",
                    visibility, card_id,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Update card visibility failed: {exc}")
            return False

    async def get_liked_card_ids(self, user_id: str) -> list[str]:
        """Return all card IDs the user has liked (for frontend highlight)."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT card_id FROM card_likes WHERE user_id = $1", user_id,
                )
            return [r[0] for r in rows]
        except Exception as exc:
            print(f"[PostgresStore] Get liked card ids failed: {exc}")
            return []

    async def get_recent_card_session(self, card_id: str, exclude_id: str = "") -> dict | None:
        """Get the most recent session for a card (excluding a given session id)."""
        try:
            async with await self._connect() as conn:
                if exclude_id:
                    row = await conn.fetchrow(
                        """
                        SELECT id FROM sessions
                        WHERE card_id = $1 AND id != $2
                        ORDER BY updated_at DESC LIMIT 1
                        """, card_id, exclude_id,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT id FROM sessions
                        WHERE card_id = $1
                        ORDER BY updated_at DESC LIMIT 1
                        """, card_id,
                    )
                if row:
                    return self._row_to_dict(row)
                return None
        except Exception as exc:
            print(f"[PostgresStore] Get recent card session failed: {exc}")
            return None

    async def save_session(self, id: str, card_id: str, user_role: str, avatar_data: str, user_id: str = "") -> dict:
        """Save or update one session record."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO sessions (id, card_id, user_role, avatar_data, user_id)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT(id) DO UPDATE SET
                        card_id = EXCLUDED.card_id,
                        user_role = EXCLUDED.user_role,
                        avatar_data = EXCLUDED.avatar_data,
                        user_id = EXCLUDED.user_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    id, card_id, user_role, avatar_data, user_id,
                )
            return await self.get_session(id) or {}
        except Exception as exc:
            print(f"[PostgresStore] Save session failed: {exc}")
            raise

    async def get_session(self, id: str) -> dict | None:
        """Get one session with character name."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, s.user_id, c.text_id, c.name AS character_name
                    FROM sessions s
                    JOIN cards c ON s.card_id = c.id
                    WHERE s.id = $1
                    """, id,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get session failed: {exc}")
            raise

    async def list_sessions(self, keyword: str, character: str, text_id: str, page: int, page_size: int, user_id: str = "", card_id: str = "") -> dict:
        """List sessions with filters, pagination and total."""
        safe_page = max(page, 1)
        safe_page_size = max(page_size, 1)
        offset = (safe_page - 1) * safe_page_size
        where_clauses: list[str] = ["s.deleted_at IS NULL"]
        params: list[Any] = []
        if user_id:
            where_clauses.append(f"s.user_id = ${len(params) + 1}")
            params.append(user_id)
        if character:
            where_clauses.append(f"c.name = ${len(params) + 1}")
            params.append(character)
        if text_id:
            where_clauses.append(f"c.text_id = ${len(params) + 1}")
            params.append(text_id)
        if card_id:
            where_clauses.append(f"s.card_id = ${len(params) + 1}")
            params.append(card_id)
        if keyword:
            where_clauses.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM messages m2
                    WHERE m2.session_id = s.id
                      AND m2.content LIKE ${len(params) + 1}
                )
                """
            )
            params.append(f"%{keyword}%")
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        try:
            async with await self._connect() as conn:
                total_row = await conn.fetchrow(
                    f"""
                    SELECT COUNT(*) AS total
                    FROM sessions s
                    JOIN cards c ON s.card_id = c.id
                    {where_sql}
                    """, *params,
                )
                total = total_row["total"] if total_row else 0

                rows = await conn.fetch(
                    f"""
                    SELECT
                        s.id,
                        s.card_id,
                        s.user_role,
                        s.avatar_data,
                        s.created_at,
                        s.updated_at,
                        s.affinity,
                        s.trust,
                        s.mood,
                        s.guard,
                        s.affinity_reason,
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
                    ORDER BY last_message_at DESC NULLS LAST, s.updated_at DESC, s.created_at DESC
                    LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                    """,
                    *params, safe_page_size, offset,
                )

            return {
                "total": total,
                "page": safe_page,
                "page_size": safe_page_size,
                "items": self._list_rows(rows),
            }
        except Exception as exc:
            print(f"[PostgresStore] List sessions failed: {exc}")
            raise

    async def delete_session(self, id: str) -> bool:
        """Soft-delete one session (set deleted_at timestamp)."""
        try:
            from datetime import datetime
            now = datetime.now().isoformat()
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE sessions SET deleted_at = $1 WHERE id = $2 AND deleted_at IS NULL",
                    now, id,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Delete session failed: {exc}")
            raise

    async def clear_all_sessions(self, user_id: str = "") -> int:
        """Soft-delete all non-deleted sessions. Returns count of affected sessions."""
        try:
            from datetime import datetime
            now = datetime.now().isoformat()
            async with await self._connect() as conn:
                if user_id:
                    tag = await conn.execute(
                        "UPDATE sessions SET deleted_at = $1 WHERE deleted_at IS NULL AND user_id = $2",
                        now, user_id,
                    )
                else:
                    tag = await conn.execute(
                        "UPDATE sessions SET deleted_at = $1 WHERE deleted_at IS NULL",
                        now,
                    )
                return self._parse_rowcount(tag)
        except Exception as exc:
            print(f"[PostgresStore] Clear all sessions failed: {exc}")
            raise

    async def list_trash_sessions(self, user_id: str = "") -> list[dict]:
        """List soft-deleted sessions (in trash)."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    rows = await conn.fetch(
                        """
                        SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, s.deleted_at,
                               c.text_id, c.name AS character_name
                        FROM sessions s
                        JOIN cards c ON s.card_id = c.id
                        WHERE s.deleted_at IS NOT NULL AND s.user_id = $1
                        ORDER BY s.deleted_at DESC
                        """, user_id,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, s.deleted_at,
                               c.text_id, c.name AS character_name
                        FROM sessions s
                        JOIN cards c ON s.card_id = c.id
                        WHERE s.deleted_at IS NOT NULL
                        ORDER BY s.deleted_at DESC
                        """
                    )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List trash sessions failed: {exc}")
            raise

    async def restore_session(self, id: str) -> bool:
        """Restore a soft-deleted session (clear deleted_at)."""
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE sessions SET deleted_at = NULL WHERE id = $1 AND deleted_at IS NOT NULL",
                    id,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Restore session failed: {exc}")
            raise

    async def purge_trash(self, user_id: str = "") -> int:
        """Permanently delete all soft-deleted sessions. Returns count."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    tag = await conn.execute(
                        "DELETE FROM sessions WHERE deleted_at IS NOT NULL AND user_id = $1",
                        user_id,
                    )
                else:
                    tag = await conn.execute(
                        "DELETE FROM sessions WHERE deleted_at IS NOT NULL"
                    )
                return self._parse_rowcount(tag)
        except Exception as exc:
            print(f"[PostgresStore] Purge trash failed: {exc}")
            raise

    async def hard_delete_session(self, id: str) -> bool:
        """Permanently delete one session (hard delete)."""
        try:
            async with await self._connect() as conn:
                tag = await conn.execute("DELETE FROM sessions WHERE id = $1", id)
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Hard delete session failed: {exc}")
            raise

    async def update_session_voice_ref(self, card_id: str, voice_ref_json: str) -> None:
        """Update voice_ref_json on the card."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET voice_ref_json = $1 WHERE id = $2",
                    voice_ref_json, card_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update card voice_ref failed: {exc}")
            raise

    async def get_session_voice_ref(self, card_id: str) -> str | None:
        """Get voice_ref_json from the card."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT voice_ref_json FROM cards WHERE id = $1",
                    card_id,
                )
            return row[0] if row and row[0] else None
        except Exception as exc:
            print(f"[PostgresStore] Get card voice_ref failed: {exc}")
            raise

    # ---- WeChat user mapping ----

    async def get_wechat_user(self, openid: str) -> dict | None:
        """Get stored wechat user mapping."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT openid, session_id, card_id, created_at FROM wechat_users WHERE openid = $1",
                    openid,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get wechat user failed: {exc}")
            raise

    async def save_wechat_user(self, openid: str, session_id: str, card_id: str) -> None:
        """Upsert wechat user mapping."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO wechat_users (openid, session_id, card_id)
                    VALUES ($1, $2, $3)
                    ON CONFLICT(openid) DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        card_id = EXCLUDED.card_id
                    """,
                    openid, session_id, card_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Save wechat user failed: {exc}")
            raise

    async def save_message(self, session_id: str, role: str, content: str, rag_context: str,
                           reply_to_id: int | None = None, reply_to_preview: str = "",
                           retracted: bool = False) -> dict:
        """Save one message and touch session updated_at."""
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        """
                        INSERT INTO messages (session_id, role, content, rag_context, reply_to_id, reply_to_preview, retracted)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        RETURNING id, session_id, role, content, rag_context, created_at, reply_to_id, reply_to_preview, retracted
                        """,
                        session_id, role, content, rag_context, reply_to_id, reply_to_preview, retracted,
                    )
                    await conn.execute(
                        "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = $1",
                        session_id,
                    )
            return self._row_to_dict(row) or {}
        except Exception as exc:
            print(f"[PostgresStore] Save message failed: {exc}")
            raise

    async def get_messages(self, session_id: str) -> list[dict]:
        """List all messages in one session."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, session_id, role, content, rag_context, created_at, reply_to_id, reply_to_preview, retracted
                    FROM messages
                    WHERE session_id = $1
                    ORDER BY id ASC
                    """, session_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get messages failed: {exc}")
            raise

    # ── group sessions ────────────────────────────────────────────────

    async def create_group_session(self, id: str, name: str, card_ids: list[str], user_id: str,
                                   user_persona_type: str = "director",
                                   user_persona_card_id: str = "",
                                   user_persona_name: str = "",
                                   user_persona_desc: str = "") -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO group_sessions (id, name, card_ids, user_id,
                       user_persona_type, user_persona_card_id,
                       user_persona_name, user_persona_desc)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                    id, name, json.dumps(card_ids), user_id,
                    user_persona_type, user_persona_card_id,
                    user_persona_name, user_persona_desc,
                )
        except Exception as exc:
            print(f"[PostgresStore] Create group session failed: {exc}")
            raise

    async def get_group_session(self, id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id, name, card_ids, user_id, created_at, deleted_at, user_persona_type, user_persona_card_id, user_persona_name, user_persona_desc FROM group_sessions WHERE id = $1",
                    id,
                )
            if row is None:
                return None
            d = self._row_to_dict(row)
            d["card_ids"] = json.loads(d["card_ids"])
            return d
        except Exception as exc:
            print(f"[PostgresStore] Get group session failed: {exc}")
            raise

    async def list_group_sessions(self, user_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT id, name, card_ids, user_id, created_at,
                              user_persona_type, user_persona_card_id,
                              user_persona_name, user_persona_desc
                       FROM group_sessions
                       WHERE user_id = $1 AND (deleted_at IS NULL OR deleted_at = '')
                       ORDER BY created_at DESC""",
                    user_id,
                )
            results = []
            for row in rows:
                d = self._row_to_dict(row)
                d["card_ids"] = json.loads(d["card_ids"])
                results.append(d)
            return results
        except Exception as exc:
            print(f"[PostgresStore] List group sessions failed: {exc}")
            raise

    async def get_deleted_group_sessions(self, user_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT id, name, card_ids, user_id, created_at, deleted_at
                       FROM group_sessions
                       WHERE user_id = $1 AND deleted_at != '' AND deleted_at IS NOT NULL
                       ORDER BY deleted_at DESC""",
                    user_id,
                )
            results = []
            for row in rows:
                d = self._row_to_dict(row)
                d["card_ids"] = json.loads(d["card_ids"])
                results.append(d)
            return results
        except Exception as exc:
            print(f"[PostgresStore] Get deleted group sessions failed: {exc}")
            raise

    async def restore_group_session(self, id: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE group_sessions SET deleted_at = '' WHERE id = $1",
                    id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Restore group session failed: {exc}")
            raise

    async def hard_delete_group_session(self, id: str) -> None:
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM group_messages WHERE group_id = $1", id)
                    await conn.execute("DELETE FROM group_sessions WHERE id = $1", id)
        except Exception as exc:
            print(f"[PostgresStore] Hard delete group session failed: {exc}")
            raise

    async def save_group_message(self, group_id: str, speaker: str, role: str, content: str,
                                 speaker_card_id: str = "", reply_to_id: int | None = None,
                                 reply_to_preview: str = "") -> int:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO group_messages (group_id, speaker, role, content, speaker_card_id, reply_to_id, reply_to_preview)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       RETURNING id""",
                    group_id, speaker, role, content, speaker_card_id, reply_to_id, reply_to_preview,
                )
                return row[0]
        except Exception as exc:
            print(f"[PostgresStore] Save group message failed: {exc}")
            raise

    async def get_group_messages(self, group_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT id, group_id, speaker, role, content, speaker_card_id, created_at,
                              reply_to_id, reply_to_preview
                       FROM group_messages
                       WHERE group_id = $1
                       ORDER BY id ASC""",
                    group_id,
                )
                messages = self._list_rows(rows)
                msg_ids = [m["id"] for m in messages]
                if msg_ids:
                    reactions_map = await self.get_reactions(msg_ids)
                    for m in messages:
                        m["reactions"] = reactions_map.get(m["id"], [])
            return messages
        except Exception as exc:
            print(f"[PostgresStore] Get group messages failed: {exc}")
            raise

    async def toggle_reaction(self, message_id: int, user_id: str, emoji: str) -> bool:
        """Toggle a reaction. Returns True if added, False if removed."""
        try:
            async with await self._connect() as conn:
                existing = await conn.fetchrow(
                    "SELECT id FROM message_reactions WHERE message_id = $1 AND user_id = $2 AND emoji = $3",
                    message_id, user_id, emoji,
                )
                if existing:
                    await conn.execute(
                        "DELETE FROM message_reactions WHERE id = $1",
                        existing[0],
                    )
                    return False
                else:
                    await conn.execute(
                        """INSERT INTO message_reactions (message_id, user_id, emoji)
                           VALUES ($1, $2, $3)""",
                        message_id, user_id, emoji,
                    )
                    return True
        except Exception as exc:
            print(f"[PostgresStore] Toggle reaction failed: {exc}")
            raise

    async def get_reactions(self, message_ids: list[int]) -> dict[int, list]:
        """Batch query reactions for given message IDs."""
        if not message_ids:
            return {}
        try:
            async with await self._connect() as conn:
                placeholders = ",".join(f"${i+1}" for i in range(len(message_ids)))
                rows = await conn.fetch(
                    f"""SELECT message_id, emoji, user_id
                        FROM message_reactions
                        WHERE message_id IN ({placeholders})
                        ORDER BY id ASC""",
                    *message_ids,
                )
            result: dict[int, dict[str, dict]] = {}
            for row in rows:
                mid = row["message_id"]
                emoji = row["emoji"]
                uid = row["user_id"]
                if mid not in result:
                    result[mid] = {}
                if emoji not in result[mid]:
                    result[mid][emoji] = {"emoji": emoji, "count": 0, "users": []}
                result[mid][emoji]["count"] += 1
                result[mid][emoji]["users"].append(uid)
            return {
                mid: list(emoji_map.values())
                for mid, emoji_map in result.items()
            }
        except Exception as exc:
            print(f"[PostgresStore] Get reactions failed: {exc}")
            raise

    async def get_reactions_after(self, session_id: str, after_reaction_id: int) -> list[dict]:
        """Return reactions with id > after_reaction_id for a single-chat session."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT r.id, r.emoji, m.content, r.user_id
                       FROM message_reactions r
                       JOIN messages m ON m.id = r.message_id
                       WHERE m.session_id = $1 AND r.id > $2
                       ORDER BY r.id ASC""",
                    session_id, after_reaction_id,
                )
            return [
                {"reaction_id": r["id"], "emoji": r["emoji"],
                 "msg_content": r["content"], "user_id": r["user_id"]}
                for r in rows
            ]
        except Exception as exc:
            print(f"[PostgresStore] Get reactions after failed: {exc}")
            raise

    async def get_group_reactions_after(self, group_id: str, after_reaction_id: int) -> list[dict]:
        """Return reactions with id > after_reaction_id for a group session."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT r.id, r.emoji, m.content, m.speaker_card_id
                       FROM message_reactions r
                       JOIN group_messages m ON m.id = r.message_id
                       WHERE m.group_id = $1 AND r.id > $2 AND m.role = 'assistant'
                       ORDER BY r.id ASC""",
                    group_id, after_reaction_id,
                )
            return [
                {"reaction_id": r["id"], "emoji": r["emoji"],
                 "msg_content": r["content"], "speaker_card_id": r["speaker_card_id"]}
                for r in rows
            ]
        except Exception as exc:
            print(f"[PostgresStore] Get group reactions after failed: {exc}")
            raise

    async def update_group_session(self, id: str, name: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE group_sessions SET name = $1 WHERE id = $2",
                    name, id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update group session failed: {exc}")
            raise

    async def update_group_card_ids(self, id: str, card_ids: list[str]) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE group_sessions SET card_ids = $1 WHERE id = $2",
                    json.dumps(card_ids), id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update group card_ids failed: {exc}")
            raise

    async def delete_group_session(self, id: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE group_sessions SET deleted_at = CURRENT_TIMESTAMP::text WHERE id = $1",
                    id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Delete group session failed: {exc}")
            raise

    async def delete_messages_after(self, session_id: str, message_id: int) -> int:
        """Delete messages after and including one message id."""
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    tag = await conn.execute(
                        """
                        DELETE FROM messages
                        WHERE session_id = $1
                          AND id >= $2
                        """, session_id, message_id,
                    )
                    await conn.execute(
                        "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = $1",
                        session_id,
                    )
                return self._parse_rowcount(tag)
        except Exception as exc:
            print(f"[PostgresStore] Delete messages after failed: {exc}")
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
                print(f"[PostgresStore] Parse card_json failed: {exc}")
                card_parsed = {"raw": card["card_json"]}
        if fmt == "json":
            payload = {"session": session, "card": card_parsed, "messages": messages}
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

    # ── Users ──────────────────────────────────────────────────────

    async def create_user(self, id: str, username: str, password_hash: str, email: str = "") -> dict:
        """Create a new user. Raises on duplicate username."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO users (id, username, password_hash, email, email_verified) VALUES ($1, $2, $3, $4, $5)",
                    id, username, password_hash, email, 1 if email else 0,
                )
                return await self.get_user_by_username(username) or {}
        except asyncpg.IntegrityConstraintViolationError as exc:
            raise ValueError("用户名已存在") from exc
        except Exception as exc:
            print(f"[PostgresStore] Create user failed: {exc}")
            raise

    async def get_user_by_username(self, username: str) -> dict | None:
        """Get a user by username."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id, username, password_hash, is_admin, is_disabled, created_at FROM users WHERE username = $1",
                    username,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get user failed: {exc}")
            raise

    async def get_user_by_email(self, email: str) -> dict | None:
        """Get a user by email."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id, username, password_hash, email, email_verified, is_admin, is_disabled, created_at FROM users WHERE email = $1 AND email != ''",
                    email,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get user by email failed: {exc}")
            raise

    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Get a user by ID."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id, username, password_hash, is_admin, is_disabled, created_at, avatar_data, banner_data, profile_stats_visible, cards_visible, books_visible, bio, last_active_at, presence_visibility, following_visible FROM users WHERE id = $1",
                    user_id,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get user by id failed: {exc}")
            raise

    async def set_user_privacy(self, user_id: str, **kwargs) -> bool:
        """Set privacy fields (stats_visible, cards_visible, books_visible)."""
        allowed = {'profile_stats_visible', 'cards_visible', 'books_visible', 'following_visible'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return True
        try:
            async with await self._connect() as conn:
                set_clause = ', '.join(f'{k} = ${i+1}' for i, k in enumerate(updates))
                values = [1 if v else 0 for v in updates.values()]
                values.append(user_id)
                await conn.execute(
                    f"UPDATE users SET {set_clause} WHERE id = ${len(values)}",
                    *values,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Set user privacy failed: {exc}")
            return False

    async def set_user_presence_visibility(self, user_id: str, visibility: str) -> bool:
        """Set presence_visibility for a user."""
        if visibility not in ('all', 'fans', 'mutual', 'none'):
            return False
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET presence_visibility = $1 WHERE id = $2",
                    visibility, user_id,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Set presence visibility failed: {exc}")
            return False

    # ---- Email & verification codes ----

    async def get_user_email(self, user_id: str) -> str:
        """Get a user's verified email, empty string if none."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT email FROM users WHERE id = $1", user_id,
                )
            return row[0] if row else ""
        except Exception as exc:
            print(f"[PostgresStore] Get user email failed: {exc}")
            raise

    async def update_user_email(self, user_id: str, email: str) -> None:
        """Set a user's email and mark verified."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET email = $1, email_verified = 1 WHERE id = $2",
                    email, user_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update user email failed: {exc}")
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
                    "INSERT INTO verification_codes (id, email, code, purpose, expires_at) VALUES ($1, $2, $3, $4, $5)",
                    cid, email, code, purpose, expires_at,
                )
        except Exception as exc:
            print(f"[PostgresStore] Save verification code failed: {exc}")
            raise

    async def verify_code(self, email: str, code: str, purpose: str) -> bool:
        """Verify a code. Returns True if valid, consumes it. False otherwise."""
        from datetime import datetime, timezone
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id, expires_at FROM verification_codes WHERE email = $1 AND code = $2 AND purpose = $3 AND used = 0 ORDER BY created_at DESC LIMIT 1",
                    email, code, purpose,
                )
                if not row:
                    return False
                if row["expires_at"] < datetime.now(timezone.utc).isoformat():
                    return False
                await conn.execute(
                    "UPDATE verification_codes SET used = 1 WHERE id = $1",
                    row["id"],
                )
                return True
        except Exception as exc:
            print(f"[PostgresStore] Verify code failed: {exc}")
            raise

    async def cleanup_expired_codes(self) -> int:
        """Delete expired verification codes. Returns count deleted."""
        from datetime import datetime, timezone
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "DELETE FROM verification_codes WHERE expires_at < $1",
                    datetime.now(timezone.utc).isoformat(),
                )
                return self._parse_rowcount(tag)
        except Exception as exc:
            print(f"[PostgresStore] Cleanup expired codes failed: {exc}")
            raise

    # ---- Admin ----

    async def get_all_users(self) -> list[dict]:
        """List all users (without password_hash)."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT id, username, email, email_verified, is_admin, is_disabled, created_at, last_login_at, last_active_at, presence_visibility FROM users ORDER BY created_at DESC"
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get all users failed: {exc}")
            raise

    async def update_last_login(self, user_id: str) -> None:
        """Update the last_login_at timestamp for a user."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET last_login_at = CURRENT_TIMESTAMP::text WHERE id = $1",
                    user_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update last_login failed: {exc}")

    async def update_last_active(self, user_id: str) -> None:
        """Update the last_active_at timestamp for a user."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET last_active_at = CURRENT_TIMESTAMP::text WHERE id = $1",
                    user_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update last_active failed: {exc}")

    async def get_dashboard_stats(self) -> dict:
        """Aggregate dashboard statistics for admin panel."""
        try:
            async with await self._connect() as conn:
                total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
                today_new_users = await conn.fetchval(
                    "SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE"
                )
                today_active_users = await conn.fetchval(
                    "SELECT COUNT(DISTINCT user_id) FROM usage_stats WHERE created_at::date = CURRENT_DATE"
                )
                today_api_calls = await conn.fetchval(
                    "SELECT COUNT(*) FROM usage_stats WHERE created_at::date = CURRENT_DATE"
                )
                today_tokens = await conn.fetchval(
                    "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM usage_stats WHERE created_at::date = CURRENT_DATE"
                )
                trend_rows = await conn.fetch(
                    """SELECT created_at::date AS day,
                              COUNT(*)::int AS calls,
                              COALESCE(SUM(prompt_tokens + completion_tokens), 0)::bigint AS tokens
                       FROM usage_stats
                       WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
                       GROUP BY created_at::date
                       ORDER BY day ASC"""
                )
                trend = [{"day": str(r[0]), "calls": r[1], "tokens": r[2]} for r in trend_rows]

            import psutil, shutil
            mem = psutil.virtual_memory()
            disk = shutil.disk_usage("/")

            return {
                "total_users": total_users,
                "today_new_users": today_new_users,
                "today_active_users": today_active_users,
                "today_api_calls": today_api_calls,
                "today_tokens": today_tokens,
                "trend": trend,
                "system": {
                    "memory_total": mem.total,
                    "memory_used": mem.used,
                    "memory_percent": round(mem.percent, 1),
                    "disk_total": disk.total,
                    "disk_used": disk.used,
                    "disk_percent": round(disk.used / disk.total * 100, 1) if disk.total else 0,
                },
            }
        except Exception as exc:
            print(f"[PostgresStore] Get dashboard stats failed: {exc}")
            raise

    async def set_user_admin(self, user_id: str, is_admin: bool) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute("UPDATE users SET is_admin = $1 WHERE id = $2", int(is_admin), user_id)
        except Exception as exc:
            print(f"[PostgresStore] Set user admin failed: {exc}")
            raise

    async def set_user_disabled(self, user_id: str, is_disabled: bool) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute("UPDATE users SET is_disabled = $1 WHERE id = $2", int(is_disabled), user_id)
        except Exception as exc:
            print(f"[PostgresStore] Set user disabled failed: {exc}")
            raise

    async def reset_user_password(self, user_id: str, password_hash: str) -> bool:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE users SET password_hash = $1 WHERE id = $2",
                    password_hash, user_id,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Reset password failed: {exc}")
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
        """Get a user's API config. api_key and embedding_key are returned decrypted."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT api_key, base_url, model, embedding_key, embedding_region FROM users WHERE id = $1",
                    user_id,
                )
            if not row:
                return {"api_key": "", "base_url": "", "model": "", "embedding_key": "", "embedding_region": "cn"}

            def _decrypt(val: str) -> str:
                if not val:
                    return ""
                try:
                    return self._get_fernet().decrypt(val.encode()).decode()
                except Exception as exc:
                    print(f"[PostgresStore] decrypt failed: {exc}")
                    return ""

            return {
                "api_key": _decrypt(row[0] or ""),
                "base_url": row[1] or "https://api.deepseek.com",
                "model": row[2] or "deepseek-v4-pro",
                "embedding_key": _decrypt(row[3] or ""),
                "embedding_region": row[4] or "cn",
            }
        except Exception as exc:
            print(f"[PostgresStore] Get user API config failed: {exc}")
            raise

    async def update_user_api_config(self, user_id: str, api_key: str, base_url: str, model: str, embedding_key: str = "", embedding_region: str = "cn") -> None:
        """Update a user's API config. api_key and embedding_key are encrypted before storage.

        Empty keys are not written, so a blank field in the request does not
        overwrite an existing encrypted key (same logic for both api_key and embedding_key).
        """
        try:
            async with await self._connect() as conn:
                if api_key:
                    encrypted = self._get_fernet().encrypt(api_key.encode()).decode()
                    if embedding_key:
                        enc_emb = self._get_fernet().encrypt(embedding_key.encode()).decode()
                        await conn.execute(
                            "UPDATE users SET api_key = $1, base_url = $2, model = $3, embedding_key = $4, embedding_region = $5 WHERE id = $6",
                            encrypted, base_url, model, enc_emb, embedding_region, user_id,
                        )
                    else:
                        await conn.execute(
                            "UPDATE users SET api_key = $1, base_url = $2, model = $3, embedding_region = $4 WHERE id = $5",
                            encrypted, base_url, model, embedding_region, user_id,
                        )
                elif embedding_key:
                    enc_emb = self._get_fernet().encrypt(embedding_key.encode()).decode()
                    await conn.execute(
                        "UPDATE users SET base_url = $1, model = $2, embedding_key = $3, embedding_region = $4 WHERE id = $5",
                        base_url, model, enc_emb, embedding_region, user_id,
                    )
                else:
                    await conn.execute(
                        "UPDATE users SET base_url = $1, model = $2, embedding_region = $3 WHERE id = $4",
                        base_url, model, embedding_region, user_id,
                    )
        except Exception as exc:
            print(f"[PostgresStore] Update user API config failed: {exc}")
            raise

    async def update_user_avatar(self, user_id: str, avatar_data: str) -> None:
        """Store base64 avatar for a user."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET avatar_data = $1 WHERE id = $2",
                    avatar_data, user_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update user avatar failed: {exc}")
            raise

    async def update_user_password(self, user_id: str, password_hash: str) -> None:
        """Update a user's password hash."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET password_hash = $1 WHERE id = $2",
                    password_hash, user_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update user password failed: {exc}")
            raise

    async def get_user_avatar(self, user_id: str) -> str:
        """Get base64 avatar for a user, empty string if none."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT avatar_data FROM users WHERE id = $1", user_id,
                )
            return row[0] if row and row[0] else ""
        except Exception as exc:
            print(f"[PostgresStore] Get user avatar failed: {exc}")
            raise

    async def update_user_banner(self, user_id: str, banner_data: str) -> None:
        """Store base64 banner for a user."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET banner_data = $1 WHERE id = $2",
                    banner_data, user_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update user banner failed: {exc}")
            raise

    async def get_user_banner(self, user_id: str) -> str:
        """Get base64 banner for a user, empty string if none."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT banner_data FROM users WHERE id = $1", user_id,
                )
            return row[0] if row and row[0] else ""
        except Exception as exc:
            print(f"[PostgresStore] Get user banner failed: {exc}")
            raise

    async def update_user_bio(self, user_id: str, bio: str) -> None:
        """Update a user's bio text."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET bio = $1 WHERE id = $2",
                    bio, user_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update user bio failed: {exc}")
            raise

    async def record_geo_block(self, user_id: str, ip: str, base_url: str, reason: str) -> None:
        """Record a geo-blocking event for compliance audit trail."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO geo_block_log (user_id, ip, base_url, reason) VALUES ($1, $2, $3, $4)",
                    user_id, ip, base_url, reason,
                )
        except Exception as exc:
            print(f"[PostgresStore] Record geo block failed: {exc}")

    async def record_user_consent(self, user_id: str, terms_version: str, privacy_version: str, ip: str) -> None:
        """Record user's consent to legal agreements for compliance audit trail."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO user_consent (user_id, terms_version, privacy_version, ip) VALUES ($1, $2, $3, $4)",
                    user_id, terms_version, privacy_version, ip,
                )
        except Exception as exc:
            print(f"[PostgresStore] Record user consent failed: {exc}")

    async def create_invite_code(self, code: str, created_by: str) -> dict:
        import uuid as _uuid
        cid = _uuid.uuid4().hex[:16]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO invite_codes (id, code, created_by) VALUES ($1, $2, $3)",
                    cid, code, created_by,
                )
            return await self.get_invite_code(code) or {}
        except Exception as exc:
            print(f"[PostgresStore] Create invite code failed: {exc}")
            raise

    async def get_invite_code(self, code: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id, code, created_by, used_by, used_at, created_at FROM invite_codes WHERE code = $1",
                    code,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get invite code failed: {exc}")
            raise

    async def use_invite_code(self, code: str, used_by: str) -> None:
        from datetime import datetime
        now = datetime.now().isoformat()
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE invite_codes SET used_by = $1, used_at = $2 WHERE code = $3",
                    used_by, now, code,
                )
        except Exception as exc:
            print(f"[PostgresStore] Use invite code failed: {exc}")
            raise

    async def list_invite_codes(self) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT id, code, created_by, used_by, used_at, created_at FROM invite_codes ORDER BY created_at DESC"
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List invite codes failed: {exc}")
            raise

    async def delete_invite_code(self, code: str) -> bool:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "DELETE FROM invite_codes WHERE code = $1", code,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Delete invite code failed: {exc}")
            raise

    async def delete_used_invites(self) -> int:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "DELETE FROM invite_codes WHERE used_by IS NOT NULL"
                )
                return self._parse_rowcount(tag)
        except Exception as exc:
            print(f"[PostgresStore] Delete used invites failed: {exc}")
            raise

    # ---- Refresh tokens ----

    async def save_refresh_token(self, token_hash: str, user_id: str, expires_at: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO refresh_tokens (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
                    token_hash, user_id, expires_at,
                )
        except Exception as exc:
            print(f"[PostgresStore] Save refresh token failed: {exc}")
            raise

    async def get_refresh_token(self, token_hash: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT token_hash, user_id, expires_at, used FROM refresh_tokens WHERE token_hash = $1",
                    token_hash,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get refresh token failed: {exc}")
            raise

    async def mark_refresh_token_used(self, token_hash: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE refresh_tokens SET used = 1 WHERE token_hash = $1",
                    token_hash,
                )
        except Exception as exc:
            print(f"[PostgresStore] Mark refresh token used failed: {exc}")
            raise

    async def delete_user_refresh_tokens(self, user_id: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "DELETE FROM refresh_tokens WHERE user_id = $1",
                    user_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Delete user refresh tokens failed: {exc}")
            raise

    async def get_user_card_ids(self, user_id: str) -> list[str]:
        """Get all card IDs owned by a user (for Mem0 cleanup)."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT id FROM cards WHERE user_id = $1", user_id,
                )
            return [row[0] for row in rows]
        except Exception as exc:
            print(f"[PostgresStore] Get user card ids failed: {exc}")
            raise

    async def delete_user(self, user_id: str) -> dict:
        """Cascade-delete a user."""
        counts = {}
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    tag = await conn.execute(
                        "DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE user_id = $1)",
                        user_id,
                    )
                    counts["messages"] = self._parse_rowcount(tag)
                    tag = await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)
                    counts["sessions"] = self._parse_rowcount(tag)
                    tag = await conn.execute("DELETE FROM cards WHERE user_id = $1", user_id)
                    counts["cards"] = self._parse_rowcount(tag)
                    tag = await conn.execute("DELETE FROM texts WHERE user_id = $1", user_id)
                    counts["texts"] = self._parse_rowcount(tag)
                    tag = await conn.execute("DELETE FROM usage_stats WHERE user_id = $1", user_id)
                    counts["usage_stats"] = self._parse_rowcount(tag)
                    tag = await conn.execute("DELETE FROM refresh_tokens WHERE user_id = $1", user_id)
                    counts["refresh_tokens"] = self._parse_rowcount(tag)
                    tag = await conn.execute(
                        "UPDATE invite_codes SET created_by = '[deleted]' WHERE created_by = $1",
                        user_id,
                    )
                    counts["invite_codes"] = self._parse_rowcount(tag)
                    tag = await conn.execute("DELETE FROM users WHERE id = $1", user_id)
                    if self._parse_rowcount(tag) == 0:
                        raise ValueError("用户不存在")
                    counts["user"] = 1
            return counts
        except ValueError:
            raise
        except Exception as exc:
            print(f"[PostgresStore] Delete user failed: {exc}")
            raise

    # ---- Admin: Content Moderation ----

    async def list_all_cards_admin(self) -> list[dict]:
        """List all cards with user info for admin review."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT c.id, c.text_id, c.name, c.created_at, c.user_id, c.visibility,
                              c.deleted_at, c.card_json, COALESCE(u.username, '') AS username
                       FROM cards c
                       LEFT JOIN users u ON u.id = c.user_id
                       ORDER BY c.created_at DESC"""
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List all cards admin failed: {exc}")
            raise

    async def takedown_card(self, card_id: str) -> bool:
        """Set a public card to private (takedown)."""
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE cards SET visibility = 'private' WHERE id = $1 AND visibility = 'public'",
                    card_id,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Takedown card failed: {exc}")
            raise

    async def list_all_posts_admin(self) -> list[dict]:
        """List all user posts for admin review."""
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT p.id, p.user_id, p.content, p.visibility, p.created_at,
                              COALESCE(u.username, '') AS username
                       FROM user_posts p
                       LEFT JOIN users u ON u.id = p.user_id
                       ORDER BY p.created_at DESC"""
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List all posts admin failed: {exc}")
            raise

    async def admin_delete_post(self, post_id: str) -> bool:
        """Delete any post by id (admin)."""
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "DELETE FROM user_posts WHERE id = $1", post_id,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Admin delete post failed: {exc}")
            raise

    async def ban_user_and_contents(self, user_id: str, admin_id: str) -> dict:
        """Disable user + delete their posts + resolve comment reports."""
        counts = {"posts_deleted": 0, "reports_resolved": 0}
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    await conn.execute("UPDATE users SET is_disabled = 1 WHERE id = $1", user_id)
                    tag = await conn.execute("DELETE FROM user_posts WHERE user_id = $1", user_id)
                    counts["posts_deleted"] = self._parse_rowcount(tag)
                    tag = await conn.execute(
                        """UPDATE card_comment_reports SET status = 'resolved', resolver_id = $1
                           WHERE comment_id IN (SELECT id FROM card_comments WHERE user_id = $2)
                           AND status = 'pending'""",
                        admin_id, user_id,
                    )
                    counts["reports_resolved"] = self._parse_rowcount(tag)
            return counts
        except Exception as exc:
            print(f"[PostgresStore] Ban user failed: {exc}")
            raise

    # ---- Admin: User Detail ----

    async def get_user_detail(self, user_id: str) -> dict:
        """Get user detail for admin."""
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT id, username, email, email_verified, is_admin, is_disabled, created_at, last_login_at, last_active_at FROM users WHERE id = $1",
                    user_id,
                )
                if not row:
                    raise ValueError("用户不存在")
                result = self._row_to_dict(row)
                cards_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM cards WHERE user_id = $1 AND deleted_at IS NULL", user_id,
                )
                result["cards_count"] = cards_count
                sessions_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM sessions WHERE user_id = $1", user_id,
                )
                result["sessions_count"] = sessions_count
                usage_row = await conn.fetchrow(
                    """SELECT COUNT(*) AS calls,
                              COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                              COALESCE(SUM(completion_tokens), 0) AS completion_tokens
                       FROM usage_stats WHERE user_id = $1""",
                    user_id,
                )
                result["usage"] = dict(usage_row) if usage_row else {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
                login_rows = await conn.fetch(
                    "SELECT created_at FROM usage_stats WHERE user_id = $1 ORDER BY created_at DESC LIMIT 20",
                    user_id,
                )
                result["login_history"] = [r[0] for r in login_rows]
            return result
        except ValueError:
            raise
        except Exception as exc:
            print(f"[PostgresStore] Get user detail failed: {exc}")
            raise

    # ---- Admin: Announcements ----

    async def create_announcement(self, content: str, align: str = 'left') -> dict:
        import uuid
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    await conn.execute("UPDATE announcements SET is_active = 0")
                    aid = uuid.uuid4().hex[:12]
                    await conn.execute(
                        "INSERT INTO announcements (id, content, is_active, align) VALUES ($1, $2, 1, $3)",
                        aid, content, align,
                    )
                row = await conn.fetchrow("SELECT * FROM announcements WHERE id = $1", aid)
            return self._row_to_dict(row) if row else {"id": aid, "content": content, "is_active": 1, "align": align}
        except Exception as exc:
            print(f"[PostgresStore] Create announcement failed: {exc}")
            raise

    async def delete_announcement(self, announcement_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "DELETE FROM announcements WHERE id = $1", announcement_id,
                )
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Delete announcement failed: {exc}")
            raise

    async def get_active_announcement(self) -> dict | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM announcements WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get active announcement failed: {exc}")
            raise

    async def list_announcements(self) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM announcements ORDER BY created_at DESC"
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] List announcements failed: {exc}")
            raise

    # ---- Admin: CSV Export ----

    async def export_users_csv(self) -> str:
        import io, csv
        try:
            users = await self.get_all_users()
            output = io.StringIO()
            fieldnames = ["id", "username", "email", "is_admin", "is_disabled", "created_at", "last_login_at"]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for u in users:
                writer.writerow({k: u.get(k, "") for k in fieldnames})
            return output.getvalue()
        except Exception as exc:
            print(f"[PostgresStore] Export users CSV failed: {exc}")
            raise

    async def export_usage_csv(self) -> str:
        import io, csv
        try:
            data = await self.get_all_usage_summary()
            output = io.StringIO()
            fieldnames = ["user_id", "username", "total_calls", "total_prompt_tokens", "total_completion_tokens", "last_active"]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for d in data:
                writer.writerow({k: d.get(k, "") for k in fieldnames})
            return output.getvalue()
        except Exception as exc:
            print(f"[PostgresStore] Export usage CSV failed: {exc}")
            raise

    # ---- Config changelog ----

    async def save_config_change(self, change_id: str, admin_id: str, admin_username: str, field: str, old_value: str, new_value: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO config_changelog (id, admin_id, admin_username, field, old_value, new_value) VALUES ($1, $2, $3, $4, $5, $6)",
                    change_id, admin_id, admin_username, field, old_value, new_value,
                )
        except Exception as exc:
            print(f"[PostgresStore] Save config change failed: {exc}")

    async def get_config_changelog(self, limit: int = 50) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM config_changelog ORDER BY created_at DESC LIMIT $1", limit,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get config changelog failed: {exc}")
            return []

    # ---- Review log ----

    async def save_review_log(self, review_id: str, card_id: str, user_id: str, result: str, reason: str = "") -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO review_log (id, card_id, user_id, result, reason) VALUES ($1, $2, $3, $4, $5)",
                    review_id, card_id, user_id, result, reason,
                )
        except Exception as exc:
            print(f"[PostgresStore] Save review log failed: {exc}")

    async def get_review_logs(self, limit: int = 50) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT r.id, r.card_id, r.user_id, r.result, r.reason, r.created_at,
                              COALESCE(c.name, '') AS card_name
                       FROM review_log r
                       LEFT JOIN cards c ON c.id = r.card_id
                       ORDER BY r.created_at DESC LIMIT $1""",
                    limit,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get review logs failed: {exc}")
            return []

    # ---- Usage stats ----

    async def record_usage(self, user_id: str, action: str, prompt_tokens: int, completion_tokens: int, model: str = "", is_estimated: bool = False) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO usage_stats (user_id, action, prompt_tokens, completion_tokens, model, is_estimated) VALUES ($1, $2, $3, $4, $5, $6)",
                    user_id, action, prompt_tokens, completion_tokens, model, is_estimated,
                )
        except Exception as exc:
            print(f"[PostgresStore] Record usage failed: {exc}")

    async def get_usage_stats(self, user_id: str) -> dict:
        try:
            async with await self._connect() as conn:
                total_row = await conn.fetchrow(
                    "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = $1",
                    user_id,
                )
                total = self._row_to_dict(total_row) if total_row else {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

                by_day_rows = await conn.fetch(
                    "SELECT created_at::date AS date, COUNT(*)::int AS calls, COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens, COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens FROM usage_stats WHERE user_id = $1 GROUP BY created_at::date ORDER BY date DESC LIMIT 30",
                    user_id,
                )
                by_day = self._list_rows(by_day_rows)

                by_action_rows = await conn.fetch(
                    "SELECT action, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = $1 GROUP BY action",
                    user_id,
                )
                by_action = {}
                for r in by_action_rows:
                    d = self._row_to_dict(r)
                    by_action[d["action"]] = {"calls": d["calls"], "prompt_tokens": d["prompt_tokens"], "completion_tokens": d["completion_tokens"]}

                by_model_rows = await conn.fetch(
                    "SELECT model, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = $1 AND model != '' GROUP BY model",
                    user_id,
                )
                by_model = {}
                for r in by_model_rows:
                    d = self._row_to_dict(r)
                    by_model[d["model"]] = {"calls": d["calls"], "prompt_tokens": d["prompt_tokens"], "completion_tokens": d["completion_tokens"]}

            return {"total_calls": total["calls"], "total_prompt_tokens": total["prompt_tokens"], "total_completion_tokens": total["completion_tokens"], "by_day": by_day, "by_action": by_action, "by_model": by_model}
        except Exception as exc:
            print(f"[PostgresStore] Get usage stats failed: {exc}")
            raise

    async def get_all_usage_summary(self) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
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
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get all usage summary failed: {exc}")
            raise

    # ---- Affinity ----

    async def update_session_affinity(self, session_id: str, affinity: int, trust: int, mood: str, guard: int, reason: str = "") -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE sessions
                       SET affinity = $1, trust = $2, mood = $3, guard = $4,
                           affinity_reason = $5,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = $6""",
                    affinity, trust, mood, guard, reason, session_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update session affinity failed: {exc}")

    async def get_session_affinity(self, session_id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT affinity, trust, mood, guard, affinity_reason AS reason FROM sessions WHERE id = $1",
                    session_id,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get session affinity failed: {exc}")
            return None

    async def update_group_affinity(
        self, group_id: str, card_id: str, affinity: int, trust: int, mood: str, guard: int, reason: str = ""
    ) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO group_affinity (group_id, card_id, affinity, trust, mood, guard, affinity_reason)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (group_id, card_id) DO UPDATE SET
                           affinity = EXCLUDED.affinity,
                           trust = EXCLUDED.trust,
                           mood = EXCLUDED.mood,
                           guard = EXCLUDED.guard,
                           affinity_reason = EXCLUDED.affinity_reason""",
                    group_id, card_id, affinity, trust, mood, guard, reason,
                )
        except Exception as exc:
            print(f"[PostgresStore] Update group affinity failed: {exc}")

    async def get_group_affinity(self, group_id: str, card_id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    """SELECT affinity, trust, mood, guard, affinity_reason AS reason
                       FROM group_affinity
                       WHERE group_id = $1 AND card_id = $2""",
                    group_id, card_id,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get group affinity failed: {exc}")
            return None

    # ── Comments ──

    async def get_comments(self, card_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT c.id, c.user_id, c.username, c.content, c.created_at, "
                    "COALESCE(u.avatar_data, '') AS avatar_data, "
                    "COALESCE(c.is_ai_reply, 0) AS is_ai_reply, "
                    "COALESCE(c.ai_card_id, '') AS ai_card_id, "
                    "COALESCE(c.ai_version_label, '') AS ai_version_label, "
                    "COALESCE(c.reply_to_comment_id, '') AS reply_to_comment_id "
                    "FROM card_comments c LEFT JOIN users u ON c.user_id = u.id "
                    "WHERE c.card_id = $1 ORDER BY c.created_at ASC",
                    card_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get comments failed: {exc}")
            return []

    async def add_comment(self, card_id: str, user_id: str, username: str, content: str) -> dict:
        import uuid
        cid = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO card_comments (id, card_id, user_id, username, content) VALUES ($1, $2, $3, $4, $5)",
                    cid, card_id, user_id, username, content,
                )
            return {"id": cid, "card_id": card_id, "user_id": user_id, "username": username, "content": content}
        except Exception as exc:
            print(f"[PostgresStore] Add comment failed: {exc}")
            raise

    async def get_public_cards_by_text_id(self, text_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT c.id, c.name, c.user_id, u.username AS author_username
                       FROM cards c LEFT JOIN users u ON c.user_id = u.id
                       WHERE c.text_id = $1 AND c.visibility = 'public' AND c.deleted_at IS NULL
                       ORDER BY c.created_at ASC""",
                    text_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] get_public_cards_by_text_id failed: {exc}")
            return []

    async def add_ai_reply_comment(self, card_id: str, ai_card_id: str, ai_version_label: str,
                                   content: str, reply_to_comment_id: str) -> dict:
        import uuid
        cid = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO card_comments
                       (id, card_id, user_id, username, content, is_ai_reply, ai_card_id, ai_version_label, reply_to_comment_id)
                       VALUES ($1, $2, '', '', $3, 1, $4, $5, $6)""",
                    cid, card_id, content, ai_card_id, ai_version_label, reply_to_comment_id,
                )
            return {
                "id": cid, "card_id": card_id, "user_id": "", "username": "",
                "content": content, "is_ai_reply": 1, "ai_card_id": ai_card_id,
                "ai_version_label": ai_version_label, "reply_to_comment_id": reply_to_comment_id,
            }
        except Exception as exc:
            print(f"[PostgresStore] add_ai_reply_comment failed: {exc}")
            raise

    async def get_card_author_id(self, card_id: str) -> str | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT user_id FROM cards WHERE id = $1 AND deleted_at IS NULL",
                    card_id,
                )
            return row[0] if row else None
        except Exception as exc:
            print(f"[PostgresStore] Get card author failed: {exc}")
            return None

    async def get_comment(self, comment_id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM card_comments WHERE id = $1", comment_id,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get comment failed: {exc}")
            return None

    async def delete_comment(self, comment_id: str, user_id: str, card_author_id: str | None = None, is_admin: bool = False) -> bool:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "DELETE FROM card_comments WHERE id = $1",
                    comment_id,
                )
            return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Delete comment failed: {exc}")
            return False

    async def batch_delete_comments(self, comment_ids: list[str]) -> bool:
        if not comment_ids:
            return True
        try:
            placeholders = ",".join(f"${i+1}" for i in range(len(comment_ids)))
            async with await self._connect() as conn:
                await conn.execute(
                    f"DELETE FROM card_comments WHERE id IN ({placeholders})",
                    *comment_ids,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Batch delete comments failed: {exc}")
            return False

    # ── Comment Reports ──

    async def add_comment_report(self, comment_id: str, card_id: str, reporter_id: str, reason: str) -> bool:
        try:
            report_id = uuid.uuid4().hex[:12]
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO card_comment_reports
                       (id, comment_id, card_id, reporter_id, reason)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT DO NOTHING""",
                    report_id, comment_id, card_id, reporter_id, reason,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Add comment report failed: {exc}")
            return False

    async def get_comment_reports(self, status: str = 'pending') -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT r.id, r.comment_id, r.card_id, r.reporter_id, r.reason,
                              r.status, r.created_at, r.resolved_at, r.resolver_id,
                              c.content AS comment_content, c.user_id AS comment_author_id,
                              c.username AS comment_author_name,
                              (SELECT COUNT(*) FROM card_comment_reports r2
                               WHERE r2.comment_id = r.comment_id AND r2.status = 'pending') AS report_count
                       FROM card_comment_reports r
                       JOIN card_comments c ON c.id = r.comment_id
                       WHERE r.status = $1
                       ORDER BY report_count DESC, r.created_at ASC""",
                    status,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get comment reports failed: {exc}")
            return []

    async def resolve_report(self, report_id: str, resolver_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE card_comment_reports
                       SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, resolver_id = $1
                       WHERE id = $2 AND status = 'pending'""",
                    resolver_id, report_id,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Resolve report failed: {exc}")
            return False

    async def delete_comment_and_resolve_report(self, comment_id: str, report_id: str, resolver_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM card_comments WHERE id = $1", comment_id)
                    await conn.execute(
                        """UPDATE card_comment_reports
                           SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, resolver_id = $1
                           WHERE id = $2 AND status = 'pending'""",
                        resolver_id, report_id,
                    )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Delete comment and resolve report failed: {exc}")
            return False

    async def get_comment_reports_grouped(self, status: str = 'pending') -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT r.comment_id, r.card_id,
                              c.content AS comment_content,
                              c.user_id AS comment_author_id,
                              c.username AS comment_author_name,
                              COUNT(*) AS report_count,
                              STRING_AGG(r.reason, ' | ') AS reasons,
                              MIN(r.created_at) AS first_reported_at
                       FROM card_comment_reports r
                       JOIN card_comments c ON c.id = r.comment_id
                       WHERE r.status = $1
                       GROUP BY r.comment_id, c.content, c.user_id, c.username
                       ORDER BY report_count DESC, first_reported_at ASC""",
                    status,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get comment reports grouped failed: {exc}")
            return []

    async def resolve_all_reports(self, comment_id: str, resolver_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE card_comment_reports
                       SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, resolver_id = $1
                       WHERE comment_id = $2 AND status = 'pending'""",
                    resolver_id, comment_id,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Resolve all reports failed: {exc}")
            return False

    async def delete_comment_and_resolve_reports(self, comment_id: str, resolver_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM card_comments WHERE id = $1", comment_id)
                    await conn.execute(
                        """UPDATE card_comment_reports
                           SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, resolver_id = $1
                           WHERE comment_id = $2 AND status = 'pending'""",
                        resolver_id, comment_id,
                    )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Delete comment and resolve reports failed: {exc}")
            return False

    # ── Follows ──

    async def get_followers(self, user_id: str) -> list[str]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT follower_id FROM user_follows WHERE following_id = $1", user_id,
                )
            return [r[0] for r in rows]
        except Exception as exc:
            print(f"[PostgresStore] Get followers failed: {exc}")
            return []

    async def get_followers_details(self, user_id: str, viewer_id: str = "") -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT u.id, u.username, u.avatar_data,
                              (SELECT 1 FROM user_follows WHERE follower_id = $2 AND following_id = u.id) IS NOT NULL AS is_following,
                              (SELECT COUNT(*) FROM cards WHERE user_id = u.id AND visibility = 'public' AND deleted_at IS NULL) AS cards_count
                       FROM user_follows f JOIN users u ON u.id = f.follower_id WHERE f.following_id = $1 AND f.follower_id != f.following_id""",
                    user_id, viewer_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get followers details failed: {exc}")
            return []

    async def get_following(self, user_id: str) -> list[str]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT following_id FROM user_follows WHERE follower_id = $1", user_id,
                )
            return [r[0] for r in rows]
        except Exception as exc:
            print(f"[PostgresStore] Get following failed: {exc}")
            return []

    async def get_following_details(self, user_id: str, viewer_id: str = "") -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT u.id, u.username, u.avatar_data,
                              (SELECT 1 FROM user_follows WHERE follower_id = $2 AND following_id = u.id) IS NOT NULL AS is_following,
                              (SELECT COUNT(*) FROM cards WHERE user_id = u.id AND visibility = 'public' AND deleted_at IS NULL) AS cards_count
                       FROM user_follows f JOIN users u ON u.id = f.following_id WHERE f.follower_id = $1""",
                    user_id, viewer_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get following details failed: {exc}")
            return []

    async def toggle_follow(self, follower_id: str, following_id: str) -> dict:
        try:
            async with await self._connect() as conn:
                exists = await conn.fetchrow(
                    "SELECT 1 FROM user_follows WHERE follower_id = $1 AND following_id = $2",
                    follower_id, following_id,
                )
                if exists:
                    await conn.execute(
                        "DELETE FROM user_follows WHERE follower_id = $1 AND following_id = $2",
                        follower_id, following_id,
                    )
                    return {"following": False}
                else:
                    await conn.execute(
                        "INSERT INTO user_follows (follower_id, following_id) VALUES ($1, $2)",
                        follower_id, following_id,
                    )
                    return {"following": True}
        except Exception as exc:
            print(f"[PostgresStore] Toggle follow failed: {exc}")
            return {"following": False}

    # ── Author ──

    async def get_author_cards(self, user_id: str, include_private: bool = False) -> list[dict]:
        try:
            async with await self._connect() as conn:
                visibility_clause = "" if include_private else "AND visibility = 'public'"
                rows = await conn.fetch(
                    f"""SELECT id, name, card_json, forked_from, likes, created_at, avatar_data,
                              market_description, market_tags, visibility,
                              (SELECT COUNT(*) FROM sessions WHERE card_id = cards.id) AS chat_count,
                              (SELECT title FROM texts WHERE id = cards.text_id) AS text_title
                       FROM cards WHERE user_id = $1 AND deleted_at IS NULL {visibility_clause}
                       ORDER BY created_at DESC""",
                    user_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get author cards failed: {exc}")
            return []

    # ── User Posts ──

    async def add_post(self, user_id: str, content: str, visibility: str, images: str = "", card_id: str = "", location: str = "") -> dict:
        import uuid
        post_id = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO user_posts (id, user_id, content, visibility, images, card_id, location) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    post_id, user_id, content, visibility, images, card_id, location,
                )
                row = await conn.fetchrow(
                    "SELECT id, user_id, content, visibility, images, card_id, likes, created_at, location FROM user_posts WHERE id = $1",
                    post_id,
                )
            return self._row_to_dict(row) if row else {"id": post_id, "user_id": user_id, "content": content, "visibility": visibility, "images": images, "card_id": card_id, "likes": 0, "location": location}
        except Exception as exc:
            print(f"[PostgresStore] Add post failed: {exc}")
            raise

    async def get_user_posts(self, user_id: str, viewer_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                base = """SELECT p.id, p.user_id, p.content, p.visibility, p.images, p.card_id, p.likes, p.created_at, p.location,
                                 COALESCE(u.username, '') AS author_name,
                                 COALESCE(u.avatar_data, '') AS author_avatar,
                                 (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id = p.id) AS comment_count,
                                 c.name AS card_name,
                                 c.card_json AS card_json,
                                 c.avatar_data AS card_avatar_data
                          FROM user_posts p
                          LEFT JOIN users u ON u.id = p.user_id
                          LEFT JOIN cards c ON c.id = p.card_id AND p.card_id != ''
                          WHERE p.user_id = $1"""
                if viewer_id == user_id:
                    rows = await conn.fetch(base + " ORDER BY p.created_at DESC", user_id)
                else:
                    rows = await conn.fetch(base + " AND p.visibility = 'public' ORDER BY p.created_at DESC", user_id)
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get user posts failed: {exc}")
            return []

    async def delete_post(self, post_id: str, user_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "DELETE FROM user_posts WHERE id = $1 AND user_id = $2",
                    post_id, user_id,
                )
            return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Delete post failed: {exc}")
            return False

    async def get_feed_posts(self, user_id: str, page: int = 1, page_size: int = 20) -> list[dict]:
        try:
            async with await self._connect() as conn:
                offset = (page - 1) * page_size
                rows = await conn.fetch(
                    """SELECT p.id, p.user_id, p.content, p.visibility, p.images, p.card_id, p.location,
                              p.likes, p.created_at,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(u.avatar_data, '') AS author_avatar,
                              (SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = $1) IS NOT NULL AS liked_by_me,
                              (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id = p.id) AS comment_count,
                              c.name AS card_name,
                              c.card_json AS card_json,
                              c.avatar_data AS card_avatar_data
                        FROM user_posts p
                        LEFT JOIN users u ON u.id = p.user_id
                        LEFT JOIN cards c ON c.id = p.card_id AND p.card_id != ''
                        WHERE p.user_id IN (SELECT following_id FROM user_follows WHERE follower_id = $1)
                          AND p.visibility = 'public'
                        ORDER BY p.created_at DESC
                        LIMIT $2 OFFSET $3""",
                    user_id, page_size, offset,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get feed posts failed: {exc}")
            return []

    async def toggle_post_like(self, post_id: str, user_id: str) -> dict:
        try:
            async with await self._connect() as conn:
                async with conn.transaction():
                    liked_row = await conn.fetchrow(
                        "SELECT 1 FROM post_likes WHERE user_id = $1 AND post_id = $2",
                        user_id, post_id,
                    )
                    liked = liked_row is not None
                    if liked:
                        await conn.execute(
                            "DELETE FROM post_likes WHERE user_id = $1 AND post_id = $2",
                            user_id, post_id,
                        )
                        await conn.execute(
                            "UPDATE user_posts SET likes = GREATEST(0, likes - 1) WHERE id = $1",
                            post_id,
                        )
                    else:
                        await conn.execute(
                            "INSERT INTO post_likes (user_id, post_id) VALUES ($1, $2)",
                            user_id, post_id,
                        )
                        await conn.execute(
                            "UPDATE user_posts SET likes = likes + 1 WHERE id = $1",
                            post_id,
                        )
                count_row = await conn.fetchrow(
                    "SELECT likes FROM user_posts WHERE id = $1", post_id,
                )
                new_count = count_row[0] if count_row else 0
            return {"liked": not liked, "likes": new_count}
        except Exception as exc:
            print(f"[PostgresStore] Toggle post like failed: {exc}")
            raise

    async def get_post_comments(self, post_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT pc.id, pc.user_id, pc.username, pc.content, pc.created_at, pc.ip_location,
                              COALESCE(u.avatar_data, '') AS avatar_data
                       FROM post_comments pc
                       LEFT JOIN users u ON pc.user_id = u.id
                       WHERE pc.post_id = $1
                       ORDER BY pc.created_at DESC""",
                    post_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get post comments failed: {exc}")
            return []

    async def add_post_comment(self, post_id: str, user_id: str, username: str, content: str, ip_location: str = "") -> dict:
        import uuid
        from datetime import datetime, timezone
        cid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        avatar_data = ""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO post_comments (id, post_id, user_id, username, content, ip_location) VALUES ($1, $2, $3, $4, $5, $6)",
                    cid, post_id, user_id, username, content, ip_location,
                )
                try:
                    avatar_row = await conn.fetchrow("SELECT avatar_data FROM users WHERE id = $1", user_id)
                    if avatar_row and avatar_row[0]:
                        avatar_data = avatar_row[0]
                except Exception as exc:
                    print(f"[PostgresStore] Avatar data query failed: {exc}")
            return {"id": cid, "post_id": post_id, "user_id": user_id, "username": username, "content": content, "created_at": now, "ip_location": ip_location, "avatar_data": avatar_data}
        except Exception as exc:
            print(f"[PostgresStore] Add post comment failed: {exc}")
            raise

    async def get_liked_post_ids(self, user_id: str) -> list[str]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT post_id FROM post_likes WHERE user_id = $1", user_id,
                )
            return [r[0] for r in rows]
        except Exception as exc:
            print(f"[PostgresStore] Get liked post ids failed: {exc}")
            return []

    # ── Text Comments ──

    async def get_text_comments(self, text_id: str, page: int = 1, page_size: int = 20) -> dict:
        try:
            offset = (page - 1) * page_size
            async with await self._connect() as conn:
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM text_comments WHERE text_id = $1 AND parent_id = ''",
                    text_id,
                ) or 0

                rows = await conn.fetch(
                    """SELECT id, text_id, user_id, username, content, parent_id, likes, created_at
                       FROM text_comments
                       WHERE text_id = $1 AND parent_id = ''
                       ORDER BY created_at DESC
                       LIMIT $2 OFFSET $3""",
                    text_id, page_size, offset,
                )
                comments = self._list_rows(rows)

                comment_ids = [c["id"] for c in comments]
                if comment_ids:
                    placeholders = ",".join(f"${i+2}" for i in range(len(comment_ids)))
                    reply_rows = await conn.fetch(
                        f"""SELECT id, text_id, user_id, username, content, parent_id, likes, created_at
                            FROM text_comments
                            WHERE parent_id IN ({placeholders})
                            ORDER BY created_at ASC""",
                        text_id, *comment_ids,
                    )
                    replies = self._list_rows(reply_rows)
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
            print(f"[PostgresStore] Get text comments failed: {exc}")
            raise

    async def add_text_comment(self, text_id: str, user_id: str, username: str, content: str, parent_id: str = "") -> dict:
        import uuid
        comment_id = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO text_comments (id, text_id, user_id, username, content, parent_id) VALUES ($1, $2, $3, $4, $5, $6)",
                    comment_id, text_id, user_id, username, content, parent_id,
                )
                row = await conn.fetchrow(
                    "SELECT id, text_id, user_id, username, content, parent_id, likes, created_at FROM text_comments WHERE id = $1",
                    comment_id,
                )
            return self._row_to_dict(row) if row else {"id": comment_id, "text_id": text_id, "user_id": user_id, "username": username, "content": content, "parent_id": parent_id, "likes": 0}
        except Exception as exc:
            print(f"[PostgresStore] Add text comment failed: {exc}")
            raise

    async def toggle_text_comment_like(self, comment_id: str, user_id: str) -> dict:
        try:
            async with await self._connect() as conn:
                exists = await conn.fetchrow(
                    "SELECT 1 FROM text_comment_likes WHERE comment_id = $1 AND user_id = $2",
                    comment_id, user_id,
                )
                if exists:
                    await conn.execute(
                        "DELETE FROM text_comment_likes WHERE comment_id = $1 AND user_id = $2",
                        comment_id, user_id,
                    )
                    await conn.execute(
                        "UPDATE text_comments SET likes = likes - 1 WHERE id = $1",
                        comment_id,
                    )
                    liked = False
                else:
                    await conn.execute(
                        "INSERT INTO text_comment_likes (comment_id, user_id) VALUES ($1, $2)",
                        comment_id, user_id,
                    )
                    await conn.execute(
                        "UPDATE text_comments SET likes = likes + 1 WHERE id = $1",
                        comment_id,
                    )
                    liked = True
                count_row = await conn.fetchrow(
                    "SELECT likes FROM text_comments WHERE id = $1",
                    comment_id,
                )
            return {"liked": liked, "likes": count_row[0] if count_row else 0}
        except Exception as exc:
            print(f"[PostgresStore] Toggle text comment like failed: {exc}")
            raise

    async def delete_text_comment(self, comment_id: str, user_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "DELETE FROM text_comments WHERE id = $1 AND user_id = $2",
                    comment_id, user_id,
                )
            return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Delete text comment failed: {exc}")
            return False

    async def get_liked_comment_ids(self, comment_ids: list[str], user_id: str) -> set[str]:
        if not comment_ids:
            return set()
        try:
            placeholders = ",".join(f"${i+1}" for i in range(len(comment_ids)))
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    f"SELECT comment_id FROM text_comment_likes WHERE comment_id IN ({placeholders}) AND user_id = ${len(comment_ids) + 1}",
                    *comment_ids, user_id,
                )
            return {r[0] for r in rows}
        except Exception as exc:
            print(f"[PostgresStore] Get liked comment ids failed: {exc}")
            return set()

    # ── Direct Messages ──

    async def send_message(self, sender_id: str, receiver_id: str, content: str) -> dict:
        import uuid
        msg_id = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO direct_messages (id, sender_id, receiver_id, content) VALUES ($1, $2, $3, $4)",
                    msg_id, sender_id, receiver_id, content,
                )
                row = await conn.fetchrow(
                    "SELECT id, sender_id, receiver_id, content, is_read, created_at FROM direct_messages WHERE id = $1",
                    msg_id,
                )
            return self._row_to_dict(row) if row else {"id": msg_id, "sender_id": sender_id, "receiver_id": receiver_id, "content": content, "is_read": 0}
        except Exception as exc:
            print(f"[PostgresStore] Send message failed: {exc}")
            raise

    async def get_conversations(self, user_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT
                         sub.other_id,
                         u.username,
                         u.avatar_data,
                         (SELECT dm2.content FROM direct_messages dm2
                          WHERE (dm2.sender_id = $1 AND dm2.receiver_id = sub.other_id)
                             OR (dm2.sender_id = sub.other_id AND dm2.receiver_id = $2)
                          ORDER BY dm2.created_at DESC LIMIT 1
                         ) AS last_message,
                         (SELECT dm2.created_at FROM direct_messages dm2
                          WHERE (dm2.sender_id = $3 AND dm2.receiver_id = sub.other_id)
                             OR (dm2.sender_id = sub.other_id AND dm2.receiver_id = $4)
                          ORDER BY dm2.created_at DESC LIMIT 1
                         ) AS last_time,
                         (SELECT COUNT(*) FROM direct_messages dm2
                          WHERE dm2.sender_id = sub.other_id AND dm2.receiver_id = $5 AND dm2.is_read = 0
                         ) AS unread
                       FROM (
                         SELECT DISTINCT
                           CASE WHEN sender_id = $6 THEN receiver_id ELSE sender_id END AS other_id
                         FROM direct_messages
                         WHERE sender_id = $7 OR receiver_id = $8
                       ) sub
                       JOIN users u ON u.id = sub.other_id
                       ORDER BY last_time DESC""",
                    user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get conversations failed: {exc}")
            return []

    async def get_conversation_messages(self, user_id: str, other_id: str, page: int = 1, page_size: int = 30) -> list[dict]:
        try:
            offset = (page - 1) * page_size
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT id, sender_id, receiver_id, content, is_read, created_at
                       FROM direct_messages
                       WHERE (sender_id = $1 AND receiver_id = $2) OR (sender_id = $3 AND receiver_id = $4)
                       ORDER BY created_at DESC
                       LIMIT $5 OFFSET $6""",
                    user_id, other_id, other_id, user_id, page_size, offset,
                )
            messages = self._list_rows(rows)
            messages.reverse()
            return messages
        except Exception as exc:
            print(f"[PostgresStore] Get conversation messages failed: {exc}")
            return []

    async def mark_read(self, user_id: str, other_id: str) -> int:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE direct_messages SET is_read = 1 WHERE sender_id = $1 AND receiver_id = $2 AND is_read = 0",
                    other_id, user_id,
                )
            return self._parse_rowcount(tag)
        except Exception as exc:
            print(f"[PostgresStore] Mark read failed: {exc}")
            return 0

    async def get_unread_count(self, user_id: str) -> int:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) FROM direct_messages WHERE receiver_id = $1 AND is_read = 0",
                    user_id,
                )
            return row[0] if row else 0
        except Exception as exc:
            print(f"[PostgresStore] Get unread count failed: {exc}")
            return 0

    # ── Text Visibility & Author Public Data ──

    async def update_text_visibility(self, text_id: str, user_id: str, visibility: str) -> bool:
        if visibility not in ("public", "private"):
            print(f"[PostgresStore] Invalid visibility value: {visibility}")
            return False
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE texts SET visibility = $1 WHERE id = $2 AND user_id = $3",
                    visibility, text_id, user_id,
                )
            return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Update text visibility failed: {exc}")
            return False

    async def get_author_texts(self, user_id: str, viewer_id: str = "") -> list[dict]:
        try:
            async with await self._connect() as conn:
                if viewer_id == user_id:
                    rows = await conn.fetch(
                        """SELECT id, title, description, text_type, char_count, created_at, visibility, cover_data
                           FROM texts WHERE user_id = $1 AND (deleted_at IS NULL OR deleted_at = '')
                           ORDER BY created_at DESC""",
                        user_id,
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT id, title, description, text_type, char_count, created_at, visibility, cover_data
                           FROM texts WHERE user_id = $1 AND visibility = 'public' AND (deleted_at IS NULL OR deleted_at = '')
                           ORDER BY created_at DESC""",
                        user_id,
                    )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get author texts failed: {exc}")
            return []

    async def get_followers_count(self, user_id: str) -> int:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) FROM user_follows WHERE following_id = $1 AND follower_id != following_id",
                    user_id,
                )
            return row[0] if row else 0
        except Exception as exc:
            print(f"[PostgresStore] Get followers count failed: {exc}")
            return 0

    async def get_following_count(self, user_id: str) -> int:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) FROM user_follows WHERE follower_id = $1",
                    user_id,
                )
            return row[0] if row else 0
        except Exception as exc:
            print(f"[PostgresStore] Get following count failed: {exc}")
            return 0

    async def is_following(self, user_id: str, target_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) FROM user_follows WHERE follower_id = $1 AND following_id = $2",
                    user_id, target_id,
                )
            return row[0] > 0 if row else False
        except Exception as exc:
            print(f"[PostgresStore] Is following check failed: {exc}")
            return False

    async def is_friend(self, user_id: str, target_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                a = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_follows WHERE follower_id = $1 AND following_id = $2",
                    user_id, target_id,
                )
                b = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_follows WHERE follower_id = $1 AND following_id = $2",
                    target_id, user_id,
                )
            return a > 0 and b > 0
        except Exception as exc:
            print(f"[PostgresStore] Is friend check failed: {exc}")
            return False

    async def can_see_online_status(self, viewer_id: str, target_id: str, is_admin: bool = False) -> bool:
        if viewer_id == target_id:
            return True
        if is_admin:
            return True
        try:
            async with await self._connect() as conn:
                target_row = await conn.fetchrow(
                    "SELECT presence_visibility FROM users WHERE id = $1", target_id,
                )
                if not target_row:
                    return False
                target_vis = target_row[0]
                viewer_row = await conn.fetchrow(
                    "SELECT presence_visibility FROM users WHERE id = $1", viewer_id,
                )
        except Exception as exc:
            print(f"[PostgresStore] Can see online status failed: {exc}")
            return False

        if viewer_row and viewer_row[0] == 'none':
            return False
        if target_vis == 'all':
            return True
        if target_vis == 'none':
            return False
        if target_vis == 'fans':
            return await self.is_following(viewer_id, target_id)
        if target_vis == 'mutual':
            return await self.is_friend(viewer_id, target_id)
        return False

    # ──── Market publish / version / fork API ────

    async def publish_card(self, card_id: str, user_id: str, description: str, tags: str, message: str, card_json_snapshot: str) -> str | None:
        try:
            async with await self._connect() as conn:
                existing = await conn.fetchrow(
                    "SELECT id FROM cards WHERE forked_from = $1 AND visibility = 'public' AND deleted_at IS NULL",
                    card_id,
                )
                if existing:
                    fork_id = existing[0]
                    await conn.execute(
                        """UPDATE cards SET market_description = $1, market_tags = $2, publish_message = $3,
                           visibility = 'public'
                           WHERE id = $4 AND deleted_at IS NULL""",
                        description, tags, message, fork_id,
                    )
                    ver_row = await conn.fetchrow(
                        "SELECT COALESCE(MAX(version_num), 0) + 1 FROM card_versions WHERE card_id = $1",
                        fork_id,
                    )
                    next_ver = ver_row[0] if ver_row else 1
                    await conn.execute(
                        """INSERT INTO card_versions (id, card_id, user_id, version_num, publish_message, diff_json, card_json_snapshot)
                           VALUES ($1, $2, $3, $4, $5, '{}', $6)""",
                        uuid.uuid4().hex[:12], fork_id, user_id, next_ver, message, card_json_snapshot,
                    )
                    return fork_id

                src = await conn.fetchrow(
                    """SELECT text_id, name, card_json, avatar_data, voice_ref_json
                       FROM cards WHERE id = $1 AND deleted_at IS NULL""",
                    card_id,
                )
                if not src:
                    return None

                fork_id = uuid.uuid4().hex[:12]
                await conn.execute(
                    """INSERT INTO cards (id, text_id, name, card_json, created_at, avatar_data, user_id,
                                          visibility, forked_from, likes, voice_ref_json,
                                          market_description, market_tags, publish_message)
                       VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, $5, $6, 'public', $7, 0, $8, $9, $10, $11)""",
                    fork_id, src[0], src[1], src[2], src[4] or "", user_id, card_id,
                    src[5] or "", description, tags, message,
                )
                await conn.execute(
                    """INSERT INTO card_versions (id, card_id, user_id, version_num, publish_message, diff_json, card_json_snapshot)
                       VALUES ($1, $2, $3, 1, $4, '{}', $5)""",
                    uuid.uuid4().hex[:12], fork_id, user_id, message, card_json_snapshot,
                )
            return fork_id
        except Exception as exc:
            print(f"[PostgresStore] Publish card failed: {exc}")
            return None

    async def update_published_card(self, card_id: str, user_id: str, card_json: str, description: str, tags: str, message: str, old_json: str) -> dict | None:
        try:
            diff = {}
            try:
                old = json.loads(old_json) if old_json else {}
                new_parsed = json.loads(card_json) if card_json else {}
                for k in set(list(old.keys()) + list(new_parsed.keys())):
                    if old.get(k) != new_parsed.get(k):
                        diff[k] = {"old": old.get(k, ""), "new": new_parsed.get(k, "")}
            except Exception:
                diff = {"_full": "parse error"}
            diff_json = json.dumps(diff, ensure_ascii=False)

            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE cards SET card_json = $1, market_description = $2, market_tags = $3, publish_message = $4
                       WHERE id = $5 AND deleted_at IS NULL""",
                    card_json, description, tags, message, card_id,
                )
                ver_row = await conn.fetchrow(
                    "SELECT COALESCE(MAX(version_num), 0) + 1 FROM card_versions WHERE card_id = $1",
                    card_id,
                )
                next_ver = ver_row[0] if ver_row else 1
                version_id = uuid.uuid4().hex[:12]
                await conn.execute(
                    """INSERT INTO card_versions (id, card_id, user_id, version_num, publish_message, diff_json, card_json_snapshot)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    version_id, card_id, user_id, next_ver, message, diff_json, card_json,
                )
                return {
                    "id": version_id,
                    "version_num": next_ver,
                    "publish_message": message,
                    "diff_json": diff_json,
                    "card_json_snapshot": card_json,
                    "created_at": None,
                }
        except Exception as exc:
            print(f"[PostgresStore] Update published card failed: {exc}")
            return None

    async def get_card_versions(self, card_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT id, card_id, user_id, version_num, publish_message, diff_json, card_json_snapshot, created_at
                       FROM card_versions WHERE card_id = $1 ORDER BY version_num DESC""",
                    card_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get card versions failed: {exc}")
            return []

    async def delete_card_version(self, card_id: str, version_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                existing = await conn.fetchrow(
                    "SELECT id FROM card_versions WHERE id = $1 AND card_id = $2",
                    version_id, card_id,
                )
                if not existing:
                    return False
                await conn.execute(
                    "DELETE FROM card_versions WHERE id = $1 AND card_id = $2",
                    version_id, card_id,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Delete card version failed: {exc}")
            return False

    async def update_card_version(self, card_id: str, version_id: str, publish_message: str) -> bool:
        try:
            async with await self._connect() as conn:
                existing = await conn.fetchrow(
                    "SELECT id FROM card_versions WHERE id = $1 AND card_id = $2",
                    version_id, card_id,
                )
                if not existing:
                    return False
                await conn.execute(
                    "UPDATE card_versions SET publish_message = $1 WHERE id = $2 AND card_id = $3",
                    publish_message, version_id, card_id,
                )
            return True
        except Exception as exc:
            print(f"[PostgresStore] Update card version failed: {exc}")
            return False

    async def get_card_forks(self, card_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data, c.likes, c.created_at,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(u.avatar_data, '') AS author_avatar
                       FROM cards c
                       LEFT JOIN users u ON u.id = c.user_id
                       WHERE c.forked_from = $1 AND c.visibility = 'public' AND c.deleted_at IS NULL
                       ORDER BY c.likes DESC, c.created_at DESC""",
                    card_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get card forks failed: {exc}")
            return []

    # ---- Admin: Featured Cards ----

    async def get_featured_cards(self) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    """SELECT fc.id, fc.card_id, fc.sort_order, fc.created_at,
                              c.name, c.card_json, c.avatar_data, c.likes,
                              c.user_id,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(u.avatar_data, '') AS author_avatar
                       FROM featured_cards fc
                       JOIN cards c ON c.id = fc.card_id
                       LEFT JOIN users u ON u.id = c.user_id
                       ORDER BY fc.sort_order ASC"""
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get featured cards failed: {exc}")
            return []

    async def add_featured_card(self, card_id: str) -> str | None:
        import uuid
        try:
            fid = uuid.uuid4().hex[:12]
            async with await self._connect() as conn:
                next_order = await conn.fetchval("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM featured_cards") or 0
                await conn.execute(
                    "INSERT INTO featured_cards (id, card_id, sort_order) VALUES ($1, $2, $3)",
                    fid, card_id, next_order,
                )
            return fid
        except Exception as exc:
            print(f"[PostgresStore] Add featured card failed: {exc}")
            return None

    async def remove_featured_card(self, id: str) -> bool:
        try:
            async with await self._connect() as conn:
                tag = await conn.execute("DELETE FROM featured_cards WHERE id = $1", id)
                return self._parse_rowcount(tag) > 0
        except Exception as exc:
            print(f"[PostgresStore] Remove featured card failed: {exc}")
            return False

    async def reorder_featured_cards(self, ids: list[str]) -> None:
        try:
            async with await self._connect() as conn:
                for idx, fid in enumerate(ids):
                    await conn.execute(
                        "UPDATE featured_cards SET sort_order = $1 WHERE id = $2",
                        idx, fid,
                    )
        except Exception as exc:
            print(f"[PostgresStore] Reorder featured cards failed: {exc}")
            raise

    # ---- Reading progress ----

    async def save_reading_progress(self, user_id: str, text_id: str, progress: float, scroll_position: int) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO reading_progress (user_id, text_id, progress, scroll_position, updated_at)
                       VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
                       ON CONFLICT(user_id, text_id) DO UPDATE SET
                           progress = EXCLUDED.progress,
                           scroll_position = EXCLUDED.scroll_position,
                           updated_at = EXCLUDED.updated_at""",
                    user_id, text_id, progress, scroll_position,
                )
        except Exception as exc:
            print(f"[PostgresStore] Save reading progress failed: {exc}")

    async def get_reading_progress(self, user_id: str, text_id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                row = await conn.fetchrow(
                    "SELECT progress, scroll_position, updated_at FROM reading_progress WHERE user_id = $1 AND text_id = $2",
                    user_id, text_id,
                )
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[PostgresStore] Get reading progress failed: {exc}")
            return None

    async def get_all_reading_progress(self, user_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                rows = await conn.fetch(
                    "SELECT text_id, progress, scroll_position, updated_at FROM reading_progress WHERE user_id = $1 ORDER BY updated_at DESC",
                    user_id,
                )
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[PostgresStore] Get all reading progress failed: {exc}")
            return []

    async def cleanup_empty_cards(self, text_id: str, user_id: str) -> int:
        """Soft-delete cards with empty card_json (cleanup after failed distillation)."""
        try:
            async with await self._connect() as conn:
                tag = await conn.execute(
                    "UPDATE cards SET deleted_at = CURRENT_TIMESTAMP::text WHERE text_id = $1 AND user_id = $2 AND (card_json IS NULL OR card_json = '' OR card_json = '{}')",
                    text_id, user_id,
                )
                return self._parse_rowcount(tag)
        except Exception as exc:
            print(f"[PostgresStore] Cleanup empty cards failed: {exc}")
            return 0

