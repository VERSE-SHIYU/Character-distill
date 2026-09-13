"""Tests for PostgresStore CRUD using a real PostgreSQL database.

These tests require a running PostgreSQL instance.  Set these env vars:

  STORAGE_BACKEND=postgres
  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/charsim_test

Skip: export SKIP_PG_TESTS=1 to skip all PostgresStore tests.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.postgres_store import PostgresStore


pytestmark = pytest.mark.skipif(
    os.getenv("SKIP_PG_TESTS") == "1",
    reason="SKIP_PG_TESTS=1 set — skipping PostgresStore tests",
)

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

    async def test_every_declared_table_is_created(self):
        """`migrations_pg/` 写了的表，真 PG 库里必须存在。"""
        created = await _pg_tables()
        missing = sorted(_declared_tables(_PG_DIR) - created)
        assert not missing, (
            f"这些表 `migrations_pg/` 声明了、真 PG 库却没有：{missing}。"
            "「文件里写了」不等于「库里有」—— 迁移没被执行、或被静默跳过。"
            "生产后端缺表会在运行期炸成 `no such table`（缺陷 21/23 同一病灶）。")

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
