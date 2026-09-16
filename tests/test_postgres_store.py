"""Tests for PostgresStore CRUD using a real PostgreSQL database.

These tests require a running PostgreSQL instance.  Set these env vars:

  STORAGE_BACKEND=postgres
  DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<db>

**PG 连不上时这些用例显式 skip（原因可见），不是失败。** 一条在标准本地环境（没跑 PG）
下恒红的断言，会训练所有人忽略红色，最终把真失败也一起忽略掉。

**但 skip 不得变成静默通道**（缺陷 21「豁免即静默放行」同型）：环境用
`REQUIRE_PG_TESTS=1` 声明「本环境保证有 PG」（CI 就是这么声明的），那时本模块**拒绝跳过**
—— PG 连不上就直接红，且
`test_pg_suite_is_not_silently_disabled_where_pg_is_required` 把「在需要 PG 的环境里被
静默跳过 / 被 SKIP_PG_TESTS 关掉」本身判红。

Skip: export SKIP_PG_TESTS=1 to skip all PostgresStore tests（显式退出，仅本地用）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import PG_ENV, pg_reachable, pg_required

from storage.postgres_store import PostgresStore
from storage.sqlite_store import SQLiteStore


_REPO = Path(__file__).resolve().parent.parent
_PG_DIR = _REPO / "storage" / "migrations_pg"
_SQLITE_DIR = _REPO / "storage" / "migrations"
_PG_STORE_SRC = _REPO / "storage" / "postgres_store.py"

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(?P<table>\w+)", re.IGNORECASE)
_COMMENT_RE = re.compile(r"--[^\n]*")
_TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _dsn() -> str:
    return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/charsim_test")


# 需要真 PG 的用例统一挂这个 mark（含可见原因）。挂在**类**上而非模块级 `pytestmark`：
# `TestPgFreshSchemaClosure.test_lock_has_teeth` 是纯提取器负控、根本不需要 PG，模块级
# mark 会连它一起 skip —— 把一条本地跑得动的锁用不相干的理由关掉，正是要消灭的静默通道。
_pg = PG_ENV.skipif("PostgresStore 用例")


async def _pg_tables() -> set[str]:
    """真 PG 库 public schema 里的表集合（跑懒初始化 → 迁移已应用）。"""
    store = PostgresStore(_dsn())
    await store._ensure_initialized()
    try:
        async with await store._connect() as conn:
            rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        return {r["tablename"] for r in rows}
    finally:
        await store.close()


def _declared_tables(directory: Path) -> set[str]:
    """某个迁移目录声明的表名（去注释后扫 CREATE TABLE）—— 与 `tests/test_sqlite_fresh_schema.py`
    的 `_pg_declared_tables` 同一个口径，独立一份免得跨测试文件 import。"""
    names: set[str] = set()
    for p in sorted(directory.glob("*.sql")):
        sql = _COMMENT_RE.sub("", p.read_text(encoding="utf-8"))
        names |= {m.group("table") for m in _CREATE_TABLE_RE.finditer(sql)}
    return names


def _referenced_tables(src: str, vocabulary: set[str]) -> set[str]:
    """源码里 SQL 字符串引用的表名，按 DDL 词表过滤后返回。

    **为什么必须过滤**：裸正则会把散文与 f-string 片段当表名（实测 23 个假阳性：
    `a` / `the` / `this` / `set` / `sort_order` …）。

    **词表取两侧 DDL 的并集，不是本后端声明集** —— 用本后端声明集过滤会让断言恒真
    （`refs ∩ declared ⊆ declared ⊆ created`），锁就瞎了。用并集则 SQLite-only 的表名
    能活着走到断言处，从而被抓住。
    """
    return {t.lower() for t in _TABLE_REF_RE.findall(src)} & {v.lower() for v in vocabulary}


# ── 列级闭环：两侧**真库**的事实对事实（缺陷 23）──────────────────────────────
#
# **为什么不复用表级那个形状**（「真库 ⊇ 文本声明」）：那个形状在表级成立，靠的是一个
# 从没被写下来过的前提 —— **没有任何东西删过表**。本仓实测这个前提的边界：
#
#     DROP TABLE   0 处        （两侧迁移目录全扫）
#     DROP COLUMN  4 处        （migrations_pg/005_data_residency.sql 删 users 的
#                              password_hash / api_key / base_url / model）
#
# 而且同一个删除在两侧由**两套完全不同的机制**完成：PG 是那四条声明式
# `ALTER TABLE users DROP COLUMN IF EXISTS`；SQLite 是 `storage/sqlite_store.py` 里
# 的 Python 表重建（`if "password_hash" in all_cols` 触发），`.sql` 文本里**根本没有
# 这条语句**。
#
# 把表级形状套到列级会这样断：`test_schema_parity` 的提取器只认 CREATE TABLE +
# `ALTER ... ADD COLUMN` —— DROP 不认、Python 更不认，于是 `users` 的**声明列**比真库
# 多这 4 个 → 锁当场红；要它绿只剩加豁免清单一条路，而「豁免即永久放行」（§四），
# 且豁免理由（「运行期被删」）没有任何第二条断言闭环。两个盲区（PG 的 DROP、SQLite 的
# Python 重建）在两侧**互相抵消**，这正是一直没人发现的原因 —— 文本 parity 是绿的。
#
# 所以列级不比文本，比**另一侧真库**：两侧都是事实，直接对事实。不需要声明列标尺、
# 不需要 SQL 解析器、不需要任何豁免清单。将来谁想「顺手统一成表级那个形状」，
# 上面这几行就是拦他的：那个形状的前提在列级不成立。


async def _build_fresh_sqlite(db_path: str) -> None:
    """真建一个新 SQLite 库、真跑一次迁移。之后用 `_sqlite_columns` 读它。"""
    await SQLiteStore(db_path)._ensure_initialized()


async def _build_restarted_sqlite(db_path: str) -> None:
    """建库之后**用新实例再 init 一次** —— 「重启过的库」，生产实际运行的那个状态。

    与 `_build_fresh_sqlite` 只差一次 `_ensure_initialized`（每次都要新 store 实例，
    因为 `_initialized` 会短路第二次）。第二遍会让 018 **重新** `ADD COLUMN`
    api_key/base_url/model —— 执行器只按「列在不在」判断，没有「已应用」账本。
    所以这是唯一能看见 users 列集漂移的状态：缺陷 23 的锁只比 fresh，从没看过它。
    """
    await SQLiteStore(db_path)._ensure_initialized()
    await SQLiteStore(db_path)._ensure_initialized()


def _sqlite_columns(db_path: str) -> dict[str, set[str]]:
    """读一个**已建好**的 SQLite 库的 {表: {列}}。

    **只读，绝不重建**：`_ensure_initialized` 会把缺的列补回来（067 那套 PRAGMA 前置
    就是干这个的、users 重建则是按需触发）。拿它当「读」会让变异被当场修复 ——
    「单侧删列」的变异永远红不了，红出来的反而是「重跑又加回来」的副作用。
    """
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        return {t: {r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')}
                for t in tables}
    finally:
        conn.close()


async def _pg_columns() -> dict[str, set[str]]:
    """真 PG 库 public schema 的 {表: {列}}（跑懒初始化 → 迁移已应用）。"""
    store = PostgresStore(_dsn())
    await store._ensure_initialized()
    try:
        async with await store._connect() as conn:
            rows = await conn.fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public'")
        out: dict[str, set[str]] = {}
        for r in rows:
            out.setdefault(r["table_name"], set()).add(r["column_name"])
        return out
    finally:
        await store.close()


def _column_drift(left: dict[str, set[str]], right: dict[str, set[str]]) -> list[str]:
    """两侧 {表:{列}} 的全部差异，逐条点名**哪张表、哪一列、缺在哪侧**。"""
    problems: list[str] = []
    for t in sorted(set(left) - set(right)):
        problems.append(f"表 `{t}` 只在 SQLite 新库里有，真 PG 没有")
    for t in sorted(set(right) - set(left)):
        problems.append(f"表 `{t}` 只在真 PG 里有，SQLite 新库没有")
    for t in sorted(set(left) & set(right)):
        only_sq = sorted(left[t] - right[t])
        only_pg = sorted(right[t] - left[t])
        if only_sq:
            problems.append(f"[{t}] 列 {only_sq} 只在 SQLite 新库里，真 PG 缺这些列")
        if only_pg:
            problems.append(f"[{t}] 列 {only_pg} 只在真 PG 里，SQLite 新库缺这些列")
    return problems


async def _clean_tables(store: PostgresStore) -> None:
    """Truncate all tables for a clean test state."""
    async with await store._connect() as conn:
        tables = [
            "messages", "sessions", "cards", "texts", "users",
            "group_messages", "group_sessions", "direct_messages",
            "user_follows", "distill_chunks", "distill_tasks",
        ]
        for t in tables:
            await conn.execute(f"DELETE FROM {t}")


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def store():
    st = PostgresStore(_dsn())
    await st._ensure_initialized()
    await _clean_tables(st)
    yield st
    await st.close()


@pytest.fixture
def text_id():
    return f"txt_{uuid.uuid4().hex}"


@pytest.fixture
def card_id():
    return f"card_{uuid.uuid4().hex}"


@pytest.fixture
def session_id():
    return f"ses_{uuid.uuid4().hex}"


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex}"


# ── Text CRUD ────────────────────────────────────────────────────────────────

@_pg
class TestTextCrud:
    async def test_save_and_get(self, store, text_id):
        result = await store.save_text(text_id, "test.txt", "Hello world")
        assert result.get("id") == text_id
        assert result.get("filename") == "test.txt"
        assert result.get("content") == "Hello world"

        got = await store.get_text_unscoped(text_id)
        assert got is not None
        assert got["id"] == text_id
        assert got["content"] == "Hello world"

    async def test_save_with_metadata(self, store, text_id):
        result = await store.save_text(
            text_id, "novel.txt", "Long content",
            title="My Novel", description="A test",
            text_type="story", user_id="usr1",
        )
        assert result["title"] == "My Novel"
        assert result["text_type"] == "story"
        assert result["user_id"] == "usr1"

    async def test_delete_text(self, store, text_id):
        await store.save_text(text_id, "del.txt", "To be deleted")
        await store.delete_text(text_id)
        got = await store.get_text_unscoped(text_id)
        assert got is None or got.get("deleted_at", "")

    async def test_get_text_not_found(self, store):
        got = await store.get_text_unscoped("nonexistent")
        assert got is None

    async def test_list_texts(self, store):
        for i in range(3):
            tid = f"txt_{uuid.uuid4().hex}"
            await store.save_text(tid, f"file{i}.txt", f"content{i}")
        texts = await store.list_texts()
        assert len(texts) >= 3


# ── Card CRUD ────────────────────────────────────────────────────────────────

@_pg
class TestCardCrud:
    async def test_save_and_get_card(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        card_json = json.dumps({"name": "Alice", "description": "A character"}, ensure_ascii=False)
        result = await store.save_card(card_id, text_id, "Alice", card_json, user_id="usr1")
        assert result.get("id") == card_id

        got = await store.get_card_unscoped(card_id)
        assert got is not None
        assert got["name"] == "Alice"

    async def test_get_card_not_found(self, store):
        got = await store.get_card_unscoped("nonexistent")
        assert got is None

    async def test_get_cards_by_text(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        for i in range(3):
            cid = f"card_{uuid.uuid4().hex}"
            await store.save_card(cid, text_id, f"Char{i}", json.dumps({"name": f"Char{i}"}))
        cards = await store.list_cards(text_id)
        assert len(cards) >= 3

    async def test_get_card_detail(self, store, text_id, card_id, user_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Alice", json.dumps({"name": "Alice"}), user_id=user_id)
        detail = await store.get_card_detail(card_id, user_id)
        assert detail is not None


# ── Session CRUD ─────────────────────────────────────────────────────────────

@_pg
class TestSessionCrud:
    async def test_save_and_get_session(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Bob", json.dumps({"name": "Bob"}))
        result = await store.save_session(session_id, card_id, "", "")
        assert result.get("id") == session_id

        got = await store.get_session_unscoped(session_id)
        assert got is not None
        assert got["card_id"] == card_id

    async def test_list_sessions_by_card(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Bob", json.dumps({"name": "Bob"}))
        for i in range(3):
            sid = f"ses_{uuid.uuid4().hex}"
            await store.save_session(sid, card_id, "", "")
        # list_sessions filters by keyword, character, text_id, paginated
        result = await store.list_sessions("", "", text_id, 1, 10)
        assert isinstance(result, dict)
        assert len(result["items"]) >= 3


# ── Message CRUD ─────────────────────────────────────────────────────────────

@_pg
class TestMessageCrud:
    async def test_save_and_get_messages(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Char", json.dumps({"name": "Char"}))
        await store.save_session(session_id, card_id, "", "")

        msg1 = await store.save_message(session_id, "user", "Hello", "")
        assert msg1.get("id") is not None

        msg2 = await store.save_message(session_id, "assistant", "Hi there", "")
        assert msg2["id"] > msg1["id"]

        msgs = await store.get_messages(session_id)
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["content"] == "Hi there"

    async def test_delete_messages_after(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Char", json.dumps({"name": "Char"}))
        await store.save_session(session_id, card_id, "", "")
        m1 = await store.save_message(session_id, "user", "keep", "")
        m2 = await store.save_message(session_id, "user", "delete_this", "")

        # delete_messages_after includes the given message_id
        await store.delete_messages_after(session_id, m2["id"])
        msgs = await store.get_messages(session_id)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "keep"


# ── User CRUD ────────────────────────────────────────────────────────────────

@_pg
class TestUserCrud:
    async def test_create_and_get_user(self, store, user_id):
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        result = await store.create_user(user_id, username, "hash")
        assert result is not None
        assert result.get("id") == user_id

        got = await store.get_user_by_id(user_id)
        assert got is not None
        assert got["id"] == user_id

    async def test_get_user_by_username(self, store, user_id):
        username = f"unique_{uuid.uuid4().hex[:8]}"
        await store.create_user(user_id, username, "hash")
        got = await store.get_user_by_username(username)
        assert got is not None
        assert got["id"] == user_id

    async def test_get_user_not_found(self, store):
        got = await store.get_user_by_id("nonexistent")
        assert got is None

    async def test_list_users(self, store, user_id):
        await store.create_user(user_id, f"listuser_{uuid.uuid4().hex[:8]}", "hash")
        users = await store.get_all_users()
        assert len(users) >= 1


# ── User purge: distill cascade + deletion impact (F) ────────────────────────

@_pg
class TestUserPurgeDistillCascade:
    """delete_user 同事务清掉该用户的 distill_tasks / distill_chunks（F1），不波及其它用户。"""

    async def test_delete_user_clears_distill_rows(self, store, text_id, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.create_user(user_id, f"pg_u1_{uuid.uuid4().hex[:8]}", "h")
        await store.create_user(other, f"pg_u2_{uuid.uuid4().hex[:8]}", "h")
        await store.save_text(text_id, "src.txt", "source")

        await store.create_distill_task("dt_pg1", user_id, text_id, "张三")
        await store.save_distill_chunk("dt_pg1", 0, json.dumps({"r": "零"}))
        await store.save_distill_chunk("dt_pg1", 1, json.dumps({"r": "一"}))
        await store.create_distill_task("dt_pg2", other, text_id, "李四")
        await store.save_distill_chunk("dt_pg2", 0, json.dumps({"r": "零"}))

        counts = await store.delete_user(user_id)
        assert counts["distill_tasks"] == 1
        assert counts["distill_chunks"] == 2

        assert await store.get_distill_task_unscoped("dt_pg1") is None
        assert await store.get_distill_chunks("dt_pg1") == []
        # 其它用户的蒸馏行不受影响
        assert await store.get_distill_task_unscoped("dt_pg2") is not None
        assert len(await store.get_distill_chunks("dt_pg2")) == 1

    async def test_text_deletion_impact_counts_distill_rows(self, store, text_id, user_id):
        """永久删除会连带清蒸馏行，impact 必须如实计入（F2）。"""
        await store.save_text(text_id, "src.txt", "source")
        await store.create_distill_task("dt_imp", user_id, text_id, "张三")
        await store.save_distill_chunk("dt_imp", 0, json.dumps({"r": "零"}))
        await store.save_distill_chunk("dt_imp", 1, json.dumps({"r": "一"}))

        impact = await store.get_text_deletion_impact(text_id, user_id)
        assert impact["distill_task_count"] == 1
        assert impact["distill_chunk_count"] == 2


@_pg
class TestUserPurgeDistillRace:
    """delete_user 的蒸馏清理顺序（先父后子）在并发写下的闭合性 —— 实测，不靠推断。

    admin.delete_user 不停在跑的蒸馏线程，删除与 bg 分片写会真并发。闭合点与
    hard_delete_text 同：先删父行取排他锁 → save_distill_chunk 的 FOR SHARE 必阻塞、
    提交后父行已无 → 跳过；反序则插入方先拿共享锁落片，之后才删父 → 孤儿存活。
    单写者撞窗概率低，故 K 写者 × R 轮把命中率抬到可判定；正确实现下每轮都干净。
    """

    ROUNDS = 20
    WRITERS = 6

    async def test_writer_outliving_user_delete_leaves_no_rows(self, store, user_id):
        import asyncio as _aio

        async def _round() -> bool:
            uid = f"usr_{uuid.uuid4().hex}"
            await store.create_user(uid, f"u_{uuid.uuid4().hex[:8]}", "h")
            tid = f"txt_{uuid.uuid4().hex}"
            task_id = f"dt_{uuid.uuid4().hex}"
            await store.save_text(tid, "src.txt", "角色说的话" * 20, user_id=uid)
            await store.create_distill_task(task_id, uid, tid, "甲", status="running")

            stop = _aio.Event()
            ready = _aio.Event()
            written = {"n": 0}

            async def writer(w: int):
                i = w
                while not stop.is_set():
                    await store.save_distill_chunk(task_id, i, f"分析{i}", fingerprint=f"fp{i}")
                    i += self.WRITERS
                    written["n"] += 1
                    if written["n"] >= 2 * self.WRITERS:
                        ready.set()
                    await _aio.sleep(0)

            async def deleter():
                await ready.wait()          # 等写者跑起来，删除必落在写循环期间
                await store.delete_user(uid)
                stop.set()

            await _aio.gather(*[writer(w) for w in range(self.WRITERS)], deleter())
            return await store.get_distill_chunks(task_id) != []

        orphan_rounds = 0
        for _ in range(self.ROUNDS):
            if await _round():
                orphan_rounds += 1

        assert orphan_rounds == 0, f"{orphan_rounds}/{self.ROUNDS} 轮留下孤儿分片"


# ── Group Session CRUD ───────────────────────────────────────────────────────

@_pg
class TestGroupSessionCrud:
    async def test_create_and_get_group(self, store):
        gid = f"grp_{uuid.uuid4().hex}"
        await store.create_group_session(gid, "Test Group", ["card1", "card2"], user_id="")

        got = await store.get_group_session_owned(gid, "")
        assert got is not None
        assert got["name"] == "Test Group"

    async def test_list_group_sessions(self, store):
        gid = f"grp_{uuid.uuid4().hex}"
        uid = f"usr_{uuid.uuid4().hex}"
        await store.create_group_session(gid, "Group A", [], user_id=uid)
        sessions = await store.list_group_sessions(uid)
        assert any(s["id"] == gid for s in sessions)


# ── Follow / DM ──────────────────────────────────────────────────────────────

@_pg
class TestFollowAndDM:
    async def test_follow_user(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.create_user(user_id, f"f1_{uuid.uuid4().hex[:8]}", "h")
        await store.create_user(other, f"f2_{uuid.uuid4().hex[:8]}", "h")
        await store.toggle_follow(user_id, other)
        followers = await store.get_followers(other)
        assert user_id in followers

    async def test_unfollow_user(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.create_user(user_id, f"u1_{uuid.uuid4().hex[:8]}", "h")
        await store.create_user(other, f"u2_{uuid.uuid4().hex[:8]}", "h")
        await store.toggle_follow(user_id, other)
        following = await store.get_following(user_id)
        assert other in following

        await store.toggle_follow(user_id, other)
        following = await store.get_following(user_id)
        assert other not in following


@_pg
class TestDMCrud:
    async def test_send_and_get_dm(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.create_user(user_id, f"d1_{uuid.uuid4().hex[:8]}", "h")
        await store.create_user(other, f"d2_{uuid.uuid4().hex[:8]}", "h")

        result = await store.send_message(user_id, other, "Hello DM")
        assert "Hello DM" in str(result.get("content", ""))

        msgs = await store.get_conversation_messages(user_id, other)
        assert any(m["content"] == "Hello DM" for m in msgs)


# ── Distill task persistence ─────────────────────────────────────────────────

@_pg
class TestDistillTaskPersistence:
    """create_distill_task → update → chunks (idempotent) → running-count lifecycle."""

    async def test_create_get_update(self, store, text_id, user_id):
        task_id = f"dt_{uuid.uuid4().hex}"
        row = await store.create_distill_task(task_id, user_id, text_id, character="角色A")
        assert row["task_id"] == task_id and row["status"] == "queued"

        got = await store.get_distill_task_unscoped(task_id)
        assert got is not None and got["character"] == "角色A"

        assert await store.update_distill_task(task_id, progress_pct=42, message="跑到一半") == 1
        got2 = await store.get_distill_task_unscoped(task_id)
        assert got2["progress_pct"] == 42 and got2["message"] == "跑到一半"
        assert got2["status"] == "queued"  # untouched field preserved
        await store.update_distill_task(task_id, status="done", progress_pct=100)
        got3 = await store.get_distill_task_unscoped(task_id)
        assert got3["status"] == "done" and got3["message"] == "跑到一半"

    async def test_create_duplicate_id_raises(self, store, text_id, user_id):
        # INSERT-only：重复 task_id 抛异常，不再静默转 UPDATE。
        task_id = f"dt_{uuid.uuid4().hex}"
        await store.create_distill_task(task_id, user_id, text_id)
        with pytest.raises(Exception):
            await store.create_distill_task(task_id, user_id, text_id)

    async def test_update_missing_row_returns_zero(self, store):
        assert await store.update_distill_task(f"dt_{uuid.uuid4().hex}", status="done") == 0

    async def test_task_checkpoint_params_roundtrip_and_preserved(self, store, text_id, user_id):
        # 3b：任务级切分指纹（chunk_size/overlap/text_fingerprint）落库可读；
        # KEY：进度 update 不传这三参时不得把它们冲回 NULL（保住已盖章 checkpoint）。
        task_id = f"dt_{uuid.uuid4().hex}"
        await store.create_distill_task(task_id, user_id, text_id, character="A",
                                        chunk_size=8000, overlap=500, text_fingerprint="fp_full")
        got = await store.get_distill_task_unscoped(task_id)
        assert got["chunk_size"] == 8000 and got["overlap"] == 500
        assert got["text_fingerprint"] == "fp_full"

        await store.update_distill_task(task_id, status="running", progress_pct=50)
        got2 = await store.get_distill_task_unscoped(task_id)
        assert got2["chunk_size"] == 8000 and got2["overlap"] == 500
        assert got2["text_fingerprint"] == "fp_full"

    async def test_chunk_write_blocked_without_parent(self, store, text_id, user_id):
        # 改动 B：父任务行不在 → 零行写入、不报错。对照组：父在则写进。
        orphan = f"dt_{uuid.uuid4().hex}"
        await store.save_distill_chunk(orphan, 0, json.dumps({"r": "x"}))
        assert await store.get_distill_chunks(orphan) == []

        live = f"dt_{uuid.uuid4().hex}"
        await store.create_distill_task(live, user_id, text_id)
        await store.save_distill_chunk(live, 0, json.dumps({"r": "x"}))
        assert len(await store.get_distill_chunks(live)) == 1

    async def test_chunk_fingerprint_roundtrip_and_last_write_wins(self, store, text_id, user_id):
        # 3b：chunk_fingerprint 随片落库可读；同 index 重写**原地覆盖**（result 与指纹都换新）。
        # 覆盖而非 first-wins 是缺陷 4 的修法：旧契约下「原文变→指纹变→重跑」的新结果
        # 永远写不进去，该片每次续跑都重跑。
        task_id = f"dt_{uuid.uuid4().hex}"
        await store.create_distill_task(task_id, user_id, text_id, character="A")
        await store.save_distill_chunk(task_id, 2, json.dumps({"r": "二"}, ensure_ascii=False),
                                       fingerprint="fp2")
        await store.save_distill_chunk(task_id, 2, json.dumps({"r": "重写"}, ensure_ascii=False),
                                       fingerprint="fp2_other")
        chunks = await store.get_distill_chunks(task_id)
        assert len(chunks) == 1, "联合 PK 仍保证不重复行"
        assert chunks[0]["chunk_fingerprint"] == "fp2_other"
        assert chunks[0]["result"] == json.dumps({"r": "重写"}, ensure_ascii=False)

    async def test_chunk_save_replaces_same_index_no_duplicate_row(self, store, text_id, user_id):
        task_id = f"dt_{uuid.uuid4().hex}"
        await store.create_distill_task(task_id, user_id, text_id, character="A")
        await store.save_distill_chunk(task_id, 3, json.dumps({"r": "三"}, ensure_ascii=False))
        await store.save_distill_chunk(task_id, 3, json.dumps({"r": "重写"}, ensure_ascii=False))
        await store.save_distill_chunk(task_id, 1, json.dumps({"r": "一"}, ensure_ascii=False))
        chunks = await store.get_distill_chunks(task_id)
        # 联合 PK 仍保证行不重复；同 index 重写覆盖内容，最后一次写赢
        assert [c["chunk_index"] for c in chunks] == [1, 3]
        by_index = {c["chunk_index"]: c["result"] for c in chunks}
        assert by_index[3] == json.dumps({"r": "重写"}, ensure_ascii=False)

    async def test_count_running(self, store, text_id, user_id):
        a = f"dt_{uuid.uuid4().hex}"
        b = f"dt_{uuid.uuid4().hex}"
        c = f"dt_{uuid.uuid4().hex}"
        other = f"usr_{uuid.uuid4().hex}"
        await store.create_distill_task(a, user_id, text_id, status="queued")
        await store.create_distill_task(b, user_id, text_id, status="done")
        await store.create_distill_task(c, other, text_id, status="queued")
        assert await store.count_running_distills(user_id) == 1
        assert await store.count_running_distills(other) == 1
        await store.update_distill_task(a, status="error")
        assert await store.count_running_distills(user_id) == 0

    async def test_get_nonexistent(self, store, user_id):
        assert await store.get_distill_task_unscoped(f"dt_{uuid.uuid4().hex}") is None
        assert await store.get_distill_chunks(f"dt_{uuid.uuid4().hex}") == []
        assert await store.count_running_distills(f"usr_{uuid.uuid4().hex}") == 0

    async def test_count_running_window_ages_out_ghost(self, store, text_id, user_id):
        # 时效窗：活任务靠进度写刷 updated_at；幽灵行（线程已死、终态没落库）不会。
        # 超窗的 running 行不计入 → 不会永久挡住该用户。
        await store.create_distill_task("dtLive", user_id, text_id, status="running")
        await store.create_distill_task("dtGhost", user_id, text_id, status="running")
        async with await store._connect() as conn:
            await conn.execute(
                "UPDATE distill_tasks SET updated_at = now() - interval '120 minutes' "
                "WHERE task_id = 'dtGhost'"
            )

        assert await store.count_running_distills(user_id) == 2
        assert await store.count_running_distills(user_id, window_minutes=30) == 1
        assert await store.count_running_distills(user_id, window_minutes=180) == 2

    async def test_card_id_awakening_roundtrip(self, store, text_id, user_id):
        task_id = f"dt_{uuid.uuid4().hex}"
        await store.create_distill_task(task_id, user_id, text_id, character="A",
                                      status="done", progress_pct=100, message="完成",
                                      card_id="cardX", awakening="你醒了？")
        got = await store.get_distill_task_unscoped(task_id)
        assert got["card_id"] == "cardX" and got["awakening"] == "你醒了？"

    async def test_mark_interrupted_flips_running(self, store, text_id, user_id):
        a, b, c = (f"dt_{uuid.uuid4().hex}" for _ in range(3))
        await store.create_distill_task(a, user_id, text_id, status="running")
        await store.create_distill_task(b, user_id, text_id, status="done")
        await store.create_distill_task(c, user_id, text_id, status="interrupted")
        assert await store.mark_interrupted_distills() == 1
        got = await store.get_distill_task_unscoped(a)
        assert got["status"] == "interrupted"
        assert "重启" in got["message"]
        assert await store.count_running_distills(user_id) == 0

    async def test_find_interrupted_distill(self, store, text_id):
        # 续跑发现：只认 interrupted；character 精确匹配（同文本两角色不互借）；user 隔离。
        await store.create_distill_task("dtI1", "u1", text_id, character="甲", status="interrupted")
        await store.create_distill_task("dtI2", "u1", text_id, character="乙", status="interrupted")
        await store.create_distill_task("dtI3", "u1", text_id, character="甲", status="done")
        await store.create_distill_task("dtI4", "u2", text_id, character="甲", status="interrupted")

        got = await store.find_interrupted_distill("u1", text_id, "甲")
        assert got is not None and got["task_id"] == "dtI1"
        assert got["chunk_size"] is None and got["text_fingerprint"] == ""
        assert await store.find_interrupted_distill("u1", text_id, "丙") is None
        assert (await store.find_interrupted_distill("u2", text_id, "甲"))["task_id"] == "dtI4"
        assert await store.find_interrupted_distill("u1", "txt_none", "甲") is None

    async def test_list_distill_tasks_capped_and_full_row_shape(self, store, text_id):
        # G：admin 运维视图的读路径。上限必守（无 cascade 的表会无限长）；
        # 返回完整行，内部列由 admin 层白名单挡。顺序同 SQLite 侧不在此断言。
        await store.create_distill_task("dtL1", "u1", text_id, character="甲", status="done", progress_pct=100)
        await store.create_distill_task("dtL2", "u2", text_id, character="乙", status="interrupted")
        await store.create_distill_task("dtL3", "u1", text_id, character="丙", status="running")

        rows = await store.list_distill_tasks()
        assert {r["task_id"] for r in rows} == {"dtL1", "dtL2", "dtL3"}
        assert "user_id" in rows[0] and "text_fingerprint" in rows[0]

        assert len(await store.list_distill_tasks(limit=2)) == 2
        assert await store.count_distill_tasks() == 3      # 全表数，不被 limit 污染

    async def test_cancel_by_text_id(self, store, text_id, user_id):
        x, y, z = (f"dt_{uuid.uuid4().hex}" for _ in range(3))
        await store.create_distill_task(x, user_id, text_id, status="running")
        await store.create_distill_task(y, user_id, text_id, status="interrupted")
        await store.create_distill_task(z, user_id, text_id, status="done")
        n = await store.cancel_distills_by_text_id(text_id)
        assert n == 2
        assert (await store.get_distill_task_unscoped(x))["status"] == "error"
        assert (await store.get_distill_task_unscoped(y))["status"] == "error"
        assert (await store.get_distill_task_unscoped(z))["status"] == "done"

    async def test_hard_delete_purges_distill_rows_and_chunks(self, store):
        # 改动 C：硬删文本 → 该 text 的 distill 两表清零，别的 text 不受影响。
        ta, tb = f"txt_{uuid.uuid4().hex}", f"txt_{uuid.uuid4().hex}"
        await store.save_text(ta, "a.txt", "A 内容" * 10)
        await store.save_text(tb, "b.txt", "B 内容" * 10)
        va, vb = f"dt_{uuid.uuid4().hex}", f"dt_{uuid.uuid4().hex}"
        await store.create_distill_task(va, "u1", ta, status="done")
        await store.save_distill_chunk(va, 0, json.dumps({"r": "零"}))
        await store.save_distill_chunk(va, 1, json.dumps({"r": "一"}))
        await store.create_distill_task(vb, "u1", tb, status="done")
        await store.save_distill_chunk(vb, 0, json.dumps({"r": "零"}))

        assert await store.hard_delete_text(ta) is True

        assert await store.get_distill_task_unscoped(va) is None
        assert await store.get_distill_chunks(va) == []
        assert (await store.get_distill_task_unscoped(vb))["task_id"] == vb
        assert len(await store.get_distill_chunks(vb)) == 1

    async def test_hard_delete_nonexistent_text_no_raise(self, store):
        assert await store.hard_delete_text(f"txt_{uuid.uuid4().hex}") is False

    async def test_soft_delete_keeps_distill_rows(self, store):
        # 软删不清理：垃圾桶可 restore，行随文本保留。
        tid = f"txt_{uuid.uuid4().hex}"
        await store.save_text(tid, "s.txt", "内容" * 10)
        v = f"dt_{uuid.uuid4().hex}"
        await store.create_distill_task(v, "u1", tid, status="done")
        assert await store.delete_text(tid) is True
        assert (await store.get_distill_task_unscoped(v))["task_id"] == v


# ── 交错竞态（6.2 / 6.4#3，PG 专属）：写入循环运行期间删文本 → 两表零行 ─────────
# 这是全仓唯一对"删除顺序"有判别力的用例。SQLite 写是库级序列化的：删除事务持写锁至
# 提交，并发分片写要么在其前提交（随即被一并删）、要么阻塞到提交后（父行已无、跳过），
# 窗口从根上不存在 —— 先父后子/先子后父都绿，测不出差别（见 test_distill_task_api.py
# 同名用例的注释）。PG 才有快照读 + 并发连接。
#
# 实测（PG 16，连续写入）：
#   无 FOR SHARE，单写者：先父后子 9/10 漏孤儿分片，先子后父 10/10 漏 —— 顺序不控制结果
#   有 FOR SHARE，单写者：先父后子 10/10 干净，先子后父仅 2/30 红 —— 窗口太窄，判不动
#   有 FOR SHARE，6 写者 × 20 轮（本用例）：先父后子 10/10 绿，先子后父 10/10 红
# 即：FOR SHARE 让"先父后子"从玄学变成可验证命题 —— 插入方取父行共享锁与删除方的
# 排他锁互斥；先删父行则插入方必阻塞、提交后跳过不写。锁与顺序是一体的，拆开不成立。
# 单写者命中窗口仅约 7%，故本用例用 K 写者 × R 轮把命中率抬到可判定水平。

@_pg
class TestDistillRaceStaleWriteAfterDelete:
    ROUNDS = 20
    WRITERS = 6

    async def test_writer_outliving_delete_leaves_no_rows(self, store, user_id, monkeypatch):
        """多轮并发写/删：每轮 K 个写线程与删除真并发，跑完断言一轮都没留下孤儿分片。

        单写者只有约 7% 的轮次能撞进窗口（实测 2/30），故用 K 写者 × R 轮把命中率抬到可
        判定的水平 —— 正确实现下**每轮都干净**（闭合是确定性的），多轮只是重复同一不变量；
        变异（先子后父）下窗口放宽，多轮里必然有一轮漏，直接红。红绿差在删除顺序：
          先父后子 —— 删除方先取父行排他锁；插入方 FOR SHARE 必阻塞，提交后父行已无 → 跳过
          先子后父 —— 删除方先删 chunk 不碰父行；插入方拿到共享锁落片，之后才删父 → 孤儿存活
        """
        from routers import distill as D
        import asyncio as _aio

        monkeypatch.setattr(D, "get_storage", lambda: store)

        async def _round() -> bool:
            """一轮：K 写者持续写进度+分片，删除落在写循环期间。返回是否留下孤儿分片。"""
            tid = f"txt_{uuid.uuid4().hex}"
            task_id = f"dt_{uuid.uuid4().hex}"
            await store.save_text(tid, "src.txt", "角色说的话" * 20, user_id=user_id)
            await store.create_distill_task(task_id, user_id, tid, "甲", status="running")

            stop = _aio.Event()
            ready = _aio.Event()
            written = {"n": 0}
            snap = {"status": "running", "progress_pct": 0, "message": "进行中",
                    "card_id": "", "awakening": ""}

            async def writer(w: int):
                i = w
                while not stop.is_set():
                    snap["progress_pct"] = i % 90
                    await D._persist_snap(task_id, snap)
                    await store.save_distill_chunk(task_id, i, f"分析{i}", fingerprint=f"fp{i}")
                    i += self.WRITERS
                    written["n"] += 1
                    if written["n"] >= 2 * self.WRITERS:
                        ready.set()
                    await _aio.sleep(0)

            async def deleter():
                await ready.wait()          # 等写者跑起来，删除必落在写循环期间
                await store.hard_delete_text(tid)
                stop.set()

            try:
                await _aio.gather(*[writer(w) for w in range(self.WRITERS)], deleter())
            finally:
                with D._task_lock:
                    D._tasks.pop(task_id, None)

            return await store.get_distill_chunks(task_id) != []

        orphan_rounds = 0
        for _ in range(self.ROUNDS):
            if await _round():
                orphan_rounds += 1

        assert orphan_rounds == 0, f"{orphan_rounds}/{self.ROUNDS} 轮留下孤儿分片"


# ── 锁竞争（FOR SHARE 的代价侧）：66 片分片写 vs 进度写 ────────────────────────
# FOR SHARE 新引入一条同区竞争：update_distill_task 取父行排他锁，save_distill_chunk
# 取同一父行共享锁 —— 同一次蒸馏内进度写与分片写会在父行上互相阻塞。要的是数，不是
# "单语句事务应该没事"的推断。

@_pg
class TestDistillLockContention:
    async def test_progress_write_not_starved_by_chunk_writes(self, store, user_id):
        import asyncio as _aio
        import time as _time

        N = 66
        tid = f"txt_{uuid.uuid4().hex}"
        task_id = f"dt_{uuid.uuid4().hex}"
        await store.save_text(tid, "src.txt", "角色说的话" * 20, user_id=user_id)
        await store.create_distill_task(task_id, user_id, tid, "甲", status="running")

        shared = {"chunks_done": 0, "first_sample_at_chunks": -1, "samples": 0}
        progress_rows: list = []
        errors: list = []
        chunk_done = _aio.Event()

        async def chunk_writer():
            try:
                for i in range(N):
                    await store.save_distill_chunk(task_id, i, f"r{i}", fingerprint=f"fp{i}")
                    shared["chunks_done"] = i + 1
                    await _aio.sleep(0)
            except Exception as exc:
                errors.append(("chunk", repr(exc)))
            finally:
                chunk_done.set()

        async def progress_writer():
            try:
                while not chunk_done.is_set():
                    n = await store.update_distill_task(
                        task_id, progress_pct=shared["samples"] % 90, message="进度")
                    progress_rows.append(n)
                    if shared["first_sample_at_chunks"] < 0:
                        shared["first_sample_at_chunks"] = shared["chunks_done"]
                    shared["samples"] += 1
                    await _aio.sleep(0)
            except Exception as exc:
                errors.append(("progress", repr(exc)))

        t0 = _time.monotonic()
        try:
            await _aio.wait_for(_aio.gather(chunk_writer(), progress_writer()), timeout=60)
        except _aio.TimeoutError:
            raise AssertionError("疑似死锁/饥饿：60s 未收敛")
        elapsed = _time.monotonic() - t0
        print(f"[lock-contention] 66 片 + 进度写 {shared['samples']} 次，用时 {elapsed:.2f}s，"
              f"首样本时已完成 {shared['first_sample_at_chunks']} 片")

        assert errors == [], errors
        assert len(await store.get_distill_chunks(task_id)) == N, "66 片必须全部落库"
        assert all(n == 1 for n in progress_rows), f"进度写不该落在不存在的行上：{progress_rows}"
        assert shared["samples"] >= 10, f"进度写被饿死：只成功 {shared['samples']} 次"
        assert 0 <= shared["first_sample_at_chunks"] < N, (
            "进度写必须与分片写交错，而非被排到 66 片全写完之后："
            f"首样本时已完成 {shared['first_sample_at_chunks']} 片")


# ── 生产后端（PG）的 schema 闭环（缺陷 23）────────────────────────────────────

class TestPgFreshSchemaClosure:
    """真 PG 库必须覆盖「声明了的表」与「代码引用的表」—— 缺陷 21 那条锁的镜像。

    缺陷 21 的锁（`tests/test_sqlite_fresh_schema.py::TestExemptionClosedLoop`）断言
    **SQLite 新库 ⊇ `migrations_pg/` 声明表**。那是**测试/本地后端**；本条是同一真源的反向、
    也是**生产后端**那一面：真 PG 库 ⊇（`migrations_pg/` 声明表 ∪ `postgres_store.py` 引用表）。

    **为什么需要它（缺陷 23）**：PG 迁移执行器是 `sorted(glob('*.sql'))`，目录即清单、
    没有豁免出口，所以「文件有没有被登记」这一层天然不会漏；真正无锁的是**症状层** ——
    没有任何东西验证「迁移文件里写了」等于「真库建出来了」。SQLite 侧有
    `TestExemptionClosedLoop` 兜着，PG 侧此前**一条都没有**，而 PG 才是生产。

    **与 `tests/test_schema_parity.py` 的分工**：那条是 text 层（正则扫 `.sql` 文本），
    断言两目录的表/列集合相等 —— 它抓「加了 SQLite 迁移忘了 PG」，但抓不到
    「文件写了、真库没有」（方言/语法问题、被静默跳过的语句）。本条是运行期层。

    **零豁免、不需要豁免名单**：真源取「表集合」而不是「文件编号」之后，上线当天就是绿的
    （实测 41 声明 / 41 建出 / 引用 41，双向差集全 0）。这与缺陷 21 的关键差别就在这里 ——
    那条锁若拿文件/编号当真源会带一堆豁免，而**豁免即永久放行**（§四 纪律）。将来真出现
    SQLite-only 的表也不在这里豁免：它以「不在 `postgres_store.py` 引用集里」被
    `test_every_referenced_table_is_created` 直接验掉，不靠理由文本。

    **判别力（变异，实测）**：删掉 `migrations_pg/012_remote_user_profiles.sql` 而
    `postgres_store.py` 仍在用它 → 只有第 2 条红；让一条迁移「声明了但不会建出来」→ 只有
    第 1 条红。两条各抓一类，互不代偿。

    **前提：库必须是「新鲜」的**。CI 每次跑给一个新的 postgres service 容器，成立。对着
    长期存在的 dev 库跑则会被掩盖 —— 实测：删掉 `012_remote_user_profiles.sql` 后若库里
    还留着该表，两条断言都绿（`declared` 缩了、`created` 没缩）。复现变异要先
    `DROP SCHEMA public CASCADE`。本锁判的是「文件与库一致」，不是「能不能从零建出」。

    **列级是另一种粒度，用的是另一种形状**：上面两条（以及 SQLite 侧的镜像
    `tests/test_sqlite_fresh_schema.py::TestExemptionClosedLoop`）都是「真库 ⊇ **文本声明**」。
    列级**不能**套这个形状 —— 全文理由写在下面 `_sqlite_columns` / `_pg_columns` 那段
    注释里，一句话版：`DROP TABLE` 0 处、`DROP COLUMN` 4 处，删列在本仓是既有事实，
    而声明列提取器看不见 DROP（PG 的声明式 DROP 不认，SQLite 的 Python 表重建更是
    `.sql` 里没有），两侧盲区互相抵消。故列级改比**另一侧真库**：
    `test_fresh_sqlite_and_fresh_pg_have_the_same_columns`。
    """

    def test_lock_has_teeth(self, tmp_path: Path):
        """负控：探针必须真看得见它要抓的两类事实，否则「0 处」只是它瞎了。"""
        d = tmp_path / "m"
        d.mkdir()
        (d / "001_x.sql").write_text(
            "-- CREATE TABLE ignored_comment\nCREATE TABLE real_one (id TEXT);\n", encoding="utf-8")
        assert _declared_tables(d) == {"real_one"}, "DDL 提取器看不见前一行注释里的假 CREATE"

        # 引用提取器：词表里的名字看得见，词表外的散文词被滤掉
        assert _referenced_tables("SELECT * FROM ghost WHERE x=1", {"ghost"}) == {"ghost"}
        assert _referenced_tables("SELECT * FROM the WHERE x=1", {"ghost"}) == set()
        # 且它不会把 SQLite-only 的名字悄悄吞掉（词表是并集）
        assert _referenced_tables("SELECT * FROM sqlite_only_t", {"sqlite_only_t"}) == {"sqlite_only_t"}

    @_pg
    async def test_every_declared_table_is_created(self):
        """`migrations_pg/` 写了的表，真 PG 库里必须存在。"""
        created = await _pg_tables()
        missing = sorted(_declared_tables(_PG_DIR) - created)
        assert not missing, (
            f"这些表 `migrations_pg/` 声明了、真 PG 库却没有：{missing}。"
            "「文件里写了」不等于「库里有」—— 迁移没被执行、或被静默跳过。"
            "生产后端缺表会在运行期炸成 `no such table`（缺陷 21/23 同一病灶）。")

    @_pg
    async def test_every_referenced_table_is_created(self):
        """`postgres_store.py` 引用的表，真 PG 库里必须存在 —— 直接锁 079 的症状形态。"""
        vocabulary = _declared_tables(_PG_DIR) | _declared_tables(_SQLITE_DIR)
        refs = _referenced_tables(_PG_STORE_SRC.read_text(encoding="utf-8"), vocabulary)
        created = await _pg_tables()
        missing = sorted(refs - created)
        assert not missing, (
            f"这些表 `postgres_store.py` 在 SQL 里引用了、真 PG 库却没有：{missing}。"
            "代码在用一张不存在的表 = 生产运行期 `no such table`（SQLite 侧就是缺陷 079 的"
            "现场：代码在用、新库没有）。要么补 `migrations_pg/` 迁移，要么改代码别用它。")
        assert len(refs) >= 30, (
            f"只从 postgres_store.py 提出 {len(refs)} 个表名 —— 提取器写歪了，断言会空转")

    async def test_column_probe_has_teeth(self, tmp_path: Path):
        """负控（§四「先验探针看得见」）：列级探针不能是靠「读空了」换来的 0 处。

        这一条**不需要 PG** —— 它只验 SQLite 侧的读取与差异函数本身有没有判别力。
        探针瞎了的 0 处与真无漂移的 0 处长得一模一样，这才是要挡的。
        """
        db_path = str(tmp_path / "teeth.db")
        await _build_fresh_sqlite(db_path)
        cols = _sqlite_columns(db_path)
        assert len(cols) >= 30, f"只读到 {len(cols)} 张表 —— 探针瞎了"
        assert "id" in cols.get("users", set()), (
            f"读得到 users 却读不到它的列：{sorted(cols.get('users', set()))}")

        # 差异函数两个方向都要点名表+列+侧
        drift = _column_drift({"t": {"a", "b"}}, {"t": {"a"}})
        assert any("t" in m and "b" in m for m in drift), drift
        drift = _column_drift({"t": {"a"}}, {"t": {"a", "b"}})
        assert any("t" in m and "b" in m for m in drift), drift
        # 表不存在于另一侧也要报
        assert _column_drift({"ghost": {"a"}}, {"t": {"a"}}), "只在单侧的表没被报出来"

    @_pg
    async def test_fresh_sqlite_and_fresh_pg_have_the_same_columns(self, tmp_path: Path):
        """两侧**真库**逐表列集合必须相等，双向 —— 列级闭环（缺陷 23）。

        真源是「另一侧真库」，没有文本标尺（理由见类 docstring 与 `_column_drift` 上方
        注释）。两侧都真跑迁移、真读 catalog：SQLite 读 `PRAGMA table_info`，
        PG 读 `information_schema.columns`。
        """
        db_path = str(tmp_path / "fresh.db")
        await _build_fresh_sqlite(db_path)
        sqlite_cols = _sqlite_columns(db_path)
        pg_cols = await _pg_columns()

        # 防空转：读空/提取器写歪时，下面的断言会恒真
        assert len(sqlite_cols) >= 30, f"只读到 {len(sqlite_cols)} 张表 —— 锁在空转"
        total = sum(len(c) for c in sqlite_cols.values())
        assert total >= 250, f"只读到 {total} 个列名 —— 锁在空转"

        drift = _column_drift(sqlite_cols, pg_cols)
        assert not drift, (
            "两个后端的真库列集合漂移了：\n"
            + "\n".join(f"  - {d}" for d in drift)
            + "\n\n列级漂移 = 某一侧运行期炸 `no column named X`。"
            "修法：把缺的列补到缺的那一侧的迁移里。"
            "**不要在本测试里开豁免** —— 列级的真源是「另一侧真库」，没有文本标尺，"
            "也就没有需要豁免的对象；加豁免清单等于把洞重新打开。")

    @_pg
    async def test_restarted_sqlite_matches_fresh_pg(self, tmp_path: Path):
        """**重启过的** SQLite 库也必须与 fresh PG 列集相等 —— 缺陷 26（缺陷 23 的连带发现）。

        上面那条只比 fresh ⟷ fresh，而**生产上跑的是「重启过的库」**。第二次 init 会让
        018 重新 `ADD COLUMN` api_key/base_url/model，而 users 删列块原先只认
        password_hash —— 那个列首次启动后再也不会回来，于是残留态永久驻留，同一个库的
        「全新」与「重启过」成了两个 schema。上面那条绿，是因为**它只比 fresh**：
        锁的输入状态本身也是判据的一部分，只守理想初态的锁会漏掉生产实际运行的那个状态。

        **变异（实测）**：把 `sqlite_store.py` 的触发条件改回 `if "password_hash" in
        all_cols` → 本条红（users 多 api_key/base_url/model），而 fresh 那条**仍绿**。
        """
        db_path = str(tmp_path / "restarted.db")
        await _build_restarted_sqlite(db_path)
        sqlite_cols = _sqlite_columns(db_path)
        pg_cols = await _pg_columns()

        assert len(sqlite_cols) >= 30, f"只读到 {len(sqlite_cols)} 张表 —— 锁在空转"

        drift = _column_drift(sqlite_cols, pg_cols)
        assert not drift, (
            "「重启过的」SQLite 库与 fresh PG 漂移了（缺陷 26 那一类）：\n"
            + "\n".join(f"  - {d}" for d in drift)
            + "\n\n这类漂移的特征是**第二次 init 才出现** —— 执行器按「列在不在」判断迁移"
            "是否已应用，于是「迁移加过、后来被有意删掉」的列每轮都会被加回来。"
            "修法：让删除的**触发条件**与不变量同口径（本仓是 `_USERS_LEGACY_COLUMNS`），"
            "别用一个「删一次就再也不出现」的列当哨兵。**不要在本测试里开豁免**。")

    @_pg
    async def test_unilateral_column_change_goes_red(self, tmp_path: Path):
        """变异：只在**一侧真库**改列 —— 单侧加一列 / 单侧删一列，两条都必须红且点名表+列+侧。

        变异打在**真库**上（对 throwaway SQLite 真跑 ALTER、真重读 catalog），不是改
        比较函数的入参：只有真库变了，才证明这条锁读的是「真库事实」而不是「传进去的字典」。
        打 SQLite 而不是 PG，是因为 PG 是共享库 —— 在它上面加删列会污染同一次会话里
        后面所有用例。
        """
        db_path = str(tmp_path / "mut.db")
        await _build_fresh_sqlite(db_path)
        pg_cols = await _pg_columns()
        assert _column_drift(_sqlite_columns(db_path), pg_cols) == [], (
            "静置态就不绿 —— 先修静置漂移，本条变异无从判定")

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN mutation_probe TEXT")
            conn.commit()
        finally:
            conn.close()
        added = _column_drift(_sqlite_columns(db_path), pg_cols)
        assert added, "SQLite 单侧加了一列，列级锁没红 —— 锁瞎了"
        assert any("users" in m and "mutation_probe" in m for m in added), (
            f"红了但没点名表+列：{added}")

        conn = sqlite3.connect(db_path)
        try:
            # mutation_probe 是变异自己加的，先撤掉，让下面那条只留「单侧缺列」一个红源
            conn.execute("ALTER TABLE users DROP COLUMN mutation_probe")
            conn.execute("ALTER TABLE users DROP COLUMN embedding_region")
            conn.commit()
        finally:
            conn.close()
        dropped = _column_drift(_sqlite_columns(db_path), pg_cols)
        assert dropped, "SQLite 单侧删了一列，列级锁没红 —— 锁瞎了"
        assert any("users" in m and "embedding_region" in m for m in dropped), (
            f"红了但没点名表+列：{dropped}")


# ── `*_owned` 的身份谓词在 PG 侧真的生效（运行期层）──────────────────────────
#
# `tests/test_storage_scope_lock.py` 的 SQL 事实锁是**静态层**（AST 读 SQL 文本）：它证明
# 「WHERE 里写了身份列谓词」，不证明「这条谓词在真库上真的筛掉了非属主行」。两层各管各的
# （AGENTS.md §四「形态锁与语义用例是两层防线」）—— SQLite 语义用例在本机跑，PG 语义用例
# 必须在真 PG 上跑（§四「SQLite 全绿不构成并发/后端命题的证据」）。本类补 PG 侧那一层。
#
# **变异（实测）**：删掉 `get_text_owned` 的 `AND user_id = $2`（连带 `$2` 实参，否则
# asyncpg 参数数不符）→ `test_get_text_owned_hides_non_owner_rows` 红（真库返回了非属主行）。
# 静态锁在同一次改动里也红 —— 但那是两个独立的红源：静态锁看**文本**，本类看**真库返回**。

@_pg
class TestPgOwnedIdentityIsolation:
    async def test_get_text_owned_hides_non_owner_rows(self, store, text_id, user_id):
        await store.save_text(text_id, "src.txt", "属主内容", user_id=user_id)
        other = f"usr_{uuid.uuid4().hex}"
        assert await store.get_text_owned(text_id, user_id) is not None
        assert await store.get_text_owned(text_id, other) is None, "非属主读到了他人 text"

    async def test_get_card_owned_hides_non_owner_rows(self, store, text_id, card_id, user_id):
        await store.save_text(text_id, "src.txt", "source", user_id=user_id)
        await store.save_card(card_id, text_id, "Alice",
                              json.dumps({"name": "Alice"}), user_id=user_id)
        other = f"usr_{uuid.uuid4().hex}"
        assert await store.get_card_owned(card_id, user_id) is not None
        assert await store.get_card_owned(card_id, other) is None, "非属主读到了他人 card"


# ── 元断言：skip 不得成为静默通道（缺陷 21 同型）──────────────────────────────
# 故意**不挂** `@_pg` —— 它是用来验「_pg 到底跳了没跳」的那把尺子，自己不能被同一把尺子量。

def test_pg_suite_is_not_silently_disabled_where_pg_is_required():
    """声明了需要 PG 的环境（CI）里，本模块必须真跑；靠 skip / SKIP_PG_TESTS 溜过去即红。

    `_pg` 在 PG 不可达时会把整批 PostgresStore 用例变成 skip。这在本地是对的（没 PG 就没得
    跑），但在 CI 就成了新的静默通道 —— 一条永远 skip 的锁和没有锁是一回事。CI 用
    `REQUIRE_PG_TESTS=1` 声明「我有 PG」，本断言就是那份声明的对账。
    """
    if not pg_required():
        pytest.skip(
            "本地未声明 REQUIRE_PG_TESTS=1 —— 本模块按「PG 不可达即显式 skip」处理；"
            "CI 声明了该变量，在那边本用例会真跑并核对"
        )
    assert os.getenv("SKIP_PG_TESTS") != "1", (
        "REQUIRE_PG_TESTS=1 与 SKIP_PG_TESTS=1 同时置位：skip 成了静默通道。"
        "要么去掉 SKIP_PG_TESTS，要么别声明 REQUIRE_PG_TESTS —— 不许两边都要。"
    )
    assert pg_reachable(), (
        "REQUIRE_PG_TESTS=1（CI 用它在 build.yml 里声明「保证有 PG」）但探针连不上 PG："
        "本模块 50 条用例会整体 skip 成静默通道。修 DATABASE_URL / postgres service，"
        "不要靠关掉这条断言来让它变绿。"
    )
