"""SQLite implementation for StorageBase."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import aiosqlite  # type: ignore[import-not-found]
except ModuleNotFoundError:
    aiosqlite = None  # type: ignore[assignment]

from .base import StorageBase, StoreError

logger = logging.getLogger(__name__)

# 迁移应用次序（缺陷 15）。显式列出而不是 glob 整个目录：077 与 078 之间夹着 users 表
# 重建，次序有意义。**新增迁移文件必须登记在这里** —— tests/test_migration_dispatch.py
# 会扫目录求差集，漏登记即红（该文件的 `_NOT_APPLIED` 是唯一豁免出口，必须带理由）。
_MIGRATIONS_BEFORE_USER_REBUILD = (
    "002_voice.sql", "003_wechat.sql", "004_title_desc.sql", "005_characters_cache.sql",
    "006_card_avatar.sql", "007_text_type.sql", "008_original_char_count.sql", "009_users.sql",
    "010_user_id_texts.sql", "011_user_id_cards.sql", "012_user_id_sessions.sql",
    "013_admin.sql", "014_sessions_deleted_at.sql", "015_refresh_tokens.sql",
    "016_usage_stats.sql", "017_affinity.sql", "018_user_api_config.sql",
    "019_usage_stats_model.sql", "020_affinity_reason.sql", "021_user_avatar.sql",
    "022_message_retracted.sql", "023_user_email.sql", "024_verification_codes.sql",
    "025_market.sql", "026_group_sessions.sql", "027_voice_to_cards.sql",
    "028_comments_follows.sql", "029_soft_delete_cards.sql", "030_user_posts.sql",
    "031_text_comments.sql", "032_direct_messages.sql", "033_text_visibility.sql",
    "034_post_enhancements.sql", "035_card_updated_at.sql", "036_market_publish.sql",
    "038_card_comment_reports.sql", "039_user_profile_visibility.sql",
    "040_user_privacy_fields.sql", "041_banner_data.sql", "042_comment_ip_location.sql",
    "043_user_last_login.sql", "044_announcements.sql", "045_config_changelog.sql",
    "046_review_log.sql", "047_featured_cards.sql", "048_user_last_active.sql",
    "049_announcement_align.sql", "050_group_soft_delete.sql", "051_message_reactions.sql",
    "052_user_bio.sql", "053_reading_progress.sql", "054_text_soft_delete.sql",
    "055_chat_reply.sql", "056_coref_resolved.sql", "057_presence_visibility.sql",
    "058_following_visible.sql", "059_presence_visibility_rename.sql",
    "060_group_user_persona.sql", "061_post_location.sql", "062_card_comment_at_reply.sql",
    "063_text_cover.sql", "064_geo_block.sql", "065_user_consent.sql", "066_group_affinity.sql",
    "067_embedding_config.sql", "068_usage_estimated.sql", "069_dm_reactions.sql",
    "070_data_residency.sql", "071_cross_border_consent.sql", "072_card_sync.sql",
    "073_remote_cards.sql", "074_delete_outbox.sql", "075_dm_retracted.sql",
    "077_nickname.sql",
)

# 必须排在 users 表重建之后 —— 重建会把 idx_users_username_lower 一起丢掉。
# 079 建的是独立表（无外键、不碰 users），重建边界对它没有约束；放在这里是为了保住
# 「≤077 在重建前 / ≥078 在重建后」这条分段不变量，编号在段内仍单调。
_MIGRATIONS_AFTER_USER_REBUILD = (
    "078_username_lower.sql", "079_remote_user_profiles.sql", "080_group_user_avatar.sql",
    "081_refresh_token_grace.sql", "082_affinity_state.sql", "083_card_reports.sql",
    "084_distill_tasks.sql",
)

# `ALTER TABLE t ADD COLUMN c ...;` —— 迁移里唯一「重复执行即报错」的形态。
_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?P<table>\w+)\s+ADD\s+COLUMN\s+(?!IF\b)(?P<column>\w+)[^;]*;",
    re.IGNORECASE,
)


async def _existing_columns(conn: Any, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _apply_migration(conn: Any, path: Path) -> None:
    """执行一份迁移脚本，幂等靠**读现状**（PRAGMA table_info），不靠猜错误串。

    SQLite 没有 `ADD COLUMN IF NOT EXISTS`（PG 才有），`ALTER TABLE ... ADD COLUMN` 是迁移
    脚本里唯一「重复执行即报错」的形态。规则：

    - 脚本里每个 ADD COLUMN 的列都已存在 → 这份脚本早已应用过，**整份跳过**
      （这类脚本尾部常跟一段数据回填 UPDATE/INSERT，语义上只属于首次应用）
    - 有列缺失 → 剥掉那些**已存在**的 ADD COLUMN，其余照常执行

    **没有 except**：真失败照常上抛。此前 74 个块各自 `except Exception: print` 把它吞成
    「初始化成功」——「已建库重跑」这一正常路径每次都打一行假失败，而真正跑错也只留一行 print。
    """
    sql = path.read_text(encoding="utf-8")
    add_cols = list(_ADD_COLUMN_RE.finditer(sql))
    if add_cols:
        present: dict[str, set[str]] = {}
        for m in add_cols:
            table = m.group("table")
            if table not in present:
                present[table] = await _existing_columns(conn, table)
        if all(m.group("column") in present[m.group("table")] for m in add_cols):
            return
        sql = _ADD_COLUMN_RE.sub(
            lambda m: "" if m.group("column") in present[m.group("table")] else m.group(0), sql)
    await conn.executescript(sql)
    await conn.commit()


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
            # store-empty-ok: 关闭失败不改变本次操作的结果 —— 连接在此即弃；且 __aexit__
            # 上抛会顶替调用方真正的异常（Python 把它链成新异常），把真因埋掉。
            # 泄漏风险由这行 print 可见，不归 StoreError 管。
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
            except OSError as exc:
                print(f"[SQLiteStore] Read migration file failed: {exc}")
                raise

            try:
                async with aiosqlite.connect(self.db_path) as conn:  # type: ignore[union-attr]
                    await conn.execute("PRAGMA foreign_keys = ON;")
                    await _apply_migration(conn, migration_path)

                    for _name in _MIGRATIONS_BEFORE_USER_REBUILD:
                        _path = migrations_dir / _name
                        if _path.exists():
                            await _apply_migration(conn, _path)

                    # Migration 076 is handled inline as part of the operation — no SQL file needed.

                    # Data residency: remove password_hash/api_key/base_url/model from users
                    # SQLite table-recreate approach for portability (< 3.35.0 compat)
                    # `if` 守卫本身就是幂等机制（列已删就整块跳过），所以不需要 except ——
                    # 重建失败照常上抛，不再被 print 吞成「初始化成功」。
                    cursor = await conn.execute("PRAGMA table_info(users)")
                    all_cols = [row[1] for row in await cursor.fetchall()]
                    if "password_hash" in all_cols:
                        # Build the column list dynamically so that columns added by
                        # later migrations survive the rebuild; col_defs supplies the
                        # type for known ones (unknown ones fall back to TEXT).
                        keep_cols = [c for c in all_cols
                                     if c not in ("password_hash", "api_key", "base_url", "model")]
                        col_defs = {
                            "id": "TEXT PRIMARY KEY",
                            "username": "TEXT NOT NULL UNIQUE",
                            "is_admin": "INTEGER DEFAULT 0",
                            "is_disabled": "INTEGER DEFAULT 0",
                            "avatar_data": "TEXT DEFAULT ''",
                            "banner_data": "TEXT DEFAULT ''",
                            "bio": "TEXT DEFAULT ''",
                            "email": "TEXT DEFAULT ''",
                            "email_verified": "INTEGER DEFAULT 0",
                            "profile_stats_visible": "INTEGER DEFAULT 1",
                            "cards_visible": "INTEGER NOT NULL DEFAULT 1",
                            "books_visible": "INTEGER NOT NULL DEFAULT 1",
                            "following_visible": "INTEGER NOT NULL DEFAULT 1",
                            "presence_visibility": "TEXT NOT NULL DEFAULT 'mutual'",
                            "last_login_at": "TEXT DEFAULT ''",
                            "last_active_at": "TEXT DEFAULT ''",
                            "embedding_key": "TEXT DEFAULT ''",
                            "embedding_region": "TEXT DEFAULT 'cn'",
                            "home_region": "TEXT NOT NULL DEFAULT 'cn-shenzhen'",
                            "nickname": "TEXT DEFAULT ''",
                            "username_lower": "TEXT",
                            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                        }
                        col_list = ", ".join(keep_cols)
                        create_defs = ", ".join(
                            f"{c} {col_defs.get(c, 'TEXT DEFAULT \"\"')}"
                            for c in keep_cols
                        )
                        await conn.executescript(f"""
                            PRAGMA defer_foreign_keys = ON;
                            CREATE TABLE users_mig ({create_defs});
                            INSERT INTO users_mig ({col_list}) SELECT {col_list} FROM users;
                            DROP TABLE users;
                            ALTER TABLE users_mig RENAME TO users;
                        """)
                        await conn.commit()

                    # 078 起必须排在 users 表重建之后 —— 重建会丢掉 idx_users_username_lower。
                    # 原先这段额外内联做了一遍 ADD COLUMN/backfill/CREATE INDEX，与 078 文件重复；
                    # 内联那份已删 —— 索引创建失败（用户名重复）照样上抛，不再只 print。
                    for _name in _MIGRATIONS_AFTER_USER_REBUILD:
                        _path = migrations_dir / _name
                        if _path.exists():
                            await _apply_migration(conn, _path)

                    # 两个去重 DELETE 依赖窗口函数（SQLite >= 3.25）。此前靠 except 猜
                    # "no such window function" 来兼容老库 —— 换成一次版本判断：能力不足时
                    # 明确不发这两条语句，其余失败照常上抛。
                    if sqlite3.sqlite_version_info >= (3, 25):
                        # Auto-deduplicate: keep only the newest card per text_id+name
                        # Exclude forked cards (forked_from != '') to preserve independent copies
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

                        # Auto-deduplicate forked cards: same forked_from+user_id+text_id, keep newest
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

                self._initialized = True
            except Exception as exc:
                print(f"[SQLiteStore] Initialize database failed: {exc}")
                raise

    async def _connect(self):
        """Open a sqlite connection and return a managed context wrapper."""
        await self._ensure_initialized()
        conn = await aiosqlite.connect(self.db_path)  # type: ignore[union-attr]
        conn.row_factory = aiosqlite.Row  # type: ignore[union-attr]
        # busy_timeout 最先设置，兜底普通读写的锁等待；但 journal_mode 的切换锁不受它保护。
        await conn.execute("PRAGMA busy_timeout = 5000;")
        await conn.execute("PRAGMA foreign_keys = ON;")
        # journal_mode 是库全局持久状态：稳态已 WAL 时此处是 no-op，只有库处于 rollback
        # （全新部署/迁移后/被并发进程切换）才需切换，而该切换锁不受 busy_timeout 保护、
        # 遇写锁会瞬时失败。确属锁争用则跳过——连接按当前 journal 模式运行（rollback 同样
        # ACID），下一次无竞争的 _connect 会补切回 WAL；非锁类异常照常上抛，不静默降级。
        try:
            await conn.execute("PRAGMA journal_mode = WAL;")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            logger.warning("[SQLiteStore] journal_mode=WAL skipped under lock: %s", exc)
        return _ConnectionContext(conn)

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
            return val  # no binary in this schema; keep as-is defensively
        return val

    @staticmethod
    def _normalize_record(rec: dict) -> dict:
        """Normalize all values in a record dict for JSON safety."""
        return {k: SQLiteStore._normalize_value(v) for k, v in rec.items()}

    @staticmethod
    def _row_to_dict(row: Any) -> dict | None:
        """Convert sqlite row to dict with type normalization."""
        if row is None:
            return None
        return SQLiteStore._normalize_record(dict(row))

    @staticmethod
    def _list_rows(rows) -> list[dict]:
        """Convert a list of sqlite rows to a list of normalized dicts."""
        return [SQLiteStore._row_to_dict(r) for r in rows]

    async def execute(self, sql: str, params=()) -> None:
        """Execute a single SQL statement (INSERT/UPDATE/DELETE)."""
        async with await self._connect() as conn:
            await conn.execute(sql, params)
            await conn.commit()

    async def fetch_one(self, sql: str, params=()) -> dict | None:
        """Query a single row, returns dict or None."""
        async with await self._connect() as conn:
            cursor = await conn.execute(sql, params)
            row = await cursor.fetchone()
            return self._row_to_dict(row)

    async def save_text(self, id: str, filename: str, content: str, title: str = "", description: str = "", text_type: str = "story", original_char_count: int | None = None, user_id: str = "", content_resolved: str = "", coref_resolved: int = 0) -> dict:
        """Save or update one text record."""
        try:
            char_count = len(content)
            async with await self._connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO texts (id, filename, content, char_count, title, description, text_type, original_char_count, user_id, content_resolved, coref_resolved)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        filename = excluded.filename,
                        content = excluded.content,
                        char_count = excluded.char_count,
                        title = excluded.title,
                        description = excluded.description,
                        text_type = excluded.text_type,
                        original_char_count = excluded.original_char_count,
                        user_id = excluded.user_id,
                        content_resolved = excluded.content_resolved,
                        coref_resolved = excluded.coref_resolved
                    """,
                    (id, filename, content, char_count, title, description, text_type, original_char_count, user_id, content_resolved, coref_resolved),
                )
                await conn.commit()
            return await self.get_text_owned(id, user_id) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Save text failed: {exc}")
            raise

    async def update_text_resolved(self, text_id: str, content_resolved: str) -> None:
        """Write back coref-resolved content and mark coref_resolved=1."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE texts SET content_resolved=?, coref_resolved=1 WHERE id=?",
                    (content_resolved, text_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] update_text_resolved failed: {exc}")
            raise

    async def update_text_cover(self, text_id: str, cover_data: str) -> None:
        """Update cover_data for a text."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE texts SET cover_data = ? WHERE id = ?",
                    (cover_data, text_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] update_text_cover failed: {exc}")
            raise

    async def get_text_unscoped(self, id: str) -> dict | None:
        """Get one text record by id, no ownership filter."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count, user_id, deleted_at, content_resolved, coref_resolved FROM texts WHERE id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get text failed: {exc}")
            raise

    async def get_text_owned(self, id: str, user_id: str) -> dict | None:
        """Get one text record by id, filtered to its owner in SQL."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, filename, title, description, content, char_count, created_at, text_type, original_char_count, user_id, deleted_at, content_resolved, coref_resolved FROM texts WHERE id = ? AND user_id = ?",
                    (id, user_id),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get text (owned) failed: {exc}")
            raise

    async def list_texts(self, user_id: str = "") -> list[dict]:
        """List texts for a user in descending created order (excludes soft-deleted)."""
        try:
            async with await self._connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        """
                        SELECT id, filename, title, description, char_count, created_at, text_type, original_char_count, visibility, cover_data
                        FROM texts WHERE user_id = ? AND (deleted_at IS NULL OR deleted_at = '')
                        ORDER BY created_at DESC
                        """, (user_id,),
                    )
                else:
                    cursor = await conn.execute(
                        """
                        SELECT id, filename, title, description, char_count, created_at, text_type, original_char_count, visibility, cover_data
                        FROM texts WHERE (deleted_at IS NULL OR deleted_at = '')
                        ORDER BY created_at DESC
                        """
                    )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
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
        """Soft-delete one text record."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE texts SET deleted_at = ? WHERE id = ? AND (deleted_at IS NULL OR deleted_at = '')",
                    (now, id),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete text failed: {exc}")
            raise

    async def get_deleted_texts(self, user_id: str) -> list[dict]:
        """List soft-deleted texts for a user."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT id, filename, title, description, char_count, created_at, text_type, original_char_count, deleted_at
                    FROM texts WHERE user_id = ? AND deleted_at != ''
                    ORDER BY deleted_at DESC
                    """,
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get deleted texts failed: {exc}")
            raise

    async def restore_text(self, id: str) -> bool:
        """Restore a soft-deleted text."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE texts SET deleted_at = '' WHERE id = ? AND deleted_at != ''",
                    (id,),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Restore text failed: {exc}")
            raise

    async def detach_text_cards(self, id: str) -> int:
        """Detach all cards from a text by setting text_id to ''."""
        try:
            async with await self._connect() as conn:
                # Temporarily disable FK checks: '' won't reference texts.id
                await conn.execute("PRAGMA foreign_keys = OFF")
                cursor = await conn.execute(
                    "UPDATE cards SET text_id = '' WHERE text_id = ?",
                    (id,),
                )
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.commit()
                return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Detach text cards failed: {exc}")
            raise

    async def hard_delete_text(self, id: str, keep_cards: bool = False) -> bool:
        """Permanently delete a text.

        keep_cards=True: detach cards (text_id→NULL) so cards+sessions survive.
        keep_cards=False (default): cascade-delete cards and their sessions.
        When keep_cards=False, public cards get delete-propagation enqueued.
        """
        try:
            async with await self._connect() as conn:
                if keep_cards:
                    # Detach cards from the text so CASCADE doesn't hit them
                    await conn.execute("PRAGMA foreign_keys = OFF")
                    await conn.execute(
                        "UPDATE cards SET text_id = '' WHERE text_id = ?",
                        (id,),
                    )
                    await conn.execute("PRAGMA foreign_keys = ON")
                else:
                    # Enqueue delete propagation for public cards
                    cursor = await conn.execute(
                        "SELECT id FROM cards WHERE text_id = ? AND visibility = 'public' AND deleted_at IS NULL",
                        (id,),
                    )
                    public_ids = [row[0] for row in (await cursor.fetchall())]
                    for cid in public_ids:
                        await conn.execute(
                            "INSERT OR IGNORE INTO cross_border_delete_outbox (op_type, target_id, payload) VALUES (?, ?, ?)",
                            ("card_delete", cid, ""),
                        )
                    # Cascade-delete sessions, then cards
                    await conn.execute(
                        "DELETE FROM sessions WHERE card_id IN (SELECT id FROM cards WHERE text_id = ?)",
                        (id,),
                    )
                    await conn.execute("DELETE FROM cards WHERE text_id = ?", (id,))
                # Delete reading progress
                await conn.execute("DELETE FROM reading_progress WHERE text_id = ?", (id,))
                # 清理本文本的蒸馏行。顺序**先父后子**，与通常相反：父行一消失，
                # save_distill_chunk 的 WHERE EXISTS 即失效，写路径随之关闭。
                # SQLite 写是库级序列化的（删除事务持写锁直至提交），并发分片写要么在
                # 事务前提交（随即被一并删掉）、要么阻塞到提交后（父行已无、跳过）——
                # 窗口从根上不存在，故此处无需 PG 那侧的 FOR SHARE，顺序即足够。
                # 两表零外键（migration 084），删父不会级联子，必须显式删两张。
                # 不清理 delete_card / purge_card / detach_text_cards：断点行的生命周期
                # **跟文本走、不跟卡走** —— 行身份是 (user, text, character)，card_id 只是
                # 产物反向指针，删卡/解绑都不改变这个身份，故那些路径不产生孤儿行。
                # 别把它读成「保住断点省 API」：续跑发现 find_interrupted_distill 只认
                # status='interrupted'，删卡时行一般是 running/done/error，重蒸命不中、
                # 整批重跑。保留只是「不去动与文本无关的状态」的自然结果，不是收益论证。
                # 证据（双 store 现跑现测，2026-09-12）：tests/perf/distill_resume_reachability.py
                # 与 tests/perf/distill_orphan_matrix.py，产物 docs/evidence/distill-resume-reachability.json
                # 与 docs/evidence/distill-orphan-matrix.json；见 AGENTS.md 缺陷 20。
                cursor = await conn.execute(
                    "SELECT task_id FROM distill_tasks WHERE text_id = ?", (id,))
                dt_ids = [row[0] for row in await cursor.fetchall()]
                await conn.execute("DELETE FROM distill_tasks WHERE text_id = ?", (id,))
                for tid in dt_ids:
                    await conn.execute("DELETE FROM distill_chunks WHERE task_id = ?", (tid,))
                # Delete the text itself
                cursor = await conn.execute("DELETE FROM texts WHERE id = ?", (id,))
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Hard delete text failed: {exc}")
            raise

    async def get_text_deletion_impact(self, text_id: str, user_id: str) -> dict:
        """Count cards, sessions, and messages affected by text deletion."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM cards WHERE text_id = ? AND deleted_at IS NULL",
                    (text_id,),
                )
                row = await cursor.fetchone()
                card_count = row[0] if row else 0

                cursor = await conn.execute(
                    "SELECT COUNT(DISTINCT s.id) FROM sessions s JOIN cards c ON s.card_id = c.id WHERE c.text_id = ? AND s.deleted_at IS NULL",
                    (text_id,),
                )
                row = await cursor.fetchone()
                session_count = row[0] if row else 0

                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM messages m WHERE m.session_id IN (SELECT s.id FROM sessions s JOIN cards c ON s.card_id = c.id WHERE c.text_id = ?)",
                    (text_id,),
                )
                row = await cursor.fetchone()
                message_count = row[0] if row else 0

                # 永久删除会连带清本文本的蒸馏行（hard_delete_text），弹窗须如实告知。
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM distill_tasks WHERE text_id = ?", (text_id,),
                )
                row = await cursor.fetchone()
                distill_task_count = row[0] if row else 0

                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM distill_chunks WHERE task_id IN "
                    "(SELECT task_id FROM distill_tasks WHERE text_id = ?)",
                    (text_id,),
                )
                row = await cursor.fetchone()
                distill_chunk_count = row[0] if row else 0

            return {
                "card_count": card_count,
                "session_count": session_count,
                "message_count": message_count,
                "distill_task_count": distill_task_count,
                "distill_chunk_count": distill_chunk_count,
            }
        except Exception as exc:
            print(f"[SQLiteStore] Get text deletion impact failed: {exc}")
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
                        "UPDATE cards SET card_json = ?, deleted_at = NULL WHERE id = ?",
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
            pub_sub = ("SELECT c2.id FROM cards c2 WHERE c2.forked_from = c.id"
                       " AND c2.visibility = 'public' AND c2.deleted_at IS NULL LIMIT 1")
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    f"SELECT c.id AS id, c.text_id AS text_id, c.name AS name, c.card_json AS card_json, c.created_at AS created_at, c.user_id AS user_id, c.visibility AS visibility, c.forked_from AS forked_from, c.deleted_at AS deleted_at, c.avatar_data AS avatar_data, c.market_description AS market_description, c.market_tags AS market_tags, c.publish_message AS publish_message, ({pub_sub}) AS published_id, COALESCE(u.username, '') AS author_username FROM cards c LEFT JOIN users u ON c.user_id = u.id WHERE c.id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get card failed: {exc}")
            raise

    async def get_card_detail(self, card_id: str, user_id: str) -> dict | None:
        """Get card detail with author info — works for both market and non-market cards."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
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
                        WHERE c.id = ? AND c.deleted_at IS NULL""",
                    (card_id,),
                )
                row = await cursor.fetchone()
                card = self._row_to_dict(row)
                if card:
                    like_cursor = await conn.execute(
                        "SELECT 1 FROM card_likes WHERE card_id = ? AND user_id = ?",
                        (card_id, user_id),
                    )
                    card["liked_by_me"] = await like_cursor.fetchone() is not None
                    card["is_market_card"] = card["visibility"] == "public"
                return card
        except Exception as exc:
            print(f"[SQLiteStore] Get card detail failed: {exc}")
            raise StoreError("get_card_detail", exc) from exc

    async def get_market_card_detail(self, card_id: str, user_id: str) -> dict | None:
        """Get a single public card detail with author info. Falls back to remote_cards."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
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
                        WHERE c.id = ? AND c.visibility = 'public' AND c.deleted_at IS NULL""",
                    (card_id,),
                )
                row = await cursor.fetchone()
                card = self._row_to_dict(row)
                if card:
                    like_cursor = await conn.execute(
                        "SELECT 1 FROM card_likes WHERE card_id = ? AND user_id = ?",
                        (card_id, user_id),
                    )
                    card["liked_by_me"] = await like_cursor.fetchone() is not None
                    card["is_remote"] = False
                    card["origin_region"] = ""
                    return card

            # Fallback: check remote_cards
            remote = await self.get_remote_card(card_id)
            if remote:
                remote["is_remote"] = True
                remote["liked_by_me"] = False
                remote["author_name"] = ""
                remote["author_avatar"] = ""
                remote["text_title"] = ""
                remote["comment_count"] = 0
                remote["forked_from"] = ""
                remote["likes"] = 0
                remote["publish_message"] = ""
                remote["created_at"] = remote.get("origin_created_at", "")
            return remote
        except Exception as exc:
            print(f"[SQLiteStore] Get market card detail failed: {exc}")
            raise StoreError("get_market_card_detail", exc) from exc

    async def list_cards(self, text_id: str, user_id: str = "") -> list[dict]:
        """List all cards under one text id, optionally filtered by user."""
        try:
            pub_sub = ("SELECT c2.id FROM cards c2 WHERE c2.forked_from = cards.id"
                       " AND c2.visibility = 'public' AND c2.deleted_at IS NULL LIMIT 1")
            async with await self._connect() as conn:
                if user_id:
                    cursor = await conn.execute(
                        f"SELECT id, text_id, name, card_json, created_at, visibility, forked_from, market_description, market_tags, ({pub_sub}) AS published_id FROM cards WHERE text_id = ? AND user_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
                        (text_id, user_id),
                    )
                else:
                    cursor = await conn.execute(
                        f"SELECT id, text_id, name, card_json, created_at, visibility, forked_from, market_description, market_tags, ({pub_sub}) AS published_id FROM cards WHERE text_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
                        (text_id,),
                    )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] List cards failed: {exc}")
            raise

    async def list_standalone_cards(self, user_id: str) -> list[dict]:
        """List cards with no text_id attachment (standalone/market-forked)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, text_id, name, card_json, created_at, visibility, forked_from, market_description, market_tags FROM cards WHERE (text_id IS NULL OR text_id = '') AND user_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] List standalone cards failed: {exc}")
            raise

    async def save_card_avatar(self, card_id: str, avatar_data: str) -> None:
        """Save base64 avatar image for a card, and sync to published copy."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET avatar_data = ? WHERE id = ?",
                    (avatar_data, card_id),
                )
                # Also update published fork's avatar if this card has one
                await conn.execute(
                    "UPDATE cards SET avatar_data = ? WHERE forked_from = ? AND visibility = 'public' AND deleted_at IS NULL",
                    (avatar_data, card_id),
                )
                # If this card IS a published fork, sync back to draft too
                cursor = await conn.execute(
                    "SELECT forked_from FROM cards WHERE id = ? AND forked_from IS NOT NULL AND forked_from != ''",
                    (card_id,),
                )
                row = await cursor.fetchone()
                if row:
                    await conn.execute(
                        "UPDATE cards SET avatar_data = ? WHERE id = ?",
                        (avatar_data, row[0]),
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
            raise StoreError("get_card_avatar", exc) from exc

    # ── Market / public card methods ──────────────────────────

    async def list_public_cards(self, page: int = 1, page_size: int = 20, sort: str = "new", tag: str = "") -> list[dict]:
        """List public cards with pagination and sorting (hot=likes, new=created_at).

        UNION local cards + remote_cards.  Remote cards get is_remote=1 and
        origin_region; they have 0 likes, 0 comments, and no author/text FK joins.
        """
        try:
            order = "likes DESC, created_at DESC" if sort == "hot" else "created_at DESC"
            offset = (page - 1) * page_size
            if tag:
                like_pattern = f"%{tag}%"
                params: list[Any] = [like_pattern, like_pattern, page_size, offset]
                tag_clause = " AND c.market_tags LIKE ?"
                remote_tag_clause = " AND rc.market_tags LIKE ?"
            else:
                params = [page_size, offset]
                tag_clause = ""
                remote_tag_clause = ""
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    f"""SELECT id, name, card_json, user_id, avatar_data,
                              forked_from, likes, created_at,
                              market_description, market_tags,
                              author_name, author_avatar, text_title,
                              comment_count, is_remote, origin_region
                        FROM (
                          SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data,
                                 c.forked_from, c.likes, c.created_at,
                                 c.market_description, c.market_tags,
                                 COALESCE(u.username, '') AS author_name,
                                 COALESCE(u.avatar_data, '') AS author_avatar,
                                 COALESCE(t.title, '') AS text_title,
                                 (SELECT COUNT(*) FROM card_comments cc WHERE cc.card_id = c.id) AS comment_count,
                                 0 AS is_remote, '' AS origin_region
                          FROM cards c
                          LEFT JOIN users u ON u.id = c.user_id
                          LEFT JOIN texts t ON t.id = c.text_id
                          WHERE c.visibility = 'public' AND c.deleted_at IS NULL{tag_clause}

                          UNION ALL

                          SELECT rc.id, rc.name, rc.card_json, rc.user_id, rc.avatar_data,
                                 '' AS forked_from, 0 AS likes, COALESCE(rc.origin_created_at, '') AS created_at,
                                 rc.market_description, rc.market_tags,
                                 '' AS author_name, '' AS author_avatar, '' AS text_title,
                                 0 AS comment_count, 1 AS is_remote, rc.origin_region
                          FROM remote_cards rc
                          WHERE 1=1{remote_tag_clause}
                        ) combined
                        ORDER BY {order}
                        LIMIT ? OFFSET ?""",
                    params,
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] List public cards failed: {exc}")
            raise

    async def list_public_cards_total(self, tag: str = "") -> int:
        """Return total count of public cards + remote_cards (for pagination)."""
        try:
            tag_clause = " AND market_tags LIKE ?" if tag else ""
            params: list[Any] = [f"%{tag}%"] if tag else []
            async with await self._connect() as conn:
                # Include remote_cards in the count
                if tag:
                    cursor = await conn.execute(
                        f"""SELECT COUNT(*) FROM (
                          SELECT id FROM cards WHERE visibility = 'public' AND deleted_at IS NULL{tag_clause}
                          UNION ALL
                          SELECT id FROM remote_cards WHERE market_tags LIKE ?
                        )""",
                        [f"%{tag}%", f"%{tag}%"],
                    )
                else:
                    cursor = await conn.execute(
                        """SELECT COUNT(*) FROM (
                          SELECT id FROM cards WHERE visibility = 'public' AND deleted_at IS NULL
                          UNION ALL
                          SELECT id FROM remote_cards
                        )""",
                    )
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] List public cards total failed: {exc}")
            raise StoreError("list_public_cards_total", exc) from exc

    async def search_public_cards(self, keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
        """Search public cards by name match (case-insensitive). Also searches remote_cards."""
        try:
            offset = (page - 1) * page_size
            pattern = f"%{keyword}%"
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, name, card_json, user_id, avatar_data,
                              forked_from, likes, created_at,
                              market_description, market_tags,
                              author_name, author_avatar, text_title,
                              comment_count, is_remote, origin_region
                        FROM (
                          SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data,
                                 c.forked_from, c.likes, c.created_at,
                                 c.market_description, c.market_tags,
                                 COALESCE(u.username, '') AS author_name,
                                 COALESCE(u.avatar_data, '') AS author_avatar,
                                 COALESCE(t.title, '') AS text_title,
                                 (SELECT COUNT(*) FROM card_comments cc WHERE cc.card_id = c.id) AS comment_count,
                                 0 AS is_remote, '' AS origin_region
                          FROM cards c
                          LEFT JOIN users u ON u.id = c.user_id
                          LEFT JOIN texts t ON t.id = c.text_id
                          WHERE c.visibility = 'public' AND c.name LIKE ? AND c.deleted_at IS NULL

                          UNION ALL

                          SELECT rc.id, rc.name, rc.card_json, rc.user_id, rc.avatar_data,
                                 '' AS forked_from, 0 AS likes, COALESCE(rc.origin_created_at, '') AS created_at,
                                 rc.market_description, rc.market_tags,
                                 '' AS author_name, '' AS author_avatar, '' AS text_title,
                                 0 AS comment_count, 1 AS is_remote, rc.origin_region
                          FROM remote_cards rc
                          WHERE rc.name LIKE ?
                        ) combined
                        ORDER BY likes DESC, created_at DESC
                        LIMIT ? OFFSET ?""",
                    (pattern, pattern, page_size, offset),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Search public cards failed: {exc}")
            raise

    async def search_public_cards_total(self, keyword: str) -> int:
        """Return total count of matching public cards + remote_cards."""
        try:
            pattern = f"%{keyword}%"
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT COUNT(*) FROM (
                      SELECT id FROM cards WHERE visibility = 'public' AND name LIKE ? AND deleted_at IS NULL
                      UNION ALL
                      SELECT id FROM remote_cards WHERE name LIKE ?
                    )""",
                    (pattern, pattern),
                )
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] Search public cards total failed: {exc}")
            raise StoreError("search_public_cards_total", exc) from exc

    async def global_search(self, keyword: str, user_id: str = "") -> dict:
        """Search cards, texts, users by keyword. Returns max 5 per type."""
        like = f"%{keyword}%"
        try:
            async with await self._connect() as conn:
                # Cards (public local + remote)
                cur = await conn.execute(
                    """SELECT id, name, card_json, avatar_data, author_name
                        FROM (
                          (SELECT c.id, c.name, c.card_json, c.avatar_data,
                                  COALESCE(u.username, '') AS author_name
                           FROM cards c
                           LEFT JOIN users u ON u.id = c.user_id
                           WHERE c.visibility = 'public' AND c.deleted_at IS NULL
                             AND c.name LIKE ?
                           ORDER BY c.likes DESC
                           LIMIT 5)
                          UNION ALL
                          (SELECT id, name, card_json, avatar_data, '' AS author_name
                           FROM remote_cards
                           WHERE name LIKE ?
                           LIMIT 5)
                        ) combined LIMIT 5""",
                    (like, like),
                )
                cards = self._list_rows(await cur.fetchall())

                # Texts (user's own only)
                cur = await conn.execute(
                    """SELECT id, title, filename, char_count
                       FROM texts
                       WHERE user_id = ? AND (title LIKE ? OR filename LIKE ?)
                       ORDER BY created_at DESC
                       LIMIT 5""",
                    (user_id, like, like),
                )
                texts = self._list_rows(await cur.fetchall())

                # Users
                cur = await conn.execute(
                    """SELECT id, username, nickname, avatar_data
                       FROM users
                       WHERE (username LIKE ? OR nickname LIKE ?) AND is_disabled = 0
                       ORDER BY username
                       LIMIT 5""",
                    (like, like),
                )
                users = self._list_rows(await cur.fetchall())

            return {"cards": cards, "texts": texts, "users": users}
        except Exception as exc:
            print(f"[SQLiteStore] Global search failed: {exc}")
            raise StoreError("global_search", exc) from exc

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
            raise StoreError("fork_card", exc) from exc

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
        """Soft delete: set deleted_at timestamp, enqueue outbox atomically."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT visibility FROM cards WHERE id = ?", (card_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                visibility = row["visibility"]

                await conn.execute(
                    "UPDATE cards SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                    (now, card_id),
                )

                if visibility == "public":
                    await conn.execute(
                        """INSERT OR IGNORE INTO cross_border_delete_outbox
                           (op_type, target_id, payload) VALUES (?, ?, ?)""",
                        ("card_delete", card_id, ""),
                    )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Delete card failed: {exc}")
            raise StoreError("delete_card", exc) from exc

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
            raise StoreError("restore_card", exc) from exc

    async def purge_card(self, card_id: str) -> bool:
        """Permanently delete a card, enqueue outbox atomically."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT visibility FROM cards WHERE id = ?", (card_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return True  # already gone
                visibility = row["visibility"]

                await conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))

                if visibility == "public":
                    await conn.execute(
                        """INSERT OR IGNORE INTO cross_border_delete_outbox
                           (op_type, target_id, payload) VALUES (?, ?, ?)""",
                        ("card_delete", card_id, ""),
                    )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Purge card failed: {exc}")
            raise StoreError("purge_card", exc) from exc

    async def list_deleted_cards(self, user_id: str) -> list[dict]:
        """List soft-deleted cards for a user (recycle bin)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, text_id, name, card_json, created_at, visibility, forked_from, deleted_at FROM cards WHERE deleted_at IS NOT NULL AND user_id = ? ORDER BY deleted_at DESC",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
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
            raise StoreError("update_card_visibility", exc) from exc

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
            raise StoreError("get_liked_card_ids", exc) from exc

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
                    return self._row_to_dict(row)
                return None
        except Exception as exc:
            print(f"[SQLiteStore] Get recent card session failed: {exc}")
            raise StoreError("get_recent_card_session", exc) from exc

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
            return await self.get_session_owned(id, user_id) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Save session failed: {exc}")
            raise

    async def get_session_unscoped(self, id: str) -> dict | None:
        """Get one session with character name, with no ownership filter."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, s.user_id, s.deleted_at, c.text_id, c.name AS character_name
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

    async def get_session_owned(self, id: str, user_id: str) -> dict | None:
        """Get one session by id, filtered to its owner in SQL."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    SELECT s.id, s.card_id, s.user_role, s.avatar_data, s.created_at, s.updated_at, s.user_id, s.deleted_at, c.text_id, c.name AS character_name
                    FROM sessions s
                    JOIN cards c ON s.card_id = c.id
                    WHERE s.id = ? AND s.user_id = ?
                    """,
                    (id, user_id),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get session (owned) failed: {exc}")
            raise

    async def update_session_avatar(self, session_id: str, user_id: str, avatar_data: str) -> bool:
        """Update session-level user avatar. Ownership check prevents cross-user writes."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE sessions SET avatar_data = ? WHERE id = ? AND user_id = ?",
                    (avatar_data, session_id, user_id),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Update session avatar failed: {exc}")
            raise

    async def update_group_avatar(self, group_id: str, user_id: str, avatar_data: str) -> bool:
        """Update group-level user avatar. Ownership check prevents cross-user writes."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE group_sessions SET user_avatar_data = ? WHERE id = ? AND user_id = ?",
                    (avatar_data, group_id, user_id),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Update group avatar failed: {exc}")
            raise

    async def list_sessions(
        self, keyword: str, character: str, text_id: str, page: int, page_size: int, user_id: str = "", card_id: str = ""
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
        if card_id:
            where_clauses.append("s.card_id = ?")
            params.append(card_id)
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
                "items": self._list_rows(rows),
            }
        except Exception as exc:
            print(f"[SQLiteStore] List sessions failed: {exc}")
            raise

    async def delete_session(self, id: str) -> bool:
        """Soft-delete one session (set deleted_at timestamp)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
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
            now = datetime.now(timezone.utc).isoformat()
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
            return self._list_rows(rows)
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
            return self._row_to_dict(row)
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
        self, session_id: str, role: str, content: str, rag_context: str,
        reply_to_id: int | None = None, reply_to_preview: str = "",
        retracted: bool = False,
    ) -> dict:
        """Save one message and touch session updated_at."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """
                    INSERT INTO messages (session_id, role, content, rag_context, reply_to_id, reply_to_preview, retracted)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, role, content, rag_context, reply_to_id, reply_to_preview, retracted),
                )
                await conn.execute(
                    "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (session_id,),
                )
                await conn.commit()
                message_id = int(cursor.lastrowid)

                row_cursor = await conn.execute(
                    """
                    SELECT id, session_id, role, content, rag_context, created_at, reply_to_id, reply_to_preview, retracted
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
                    SELECT id, session_id, role, content, rag_context, created_at, reply_to_id, reply_to_preview, retracted
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id ASC
                    """,
                    (session_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get messages failed: {exc}")
            raise

    # ── group sessions ────────────────────────────────────────────────

    async def create_group_session(
        self, id: str, name: str, card_ids: list[str], user_id: str,
        user_persona_type: str = "director",
        user_persona_card_id: str = "",
        user_persona_name: str = "",
        user_persona_desc: str = "",
    ) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO group_sessions (id, name, card_ids, user_id,
                       user_persona_type, user_persona_card_id,
                       user_persona_name, user_persona_desc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (id, name, json.dumps(card_ids), user_id,
                     user_persona_type, user_persona_card_id,
                     user_persona_name, user_persona_desc),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Create group session failed: {exc}")
            raise

    async def get_group_session(self, id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, name, card_ids, user_id, created_at, deleted_at, user_persona_type, user_persona_card_id, user_persona_name, user_persona_desc, user_avatar_data FROM group_sessions WHERE id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            if row is None:
                return None
            d = self._row_to_dict(row)
            d["card_ids"] = json.loads(d["card_ids"])
            return d
        except Exception as exc:
            print(f"[SQLiteStore] Get group session failed: {exc}")
            raise

    async def list_group_sessions(self, user_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, name, card_ids, user_id, created_at,
                              user_persona_type, user_persona_card_id,
                              user_persona_name, user_persona_desc,
                              user_avatar_data
                       FROM group_sessions
                       WHERE user_id = ? AND (deleted_at IS NULL OR deleted_at = '')
                       ORDER BY created_at DESC""",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            results = []
            for row in rows:
                d = self._row_to_dict(row)
                d["card_ids"] = json.loads(d["card_ids"])
                results.append(d)
            return results
        except Exception as exc:
            print(f"[SQLiteStore] List group sessions failed: {exc}")
            raise

    async def get_deleted_group_sessions(self, user_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, name, card_ids, user_id, created_at, deleted_at
                       FROM group_sessions
                       WHERE user_id = ? AND deleted_at != '' AND deleted_at IS NOT NULL
                       ORDER BY deleted_at DESC""",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            results = []
            for row in rows:
                d = self._row_to_dict(row)
                d["card_ids"] = json.loads(d["card_ids"])
                results.append(d)
            return results
        except Exception as exc:
            print(f"[SQLiteStore] Get deleted group sessions failed: {exc}")
            raise

    async def restore_group_session(self, id: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE group_sessions SET deleted_at = '' WHERE id = ?",
                    (id,),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Restore group session failed: {exc}")
            raise

    async def hard_delete_group_session(self, id: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute("DELETE FROM group_messages WHERE group_id = ?", (id,))
                await conn.execute("DELETE FROM group_sessions WHERE id = ?", (id,))
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Hard delete group session failed: {exc}")
            raise

    async def save_group_message(
        self, group_id: str, speaker: str, role: str, content: str,
        speaker_card_id: str = "", reply_to_id: int | None = None,
        reply_to_preview: str = "",
    ) -> int:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """INSERT INTO group_messages (group_id, speaker, role, content, speaker_card_id, reply_to_id, reply_to_preview)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (group_id, speaker, role, content, speaker_card_id, reply_to_id, reply_to_preview),
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
                    """SELECT id, group_id, speaker, role, content, speaker_card_id, created_at,
                              reply_to_id, reply_to_preview
                       FROM group_messages
                       WHERE group_id = ?
                       ORDER BY id ASC""",
                    (group_id,),
                )
                rows = await cursor.fetchall()
            messages = self._list_rows(rows)
            # Attach reactions to each message
            msg_ids = [m["id"] for m in messages]
            if msg_ids:
                reactions_map = await self.get_reactions(msg_ids)
                for m in messages:
                    m["reactions"] = reactions_map.get(m["id"], [])
            return messages
        except Exception as exc:
            print(f"[SQLiteStore] Get group messages failed: {exc}")
            raise

    _REACTION_TABLES = frozenset({"message_reactions", "dm_reactions"})

    @staticmethod
    def _aggregate_reactions(rows: list[dict]) -> dict:
        """从 (message_id, emoji, user_id) 行聚合成统一返回结构，与具体来源表无关。"""
        result: dict = {}
        for row in rows:
            mid = row["message_id"]
            emoji = row["emoji"]
            uid = row["user_id"]
            result.setdefault(mid, {}).setdefault(
                emoji, {"emoji": emoji, "count": 0, "users": []}
            )
            result[mid][emoji]["count"] += 1
            result[mid][emoji]["users"].append(uid)
        return {mid: list(m.values()) for mid, m in result.items()}

    async def _toggle_reaction_generic(self, table: str, message_id, user_id: str, emoji: str) -> bool:
        """Toggle 逻辑与具体表无关，table 必须是预定义白名单中的表名。"""
        if table not in self._REACTION_TABLES:
            raise ValueError(f"非法 reaction 表名: {table}")
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    f"SELECT id FROM {table} WHERE message_id = ? AND user_id = ? AND emoji = ?",
                    (message_id, user_id, emoji),
                )
                existing = await cursor.fetchone()
                if existing:
                    await conn.execute(
                        f"DELETE FROM {table} WHERE id = ?",
                        (existing["id"],),
                    )
                    await conn.commit()
                    return False
                else:
                    await conn.execute(
                        f"INSERT INTO {table} (message_id, user_id, emoji) VALUES (?, ?, ?)",
                        (message_id, user_id, emoji),
                    )
                    await conn.commit()
                    return True
        except Exception as exc:
            print(f"[SQLiteStore] Toggle reaction failed: {exc}")
            raise

    async def toggle_reaction(self, message_id: int, user_id: str, emoji: str) -> bool:
        """Toggle a reaction. Returns True if added, False if removed."""
        return await self._toggle_reaction_generic("message_reactions", message_id, user_id, emoji)

    async def get_reactions(self, message_ids: list[int]) -> dict[int, list]:
        """Batch query reactions for given message IDs.
        Returns { message_id: [{ emoji, count, users }] }
        """
        if not message_ids:
            return {}
        try:
            async with await self._connect() as conn:
                placeholders = ",".join("?" * len(message_ids))
                cursor = await conn.execute(
                    f"""SELECT message_id, emoji, user_id
                        FROM message_reactions
                        WHERE message_id IN ({placeholders})
                        ORDER BY id ASC""",
                    message_ids,
                )
                rows = await cursor.fetchall()
            return self._aggregate_reactions(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get reactions failed: {exc}")
            raise

    async def get_reactions_after(self, session_id: str, after_reaction_id: int) -> list[dict]:
        """Return reactions with id > after_reaction_id for a single-chat session."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT r.id, r.emoji, m.content, r.user_id
                       FROM message_reactions r
                       JOIN messages m ON m.id = r.message_id
                       WHERE m.session_id = ? AND r.id > ?
                       ORDER BY r.id ASC""",
                    (session_id, after_reaction_id),
                )
                rows = await cursor.fetchall()
            return [
                {"reaction_id": r["id"], "emoji": r["emoji"],
                 "msg_content": r["content"], "user_id": r["user_id"]}
                for r in rows
            ]
        except Exception as exc:
            print(f"[SQLiteStore] Get reactions after failed: {exc}")
            raise

    async def get_group_reactions_after(self, group_id: str, after_reaction_id: int) -> list[dict]:
        """Return reactions with id > after_reaction_id for a group session."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT r.id, r.emoji, m.content, m.speaker_card_id
                       FROM message_reactions r
                       JOIN group_messages m ON m.id = r.message_id
                       WHERE m.group_id = ? AND r.id > ? AND m.role = 'assistant'
                       ORDER BY r.id ASC""",
                    (group_id, after_reaction_id),
                )
                rows = await cursor.fetchall()
            return [
                {"reaction_id": r["id"], "emoji": r["emoji"],
                 "msg_content": r["content"], "speaker_card_id": r["speaker_card_id"]}
                for r in rows
            ]
        except Exception as exc:
            print(f"[SQLiteStore] Get group reactions after failed: {exc}")
            raise

    async def toggle_dm_reaction(self, message_id: str, user_id: str, emoji: str) -> bool:
        """Toggle a DM reaction. Returns True if added, False if removed."""
        return await self._toggle_reaction_generic("dm_reactions", message_id, user_id, emoji)

    async def get_dm_message(self, message_id: str) -> dict | None:
        """Return a single direct message by id."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, sender_id, receiver_id, content, is_read, created_at FROM direct_messages WHERE id = ?",
                    (message_id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row) if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get DM message failed: {exc}")
            raise StoreError("get_dm_message", exc) from exc

    async def get_dm_reactions(self, user_id: str, other_id: str) -> dict:
        """Return reactions for messages in the conversation between user_id and other_id.
        Returns { message_id: [{emoji, count, users:[user_id...]}] }
        """
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT r.message_id, r.emoji, r.user_id
                       FROM dm_reactions r
                       JOIN direct_messages dm ON dm.id = r.message_id
                       WHERE (dm.sender_id = ? AND dm.receiver_id = ?)
                          OR (dm.sender_id = ? AND dm.receiver_id = ?)
                       ORDER BY r.id ASC""",
                    (user_id, other_id, other_id, user_id),
                )
                rows = await cursor.fetchall()
            return self._aggregate_reactions(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get DM reactions failed: {exc}")
            raise

    async def update_group_session(self, id: str, name: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE group_sessions SET name = ? WHERE id = ?",
                    (name, id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update group session failed: {exc}")
            raise

    async def update_group_card_ids(self, id: str, card_ids: list[str]) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE group_sessions SET card_ids = ? WHERE id = ?",
                    (json.dumps(card_ids), id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update group card_ids failed: {exc}")
            raise

    async def delete_group_session(self, id: str) -> None:
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE group_sessions SET deleted_at = ? WHERE id = ?",
                    (now, id),
                )
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

        # 无属主过滤：存储层导出原语没有 user 语境，属主校验由调用方端点负责。
        session = await self.get_session_unscoped(session_id)
        if session is None:
            raise ValueError("session not found")
        messages = await self.get_messages(session_id)
        card = await self.get_card(session["card_id"])

        card_parsed: dict[str, Any] = {}
        if card and card.get("card_json"):
            try:
                card_parsed = json.loads(card["card_json"])
            except json.JSONDecodeError as exc:
                # store-empty-ok: 这不是「查询失败」而是「单张卡的存量数据损坏」。导出仍完整
                # 产出，且降级在载荷里显式可见（{"raw": <原始串>} 取代解析后的卡对象）。
                # 上抛会让一张坏卡毁掉整次导出。
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

    async def create_user(self, id: str, username: str, password_hash: str, email: str = "", home_region: str = "") -> dict:
        """Create a new user. Raises on duplicate username."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO users (id, username, username_lower, email, email_verified, home_region) VALUES (?, ?, ?, ?, ?, ?)",
                    (id, username, username.lower(), email, 1 if email else 0, home_region),
                )
                await conn.execute(
                    "INSERT INTO user_secrets (user_id, password_hash) VALUES (?, ?)",
                    (id, password_hash),
                )
                await conn.commit()
                return await self.get_user_by_username(username) or {}
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise ValueError("用户名已存在") from exc
            print(f"[SQLiteStore] Create user failed: {exc}")
            raise

    async def get_user_by_username(self, username: str) -> dict | None:
        """Get a user by username (case-insensitive via username_lower)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT u.id, u.username, u.nickname, s.password_hash,
                              u.is_admin, u.is_disabled, u.created_at
                       FROM users u
                       LEFT JOIN user_secrets s ON s.user_id = u.id
                       WHERE u.username_lower = ?""",
                    (username.lower(),),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get user failed: {exc}")
            raise

    async def get_user_by_email(self, email: str) -> dict | None:
        """Get a user by email."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT u.id, u.username, u.nickname, s.password_hash,
                              u.email, u.email_verified,
                              u.is_admin, u.is_disabled, u.created_at
                       FROM users u
                       LEFT JOIN user_secrets s ON s.user_id = u.id
                       WHERE u.email = ? AND u.email != ''""",
                    (email,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get user by email failed: {exc}")
            raise

    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Get a user by ID."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT u.id, u.username, u.nickname, s.password_hash,
                              u.is_admin, u.is_disabled, u.created_at,
                              u.avatar_data, u.banner_data,
                              u.profile_stats_visible, u.cards_visible, u.books_visible,
                              u.bio, u.last_active_at, u.presence_visibility, u.following_visible,
                              u.home_region
                       FROM users u
                       LEFT JOIN user_secrets s ON s.user_id = u.id
                       WHERE u.id = ?""",
                    (user_id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get user by id failed: {exc}")
            raise

    async def set_user_privacy(self, user_id: str, **kwargs) -> bool:
        """Set privacy fields (stats_visible, cards_visible, books_visible)."""
        allowed = {'profile_stats_visible', 'cards_visible', 'books_visible', 'following_visible'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return True
        try:
            async with await self._connect() as conn:
                set_clause = ', '.join(f'{k} = ?' for k in updates)
                values = [1 if v else 0 for v in updates.values()]
                values.append(user_id)
                await conn.execute(
                    f"UPDATE users SET {set_clause} WHERE id = ?",
                    values,
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Set user privacy failed: {exc}")
            raise StoreError("set_user_privacy", exc) from exc

    async def set_user_presence_visibility(self, user_id: str, visibility: str) -> bool:
        """Set presence_visibility for a user: 'all', 'fans', 'mutual', or 'none'."""
        if visibility not in ('all', 'fans', 'mutual', 'none'):
            return False
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET presence_visibility = ? WHERE id = ?",
                    (visibility, user_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Set presence visibility failed: {exc}")
            raise StoreError("set_user_presence_visibility", exc) from exc

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
                    "SELECT id, username, nickname, email, email_verified, is_admin, is_disabled, created_at, last_login_at, last_active_at, presence_visibility FROM users ORDER BY created_at DESC"
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get all users failed: {exc}")
            raise

    async def get_all_users_admin_fields(self) -> list[dict]:
        """List all users with admin-safe fields only (for cross-border export).

        Explicit field whitelist — no password_hash, api_key, or secrets.
        """
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, nickname, home_region, is_disabled, created_at, last_active_at FROM users ORDER BY created_at DESC"
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get all users admin fields failed: {exc}")
            raise

    async def update_last_login(self, user_id: str) -> None:
        """Update the last_login_at timestamp for a user."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET last_login_at = ? WHERE id = ?",
                    (now, user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update last_login failed: {exc}")
            raise StoreError("update_last_login", exc) from exc

    async def update_last_active(self, user_id: str) -> None:
        """Update the last_active_at timestamp for a user."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET last_active_at = ? WHERE id = ?",
                    (now, user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update last_active failed: {exc}")
            raise StoreError("update_last_active", exc) from exc

    async def get_dashboard_stats(self) -> dict:
        """Aggregate dashboard statistics for admin panel."""
        try:
            async with await self._connect() as conn:
                c = await conn.execute("SELECT COUNT(*) FROM users")
                total_users = (await c.fetchone())[0]

                c = await conn.execute(
                    "SELECT COUNT(*) FROM users WHERE date(created_at) = date('now')"
                )
                today_new_users = (await c.fetchone())[0]

                c = await conn.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM usage_stats WHERE date(created_at) = date('now')"
                )
                today_active_users = (await c.fetchone())[0]

                c = await conn.execute(
                    "SELECT COUNT(*) FROM usage_stats WHERE date(created_at) = date('now')"
                )
                today_api_calls = (await c.fetchone())[0]

                c = await conn.execute(
                    "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM usage_stats WHERE date(created_at) = date('now')"
                )
                today_tokens = (await c.fetchone())[0]

                c = await conn.execute(
                    """SELECT date(created_at) AS day,
                              COUNT(*) AS calls,
                              COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tokens
                       FROM usage_stats
                       WHERE created_at >= datetime('now', '-7 days')
                       GROUP BY date(created_at)
                       ORDER BY day ASC"""
                )
                rows = await c.fetchall()
                trend = [{"day": r[0], "calls": r[1], "tokens": r[2]} for r in rows]

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
            print(f"[SQLiteStore] Get dashboard stats failed: {exc}")
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
                    "UPDATE user_secrets SET password_hash = ? WHERE user_id = ?",
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
            secret = os.getenv("JWT_SECRET")
            if not secret:
                raise RuntimeError("FERNET_KEY 或 JWT_SECRET 必须设置才能加解密 API key，拒绝使用不安全默认值")
            raw = secret.encode()
            key = base64.urlsafe_b64encode(sha256(raw).digest())
        return Fernet(key)

    async def get_user_api_config(self, user_id: str) -> dict:
        """Get a user's API config. api_key and embedding_key are returned decrypted."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT s.api_key, s.base_url, s.model,
                              u.embedding_key, u.embedding_region
                       FROM users u
                       LEFT JOIN user_secrets s ON s.user_id = u.id
                       WHERE u.id = ?""",
                    (user_id,),
                )
                row = await cursor.fetchone()
            if not row:
                return {"api_key": "", "base_url": "", "model": "", "embedding_key": "", "embedding_region": "cn"}

            def _decrypt(val: str) -> str:
                if not val:
                    return ""
                try:
                    return self._get_fernet().decrypt(val.encode()).decode()
                except Exception as exc:
                    print(f"[SQLiteStore] decrypt failed: {exc}")
                    raise StoreError("_decrypt", exc) from exc

            return {
                "api_key": _decrypt(row[0] or ""),
                "base_url": row[1] or "https://api.deepseek.com",
                "model": row[2] or "deepseek-v4-pro",
                "embedding_key": _decrypt(row[3] or ""),
                "embedding_region": row[4] or "cn",
            }
        except Exception as exc:
            print(f"[SQLiteStore] Get user API config failed: {exc}")
            raise

    async def update_user_api_config(self, user_id: str, api_key: str, base_url: str, model: str, embedding_key: str = "", embedding_region: str = "cn") -> None:
        """Update a user's API config. api_key and embedding_key are encrypted before storage.

        Empty fields are never written, so a blank field in the request does not
        overwrite an existing value (applies to api_key, base_url, model, and embedding_key).

        Secrets (api_key, base_url, model) go to user_secrets;
        embedding config (embedding_key, embedding_region) stays on users.
        """
        try:
            # ---- user_secrets: api_key, base_url, model ----
            secret_parts = []
            secret_params = []

            if api_key:
                encrypted = self._get_fernet().encrypt(api_key.encode()).decode()
                secret_parts.append("api_key = ?")
                secret_params.append(encrypted)

            if base_url:
                secret_parts.append("base_url = ?")
                secret_params.append(base_url)

            if model:
                secret_parts.append("model = ?")
                secret_params.append(model)

            if secret_parts:
                secret_params.append(user_id)
                sql = "UPDATE user_secrets SET " + ", ".join(secret_parts) + " WHERE user_id = ?"
                async with await self._connect() as conn:
                    await conn.execute(sql, tuple(secret_params))
                    await conn.commit()

            # ---- users: embedding_key, embedding_region ----
            user_parts = []
            user_params = []

            if embedding_key:
                enc_emb = self._get_fernet().encrypt(embedding_key.encode()).decode()
                user_parts.append("embedding_key = ?")
                user_params.append(enc_emb)

            if embedding_region:
                user_parts.append("embedding_region = ?")
                user_params.append(embedding_region)

            if user_parts:
                user_params.append(user_id)
                sql = "UPDATE users SET " + ", ".join(user_parts) + " WHERE id = ?"
                async with await self._connect() as conn:
                    await conn.execute(sql, tuple(user_params))
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
                    "UPDATE user_secrets SET password_hash = ? WHERE user_id = ?",
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

    async def update_user_banner(self, user_id: str, banner_data: str) -> None:
        """Store base64 banner for a user."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET banner_data = ? WHERE id = ?",
                    (banner_data, user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update user banner failed: {exc}")
            raise

    async def get_user_banner(self, user_id: str) -> str:
        """Get base64 banner for a user, empty string if none."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT banner_data FROM users WHERE id = ?", (user_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row and row[0] else ""
        except Exception as exc:
            print(f"[SQLiteStore] Get user banner failed: {exc}")
            raise

    async def update_user_bio(self, user_id: str, bio: str) -> None:
        """Update a user's bio text."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET bio = ? WHERE id = ?",
                    (bio, user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update user bio failed: {exc}")
            raise

    async def update_user_nickname(self, user_id: str, nickname: str) -> None:
        """Update a user's display nickname."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE users SET nickname = ? WHERE id = ?",
                    (nickname, user_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update user nickname failed: {exc}")
            raise

    async def record_geo_block(self, user_id: str, ip: str, base_url: str, reason: str) -> None:
        """Record a geo-blocking event for compliance audit trail."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO geo_block_log (user_id, ip, base_url, reason) VALUES (?, ?, ?, ?)",
                    (user_id, ip, base_url, reason),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Record geo block failed: {exc}")
            raise StoreError("record_geo_block", exc) from exc

    async def record_user_consent(self, user_id: str, terms_version: str, privacy_version: str, ip: str) -> None:
        """Record user's consent to legal agreements for compliance audit trail."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO user_consent (user_id, terms_version, privacy_version, ip) VALUES (?, ?, ?, ?)",
                    (user_id, terms_version, privacy_version, ip),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Record user consent failed: {exc}")
            raise StoreError("record_user_consent", exc) from exc

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
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get invite code failed: {exc}")
            raise

    async def use_invite_code(self, code: str, used_by: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
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
            return self._list_rows(rows)
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

    async def save_refresh_token(self, token_hash: str, user_id: str, expires_at: str, replaced_by: str = "") -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO refresh_tokens (token_hash, user_id, expires_at, replaced_by) VALUES (?, ?, ?, ?)",
                    (token_hash, user_id, expires_at, replaced_by),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save refresh token failed: {exc}")
            raise

    async def get_refresh_token(self, token_hash: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT token_hash, user_id, expires_at, used, used_at, replaced_by FROM refresh_tokens WHERE token_hash = ?",
                    (token_hash,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get refresh token failed: {exc}")
            raise

    async def mark_refresh_token_used(self, token_hash: str, replaced_by: str = "") -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE refresh_tokens SET used = 1, used_at = ?, replaced_by = ? WHERE token_hash = ?",
                    (datetime.now(timezone.utc).isoformat(), replaced_by, token_hash),
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

                # 4. Delete distill rows. 顺序先父后子，与 hard_delete_text 同；SQLite 写是
                # 库级序列化的，无需 PG 那侧的 FOR SHARE，顺序即足够闭合窗口。
                # 两表零外键（migration 084），删父不级联子，必须显式删两张。
                cursor = await conn.execute(
                    "SELECT task_id FROM distill_tasks WHERE user_id = ?", (user_id,)
                )
                dt_ids = [row[0] for row in await cursor.fetchall()]
                cursor = await conn.execute(
                    "DELETE FROM distill_tasks WHERE user_id = ?", (user_id,)
                )
                counts["distill_tasks"] = cursor.rowcount
                chunks = 0
                for tid in dt_ids:
                    cursor = await conn.execute(
                        "DELETE FROM distill_chunks WHERE task_id = ?", (tid,)
                    )
                    chunks += cursor.rowcount
                counts["distill_chunks"] = chunks

                # 5. Delete texts
                cursor = await conn.execute(
                    "DELETE FROM texts WHERE user_id = ?", (user_id,)
                )
                counts["texts"] = cursor.rowcount

                # 6. Delete usage stats
                cursor = await conn.execute(
                    "DELETE FROM usage_stats WHERE user_id = ?", (user_id,)
                )
                counts["usage_stats"] = cursor.rowcount

                # 7. Delete refresh tokens
                cursor = await conn.execute(
                    "DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,)
                )
                counts["refresh_tokens"] = cursor.rowcount

                # 8. Nullify invite codes created by this user
                cursor = await conn.execute(
                    "UPDATE invite_codes SET created_by = '[deleted]' WHERE created_by = ?",
                    (user_id,),
                )
                counts["invite_codes"] = cursor.rowcount

                # 9. Delete user_secrets
                await conn.execute(
                    "DELETE FROM user_secrets WHERE user_id = ?", (user_id,)
                )
                counts["user_secrets"] = 1

                # 10. Delete the user
                cursor = await conn.execute(
                    "DELETE FROM users WHERE id = ?", (user_id,)
                )
                if cursor.rowcount == 0:
                    raise ValueError("用户不存在")
                counts["user"] = 1

                # Enqueue cross-border purge — same transaction as the delete
                await conn.execute(
                    """INSERT OR IGNORE INTO cross_border_delete_outbox
                       (op_type, target_id, payload) VALUES (?, ?, ?)""",
                    ("user_purge", user_id, ""),
                )
                await conn.commit()
            return counts
        except ValueError:
            raise
        except Exception as exc:
            print(f"[SQLiteStore] Delete user failed: {exc}")
            raise

    # ---- Admin: Content Moderation ----

    async def list_all_cards_admin(self) -> list[dict]:
        """List all cards with user info for admin review."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT c.id, c.text_id, c.name, c.created_at, c.user_id, c.visibility,
                              c.deleted_at, c.card_json, COALESCE(u.username, '') AS username
                       FROM cards c
                       LEFT JOIN users u ON u.id = c.user_id
                       ORDER BY c.created_at DESC"""
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] List all cards admin failed: {exc}")
            raise

    async def takedown_card(self, card_id: str) -> bool:
        """Set a public card to private (takedown)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE cards SET visibility = 'private' WHERE id = ? AND visibility = 'public'",
                    (card_id,),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Takedown card failed: {exc}")
            raise

    async def list_all_posts_admin(self) -> list[dict]:
        """List all user posts for admin review."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT p.id, p.user_id, p.content, p.visibility, p.created_at,
                              COALESCE(u.username, '') AS username
                       FROM user_posts p
                       LEFT JOIN users u ON u.id = p.user_id
                       ORDER BY p.created_at DESC"""
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] List all posts admin failed: {exc}")
            raise

    async def admin_delete_post(self, post_id: str) -> bool:
        """Delete any post by id (admin)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM user_posts WHERE id = ?", (post_id,)
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Admin delete post failed: {exc}")
            raise

    async def ban_user_and_contents(self, user_id: str, admin_id: str) -> dict:
        """Disable user + delete their posts + resolve comment reports."""
        counts = {"posts_deleted": 0, "reports_resolved": 0}
        try:
            async with await self._connect() as conn:
                await conn.execute("UPDATE users SET is_disabled = 1 WHERE id = ?", (user_id,))
                cursor = await conn.execute("DELETE FROM user_posts WHERE user_id = ?", (user_id,))
                counts["posts_deleted"] = cursor.rowcount
                cursor = await conn.execute(
                    """UPDATE card_comment_reports SET status = 'resolved', resolver_id = ?
                       WHERE comment_id IN (SELECT id FROM card_comments WHERE user_id = ?)
                       AND status = 'pending'""",
                    (admin_id, user_id),
                )
                counts["reports_resolved"] = cursor.rowcount
                await conn.commit()
            return counts
        except Exception as exc:
            print(f"[SQLiteStore] Ban user failed: {exc}")
            raise

    # ---- Admin: User Detail ----

    async def get_user_detail(self, user_id: str) -> dict:
        """Get user detail for admin: info + cards + sessions + usage + login history."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, email, email_verified, is_admin, is_disabled, created_at, last_login_at, last_active_at FROM users WHERE id = ?",
                    (user_id,),
                )
                user = await cursor.fetchone()
                if not user:
                    raise ValueError("用户不存在")
                result = dict(user)
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM cards WHERE user_id = ? AND deleted_at IS NULL", (user_id,)
                )
                result["cards_count"] = (await cursor.fetchone())[0]
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
                )
                result["sessions_count"] = (await cursor.fetchone())[0]
                cursor = await conn.execute(
                    """SELECT COUNT(*) AS calls,
                              COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                              COALESCE(SUM(completion_tokens), 0) AS completion_tokens
                       FROM usage_stats WHERE user_id = ?""",
                    (user_id,),
                )
                row = await cursor.fetchone()
                result["usage"] = self._row_to_dict(row) if row else {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
                cursor = await conn.execute(
                    "SELECT created_at FROM usage_stats WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
                    (user_id,),
                )
                rows = await cursor.fetchall()
                result["login_history"] = [r[0] for r in rows]
            return result
        except ValueError:
            raise
        except Exception as exc:
            print(f"[SQLiteStore] Get user detail failed: {exc}")
            raise

    # ---- Admin: Announcements ----

    async def create_announcement(self, content: str, align: str = 'left') -> dict:
        """Create a new announcement (deactivates previous ones)."""
        import uuid
        try:
            async with await self._connect() as conn:
                await conn.execute("UPDATE announcements SET is_active = 0")
                aid = uuid.uuid4().hex[:12]
                await conn.execute(
                    "INSERT INTO announcements (id, content, is_active, align) VALUES (?, ?, 1, ?)",
                    (aid, content, align),
                )
                await conn.commit()
                cursor = await conn.execute("SELECT * FROM announcements WHERE id = ?", (aid,))
                row = await cursor.fetchone()
            return self._row_to_dict(row) if row else {"id": aid, "content": content, "is_active": 1, "align": align}
        except Exception as exc:
            print(f"[SQLiteStore] Create announcement failed: {exc}")
            raise

    async def delete_announcement(self, announcement_id: str) -> bool:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM announcements WHERE id = ?", (announcement_id,)
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete announcement failed: {exc}")
            raise

    async def set_announcement_active(self, announcement_id: str, active: bool) -> bool:
        try:
            async with await self._connect() as conn:
                if active:
                    await conn.execute("UPDATE announcements SET is_active = 0")
                cursor = await conn.execute(
                    "UPDATE announcements SET is_active = ? WHERE id = ?",
                    (1 if active else 0, announcement_id),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Set announcement active failed: {exc}")
            raise

    async def get_active_announcement(self) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM announcements WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get active announcement failed: {exc}")
            raise

    async def list_announcements(self) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM announcements ORDER BY created_at DESC"
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] List announcements failed: {exc}")
            raise

    # ---- Admin: CSV Export ----

    async def export_users_csv(self) -> str:
        """Export all users as CSV."""
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
            print(f"[SQLiteStore] Export users CSV failed: {exc}")
            raise

    async def export_usage_csv(self) -> str:
        """Export usage summary as CSV."""
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
            print(f"[SQLiteStore] Export usage CSV failed: {exc}")
            raise

    # ---- P3-1: Config changelog ----

    async def save_config_change(self, change_id: str, admin_id: str, admin_username: str, field: str, old_value: str, new_value: str) -> None:
        """Record a config change in the changelog."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO config_changelog (id, admin_id, admin_username, field, old_value, new_value) VALUES (?, ?, ?, ?, ?, ?)",
                    (change_id, admin_id, admin_username, field, old_value, new_value),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save config change failed: {exc}")
            raise StoreError("save_config_change", exc) from exc

    async def get_config_changelog(self, limit: int = 50) -> list[dict]:
        """Return recent config changelog entries."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM config_changelog ORDER BY created_at DESC LIMIT ?", (limit,)
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get config changelog failed: {exc}")
            raise StoreError("get_config_changelog", exc) from exc

    # ---- P3-2: Review log ----

    async def save_review_log(self, review_id: str, card_id: str, user_id: str, result: str, reason: str = "") -> None:
        """Record an AI review result."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO review_log (id, card_id, user_id, result, reason) VALUES (?, ?, ?, ?, ?)",
                    (review_id, card_id, user_id, result, reason),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save review log failed: {exc}")
            raise StoreError("save_review_log", exc) from exc

    async def get_review_logs(self, limit: int = 50) -> list[dict]:
        """Return recent review logs with card info."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT r.id, r.card_id, r.user_id, r.result, r.reason, r.created_at,
                              COALESCE(c.name, '') AS card_name
                       FROM review_log r
                       LEFT JOIN cards c ON c.id = r.card_id
                       ORDER BY r.created_at DESC LIMIT ?""",
                    (limit,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get review logs failed: {exc}")
            raise StoreError("get_review_logs", exc) from exc

    async def get_latest_review_log(self, card_id: str) -> dict | None:
        """Return the most recent review_log row for a card (by monotonic id)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, card_id, user_id, result, reason, created_at
                       FROM review_log WHERE card_id = ? ORDER BY id DESC LIMIT 1""",
                    (card_id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get latest review log failed: {exc}")
            raise StoreError("get_latest_review_log", exc) from exc

    # ---- Usage stats ----

    async def record_usage(self, user_id: str, action: str, prompt_tokens: int, completion_tokens: int, model: str = "", is_estimated: bool = False) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO usage_stats (user_id, action, prompt_tokens, completion_tokens, model, is_estimated) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, action, prompt_tokens, completion_tokens, model, int(is_estimated)),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Record usage failed: {exc}")
            raise StoreError("record_usage", exc) from exc

    async def get_usage_stats(self, user_id: str) -> dict:
        try:
            async with await self._connect() as conn:
                # Totals
                cursor = await conn.execute(
                    "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = ?",
                    (user_id,),
                )
                row = await cursor.fetchone()
                total = self._row_to_dict(row) if row else {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

                # By day
                cursor = await conn.execute(
                    "SELECT date(created_at) AS date, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = ? GROUP BY date(created_at) ORDER BY date DESC LIMIT 30",
                    (user_id,),
                )
                by_day = self._list_rows(await cursor.fetchall())

                # By action
                cursor = await conn.execute(
                    "SELECT action, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = ? GROUP BY action",
                    (user_id,),
                )
                by_action = {}
                for r in await cursor.fetchall():
                    d = self._row_to_dict(r)
                    by_action[d["action"]] = {"calls": d["calls"], "prompt_tokens": d["prompt_tokens"], "completion_tokens": d["completion_tokens"]}

                # By model
                cursor = await conn.execute(
                    "SELECT model, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens FROM usage_stats WHERE user_id = ? AND model != '' GROUP BY model",
                    (user_id,),
                )
                by_model = {}
                for r in await cursor.fetchall():
                    d = self._row_to_dict(r)
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
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get all usage summary failed: {exc}")
            raise

    async def get_usage_quality_stats(self) -> dict:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM usage_stats WHERE DATE(created_at) = DATE('now')"
                )
                row = await cursor.fetchone()
                total_n = row[0] if row else 0
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM usage_stats WHERE DATE(created_at) = DATE('now') AND is_estimated = 1"
                )
                row = await cursor.fetchone()
                est_n = row[0] if row else 0
            return {"total": total_n, "estimated": est_n, "estimated_ratio": round(est_n / total_n, 4) if total_n > 0 else 0.0}
        except Exception as exc:
            print(f"[SQLiteStore] Get usage quality stats failed: {exc}")
            raise

    # ---- Affinity ----

    async def get_session_affinity(self, session_id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT affinity, trust, mood, guard, affinity_reason as reason FROM sessions WHERE id = ?",
                    (session_id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get session affinity failed: {exc}")
            raise StoreError("get_session_affinity", exc) from exc

    async def save_affinity_state(self, session_id: str, state_json: str) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE sessions
                       SET affinity_state = ?, affinity_initialized = 1,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (state_json, session_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save affinity state failed: {exc}")
            raise StoreError("save_affinity_state", exc) from exc

    # ── Distill task persistence ────────────────

    async def create_distill_task(self, task_id: str, user_id: str, text_id: str, character: str = "", status: str = "queued", progress_pct: int = 0, message: str = "", card_id: str = "", awakening: str = "", chunk_size: int | None = None, overlap: int | None = None, text_fingerprint: str = "") -> dict | None:
        """Insert a NEW distillation task row (INSERT-only, no upsert). Returns the stored row.

        重复 task_id 抛异常：这里是新铸的 id，冲突是真 bug，不是"请更新已有行"。
        chunk_size/overlap/text_fingerprint 是创建时的切分 checkpoint；复用行的重新
        盖章走 update_distill_task，不在这里。
        """
        try:
            cols = ["task_id", "user_id", "text_id", "character", "status",
                    "progress_pct", "message", "card_id", "awakening"]
            vals: list[Any] = [task_id, user_id, text_id, character, status,
                               progress_pct, message, card_id, awakening]
            if chunk_size is not None:
                cols.append("chunk_size"); vals.append(chunk_size)
            if overlap is not None:
                cols.append("overlap"); vals.append(overlap)
            if text_fingerprint:
                cols.append("text_fingerprint"); vals.append(text_fingerprint)
            placeholders = ", ".join("?" * len(cols))
            sql = f"INSERT INTO distill_tasks ({', '.join(cols)}) VALUES ({placeholders})"
            async with await self._connect() as conn:
                await conn.execute(sql, vals)
                await conn.commit()
            return await self.get_distill_task(task_id) or {}
        except Exception as exc:
            print(f"[SQLiteStore] Create distill task failed: {exc}")
            raise

    async def get_distill_task(self, task_id: str) -> dict | None:
        """Return one distillation task row by task_id, or None if absent."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT task_id, user_id, text_id, character, status, progress_pct, message,
                              card_id, awakening, chunk_size, overlap, text_fingerprint,
                              created_at, updated_at
                       FROM distill_tasks WHERE task_id = ?""",
                    (task_id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get distill task failed: {exc}")
            raise

    async def find_interrupted_distill(self, user_id: str, text_id: str, character: str) -> dict | None:
        """Return the newest interrupted distill task for (user, text, character), or None."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT task_id, user_id, text_id, character, status, progress_pct, message,
                              card_id, awakening, chunk_size, overlap, text_fingerprint,
                              created_at, updated_at
                       FROM distill_tasks
                       WHERE user_id = ? AND text_id = ? AND character = ? AND status = 'interrupted'
                       ORDER BY updated_at DESC LIMIT 1""",
                    (user_id, text_id, character),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Find interrupted distill failed: {exc}")
            raise

    async def list_distill_tasks(self, limit: int = 200) -> list[dict]:
        """Return distillation task rows, newest-updated first, capped at limit."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT task_id, user_id, text_id, character, status, progress_pct, message,
                              card_id, awakening, chunk_size, overlap, text_fingerprint,
                              created_at, updated_at
                       FROM distill_tasks
                       ORDER BY updated_at DESC
                       LIMIT ?""",
                    (int(limit),),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] List distill tasks failed: {exc}")
            raise

    async def count_distill_tasks(self) -> int:
        """Return the total number of distill task rows, unfiltered and uncapped."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute("SELECT COUNT(*) FROM distill_tasks")
                row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] Count distill tasks failed: {exc}")
            raise

    async def update_distill_task(self, task_id: str, *, status: str | None = None, progress_pct: int | None = None, message: str | None = None, card_id: str | None = None, awakening: str | None = None, chunk_size: int | None = None, text_fingerprint: str | None = None) -> int:
        """Patch only the non-None fields of a distillation task row. 返回受影响行数。

        UPDATE-only、绝不 upsert：0 行 = 行已被删（如文本被删时 bg 线程仍在跑），
        此时本就不该写 —— 这就是竞态的闭合点，不需要任何人通知 bg 线程。
        chunk_size/text_fingerprint 供复用行重新盖章；overlap 无对应概念，不进此方法。
        """
        try:
            sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = []
            for col, val in (
                ("status", status), ("progress_pct", progress_pct), ("message", message),
                ("card_id", card_id), ("awakening", awakening),
                ("chunk_size", chunk_size), ("text_fingerprint", text_fingerprint),
            ):
                if val is not None:
                    sets.append(f"{col} = ?"); params.append(val)
            params.append(task_id)
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    f"UPDATE distill_tasks SET {', '.join(sets)} WHERE task_id = ?",
                    params,
                )
                await conn.commit()
                return cursor.rowcount or 0
        except Exception as exc:
            print(f"[SQLiteStore] Update distill task failed: {exc}")
            raise

    async def save_distill_chunk(self, task_id: str, chunk_index: int, result: str, fingerprint: str = "") -> None:
        """Persist one finished map chunk. Same (task_id, chunk_index) is replaced, never duplicated.

        WHERE EXISTS 父行：父任务行不在（文本被删、行已清）则零行写入、不报错。
        与 update-only 同一条不变量 —— 孤儿分片写不进来。

        **冲突即原地覆盖（upsert），不是 first-write-wins**。联合 PK 仍保证不重复行
        （同一 `(task_id, chunk_index)` 恒只有一行），变的只是：同一片允许被**更新的结果**
        覆盖。旧写法 `DO NOTHING` 让「原文变 → 片指纹变 → 门 3 拒绝复用 → 重跑该片」这条
        正常路径永远写不进新结果 —— 库里还是旧 `result` + 旧 `chunk_fingerprint`，下次续跑
        门 3 再次拒绝，**该片每次续跑都重跑，永不收敛**（缺陷 4）。`created_at` 不动，
        保留该片**首次**落库的时间。

        这里不需要 PG 那侧的 FOR SHARE：SQLite 写是**库级序列化**的（单写者，删除事务
        持写锁直至提交），并发分片写入要么在删除事务前提交（随即被一并删掉），要么阻塞
        到提交后（父行已无、跳过）。窗口从根上不存在，不是靠运气 —— 故不引入行锁
        （SQLite 也没有行锁语法），不为了与 PG 对称写无用代码。
        """
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO distill_chunks (task_id, chunk_index, result, chunk_fingerprint)
                       SELECT ?, ?, ?, ?
                       WHERE EXISTS (SELECT 1 FROM distill_tasks WHERE task_id = ?)
                       ON CONFLICT (task_id, chunk_index) DO UPDATE
                           SET result = excluded.result,
                               chunk_fingerprint = excluded.chunk_fingerprint""",
                    (task_id, chunk_index, result, fingerprint, task_id),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save distill chunk failed: {exc}")
            raise

    async def get_distill_chunks(self, task_id: str) -> list[dict]:
        """Return finished chunks of a task ordered by chunk_index asc."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT task_id, chunk_index, result, chunk_fingerprint, created_at
                       FROM distill_chunks WHERE task_id = ?
                       ORDER BY chunk_index ASC""",
                    (task_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get distill chunks failed: {exc}")
            raise

    async def count_running_distills(self, user_id: str, window_minutes: int | None = None) -> int:
        """Count a user's non-terminal distill tasks (status queued/running).

        window_minutes: 只统计 updated_at 在最近 N 分钟内的行。活任务的进度写会持续
        刷新 updated_at（update_distill_task 每次带 CURRENT_TIMESTAMP），幽灵行
        （线程已死、终态没落库）不会 —— 超窗即不计，避免永久挡住该用户。None = 不计时效。
        """
        try:
            sql = ("SELECT COUNT(*) FROM distill_tasks "
                   "WHERE user_id = ? AND status IN ('queued', 'running')")
            params: list[Any] = [user_id]
            if window_minutes is not None:
                # 存的是 CURRENT_TIMESTAMP 的 'YYYY-MM-DD HH:MM:SS'（UTC），与
                # datetime('now', ...) 同格式，字典序即时间序。
                sql += " AND updated_at >= datetime('now', ?)"
                params.append(f"-{int(window_minutes)} minutes")
            async with await self._connect() as conn:
                cursor = await conn.execute(sql, tuple(params))
                row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] Count running distills failed: {exc}")
            raise

    async def mark_interrupted_distills(self, message: str = "服务重启，任务已中断，等待自动恢复") -> int:
        """Boot-time reconcile: flip every status='running' row to 'interrupted'."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE distill_tasks SET status = 'interrupted', message = ?, updated_at = CURRENT_TIMESTAMP WHERE status = 'running'",
                    (message,),
                )
                await conn.commit()
                return cursor.rowcount or 0
        except Exception as exc:
            print(f"[SQLiteStore] Mark interrupted distills failed: {exc}")
            raise

    async def cancel_distills_by_text_id(self, text_id: str, message: str = "文本已删除，任务已取消") -> int:
        """Set every non-terminal distill row for a text to error."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE distill_tasks SET status = 'error', message = ?, updated_at = CURRENT_TIMESTAMP WHERE text_id = ? AND status NOT IN ('done', 'error')",
                    (message, text_id),
                )
                await conn.commit()
                return cursor.rowcount or 0
        except Exception as exc:
            print(f"[SQLiteStore] Cancel distills by text failed: {exc}")
            raise

    async def load_affinity_state(self, session_id: str) -> tuple[str, bool]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT affinity_state, affinity_initialized FROM sessions WHERE id = ?",
                    (session_id,),
                )
                row = await cursor.fetchone()
            if row is None:
                return "", False
            return row[0] or "", bool(row[1])
        except Exception as exc:
            print(f"[SQLiteStore] Load affinity state failed: {exc}")
            raise StoreError("load_affinity_state", exc) from exc

    async def update_group_affinity(
        self, group_id: str, card_id: str, affinity: int, trust: int, mood: str, guard: int, reason: str = ""
    ) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO group_affinity (group_id, card_id, affinity, trust, mood, guard, affinity_reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(group_id, card_id) DO UPDATE SET
                           affinity = excluded.affinity,
                           trust = excluded.trust,
                           mood = excluded.mood,
                           guard = excluded.guard,
                           affinity_reason = excluded.affinity_reason""",
                    (group_id, card_id, affinity, trust, mood, guard, reason),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Update group affinity failed: {exc}")
            raise StoreError("update_group_affinity", exc) from exc

    async def get_group_affinity(self, group_id: str, card_id: str) -> dict | None:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT affinity, trust, mood, guard, affinity_reason AS reason
                       FROM group_affinity
                       WHERE group_id = ? AND card_id = ?""",
                    (group_id, card_id),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get group affinity failed: {exc}")
            raise StoreError("get_group_affinity", exc) from exc

    # ── Comments ──

    async def get_comments(self, card_id: str) -> list[dict]:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT c.id, c.user_id, c.username, c.content, c.created_at, "
                    "COALESCE(u.avatar_data, '') AS avatar_data, "
                    "COALESCE(c.is_ai_reply, 0) AS is_ai_reply, "
                    "COALESCE(c.ai_card_id, '') AS ai_card_id, "
                    "COALESCE(c.ai_version_label, '') AS ai_version_label, "
                    "COALESCE(c.reply_to_comment_id, '') AS reply_to_comment_id "
                    "FROM card_comments c LEFT JOIN users u ON c.user_id = u.id "
                    "WHERE c.card_id = ? ORDER BY c.created_at ASC",
                    (card_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get comments failed: {exc}")
            raise StoreError("get_comments", exc) from exc

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

    async def get_public_cards_by_text_id(self, text_id: str) -> list[dict]:
        """查同一 text_id 下所有 public 的卡，用于 @ 选择器。"""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT c.id, c.name, c.user_id, u.username AS author_username
                       FROM cards c LEFT JOIN users u ON c.user_id = u.id
                       WHERE c.text_id = ? AND c.visibility = 'public' AND c.deleted_at IS NULL
                       ORDER BY c.created_at ASC""",
                    (text_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] get_public_cards_by_text_id failed: {exc}")
            raise StoreError("get_public_cards_by_text_id", exc) from exc

    async def add_ai_reply_comment(
        self, card_id: str, ai_card_id: str, ai_version_label: str,
        content: str, reply_to_comment_id: str
    ) -> dict:
        import uuid
        cid = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO card_comments
                       (id, card_id, user_id, username, content, is_ai_reply, ai_card_id, ai_version_label, reply_to_comment_id)
                       VALUES (?, ?, '', '', ?, 1, ?, ?, ?)""",
                    (cid, card_id, content, ai_card_id, ai_version_label, reply_to_comment_id),
                )
                await conn.commit()
            return {
                "id": cid, "card_id": card_id, "user_id": "", "username": "",
                "content": content, "is_ai_reply": 1, "ai_card_id": ai_card_id,
                "ai_version_label": ai_version_label, "reply_to_comment_id": reply_to_comment_id,
            }
        except Exception as exc:
            print(f"[SQLiteStore] add_ai_reply_comment failed: {exc}")
            raise

    async def get_card_author_id(self, card_id: str) -> str | None:
        """Return the user_id of the card's owner."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT user_id FROM cards WHERE id = ? AND deleted_at IS NULL",
                    (card_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row else None
        except Exception as exc:
            print(f"[SQLiteStore] Get card author failed: {exc}")
            raise StoreError("get_card_author_id", exc) from exc

    async def get_comment(self, comment_id: str) -> dict | None:
        """Get a single comment by ID."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM card_comments WHERE id = ?", (comment_id,)
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get comment failed: {exc}")
            raise StoreError("get_comment", exc) from exc

    async def delete_comment(self, comment_id: str, user_id: str, card_author_id: str | None = None, is_admin: bool = False) -> bool:
        """Delete a card comment. Caller must verify permission."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM card_comments WHERE id = ?",
                    (comment_id,),
                )
                await conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Delete comment failed: {exc}")
            raise StoreError("delete_comment", exc) from exc

    async def batch_delete_comments(self, comment_ids: list[str]) -> bool:
        """Batch delete card comments by IDs."""
        if not comment_ids:
            return True
        try:
            placeholders = ",".join("?" * len(comment_ids))
            async with await self._connect() as conn:
                await conn.execute(
                    f"DELETE FROM card_comments WHERE id IN ({placeholders})",
                    comment_ids,
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Batch delete comments failed: {exc}")
            raise StoreError("batch_delete_comments", exc) from exc

    # ── Comment Reports ──

    async def add_comment_report(self, comment_id: str, card_id: str, reporter_id: str, reason: str) -> bool:
        """Insert a report record. Duplicate reports from same user are ignored."""
        try:
            report_id = uuid.uuid4().hex[:12]
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT OR IGNORE INTO card_comment_reports
                       (id, comment_id, card_id, reporter_id, reason)
                       VALUES (?, ?, ?, ?, ?)""",
                    (report_id, comment_id, card_id, reporter_id, reason),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Add comment report failed: {exc}")
            raise StoreError("add_comment_report", exc) from exc

    async def get_comment_reports(self, status: str = 'pending') -> list[dict]:
        """List reports for admin view, grouped by comment with report count."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT r.id, r.comment_id, r.card_id, r.reporter_id, r.reason,
                              r.status, r.created_at, r.resolved_at, r.resolver_id,
                              c.content AS comment_content, c.user_id AS comment_author_id,
                              c.username AS comment_author_name,
                              (SELECT COUNT(*) FROM card_comment_reports r2
                               WHERE r2.comment_id = r.comment_id AND r2.status = 'pending') AS report_count
                       FROM card_comment_reports r
                       JOIN card_comments c ON c.id = r.comment_id
                       WHERE r.status = ?
                       ORDER BY report_count DESC, r.created_at ASC""",
                    (status,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get comment reports failed: {exc}")
            raise StoreError("get_comment_reports", exc) from exc

    async def resolve_report(self, report_id: str, resolver_id: str) -> bool:
        """Dismiss a report (mark resolved, don't delete comment)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE card_comment_reports
                       SET status = 'resolved', resolved_at = ?, resolver_id = ?
                       WHERE id = ? AND status = 'pending'""",
                    (now, resolver_id, report_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Resolve report failed: {exc}")
            raise StoreError("resolve_report", exc) from exc

    async def delete_comment_and_resolve_report(self, comment_id: str, report_id: str, resolver_id: str) -> bool:
        """Delete the reported comment and resolve the report."""
        try:
            async with await self._connect() as conn:
                now = datetime.now(timezone.utc).isoformat()
                await conn.execute("DELETE FROM card_comments WHERE id = ?", (comment_id,))
                await conn.execute(
                    """UPDATE card_comment_reports
                       SET status = 'resolved', resolved_at = ?, resolver_id = ?
                       WHERE id = ? AND status = 'pending'""",
                    (now, resolver_id, report_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Delete comment and resolve report failed: {exc}")
            raise StoreError("delete_comment_and_resolve_report", exc) from exc

    async def get_comment_reports_grouped(self, status: str = 'pending') -> list[dict]:
        """List pending reports grouped by comment for admin view."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT r.comment_id, r.card_id,
                              c.content AS comment_content,
                              c.user_id AS comment_author_id,
                              c.username AS comment_author_name,
                              COUNT(*) AS report_count,
                              GROUP_CONCAT(r.reason, ' | ') AS reasons,
                              MIN(r.created_at) AS first_reported_at
                       FROM card_comment_reports r
                       JOIN card_comments c ON c.id = r.comment_id
                       WHERE r.status = ?
                       GROUP BY r.comment_id
                       ORDER BY report_count DESC, first_reported_at ASC""",
                    (status,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get comment reports grouped failed: {exc}")
            raise StoreError("get_comment_reports_grouped", exc) from exc

    async def resolve_all_reports(self, comment_id: str, resolver_id: str) -> bool:
        """Resolve all pending reports for a specific comment."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE card_comment_reports
                       SET status = 'resolved', resolved_at = ?, resolver_id = ?
                       WHERE comment_id = ? AND status = 'pending'""",
                    (now, resolver_id, comment_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Resolve all reports failed: {exc}")
            raise StoreError("resolve_all_reports", exc) from exc

    async def delete_comment_and_resolve_reports(self, comment_id: str, resolver_id: str) -> bool:
        """Delete a comment and resolve all its pending reports."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                await conn.execute("DELETE FROM card_comments WHERE id = ?", (comment_id,))
                await conn.execute(
                    """UPDATE card_comment_reports
                       SET status = 'resolved', resolved_at = ?, resolver_id = ?
                       WHERE comment_id = ? AND status = 'pending'""",
                    (now, resolver_id, comment_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Delete comment and resolve reports failed: {exc}")
            raise StoreError("delete_comment_and_resolve_reports", exc) from exc

    # ── Card Reports ──

    async def add_card_report(self, card_id: str, reporter_id: str, reason: str) -> bool:
        """Insert a card report record. Duplicate reports from same user are ignored."""
        try:
            report_id = uuid.uuid4().hex[:12]
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT OR IGNORE INTO card_reports
                       (id, card_id, reporter_id, reason)
                       VALUES (?, ?, ?, ?)""",
                    (report_id, card_id, reporter_id, reason),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Add card report failed: {exc}")
            raise StoreError("add_card_report", exc) from exc

    async def get_card_reports_grouped(self, status: str = 'pending') -> list[dict]:
        """List pending card reports grouped by card for admin view."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT r.card_id,
                              COALESCE(c.name, '') AS card_name,
                              COALESCE(u.username, '') AS card_author_name,
                              COUNT(*) AS report_count,
                              GROUP_CONCAT(r.reason, ' | ') AS reasons,
                              MIN(r.created_at) AS first_reported_at
                       FROM card_reports r
                       LEFT JOIN cards c ON c.id = r.card_id
                       LEFT JOIN users u ON u.id = c.user_id
                       WHERE r.status = ?
                       GROUP BY r.card_id
                       ORDER BY report_count DESC, first_reported_at ASC""",
                    (status,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get card reports grouped failed: {exc}")
            raise StoreError("get_card_reports_grouped", exc) from exc

    async def resolve_all_card_reports(self, card_id: str, resolver_id: str) -> bool:
        """Resolve all pending reports for a card (dismiss, keep card)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE card_reports
                       SET status = 'resolved', resolved_at = ?, resolver_id = ?
                       WHERE card_id = ? AND status = 'pending'""",
                    (now, resolver_id, card_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Resolve all card reports failed: {exc}")
            raise StoreError("resolve_all_card_reports", exc) from exc

    async def takedown_card_and_resolve_reports(self, card_id: str, resolver_id: str) -> bool:
        """Takedown a public card and resolve all its pending reports."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE cards SET visibility = 'private' WHERE id = ? AND visibility = 'public'",
                    (card_id,),
                )
                await conn.execute(
                    """UPDATE card_reports
                       SET status = 'resolved', resolved_at = ?, resolver_id = ?
                       WHERE card_id = ? AND status = 'pending'""",
                    (now, resolver_id, card_id),
                )
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Takedown card and resolve reports failed: {exc}")
            raise StoreError("takedown_card_and_resolve_reports", exc) from exc

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
            raise StoreError("get_followers", exc) from exc

    async def get_followers_details(self, user_id: str, viewer_id: str = "") -> list[dict]:
        """Get followers with id, username, avatar_data, is_following, cards_count."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT u.id, u.username, u.avatar_data,
                              EXISTS(SELECT 1 FROM user_follows WHERE follower_id = :viewer AND following_id = u.id) AS is_following,
                              (SELECT COUNT(*) FROM cards WHERE user_id = u.id AND visibility = 'public' AND deleted_at IS NULL) AS cards_count
                       FROM user_follows f JOIN users u ON u.id = f.follower_id WHERE f.following_id = :uid AND f.follower_id != f.following_id""",
                    {"uid": user_id, "viewer": viewer_id},
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get followers details failed: {exc}")
            raise StoreError("get_followers_details", exc) from exc

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
            raise StoreError("get_following", exc) from exc

    async def get_following_details(self, user_id: str, viewer_id: str = "") -> list[dict]:
        """Get followed users with id, username, avatar_data, is_following, cards_count."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT u.id, u.username, u.nickname, u.avatar_data,
                              EXISTS(SELECT 1 FROM user_follows WHERE follower_id = :viewer AND following_id = u.id) AS is_following,
                              (SELECT COUNT(*) FROM cards WHERE user_id = u.id AND visibility = 'public' AND deleted_at IS NULL) AS cards_count
                       FROM user_follows f JOIN users u ON u.id = f.following_id WHERE f.follower_id = :uid""",
                    {"uid": user_id, "viewer": viewer_id},
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get following details failed: {exc}")
            raise StoreError("get_following_details", exc) from exc

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
            raise StoreError("toggle_follow", exc) from exc

    # ── Author ──

    async def get_author_cards(self, user_id: str, include_private: bool = False) -> list[dict]:
        try:
            async with await self._connect() as conn:
                visibility_clause = "" if include_private else "AND visibility = 'public'"
                cursor = await conn.execute(
                    f"""SELECT id, name, card_json, forked_from, likes, created_at, avatar_data,
                              market_description, market_tags, visibility,
                              (SELECT COUNT(*) FROM sessions WHERE card_id = cards.id) AS chat_count,
                              (SELECT title FROM texts WHERE id = cards.text_id) AS text_title
                       FROM cards WHERE user_id = ? AND deleted_at IS NULL {visibility_clause}
                       ORDER BY created_at DESC""",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get author cards failed: {exc}")
            raise StoreError("get_author_cards", exc) from exc

    # ── User Posts ──

    async def add_post(self, user_id: str, content: str, visibility: str, images: str = "", card_id: str = "", location: str = "") -> dict:
        """Add a new post. Returns the created post dict."""
        import uuid
        post_id = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO user_posts (id, user_id, content, visibility, images, card_id, location) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (post_id, user_id, content, visibility, images, card_id, location),
                )
                await conn.commit()
                cursor = await conn.execute(
                    "SELECT id, user_id, content, visibility, images, card_id, likes, created_at, location FROM user_posts WHERE id = ?",
                    (post_id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row) if row else {"id": post_id, "user_id": user_id, "content": content, "visibility": visibility, "images": images, "card_id": card_id, "likes": 0, "location": location}
        except Exception as exc:
            print(f"[SQLiteStore] Add post failed: {exc}")
            raise

    async def get_user_posts(self, user_id: str, viewer_id: str) -> list[dict]:
        """Get posts for a user. viewer_id==user_id sees all, others see only public."""
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
                          WHERE p.user_id = ?"""
                if viewer_id == user_id:
                    cursor = await conn.execute(base + " ORDER BY p.created_at DESC", (user_id,))
                else:
                    cursor = await conn.execute(base + " AND p.visibility = 'public' ORDER BY p.created_at DESC", (user_id,))
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get user posts failed: {exc}")
            raise StoreError("get_user_posts", exc) from exc

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
            raise StoreError("delete_post", exc) from exc

    async def get_feed_posts(self, user_id: str, page: int = 1, page_size: int = 20) -> list[dict]:
        """Get public posts from followed users, newest first."""
        try:
            async with await self._connect() as conn:
                offset = (page - 1) * page_size
                cursor = await conn.execute(
                    """SELECT p.id, p.user_id, p.content, p.visibility, p.images, p.card_id, p.location,
                              p.likes, p.created_at,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(u.avatar_data, '') AS author_avatar,
                              (SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = ?) AS liked_by_me,
                              (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id = p.id) AS comment_count,
                              c.name AS card_name,
                              c.card_json AS card_json,
                              c.avatar_data AS card_avatar_data
                        FROM user_posts p
                        LEFT JOIN users u ON u.id = p.user_id
                        LEFT JOIN cards c ON c.id = p.card_id AND p.card_id != ''
                        WHERE p.user_id IN (SELECT following_id FROM user_follows WHERE follower_id = ?)
                          AND p.visibility = 'public'
                        ORDER BY p.created_at DESC
                        LIMIT ? OFFSET ?""",
                    (user_id, user_id, page_size, offset),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get feed posts failed: {exc}")
            raise StoreError("get_feed_posts", exc) from exc

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
                    """SELECT pc.id, pc.user_id, pc.username, pc.content, pc.created_at, pc.ip_location,
                              COALESCE(u.avatar_data, '') AS avatar_data
                       FROM post_comments pc
                       LEFT JOIN users u ON pc.user_id = u.id
                       WHERE pc.post_id = ?
                       ORDER BY pc.created_at DESC""",
                    (post_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get post comments failed: {exc}")
            raise StoreError("get_post_comments", exc) from exc

    async def add_post_comment(self, post_id: str, user_id: str, username: str, content: str, ip_location: str = "") -> dict:
        """Add a comment to a post."""
        import uuid
        from datetime import datetime, timezone
        cid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        avatar_data = ""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO post_comments (id, post_id, user_id, username, content, ip_location) VALUES (?, ?, ?, ?, ?, ?)",
                    (cid, post_id, user_id, username, content, ip_location),
                )
                # Get user avatar for immediate return
                try:
                    cursor = await conn.execute("SELECT avatar_data FROM users WHERE id = ?", (user_id,))
                    row = await cursor.fetchone()
                    if row and row[0]:
                        avatar_data = row[0]
                except Exception as exc:
                    # store-empty-ok: 本条评论已经写入；头像只是回包里的装饰字段，查不到就留空。
                    # 上抛会把「评论已创建」变成「创建失败」，让调用方误以为没写进去。
                    print(f"[SQLiteStore] Avatar data query failed: {exc}")
            return {"id": cid, "post_id": post_id, "user_id": user_id, "username": username, "content": content, "created_at": now, "ip_location": ip_location, "avatar_data": avatar_data}
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
            raise StoreError("get_liked_post_ids", exc) from exc

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
                comments = self._list_rows(await cursor.fetchall())

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
                    replies = self._list_rows(await cursor.fetchall())
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
            return self._row_to_dict(row) if row else {"id": comment_id, "text_id": text_id, "user_id": user_id, "username": username, "content": content, "parent_id": parent_id, "likes": 0}
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
            raise StoreError("delete_text_comment", exc) from exc

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
            raise StoreError("get_liked_comment_ids", exc) from exc

    # ── Direct Messages ──

    async def send_message(self, sender_id: str, receiver_id: str, content: str, cross_border_synced: int = 1) -> dict:
        """Send a direct message. Returns the created message dict.

        cross_border_synced: 1 (default, same-region or successfully forwarded),
                             0 (cross-border, awaiting peer delivery).
        """
        import uuid
        msg_id = uuid.uuid4().hex[:12]
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO direct_messages (id, sender_id, receiver_id, content, cross_border_synced) VALUES (?, ?, ?, ?, ?)",
                    (msg_id, sender_id, receiver_id, content, cross_border_synced),
                )
                await conn.commit()
                cursor = await conn.execute(
                    "SELECT id, sender_id, receiver_id, content, is_read, created_at, cross_border_synced FROM direct_messages WHERE id = ?",
                    (msg_id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row) if row else {"id": msg_id, "sender_id": sender_id, "receiver_id": receiver_id, "content": content, "is_read": 0, "cross_border_synced": cross_border_synced}
        except Exception as exc:
            print(f"[SQLiteStore] Send message failed: {exc}")
            raise

    async def mark_message_synced(self, message_id: str) -> None:
        """Mark a cross-border DM as successfully forwarded to peer node."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE direct_messages SET cross_border_synced = 1 WHERE id = ?",
                    (message_id,),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Mark message synced failed: {exc}")
            raise

    async def get_unsynced_cross_border_messages(self, limit: int = 100) -> list[dict]:
        """Return unsynced cross-border DMs, oldest first."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, sender_id, receiver_id, content, created_at
                       FROM direct_messages
                       WHERE cross_border_synced = 0
                       ORDER BY created_at ASC
                       LIMIT ?""",
                    (limit,),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get unsynced messages failed: {exc}")
            raise

    async def has_cross_border_consent(self, user_id: str, target_region: str, scope: str = "direct_message") -> bool:
        """Check if user has granted cross-border consent for (target_region, scope)."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT 1 FROM cross_border_consent WHERE user_id = ? AND target_region = ? AND scope = ?",
                    (user_id, target_region, scope),
                )
                row = await cursor.fetchone()
            return row is not None
        except Exception as exc:
            print(f"[SQLiteStore] Has cross-border consent failed: {exc}")
            raise

    async def grant_cross_border_consent(self, user_id: str, target_region: str, scope: str = "direct_message") -> None:
        """Record user consent to send data to target_region for scope."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT OR IGNORE INTO cross_border_consent (user_id, target_region, scope) VALUES (?, ?, ?)",
                    (user_id, target_region, scope),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Grant cross-border consent failed: {exc}")
            raise

    async def revoke_cross_border_consent(self, user_id: str, target_region: str, scope: str = "direct_message") -> None:
        """Revoke user consent for cross-border data transfer."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "DELETE FROM cross_border_consent WHERE user_id = ? AND target_region = ? AND scope = ?",
                    (user_id, target_region, scope),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Revoke cross-border consent failed: {exc}")
            raise

    # ── Card cross-border sync ─────────────────────────────

    async def get_unsynced_cross_border_cards(self, limit: int = 100) -> list[dict]:
        """Return public unsynced cards, oldest first."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, user_id, name, card_json, avatar_data, visibility,
                              market_description, market_tags, created_at
                       FROM cards
                       WHERE visibility = 'public' AND cross_border_synced = 0 AND deleted_at IS NULL
                       ORDER BY created_at ASC
                       LIMIT ?""",
                    (limit,),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get unsynced cards failed: {exc}")
            raise

    async def mark_card_synced(self, card_id: str) -> None:
        """Mark a card as successfully synced to peer node."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET cross_border_synced = 1 WHERE id = ?",
                    (card_id,),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Mark card synced failed: {exc}")
            raise

    async def mark_card_unsynced(self, card_id: str) -> None:
        """Mark a published card as needing peer sync."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET cross_border_synced = 0 WHERE id = ? AND visibility = 'public'",
                    (card_id,),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Mark card unsynced failed: {exc}")
            raise

    async def get_remote_card(self, card_id: str) -> dict | None:
        """Get a remote card by ID."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM remote_cards WHERE id = ?",
                    (card_id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get remote card failed: {exc}")
            raise StoreError("get_remote_card", exc) from exc

    # ── Remote user profiles (cross-border user stubs) ──────

    async def upsert_remote_user_profile(self, id: str, username: str, home_region: str, avatar_data: str = "") -> None:
        """Create or update a remote user profile (received from peer node)."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT OR REPLACE INTO remote_user_profiles
                       (id, username, home_region, avatar_data)
                       VALUES (?, ?, ?, ?)""",
                    (id, username, home_region, avatar_data),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Upsert remote user profile failed: {exc}")
            raise

    async def get_remote_user_profile(self, id: str) -> dict | None:
        """Get a remote user profile by ID."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id, username, home_region, avatar_data, created_at FROM remote_user_profiles WHERE id = ?",
                    (id,),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get remote user profile failed: {exc}")
            raise

    # ── Remote cards ────────────────────────────────────────

    async def upsert_remote_card(self, card_id: str, origin_region: str, user_id: str,
                                  name: str, card_json: str, avatar_data: str,
                                  market_description: str, market_tags: str,
                                  origin_created_at: str) -> None:
        """Insert or update a remote card replica. No FK dependencies."""
        try:
            async with await self._connect() as conn:
                existing = await conn.execute(
                    "SELECT 1 FROM remote_cards WHERE id = ?", (card_id,),
                )
                if await existing.fetchone():
                    await conn.execute(
                        """UPDATE remote_cards SET name = ?, card_json = ?, avatar_data = ?,
                                  market_description = ?, market_tags = ?,
                                  synced_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (name, card_json, avatar_data, market_description, market_tags, card_id),
                    )
                else:
                    await conn.execute(
                        """INSERT INTO remote_cards
                           (id, origin_region, user_id, name, card_json, avatar_data,
                            market_description, market_tags, origin_created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (card_id, origin_region, user_id, name, card_json,
                         avatar_data, market_description, market_tags, origin_created_at),
                    )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Upsert remote card failed: {exc}")
            raise

    # ── Delete propagation outbox ─────────────────────────

    async def enqueue_delete_propagation(self, op_type: str, target_id: str, payload: str = "") -> None:
        """Idempotent enqueue of a delete propagation intent.

        INSERT OR IGNORE ensures the same (op_type, target_id) pair is not duplicated.
        """
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT OR IGNORE INTO cross_border_delete_outbox
                       (op_type, target_id, payload) VALUES (?, ?, ?)""",
                    (op_type, target_id, payload),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Enqueue delete propagation failed: {exc}")
            raise

    async def get_pending_delete_propagations(self, limit: int = 100) -> list[dict]:
        """Return unsynced delete propagations, oldest first."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, op_type, target_id, payload, created_at
                       FROM cross_border_delete_outbox
                       WHERE synced = 0
                       ORDER BY created_at ASC
                       LIMIT ?""",
                    (limit,),
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            print(f"[SQLiteStore] Get pending delete propagations failed: {exc}")
            raise

    async def mark_delete_propagated(self, id: int) -> None:
        """Mark a delete propagation outbox row as synced."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE cross_border_delete_outbox SET synced = 1 WHERE id = ?",
                    (id,),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Mark delete propagated failed: {exc}")
            raise

    async def delete_remote_card(self, card_id: str) -> None:
        """Delete a remote card replica by ID. Idempotent: no-op if not found."""
        try:
            async with await self._connect() as conn:
                await conn.execute("DELETE FROM remote_cards WHERE id = ?", (card_id,))
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Delete remote card failed: {exc}")
            raise

    async def purge_remote_user_data(self, user_id: str) -> dict:
        """Delete all remote card replicas + DM copies for a user."""
        counts = {}
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "DELETE FROM remote_cards WHERE user_id = ?", (user_id,),
                )
                counts["remote_cards"] = cursor.rowcount
                cursor = await conn.execute(
                    "DELETE FROM direct_messages WHERE sender_id = ? OR receiver_id = ?",
                    (user_id, user_id),
                )
                counts["direct_messages"] = cursor.rowcount
                await conn.commit()
            return counts
        except Exception as exc:
            print(f"[SQLiteStore] Purge remote user data failed: {exc}")
            raise

    async def retract_dm_message(self, message_id: str) -> None:
        """Set retracted=1 on a direct message and enqueue outbox atomically."""
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "UPDATE direct_messages SET retracted = 1 WHERE id = ?",
                    (message_id,),
                )
                await conn.execute(
                    """INSERT OR IGNORE INTO cross_border_delete_outbox
                       (op_type, target_id, payload) VALUES (?, ?, ?)""",
                    ("dm_retract", message_id, ""),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Retract DM message failed: {exc}")
            raise

    async def get_conversations(self, user_id: str) -> list[dict]:
        """Get conversation list grouped by the other participant."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT
                         sub.other_id,
                         COALESCE(u.username, rup.username, '') AS username,
                         COALESCE(u.avatar_data, rup.avatar_data, '') AS avatar_data,
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
                       LEFT JOIN users u ON u.id = sub.other_id
                       LEFT JOIN remote_user_profiles rup ON rup.id = sub.other_id
                       ORDER BY last_time DESC""",
                    (user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get conversations failed: {exc}")
            raise StoreError("get_conversations", exc) from exc

    async def get_conversation_messages(self, user_id: str, other_id: str, page: int = 1, page_size: int = 30) -> list[dict]:
        """Get paginated messages between two users."""
        try:
            offset = (page - 1) * page_size
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, sender_id, receiver_id, content, is_read, created_at, retracted, cross_border_synced
                       FROM direct_messages
                       WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
                       ORDER BY created_at DESC
                       LIMIT ? OFFSET ?""",
                    (user_id, other_id, other_id, user_id, page_size, offset),
                )
                rows = await cursor.fetchall()
            messages = self._list_rows(rows)
            messages.reverse()  # chronological order
            return messages
        except Exception as exc:
            print(f"[SQLiteStore] Get conversation messages failed: {exc}")
            raise StoreError("get_conversation_messages", exc) from exc

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
            raise StoreError("mark_read", exc) from exc

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
            raise StoreError("get_unread_count", exc) from exc

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
            raise StoreError("update_text_visibility", exc) from exc

    async def get_author_texts(self, user_id: str, viewer_id: str = "") -> list[dict]:
        """Get texts for an author profile. Returns all texts if viewer is the author, public only otherwise."""
        try:
            async with await self._connect() as conn:
                if viewer_id == user_id:
                    cursor = await conn.execute(
                        """SELECT id, title, description, text_type, char_count, created_at, visibility, cover_data
                           FROM texts WHERE user_id = ? AND (deleted_at IS NULL OR deleted_at = '')
                           ORDER BY created_at DESC""",
                        (user_id,),
                    )
                else:
                    cursor = await conn.execute(
                        """SELECT id, title, description, text_type, char_count, created_at, visibility, cover_data
                           FROM texts WHERE user_id = ? AND visibility = 'public' AND (deleted_at IS NULL OR deleted_at = '')
                           ORDER BY created_at DESC""",
                        (user_id,),
                    )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get author texts failed: {exc}")
            raise StoreError("get_author_texts", exc) from exc

    async def get_followers_count(self, user_id: str) -> int:
        """Count of users following this user."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM user_follows WHERE following_id = ? AND follower_id != following_id",
                    (user_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as exc:
            print(f"[SQLiteStore] Get followers count failed: {exc}")
            raise StoreError("get_followers_count", exc) from exc

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
            raise StoreError("get_following_count", exc) from exc

    async def is_following(self, user_id: str, target_id: str) -> bool:
        """Check if user_id follows target_id."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM user_follows WHERE follower_id = ? AND following_id = ?",
                    (user_id, target_id),
                )
                row = await cursor.fetchone()
            return row[0] > 0 if row else False
        except Exception as exc:
            print(f"[SQLiteStore] Is following check failed: {exc}")
            raise StoreError("is_following", exc) from exc

    async def is_friend(self, user_id: str, target_id: str) -> bool:
        """Mutual follow = friend."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM user_follows WHERE follower_id = ? AND following_id = ?",
                    (user_id, target_id),
                )
                a = (await cursor.fetchone())[0]
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM user_follows WHERE follower_id = ? AND following_id = ?",
                    (target_id, user_id),
                )
                b = (await cursor.fetchone())[0]
            return a > 0 and b > 0
        except Exception as exc:
            print(f"[SQLiteStore] Is friend check failed: {exc}")
            raise StoreError("is_friend", exc) from exc

    async def can_see_online_status(self, viewer_id: str, target_id: str, is_admin: bool = False) -> bool:
        """Check if viewer can see target's online status based on target's privacy setting + reciprocity."""
        if viewer_id == target_id:
            return True
        if is_admin:
            return True
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT presence_visibility FROM users WHERE id = ?", (target_id,)
                )
                row = await cursor.fetchone()
                if not row:
                    return False
                target_vis = row[0]

                # Also check viewer's own setting for reciprocity
                cursor = await conn.execute(
                    "SELECT presence_visibility FROM users WHERE id = ?", (viewer_id,)
                )
                viewer_row = await cursor.fetchone()
        except Exception as exc:
            print(f"[SQLiteStore] Can see online status failed: {exc}")
            raise StoreError("can_see_online_status", exc) from exc

        # Reciprocity: if viewer hides their own status, they can't see others'
        if viewer_row and viewer_row[0] == 'none':
            return False

        if target_vis == 'all':
            return True
        if target_vis == 'none':
            return False
        if target_vis == 'fans':
            # viewer must be following target (i.e. viewer is one of target's fans)
            return await self.is_following(viewer_id, target_id)
        if target_vis == 'mutual':
            return await self.is_friend(viewer_id, target_id)
        return False

    # ──── Market publish / version / fork API ────

    async def publish_card(self, card_id: str, user_id: str, description: str, tags: str, message: str, card_json_snapshot: str) -> str | None:
        """Publish a card to market by creating an independent fork.

        Creates a new card record (fork) with visibility='public', writes v1
        card_versions entry.  Returns the new card_id, or None on failure.

        If a published fork already exists (forked_from = card_id,
        visibility = 'public'), updates that fork in place instead — idempotent.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                # Check if a published fork already exists
                cursor = await conn.execute(
                    "SELECT id FROM cards WHERE forked_from = ? AND visibility = 'public' AND deleted_at IS NULL",
                    (card_id,),
                )
                existing = await cursor.fetchone()
                if existing:
                    # Re-publish: update existing fork
                    fork_id = existing["id"]
                    await conn.execute(
                        """UPDATE cards SET market_description = ?, market_tags = ?, publish_message = ?,
                           visibility = 'public'
                           WHERE id = ? AND deleted_at IS NULL""",
                        (description, tags, message, fork_id),
                    )
                    # Write next version
                    cursor = await conn.execute(
                        "SELECT COALESCE(MAX(version_num), 0) + 1 FROM card_versions WHERE card_id = ?",
                        (fork_id,),
                    )
                    row = await cursor.fetchone()
                    next_ver = row[0] if row else 1
                    await conn.execute(
                        """INSERT INTO card_versions (id, card_id, user_id, version_num, publish_message, diff_json, card_json_snapshot)
                           VALUES (?, ?, ?, ?, ?, '{}', ?)""",
                        (uuid.uuid4().hex[:12], fork_id, user_id, next_ver, message, card_json_snapshot),
                    )
                    await conn.commit()
                    return fork_id

                # First-time publish: create a fork (independent copy)
                # Read original card data
                cursor = await conn.execute(
                    """SELECT text_id, name, card_json, avatar_data, voice_ref_json
                       FROM cards WHERE id = ? AND deleted_at IS NULL""",
                    (card_id,),
                )
                src = await cursor.fetchone()
                if not src:
                    return None

                fork_id = uuid.uuid4().hex[:12]
                await conn.execute(
                    """INSERT INTO cards (id, text_id, name, card_json, created_at, avatar_data, user_id,
                                          visibility, forked_from, likes, voice_ref_json,
                                          market_description, market_tags, publish_message)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'public', ?, 0, ?, ?, ?, ?)""",
                    (fork_id,
                     src["text_id"], src["name"], src["card_json"],
                     now,
                     src["avatar_data"], user_id, card_id,
                     src["voice_ref_json"],
                     description, tags, message),
                )
                await conn.execute(
                    """INSERT INTO card_versions (id, card_id, user_id, version_num, publish_message, diff_json, card_json_snapshot)
                       VALUES (?, ?, ?, 1, ?, '{}', ?)""",
                    (uuid.uuid4().hex[:12], fork_id, user_id, message, card_json_snapshot),
                )
                await conn.commit()
            return fork_id
        except Exception as exc:
            print(f"[SQLiteStore] Publish card failed: {exc}")
            raise StoreError("publish_card", exc) from exc

    async def update_published_card(self, card_id: str, user_id: str, card_json: str, description: str, tags: str, message: str, old_json: str) -> dict | None:
        """Update card fields, generate field-level diff, write next card_versions entry. Returns the new version record or None."""
        try:
            diff = {}
            try:
                old = json.loads(old_json) if old_json else {}
                new_parsed = json.loads(card_json) if card_json else {}
                for k in set(list(old.keys()) + list(new_parsed.keys())):
                    if old.get(k) != new_parsed.get(k):
                        diff[k] = {"old": old.get(k, ""), "new": new_parsed.get(k, "")}
            except Exception:
                # store-empty-ok: 同 export_session —— 存量 JSON 坏了不是「查询失败」，
                # 降级在写入的 diff 里显式可见（{"_full": "parse error"}），写入本身照常进行。
                diff = {"_full": "parse error"}

            diff_json = json.dumps(diff, ensure_ascii=False)

            async with await self._connect() as conn:
                await conn.execute(
                    """UPDATE cards SET card_json = ?, market_description = ?, market_tags = ?, publish_message = ?
                       WHERE id = ? AND deleted_at IS NULL""",
                    (card_json, description, tags, message, card_id),
                )
                # Get next version number
                cursor = await conn.execute(
                    "SELECT COALESCE(MAX(version_num), 0) + 1 FROM card_versions WHERE card_id = ?",
                    (card_id,),
                )
                row = await cursor.fetchone()
                next_ver = row[0] if row else 1
                version_id = uuid.uuid4().hex[:12]
                await conn.execute(
                    """INSERT INTO card_versions (id, card_id, user_id, version_num, publish_message, diff_json, card_json_snapshot)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (version_id, card_id, user_id, next_ver, message, diff_json, card_json),
                )
                await conn.commit()
                return {
                    "id": version_id,
                    "version_num": next_ver,
                    "publish_message": message,
                    "diff_json": diff_json,
                    "card_json_snapshot": card_json,
                    "created_at": None,  # caller will add timestamp if needed
                }
        except Exception as exc:
            print(f"[SQLiteStore] Update published card failed: {exc}")
            raise StoreError("update_published_card", exc) from exc

    async def get_card_versions(self, card_id: str) -> list[dict]:
        """List all versions for a card in descending order."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT id, card_id, user_id, version_num, publish_message, diff_json, card_json_snapshot, created_at
                       FROM card_versions WHERE card_id = ? ORDER BY version_num DESC""",
                    (card_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get card versions failed: {exc}")
            raise StoreError("get_card_versions", exc) from exc

    async def delete_card_version(self, card_id: str, version_id: str) -> bool:
        """Delete a specific version of a card."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id FROM card_versions WHERE id = ? AND card_id = ?",
                    (version_id, card_id),
                )
                if not await cursor.fetchone():
                    return False
                await conn.execute(
                    "DELETE FROM card_versions WHERE id = ? AND card_id = ?",
                    (version_id, card_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Delete card version failed: {exc}")
            raise StoreError("delete_card_version", exc) from exc

    async def update_card_version(self, card_id: str, version_id: str, publish_message: str) -> bool:
        """Update the publish_message of a specific version."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT id FROM card_versions WHERE id = ? AND card_id = ?",
                    (version_id, card_id),
                )
                if not await cursor.fetchone():
                    return False
                await conn.execute(
                    "UPDATE card_versions SET publish_message = ? WHERE id = ? AND card_id = ?",
                    (publish_message, version_id, card_id),
                )
                await conn.commit()
            return True
        except Exception as exc:
            print(f"[SQLiteStore] Update card version failed: {exc}")
            raise StoreError("update_card_version", exc) from exc

    async def get_card_forks(self, card_id: str) -> list[dict]:
        """List public cards forked from this card_id."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    """SELECT c.id, c.name, c.card_json, c.user_id, c.avatar_data, c.likes, c.created_at,
                              COALESCE(u.username, '') AS author_name,
                              COALESCE(u.avatar_data, '') AS author_avatar
                       FROM cards c
                       LEFT JOIN users u ON u.id = c.user_id
                       WHERE c.forked_from = ? AND c.visibility = 'public' AND c.deleted_at IS NULL
                       ORDER BY c.likes DESC, c.created_at DESC""",
                    (card_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get card forks failed: {exc}")
            raise StoreError("get_card_forks", exc) from exc

    # ---- Admin: Featured Cards ----

    async def get_featured_cards(self) -> list[dict]:
        """Return featured cards with full card info, ordered by sort_order."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
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
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get featured cards failed: {exc}")
            raise StoreError("get_featured_cards", exc) from exc

    async def add_featured_card(self, card_id: str) -> str | None:
        """Add a card to featured. Returns the new row id or None on failure."""
        import uuid
        try:
            fid = uuid.uuid4().hex[:12]
            async with await self._connect() as conn:
                # Get next sort_order
                cursor = await conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM featured_cards")
                row = await cursor.fetchone()
                next_order = row[0] if row else 0
                await conn.execute(
                    "INSERT INTO featured_cards (id, card_id, sort_order) VALUES (?, ?, ?)",
                    (fid, card_id, next_order),
                )
                await conn.commit()
            return fid
        except Exception as exc:
            print(f"[SQLiteStore] Add featured card failed: {exc}")
            raise StoreError("add_featured_card", exc) from exc

    async def remove_featured_card(self, id: str) -> bool:
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute("DELETE FROM featured_cards WHERE id = ?", (id,))
                await conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print(f"[SQLiteStore] Remove featured card failed: {exc}")
            raise StoreError("remove_featured_card", exc) from exc

    async def reorder_featured_cards(self, ids: list[str]) -> None:
        """Update sort_order based on array index."""
        try:
            async with await self._connect() as conn:
                for idx, fid in enumerate(ids):
                    await conn.execute(
                        "UPDATE featured_cards SET sort_order = ? WHERE id = ?",
                        (idx, fid),
                    )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Reorder featured cards failed: {exc}")
            raise

    # ---- Reading progress ----

    async def save_reading_progress(self, user_id: str, text_id: str, progress: float, scroll_position: int) -> None:
        """UPSERT reading progress for a user+text pair."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                await conn.execute(
                    """INSERT INTO reading_progress (user_id, text_id, progress, scroll_position, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, text_id) DO UPDATE SET
                           progress = excluded.progress,
                           scroll_position = excluded.scroll_position,
                           updated_at = excluded.updated_at""",
                    (user_id, text_id, progress, scroll_position, now),
                )
                await conn.commit()
        except Exception as exc:
            print(f"[SQLiteStore] Save reading progress failed: {exc}")
            raise StoreError("save_reading_progress", exc) from exc

    async def get_reading_progress(self, user_id: str, text_id: str) -> dict | None:
        """Get reading progress for a user+text pair."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT progress, scroll_position, updated_at FROM reading_progress WHERE user_id = ? AND text_id = ?",
                    (user_id, text_id),
                )
                row = await cursor.fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            print(f"[SQLiteStore] Get reading progress failed: {exc}")
            raise StoreError("get_reading_progress", exc) from exc

    async def get_all_reading_progress(self, user_id: str) -> list[dict]:
        """Get all reading progress records for a user."""
        try:
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "SELECT text_id, progress, scroll_position, updated_at FROM reading_progress WHERE user_id = ? ORDER BY updated_at DESC",
                    (user_id,),
                )
                rows = await cursor.fetchall()
            return self._list_rows(rows)
        except Exception as exc:
            print(f"[SQLiteStore] Get all reading progress failed: {exc}")
            raise StoreError("get_all_reading_progress", exc) from exc

    async def cleanup_empty_cards(self, text_id: str, user_id: str) -> int:
        """Soft-delete cards with empty card_json (cleanup after failed distillation)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with await self._connect() as conn:
                cursor = await conn.execute(
                    "UPDATE cards SET deleted_at = ? WHERE text_id = ? AND user_id = ? AND (card_json IS NULL OR card_json = '' OR card_json = '{}')",
                    (now, text_id, user_id),
                )
                return cursor.rowcount
        except Exception as exc:
            print(f"[SQLiteStore] Cleanup empty cards failed: {exc}")
            raise StoreError("cleanup_empty_cards", exc) from exc
