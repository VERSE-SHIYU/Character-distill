"""幂等键（`client_key`）：同一 key 重复落库只会有一行。

72 线最后一段的底座：补写队列（`core/message_outbox.py`）会在「写失败后重放」和
「前端点重试」两条路径上把同一条消息写两次。seam 是
`save_message(..., client_key=...)` / `save_group_message(..., client_key=...)`——
队列只依赖它把重放收敛成一行，不需要知道表结构。

`client_key` 为空时行为必须与现在完全一致（既有调用点一个都不改）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.sqlite_store import SQLiteStore


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / f"ck_{uuid.uuid4().hex}.db")


@pytest.fixture
def store(db_path):
    return SQLiteStore(db_path)


async def _make_session(store) -> str:
    tid = f"txt_{uuid.uuid4().hex}"
    cid = f"card_{uuid.uuid4().hex}"
    sid = f"ses_{uuid.uuid4().hex}"
    await store.save_text(tid, "src.txt", "source")
    await store.save_card(cid, tid, "张三", json.dumps({"name": "张三"}))
    await store.save_session(sid, cid, "user", "")
    return sid


class TestClientKeySQLite:
    async def test_same_key_returns_the_existing_row(self, store):
        """K1：同 key 写两次 → 同一行，第二次不再插入，且原样返回已有行。"""
        sid = await _make_session(store)
        first = await store.save_message(sid, "user", "第一条", "", client_key="k1")
        second = await store.save_message(sid, "user", "重放的内容", "", client_key="k1")

        assert second["id"] == first["id"]
        assert second["content"] == "第一条", "重复写应原样返回已有行，不是插入新内容"
        assert len(await store.get_messages(sid)) == 1

    async def test_same_key_group_message_returns_the_existing_id(self, store):
        gid = f"grp_{uuid.uuid4().hex}"
        first = await store.save_group_message(
            gid, "张三", "assistant", "第一条", client_key="k1")
        second = await store.save_group_message(
            gid, "张三", "assistant", "重放的内容", client_key="k1")

        assert second == first
        rows = await store.get_group_messages(gid)
        assert len(rows) == 1
        assert rows[0]["content"] == "第一条"

    async def test_different_keys_are_different_rows(self, store):
        """key 只在同一会话内去重，不同 key 各写一行。"""
        sid = await _make_session(store)
        a = await store.save_message(sid, "user", "a", "", client_key="k1")
        b = await store.save_message(sid, "user", "b", "", client_key="k2")

        assert a["id"] != b["id"]
        assert len(await store.get_messages(sid)) == 2

    async def test_same_key_in_another_session_is_a_new_row(self, store):
        """去重范围是 `(session_id, client_key)`，不是全表。"""
        s1, s2 = await _make_session(store), await _make_session(store)
        a = await store.save_message(s1, "user", "a", "", client_key="k1")
        b = await store.save_message(s2, "user", "a", "", client_key="k1")

        assert a["id"] != b["id"]

    async def test_without_key_every_write_is_a_new_row(self, store):
        """老调用点行为不变：没给 key 就不去重。"""
        sid = await _make_session(store)
        await store.save_message(sid, "user", "同样的内容", "")
        await store.save_message(sid, "user", "同样的内容", "")

        assert len(await store.get_messages(sid)) == 2

    # ── 按 key 查行 id：对账（`MessageOutbox.reconcile`）的存储底座 ────────────

    async def test_lookup_returns_only_the_keys_that_are_stored(self, store):
        """给一串 key，回一张 `{key: 行 id}`：查的命中的入库，没查的不许顺手带出来。

        `k9` 那条是判别力所在 —— 少了它，「不过滤 key、把本会话的行全返回」也能过。
        """
        sid = await _make_session(store)
        first = await store.save_message(sid, "user", "第一条", "", client_key="k1")
        second = await store.save_message(sid, "assistant", "第二条", "", client_key="k2")
        await store.save_message(sid, "user", "没被问到的", "", client_key="k9")

        found = await store.find_message_ids_by_client_keys(sid, ["k1", "k2", "k3"])

        assert found == {"k1": first["id"], "k2": second["id"]}

    async def test_lookup_is_scoped_to_the_session(self, store):
        """查询按 `session_id` 约束：别的会话用了同一个 key，不算这条会话的命中。"""
        mine, other = await _make_session(store), await _make_session(store)
        elsewhere = await store.save_message(other, "user", "别处的", "", client_key="k1")

        assert await store.find_message_ids_by_client_keys(mine, ["k1"]) == {}
        assert await store.find_message_ids_by_client_keys(
            other, ["k1"]) == {"k1": elsewhere["id"]}

    async def test_group_lookup_is_scoped_to_the_group(self, store):
        g1, g2 = f"grp_{uuid.uuid4().hex}", f"grp_{uuid.uuid4().hex}"
        first = await store.save_group_message(g1, "张三", "assistant", "第一条", client_key="k1")
        second = await store.save_group_message(g1, "李四", "user", "第二条", client_key="k2")
        await store.save_group_message(g1, "张三", "assistant", "没被问到的", client_key="k9")
        await store.save_group_message(g2, "张三", "assistant", "别处的", client_key="k1")

        assert await store.find_group_message_ids_by_client_keys(g1, ["k1", "k2", "k3"]) == {
            "k1": first, "k2": second,
        }

    async def test_empty_keys_asks_nothing_and_answers_nothing(self, store):
        """空列表直接 `{}` —— 不拼 `IN ()`（那是语法错误，不是空结果）。"""
        sid = await _make_session(store)
        await store.save_message(sid, "user", "第一条", "", client_key="k1")

        assert await store.find_message_ids_by_client_keys(sid, []) == {}
        assert await store.find_group_message_ids_by_client_keys("grp_x", []) == {}

    async def test_unique_index_backstops_duplicate_key(self, store, db_path):
        """K3：绕过查重直接插同 key，唯一索引必须拦下（这条是兜底，不是主路径）。"""
        sid = await _make_session(store)

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, client_key) VALUES (?, ?, ?, ?)",
                (sid, "user", "第一条", "k1"),
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, client_key) VALUES (?, ?, ?, ?)",
                    (sid, "user", "重放的内容", "k1"),
                )

    async def test_unique_index_ignores_null_keys(self, store, db_path):
        """partial index：client_key 为 NULL 的行不受约束（老数据、老调用点照常）。"""
        sid = await _make_session(store)

        with sqlite3.connect(db_path) as conn:
            for _ in range(2):
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, client_key) VALUES (?, ?, ?, NULL)",
                    (sid, "user", "同样的内容"),
                )
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)).fetchone()[0]

        assert count == 2
