"""检索来源落库（Evidence 线 commit 4）—— messages.evidence。

**要锁的三件事，一件一个类**：

1. 形状：落库的是 `EvidenceSnapshot`（截断预览 + 显式 truncated + meta 里保留引用），
   **不是** `EvidenceItem` 的别称。`text` 承诺不了原文，就不许长得像原文。
2. 读写：新消息往返一致；**老消息（列是 NULL / INSERT 里压根没这一列）读回来是 None**，
   不是 `[]`、也不许抛。这两条要分开测 —— 只测新消息的话，「老消息路径」等于没测
   （本仓 §四：跑绿的是哪条路径，要写清楚）。
3. 接线：**两个** char 落库点都必须真的把 `engine.last_traces` 编码后传进 `save_message` ——
   `_do_chat`（非流式）与 `_do_chat_stream`（SSE，主路径）。删掉任一处 → 断言变红
   （否则就是「接线没被覆盖」）。两个点各一条，因为「覆盖了一个」不等于「覆盖了落库」。

   为什么只锁这两处：`save_message` 的生产调用点共 8 处，char 落库点 4 处 ——
   另两处（`distill.py` 卡面开场白 / `history.py` 重逢问候）生成于检索之前/之外，
   不传 evidence 落 NULL 才是对的，由 `test_default_call_sites_are_unchanged` 锁住这条机制。
4. 展示层的输入面（commit 5）：SSE 必须在 **token 流之前**发 evidence 帧（顺序是产品
   判断，不是实现细节），四态原样过帧；重连/重逢接口（`POST /resume`）与刷新接口
   （`GET /history/{sid}`）两条读路径都得把证据带回来 —— 只兑现一条等于没兑现。

断言里凡涉及「证据在不在」，都走**接口读回来**（`GET /api/history/{sid}`）而不只看 store ——
用户看到的就是那一条路。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.chat as chat_router_mod
from conftest import PG_ENV
from storage.postgres_store import PostgresStore

from core.schema import (
    EVIDENCE_SNAPSHOT_CHARS,
    SourceTrace,
    evidence_to_json,
    parse_evidence,
    scene_evidence,
    web_evidence,
)
from deps import get_sessions, get_storage
from routers.auth import get_current_user
from routers.history import router as history_router
from storage.sqlite_store import SQLiteStore

from evidence_fakes import make_trace


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / f"ev_{uuid.uuid4().hex}.db")


@pytest.fixture
def store(db_path):
    return SQLiteStore(db_path)


@pytest.fixture
def user_id():
    return f"user_{uuid.uuid4().hex[:8]}"


def _new_session(store, user_id) -> str:
    text_id = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(text_id, "src.txt", "content", user_id=user_id))
    card_id = f"card_{uuid.uuid4().hex}"
    _run_async(store.save_card(card_id, text_id, "张三", '{"name": "张三"}', user_id=user_id))
    sid = f"ses_{uuid.uuid4().hex}"
    _run_async(store.save_session(sid, card_id, "user", "", user_id=user_id))
    return sid


@pytest.fixture
def sessions():
    """进程内的会话表。**必须是同一个 dict 对象**：resume 会把重建出来的引擎塞进它，
    每次调用给一个新 dict 的话，resume 那条路永远重建不出引擎（用例恒 500）。"""
    return {}


@pytest.fixture
def client(store, user_id, sessions):
    app = FastAPI()
    app.include_router(history_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_sessions] = lambda: sessions
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "username": "testuser", "is_admin": False,
    }
    return TestClient(app)


# ── 1. 形状 ──────────────────────────────────────────────────────────

class TestSnapshotShape:
    def test_snapshot_key_set_is_locked(self):
        """快照的键集就是契约本身 —— 将来加字段必须是有意识的（改这条断言）。"""
        got = json.loads(evidence_to_json([make_trace("scene", "hit")]))
        assert set(got[0]) == {"source", "status", "items"}
        assert set(got[0]["items"][0]) == {"text", "truncated", "score", "meta"}

    def test_reference_survives(self):
        """meta 里的**引用**（scene 的 chunk_id / web 的 url）是将来按需回查原文的句柄。"""
        scene = SourceTrace(source="scene", status="hit", items=[
            scene_evidence(text="屋顶上的旧事", final=0.7, semantic=0.8,
                           emotion_affinity=0.4, chunk_id="scene_7", chapter="第三章"),
        ])
        web = SourceTrace(source="web", status="hit", items=[
            web_evidence(text="莲花坞", url="https://example.org/l", source="示例百科",
                         fetched_at="2026-01-01T00:00:00+00:00"),
        ])
        got = json.loads(evidence_to_json([scene, web]))
        assert got[0]["items"][0]["meta"]["chunk_id"] == "scene_7"
        assert got[0]["items"][0]["meta"]["chapter"] == "第三章"
        assert got[1]["items"][0]["meta"]["url"] == "https://example.org/l"

    def test_long_text_is_truncated_and_says_so(self):
        """全文不落库；被裁这件事必须显式记 —— 静默截断 = 下游把预览当原文。"""
        long_text = "甲" * (EVIDENCE_SNAPSHOT_CHARS + 50)
        got = json.loads(evidence_to_json([make_trace("scene", "hit", text=long_text)]))
        item = got[0]["items"][0]
        assert len(item["text"]) == EVIDENCE_SNAPSHOT_CHARS
        assert item["truncated"] is True
        assert long_text not in json.dumps(got), "全文进了库，截断没生效"

    def test_exactly_at_the_limit_is_not_truncated(self):
        """边界：正好 N 字不算被裁 —— 否则 truncated 恒真，标记失去信息量。"""
        got = json.loads(evidence_to_json(
            [make_trace("scene", "hit", text="乙" * EVIDENCE_SNAPSHOT_CHARS)]))
        assert got[0]["items"][0]["truncated"] is False

    def test_no_traces_stores_nothing_not_an_empty_list(self):
        """一种「无证据」只留一种表示：空 → None（列 NULL），不写 '[]'。

        同时守住反面：**真发生过的调用**（trace 存在、items 为空 —— 真无/失败/超时）
        不许被当成「没证据」丢掉，那是 commit 3「空与失败不许被吞成没检索过」的落库延伸。
        """
        assert evidence_to_json([]) is None
        assert evidence_to_json([make_trace("scene", "empty")]) is not None


# ── 2. 读写 ──────────────────────────────────────────────────────────

class TestRoundTripThroughTheStore:
    def test_new_message_evidence_survives(self, store, user_id):
        sid = _new_session(store, user_id)
        traces = [make_trace("scene", "hit", text="屋顶上的旧事，风很凉。"),
                  make_trace("memory", "empty")]
        raw = evidence_to_json(traces)
        _run_async(store.save_message(sid, "char", "回复", "rag", evidence=raw))

        rows = _run_async(store.get_messages(sid))
        char_rows = [m for m in rows if m["role"] == "char"]
        assert len(char_rows) == 1
        assert parse_evidence(char_rows[0]["evidence"]) == json.loads(raw)
        assert [t["status"] for t in parse_evidence(char_rows[0]["evidence"])] == ["hit", "empty"]

    def test_old_message_without_the_column_reads_as_none(self, store, user_id, db_path):
        """**老消息路径的专门断言**：列是 NULL（迁移前写入的行就是这个形态）。

        造法与加列前的代码逐字一致 —— INSERT 里**压根没有** evidence 这一列，
        而不是「写了新参数但传 None」。两者对存储是同一种结局，但对测试不是：
        前者证明「旧版写入的行照样读得出来」，那才是要保的兼容性。
        """
        sid = _new_session(store, user_id)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, rag_context) VALUES (?, ?, ?, ?)",
                (sid, "char", "旧回复", "旧 rag"),
            )
            conn.commit()
        finally:
            conn.close()

        rows = _run_async(store.get_messages(sid))
        assert len(rows) == 1
        assert rows[0]["content"] == "旧回复"
        assert rows[0]["evidence"] is None, "老消息读成了非 None —— 造出了库里没有的证据"
        assert parse_evidence(rows[0]["evidence"]) is None

    def test_default_call_sites_are_unchanged(self, store, user_id):
        """开闭：不传 evidence 的老调用点（用户消息 / 摘要 / 群聊）一字不改照样工作。"""
        sid = _new_session(store, user_id)
        rec = _run_async(store.save_message(sid, "user", "你好", ""))
        assert rec["id"] and rec["role"] == "user"
        assert rec["evidence"] is None

    def test_second_init_keeps_the_column_and_prints_no_failure(self, db_path, capsys):
        """迁移双跑（新库 + 已建库）：第二次 init 不得打失败行，列仍在。"""
        out1 = _init(db_path, capsys)
        assert "evidence" in _message_columns(db_path), "新库没有 evidence 列 —— 迁移没生效"
        out2 = _init(db_path, capsys)
        assert "failed" not in out1, f"首次 init 打了失败行:\n{out1}"
        assert "failed" not in out2, f"第二次 init 打了失败行:\n{out2}"
        assert "evidence" in _message_columns(db_path)


def _init(db_path: str, capsys) -> str:
    _run_async(SQLiteStore(db_path)._ensure_initialized())
    return capsys.readouterr().out


def _message_columns(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    finally:
        conn.close()


# ── 2b. 接口层：刷新后还在不在 ────────────────────────────────────────

class TestRefreshKeepsEvidence:
    def test_evidence_is_still_there_after_a_reload(self, client, store, user_id):
        """**刷新后证据仍在** —— 走接口读回来（前端刷新时走的正是这条）。"""
        sid = _new_session(store, user_id)
        expected = json.loads(evidence_to_json([make_trace("scene", "hit", text="屋顶上的旧事。")]))
        _run_async(store.save_message(
            sid, "char", "回复", "rag",
            evidence=evidence_to_json([make_trace("scene", "hit", text="屋顶上的旧事。")]),
        ))

        resp = client.get(f"/api/history/{sid}")
        assert resp.status_code == 200
        msgs = [m for m in resp.json()["messages"] if m["role"] == "char"]
        assert msgs and msgs[0]["evidence"] == expected

    def test_old_message_reads_as_null_not_empty_list(self, client, store, user_id, db_path):
        """**老消息走接口**：evidence 是 null（无数据显式信号），不是 []，更不许 500。"""
        sid = _new_session(store, user_id)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, rag_context) VALUES (?, ?, ?, ?)",
                (sid, "char", "旧回复", "旧 rag"),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/api/history/{sid}")
        assert resp.status_code == 200
        msgs = [m for m in resp.json()["messages"] if m["role"] == "char"]
        assert msgs and msgs[0]["evidence"] is None

    def test_a_corrupt_row_does_not_break_the_history(self, client, store, user_id, db_path):
        """坏行不该毁整段会话：接口仍 200，该条按「无证据」返回，且**日志点名解析失败**。"""
        sid = _new_session(store, user_id)
        _run_async(store.save_message(sid, "char", "回复", "rag"))
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE messages SET evidence = ? WHERE role = 'char'", ("{不是 JSON",))
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/api/history/{sid}")
        assert resp.status_code == 200
        msgs = [m for m in resp.json()["messages"] if m["role"] == "char"]
        assert msgs and msgs[0]["evidence"] is None


def test_parse_failure_is_visible_not_silent(capsys):
    """解析失败与「本来就没有」可辨 —— 前者打日志，后者不打。"""
    assert parse_evidence(None) is None
    assert capsys.readouterr().out == ""
    assert parse_evidence("{不是 JSON") is None
    assert "解析失败" in capsys.readouterr().out
    assert parse_evidence('{"a": 1}') is None  # 是合法 JSON 但不是数组
    assert "不是 JSON 数组" in capsys.readouterr().out


# ── 2c. PG 侧：已建库重跑迁移（缺陷 21/23/26 的教训）──────────────────

_pg = PG_ENV.skipif(
    "messages.evidence 的 PG 落库",
    disabled_label="messages.evidence 的 PG 落库用例",
)


def _dsn() -> str:
    return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/charsim_test")


async def _pg_session(store) -> str:
    """PG 上造一条最小会话链（text → card → session），返回 sid。"""
    uid = f"u_{uuid.uuid4().hex[:8]}"
    txt = f"t_{uuid.uuid4().hex[:8]}"
    card = f"c_{uuid.uuid4().hex[:8]}"
    sid = f"s_{uuid.uuid4().hex[:8]}"
    await store.save_text(txt, "a.txt", "x", user_id=uid)
    await store.save_card(card, txt, "张三", '{"name":"张三"}', user_id=uid)
    await store.save_session(sid, card, "user", "", user_id=uid)
    return sid


@_pg
class TestPgEvidenceColumn:
    """生产后端那一面：列真在（且是 jsonb）、往返真通、**已建库重跑迁移**真幂等。

    「已建库」不是假想状态：``PostgresStore._ensure_initialized`` 每次构造都把
    ``migrations_pg/*.sql`` 全部重放，所以第二个 store 走的正是生产重启时走的那条路。
    019 写成 `ADD COLUMN IF NOT EXISTS`（而非仓库既有的「改 001_init」惯例）就是为了这一刻 ——
    正是缺陷 21/23 的病灶：改一条已应用的迁移，在新库上看着生效，在老库上是空操作。

    不断言 asyncpg 的 jsonb 返回类型：``parse_evidence`` 的入参契约是 ``Any``（sqlite 的
    TEXT 给 str、PG 的 jsonb 给什么就接什么），这里锁的是**行为**，不是某个驱动的编解码细节。
    """

    def test_column_is_jsonb_and_values_round_trip(self):
        async def _body():
            store = PostgresStore(_dsn())
            await store._ensure_initialized()
            try:
                async with await store._connect() as conn:
                    col = await conn.fetchval(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name='messages' AND column_name='evidence'")
                assert col == "jsonb", f"messages.evidence 的 PG 类型是 {col!r}，期望 jsonb"

                sid = await _pg_session(store)
                raw = evidence_to_json([make_trace("scene", "hit", text="屋顶上的旧事。")])
                await store.save_message(sid, "char", "回复", "rag", evidence=raw)
                rows = await store.get_messages(sid)
                back = [m for m in rows if m["role"] == "char"][0]["evidence"]
                assert parse_evidence(back) == json.loads(raw)
            finally:
                await store.close()
        _run_async(_body())

    def test_second_init_keeps_the_column_and_the_rows(self):
        """第二个 store 重放全部迁移：列不丢、带证据的行不丢、无证据的行仍是 NULL。"""
        async def _body():
            dsn = _dsn()
            store = PostgresStore(dsn)
            await store._ensure_initialized()
            try:
                sid = await _pg_session(store)
                raw = evidence_to_json([make_trace("scene", "hit", text="屋顶上的旧事。")])
                await store.save_message(sid, "char", "回复", "rag", evidence=raw)
                await store.save_message(sid, "user", "你好", "")  # 没发生检索 → NULL
            finally:
                await store.close()

            store2 = PostgresStore(dsn)  # 全新对象 → 把 migrations_pg/*.sql 全部重放
            await store2._ensure_initialized()
            try:
                async with await store2._connect() as conn:
                    col = await conn.fetchval(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name='messages' AND column_name='evidence'")
                    n = await conn.fetchval(
                        "SELECT count(*) FROM messages WHERE session_id=$1", sid)
                assert col == "jsonb", "二次 init 后 evidence 列没了"
                assert n == 2, f"二次 init 后数据丢了：{n} != 2"

                rows = await store2.get_messages(sid)
                assert parse_evidence([m for m in rows if m["role"] == "char"][0]["evidence"]) \
                    == json.loads(raw)
                assert [m for m in rows if m["role"] == "user"][0]["evidence"] is None, \
                    "无证据行读出了非 None —— 造出了库里没有的证据"
            finally:
                await store2.close()
        _run_async(_body())


# ── 3. 接线：证据真的从引擎走到了库里 ────────────────────────────────

class _Engine:
    """够 `_do_chat` 与 `_do_chat_stream` 两条路径跑完的**同一个**最小引擎替身。

    一条定义服务两条路径 —— 真引擎接口一变，两个点的替身一起滞后，不会出现
    「流式那份替身还是老的」这种各管各的漂移。``last_traces`` 就是被验的那个字段。
    """

    def __init__(self, traces, stream_pieces=("回", "复")):
        self.history = []
        self.last_traces = traces
        self.last_summary = ""
        self._last_rag_context = "rag-ctx"
        self._ctx_engine = type("Ctx", (), {"web_search_enabled": False})()
        self.affinity_enabled = True
        self.agent_mode = False
        self._stream_pieces = list(stream_pieces)
        self.post_processed: list[tuple[str, str]] = []

    def chat(self, *a, **kw):
        return "回复"

    def chat_stream(self, *a, **kw):
        # 真引擎的 chat_stream 是同步迭代器，调用方用 next() 逐片取（_next_piece）。
        return iter(self._stream_pieces)

    def post_stream_process(self, user_message, full_reply):
        # 排在落库之后的收尾钩子；起替身是为了让生成器能真的跑到底（否则打一行
        # 非致命错误日志，用例就成了「排干到一半」）。
        self.post_processed.append((user_message, full_reply))

    def _should_retract(self, reply):
        return False


class _RecordingStorage:
    def __init__(self):
        self.saved: list[dict] = []

    async def save_message(self, session_id, role, content, rag_context, *a, **kw):
        self.saved.append({"role": role, "content": content, "evidence": kw.get("evidence")})
        return {"id": len(self.saved), "role": role, "created_at": ""}

    async def get_messages(self, session_id):
        return []


def _wire(monkeypatch, engine, storage):
    session = {"engine": engine, "lock": asyncio.Lock(), "user_id": "u1"}

    async def _fake_ensure(session_id, storage_, sessions, user_id=""):
        return session

    monkeypatch.setattr(chat_router_mod, "_ensure_session", _fake_ensure)
    return session


def test_do_chat_persists_engine_traces(monkeypatch):
    """`_do_chat` 把 engine.last_traces 编码后随 char 消息落库（删掉那行即红）。"""
    engine = _Engine([make_trace("scene", "hit", text="屋顶上的旧事。")])
    storage = _RecordingStorage()
    _wire(monkeypatch, engine, storage)
    asyncio.run(chat_router_mod._do_chat("s1", "hi", storage=storage, sessions={}, user_id="u1"))

    char = [s for s in storage.saved if s["role"] == "char"]
    assert len(char) == 1
    snap = parse_evidence(char[0]["evidence"])
    assert snap is not None, "char 消息没带证据 —— 接线断了"
    assert snap[0]["source"] == "scene"
    assert snap[0]["items"][0]["text"] == "屋顶上的旧事。"


def test_do_chat_without_retrieval_stores_null(monkeypatch):
    """没发生检索时落 NULL，不落 '[]' —— 一种「无证据」只有一种表示。"""
    storage = _RecordingStorage()
    _wire(monkeypatch, _Engine([]), storage)
    asyncio.run(chat_router_mod._do_chat("s1", "hi", storage=storage, sessions={}, user_id="u1"))

    char = [s for s in storage.saved if s["role"] == "char"]
    assert char and char[0]["evidence"] is None


async def _drive_stream(storage, **kwargs) -> list[dict]:
    """调用 `_do_chat_stream` 并把 SSE 排干 —— 落库发生在生成器体内，不排干就看不到。

    调用与排干在**同一个事件循环**里：`_run_async` 每次新建 loop，跨 loop 用
    `asyncio.Lock` / `to_thread` 是自找麻烦。排干用的是 `StreamingResponse.body_iterator`
    （就是 `T.trace_sse_async(_event_generator(), ...)`，OTel 关时零开销透传）。

    返回按**到达顺序**解析出的事件 payload —— 「evidence 帧排在 token 流之前」这条
    只能靠顺序断言，故帧不能只被丢掉。
    """
    resp = await chat_router_mod._do_chat_stream(
        "s1", "hi", storage=storage, sessions={}, user_id="u1", **kwargs,
    )
    frames: list[dict] = []
    async for chunk in resp.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("data: "):
                frames.append(json.loads(line[6:]))
    return frames


def test_do_chat_stream_persists_engine_traces(monkeypatch):
    """`_do_chat_stream` 也把 engine.last_traces 编码后落库 —— 流式是主路径，不能只覆盖非流式。"""
    engine = _Engine([make_trace("scene", "hit", text="屋顶上的旧事。")])
    storage = _RecordingStorage()
    _wire(monkeypatch, engine, storage)
    _run_async(_drive_stream(storage))

    char = [s for s in storage.saved if s["role"] == "char"]
    assert len(char) == 1
    snap = parse_evidence(char[0]["evidence"])
    assert snap is not None, "流式 char 消息没带证据 —— 接线断了"
    assert snap[0]["source"] == "scene"
    assert snap[0]["items"][0]["text"] == "屋顶上的旧事。"
    # 排干到底的凭据：收尾钩子在落库之后，它被调到 = 生成器真跑完了
    assert engine.post_processed == [("hi", "回复")]


def test_do_chat_stream_without_retrieval_stores_null(monkeypatch):
    """流式路径没发生检索时同样落 NULL，不落 '[]' —— 两条路径同一种「无证据」表示。"""
    storage = _RecordingStorage()
    _wire(monkeypatch, _Engine([]), storage)
    _run_async(_drive_stream(storage))

    char = [s for s in storage.saved if s["role"] == "char"]
    assert char and char[0]["evidence"] is None


# ── 4. 展示层的输入面：SSE evidence 帧 ────────────────────────────────

class TestEvidenceFrame:
    """帧的位置与内容。

    位置是**产品判断**不是实现细节：证据先渲染、文字后流入，用户看到的才是「先查了
    什么，再据此说话」。把那条帧挪到 token 之后 → `test_..._before_the_first_token` 变红。
    内容只做一件事：把三源 × 四态**原样**交出去 —— 后端压成一态，前端就再也分不出来。
    """

    def test_evidence_frame_arrives_before_the_first_token(self, monkeypatch):
        engine = _Engine([make_trace("scene", "hit", text="屋顶上的旧事。")])
        storage = _RecordingStorage()
        _wire(monkeypatch, engine, storage)
        frames = _run_async(_drive_stream(storage))

        ev = [i for i, f in enumerate(frames) if f.get("type") == "evidence"]
        tk = [i for i, f in enumerate(frames) if "token" in f]
        assert ev, "没有 evidence 帧 —— 前端拿不到检索来源"
        assert tk, "没有 token 帧 —— 本用例的前提不成立"
        assert ev[0] < tk[0], "evidence 帧排在 token 之后了 —— 先证据后文字的关系倒置"
        assert frames[ev[0]]["evidence"][0]["source"] == "scene"
        assert frames[ev[0]]["evidence"][0]["items"][0]["text"] == "屋顶上的旧事。"

    def test_the_frame_carries_all_four_states_intact(self, monkeypatch):
        """四态原样过帧 —— 后端把 empty/failed/timeout 合并，前端就没得可辨了。"""
        engine = _Engine([
            make_trace("scene", "hit", text="屋顶上的旧事。"),
            make_trace("memory", "empty"),
            make_trace("web", "failed"),
            make_trace("web", "timeout"),
        ])
        storage = _RecordingStorage()
        _wire(monkeypatch, engine, storage)
        frames = _run_async(_drive_stream(storage))

        ev = [f for f in frames if f.get("type") == "evidence"][0]["evidence"]
        assert [t["status"] for t in ev] == ["hit", "empty", "failed", "timeout"]
        assert ev[0]["items"], "hit 态的 items 不该是空的"
        assert all(t["items"] == [] for t in ev[1:]), "未命中的三态不该凭空有 items"

    def test_frame_without_retrieval_is_null_not_empty_list(self, monkeypatch):
        """非 agent 模式 / 本轮压根没检索：帧里是 null —— 一种「无证据」只有一种表示。

        前端据此不渲染整条 rail（见 EvidenceRail 的 null 用例），且不许因为收到 null 报错。
        """
        storage = _RecordingStorage()
        _wire(monkeypatch, _Engine([]), storage)
        frames = _run_async(_drive_stream(storage))

        ev = [f for f in frames if f.get("type") == "evidence"]
        assert ev, "没检索也要发帧 —— 前端否则只能靠超时猜"
        assert ev[0]["evidence"] is None, "空检索发成了 []，凭空造出一次「查过但没结果」"

    def test_legacy_non_stream_path_carries_no_evidence_key(self, monkeypatch):
        """legacy 非流式路径（`stream:false`）：响应里没有 evidence 这个键。

        证据的家只有一处（SSE 帧），非流式那条路不产半份形状 —— 前端在它上面不该期待
        任何证据载荷，也就不会拿一个没约定的字段去渲染。
        """
        storage = _RecordingStorage()
        _wire(monkeypatch, _Engine([make_trace("scene", "hit")]), storage)
        result = _run_async(chat_router_mod._do_chat(
            "s1", "hi", storage=storage, sessions={}, user_id="u1"))
        assert "evidence" not in result


# ── 5. 重连/重逢：证据要跟着历史一起回来 ──────────────────────────────

class _StubEngine:
    """够 `resume_session` 走完的最小引擎替身（只补它真会碰到的字段/方法）。"""

    def __init__(self):
        self.history: list[dict] = []
        self.last_summary = ""
        self.user_role = ""
        self._user_tz = "UTC"
        self._storage = None

    def load_affinity(self, data, initialized=False):
        pass

    def generate_reunion_greeting(self, *_a, **_kw):
        return ""


class TestResumeCarriesEvidence:
    """服务重启 / 换进程后重连：`POST /resume` 走的是与 `GET /{sid}` **另一条**读路径。

    只修 GET 那条，「刷新后仍能看到检索来源」就只兑现了一半 —— 用户日常重进会话走的
    恰恰是 resume（还会顺手生成一句重逢问候，那句在本轮检索之外，如实 None）。
    """

    def test_reunion_after_restart_still_shows_the_sources(
        self, client, store, user_id, sessions, monkeypatch,
    ):
        sid = _new_session(store, user_id)
        raw = evidence_to_json([make_trace("scene", "hit", text="屋顶上的旧事。")])
        _run_async(store.save_message(sid, "char", "回复", "rag", evidence=raw))
        _run_async(store.save_message(sid, "user", "你好", ""))

        engine = _StubEngine()

        class _StubIndexing:
            def get_rag_for_session(self, *_a, **_kw):
                return None

        class _StubTextManager:
            _indexing_service = _StubIndexing()

            def _create_session(self, *_a, **_kw):
                sessions["ses_rebuilt"] = {"engine": engine}
                return "ses_rebuilt"

        async def _fake_user_llm(*_a, **_kw):
            return object()

        import deps
        monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
        monkeypatch.setattr(deps, "get_text_manager", lambda *_a, **_kw: _StubTextManager())

        resp = client.post(f"/api/history/{sid}/resume", json={})
        assert resp.status_code == 200, resp.text

        char_msgs = [m for m in resp.json()["messages"] if m["role"] == "char"]
        assert char_msgs, "接回来的历史里没有 char 消息"
        assert char_msgs[0]["evidence"] == json.loads(raw), \
            "resume 没把证据带回来 —— 重连后检索来源消失"
        assert [m for m in resp.json()["messages"] if m["role"] == "user"][0]["evidence"] is None

    def test_reunion_greeting_carries_no_evidence(
        self, client, store, user_id, sessions, monkeypatch,
    ):
        """重逢问候生成于本轮检索之外 → 它那条消息的 evidence 如实 None（不是缺键）。"""
        sid = _new_session(store, user_id)
        engine = _StubEngine()
        engine.generate_reunion_greeting = lambda *_a, **_kw: "好久不见。"

        class _StubIndexing:
            def get_rag_for_session(self, *_a, **_kw):
                return None

        class _StubTextManager:
            _indexing_service = _StubIndexing()

            def _create_session(self, *_a, **_kw):
                sessions["ses_rebuilt"] = {"engine": engine}
                return "ses_rebuilt"

        async def _fake_user_llm(*_a, **_kw):
            return object()

        import deps
        monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
        monkeypatch.setattr(deps, "get_text_manager", lambda *_a, **_kw: _StubTextManager())

        resp = client.post(f"/api/history/{sid}/resume", json={})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("reunion_greeting") == "好久不见。"
        greeting = [
            m for m in body["messages"] if m["id"] == body["reunion_greeting_id"]
        ][0]
        assert "evidence" in greeting and greeting["evidence"] is None
