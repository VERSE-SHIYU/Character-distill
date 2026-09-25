# -*- coding: utf-8 -*-
"""金丝雀：假凭据跑完整条链路，明文一次都不落库。

链路（全部在**同一个 event loop** 里直驱生产函数）：
    保存配置 → 建会话（user_role 留空 = 缺陷 55 的射程）→ 好感度评估落库
    → 逐出内存 → 恢复会话重建（`_ensure_session` 本身）→ 再评估落库 → 扫全库

**为什么不走 HTTP**：见缺陷 123。起一个 HTTP 面就要跨 event loop（TestClient 每个请求
另起一个 loop，而 asyncpg 池绑在 loop 上），那条路本身就是 123 的根因，不在本 spec 修。
所以这里不镜像路由，而是直接调路由里那段逻辑 —— `routers.chat._ensure_session` 就是
「从 DB 重建会话」那段代码本身。

**扫描前先断言 affinity_state 确实写进了库**。缺陷 56 的形态是「该加密的加了密，同一个值
从别的路径落进明文列」；这条链路上真正会落凭据的格子就是 affinity_state。夹具哪一步没跑
起来时，扫描全绿只能说明「没东西可泄漏」—— 那是最难发现的假绿，故用一条前置断言挡掉。

缺陷 55 的射程是**空 user_role 的会话**（有角色的会话不漏），故建会话时刻意不传角色：
要金的丝雀就得站在缺陷的射程里。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from unittest.mock import MagicMock, patch

import pytest
from pwdlib import PasswordHash

from conftest import PG_ENV, main_loop_registered

import deps
from core.schema import CharacterCard
from core.text_manager import TextManager
from routers.chat import _ensure_session
from storage.sqlite_store import SQLiteStore

_PW_HASH = PasswordHash.recommended().hash("Pass1234")


# ── 假 LLM：会说 JSON，但说的是**喂进去的那串** ──────────────────────────────
#
# 这是金丝雀能咬人的那一格：凭据进 prompt 本身还不算落库，落库要等模型把它复述回来、
# 被当成情感状态存下（缺陷 55 的实际经过）。一个只会说「固定回复」的桩会让
# 「凭据 → prompt → 落库」这条路在测试里断掉，于是它守不住。


class _EchoLLM:
    last_usage: dict = {}
    _model = "echo"

    def preflight(self) -> None:
        return None

    def chat(self, system_prompt, messages, *a, **kw) -> str:
        prompt = (messages[-1].get("content") if messages else "") or ""
        return json.dumps(
            {"inner_voice": prompt, "affinity": 60, "trust": 40,
             "mood": "平静", "guard": 60, "importance": 5},
            ensure_ascii=False,
        )

    async def achat(self, system_prompt, messages, *a, **kw) -> str:
        return self.chat(system_prompt, messages, *a, **kw)


class _NoRAG:
    def get_rag_for_session(self, *a, **kw):
        return None


class _MemMgr:
    enabled = False


def _text_manager(store, llm, sessions) -> TextManager:
    return TextManager(lambda: store, None, llm, sessions,
                       indexing_service=_NoRAG(), memory_manager=_MemMgr())


# ── 链路 ─────────────────────────────────────────────────────────────────────


async def _turn(engine, sid: str) -> None:
    """一轮：好感度评估 —— 落库由生产代码自己完成，用例不替它写。

    `_evaluate_affinity` 是同步函数，其中几处 `submit_to_main_loop(wait=True)` 在主 loop
    线程上调用即自等、必被拒（缺陷 123 的生产形态）。故经 `asyncio.to_thread` 让它站到
    工作线程上 —— 这正是 async 路由该有的写法；投递实现由 `main_loop_registered` 注册到
    当前运行的 loop。改前这里手工 `await store.save_affinity_state(...)` 绕开那条链
    （退路一拿掉就绕不开了），那样测的是「用例自己会写库」，不是「链路会写库」。
    """
    engine._session_id = sid
    async with main_loop_registered():
        await asyncio.to_thread(engine._evaluate_affinity, "你好", "……")


async def _drive(store, uid: str, llm, api_key: str, embedding_key: str) -> str:
    sessions = deps.get_sessions()
    await store.create_user(uid, f"canary_{uid[-6:]}", _PW_HASH)
    await store.update_user_api_config(
        uid,
        api_key=api_key,
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        embedding_key=embedding_key,
    )
    cfg = await store.get_user_api_config(uid)
    assert cfg["api_key"] == api_key, "凭据没写进去 —— 后面的扫描会是一条恒真的锁"
    assert cfg["embedding_key"] == embedding_key, "embedding key 没写进去"

    tid = f"txt_{uuid.uuid4().hex}"
    await store.save_text(tid, "src.txt", "content", user_id=uid)
    card_id = f"card_{uuid.uuid4().hex}"
    await store.save_card(
        card_id, tid, "张三",
        json.dumps({"name": "张三"}, ensure_ascii=False), user_id=uid)

    text_manager = _text_manager(store, llm, sessions)
    card = CharacterCard.model_validate({"name": "张三"})

    # 建会话：不传 user_role —— 站在缺陷 55 的射程里（空角色会话才会漏）
    sid = text_manager._create_session(
        card, all_characters=[{"name": "张三", "aliases": []}],
        card_id=card_id, user_id=uid)
    await store.save_session(sid, card_id, "", "", uid)
    await _turn(sessions[sid]["engine"], sid)

    # 恢复会话：走生产代码 `_ensure_session`（路由里那段重建逻辑本身，不是镜像）
    sessions.pop(sid, None)
    session = await _ensure_session(sid, store, sessions, uid)
    await _turn(session["engine"], sid)

    state_json, initialized = await store.load_affinity_state_unscoped(sid)
    assert initialized and state_json, (
        "affinity_state 是空的 —— 链路没真跑到落库那一步，"
        "这时扫描全绿只能说明「没东西可泄漏」，不是「守住了」")
    return sid


# ── 扫描：整个库，不是「我知道的那几列」 ─────────────────────────────────────


def _sqlite_hits(db_path, sentinels) -> dict[str, list[str]]:
    conn = sqlite3.connect(str(db_path))
    hits: dict[str, list[str]] = {}
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            for (_i, col, *_rest) in conn.execute(f'PRAGMA table_info("{table}")'):
                for name, sentinel in sentinels.items():
                    n = conn.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE CAST("{col}" AS TEXT) LIKE ?',
                        (f"%{sentinel}%",),
                    ).fetchone()[0]
                    if n:
                        hits.setdefault(name, []).append(f"表 {table}.{col} × {n}")
    finally:
        conn.close()

    raw = b""
    for path in [db_path, *db_path.parent.glob(db_path.name + "-*")]:
        raw += path.read_bytes()
    for name, sentinel in sentinels.items():
        if sentinel.encode() in raw:
            hits.setdefault(name, []).append("库文件字节")
    return hits


async def _pg_hits(store, sentinels) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    async with await store._connect() as conn:
        rows = await conn.fetch(
            """SELECT c.table_name, c.column_name
               FROM information_schema.columns c
               JOIN information_schema.tables t
                 ON t.table_schema = c.table_schema AND t.table_name = c.table_name
               WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'"""
        )
        by_table: dict[str, list[str]] = {}
        for r in rows:
            by_table.setdefault(r["table_name"], []).append(r["column_name"])

        for table, cols in by_table.items():
            where = " OR ".join(f'"{c}"::text LIKE $1' for c in cols)
            for name, sentinel in sentinels.items():
                n = await conn.fetchval(
                    f'SELECT COUNT(*) FROM "{table}" WHERE {where}', f"%{sentinel}%")
                if n:
                    hits.setdefault(name, []).append(f"表 {table} × {n}")
    return hits


def _assert_clean(hits, sentinels):
    assert not hits, (
        "假凭据以**明文**出现在库里，该走的加密没走到这条路径上：\n"
        + "\n".join(f"  {name}: " + "；".join(v) for name, v in hits.items())
        + f"\n（哨兵：{', '.join(sentinels.values())}）"
    )


def _sentinels() -> dict[str, str]:
    return {
        "api_key": f"sk-canary-{uuid.uuid4().hex}",
        "embedding_key": f"emb-canary-{uuid.uuid4().hex}",
    }


def _patch_runtime(monkeypatch, store):
    """钉死 `_ensure_session` 里那两个按名取的口 + 存储单例。

    全是**函数内 import**（`from deps import get_text_manager, get_user_llm`），故打在
    `deps` 这个模块对象上就能生效 —— 打 `web.deps` 是另一个模块对象，打不到（踩过）。
    """
    monkeypatch.setattr(deps, "_storage", store)
    monkeypatch.setattr(deps, "get_user_llm", lambda *_a, **_kw: _async_echo())
    monkeypatch.setattr(
        deps, "get_text_manager",
        lambda llm=None: _text_manager(store, llm or _EchoLLM(), deps.get_sessions()))


async def _async_echo():
    return _EchoLLM()


# ── 日志面：取 RAG 时 key 不进 stdout / 日志（缺陷 72）─────────────────────────
#
# 两个金丝雀走的是**落库**那条链，守不住日志。这条是同一把锁在日志上的最小面：
# 取 RAG 的每次 HIT / MISS 都把完整 key 打给 stdout，容器里就是 docker logs。

_KEY_WINDOW = 8  # 旧身份只取 key 前 8 位（sk- 之后仅 5 个随机字符），8 位窗口即已辨识


def test_indexing_service_never_logs_the_embedding_key(capsys, caplog):
    from core.indexing_service import IndexingService

    secret = f"sk-test-{uuid.uuid4().hex}"
    svc = IndexingService(storage=MagicMock(), rag_config={})
    with caplog.at_level(logging.DEBUG), \
            patch("core.indexing_service.RAGEngine") as cls:
        inst = MagicMock()
        inst.load_existing.return_value = True  # 不真建索引、不出网
        cls.return_value = inst
        svc._get_or_build_rag("text_abc", "正文", embedding_key=secret)
        svc._get_or_build_rag("text_abc", "正文", embedding_key=secret)  # HIT 那条

    captured = capsys.readouterr().out + "\n".join(
        r.getMessage() for r in caplog.records)
    leaked = [secret[i:i + _KEY_WINDOW]
              for i in range(len(secret) - _KEY_WINDOW + 1)
              if secret[i:i + _KEY_WINDOW] in captured]
    assert not leaked, (
        f"嵌入 key 的片段进了日志（命中 {leaked[0]!r}）：{captured!r}")


# ── SQLite ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSqliteCanary:
    async def test_credential_never_lands_in_plaintext(self, tmp_path, monkeypatch):
        store = SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))
        _patch_runtime(monkeypatch, store)
        uid = f"usr_{uuid.uuid4().hex[:12]}"
        sentinels = _sentinels()

        await _drive(store, uid, _EchoLLM(), **sentinels)
        _assert_clean(_sqlite_hits(store.db_path, sentinels), sentinels)


# ── PostgreSQL ───────────────────────────────────────────────────────────────


def _dsn() -> str:
    return os.environ["DATABASE_URL"]


@pytest.mark.asyncio
@PG_ENV.skipif("PostgresStore 金丝雀")
class TestPostgresCanary:
    async def test_credential_never_lands_in_plaintext(self, monkeypatch):
        from storage.postgres_store import PostgresStore

        store = PostgresStore(_dsn())
        _patch_runtime(monkeypatch, store)
        uid = f"usr_{uuid.uuid4().hex[:12]}"
        sentinels = _sentinels()
        try:
            await _drive(store, uid, _EchoLLM(), **sentinels)
            _assert_clean(await _pg_hits(store, sentinels), sentinels)
        finally:
            await store.close()
