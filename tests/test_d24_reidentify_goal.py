# -*- coding: utf-8 -*-
"""D24 ①b 第 3 步目标检查：「重新识别」真的重新识别。

名单按（文本、``IDENTIFY_VERSION``）缓存，而 #234 的同片主名否决没有升版本号。
名单页的「重新识别」与首次「开始蒸馏」走同一个入口，请求体只有 text_id，于是
库里命中的旧名单被原样返回 —— 按钮点了等于没点。本文件锁的是 ``force`` 链路：

  1. 默认（force=False）命中库缓存 → 原样返回，不调 LLM（对照，改前已成立）；
  2. force=True 跳过库缓存 → 调 LLM，返回并落库新名单 + 留证；
  3. 进程内缓存已有结果 → force=True 仍调 LLM；
  4. force=True 且任一分片失败 → 抛 ``DistillError``，库内旧名单不变；
  5. ``POST /api/distill/identify`` 把 ``force`` 透到 ``resolve_characters``。

第 2–5 行在目标检查提交里是 ``xfail(strict=True)``，实现后转正。期望值只用字面量或替身调用计数，
不从被测模块取。
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
from conftest import TEST_DATABASE_URL
from core.character_roster import resolve_characters
from core.distiller import DistillError, Distiller, IdentifyResult, _IDENTIFY_CACHE
from deps import get_storage
from routers.auth import get_current_user
from routers.distill import router as distill_router
from storage.postgres_store import PostgresStore
from storage.sqlite_store import SQLiteStore

CHUNK_SIZE = 3000
MERGE_REPLY = '{"merge": [["甲", "乙"]], "impersonal": []}'
CHUNKS = [
    [{"name": "甲", "aliases": ["阿甲"], "spoke": True, "reason": "甲说了话"}],
    [{"name": "乙", "aliases": ["阿乙"], "spoke": False, "reason": "乙只被提到"}],
    [{"name": "丙", "aliases": ["阿丙"], "spoke": True, "reason": "丙说了话"}],
]
CONTENT = "\n\n".join(["甲" * 2500, "乙" * 2500, "丙" * 2500])
OLD_ROSTER = [{"name": "旧名", "aliases": []}]
NEW_ROSTER = [{"name": "新名", "aliases": ["新别名"]}]
NEW_TRACE = {"identify_version": 4, "model": "test-model", "chunk_count": 1}


class _IdentifyLLM:
    """识别用的假 LLM：逐片按首字回固定条目，判定回固定样本。"""

    def __init__(self, *, fail_chunk=None):
        self.model = "test-model"
        self.last_usage = None
        self.learned_map_concurrency = 4
        self.fail_chunk = fail_chunk
        self.chunk_calls = 0
        self.judge_calls = 0

    def _usage(self):
        return {"prompt_tokens": 10, "completion_tokens": 5}

    async def async_chat(self, system, messages, client=None, gate=None, **kw):
        content = messages[0]["content"]
        if "merge" in system and "impersonal" in system:
            self.judge_calls += 1
            return MERGE_REPLY, self._usage()
        self.chunk_calls += 1
        if self.fail_chunk and content.startswith(self.fail_chunk):
            raise RuntimeError("connection timeout")
        for items in CHUNKS:
            if content.startswith(items[0]["name"]):
                return json.dumps(items, ensure_ascii=False), self._usage()
        return "[]", self._usage()

    def chat(self, system, messages, max_tokens=None, **kw):
        return "[]"

    def _make_async_client(self):
        class _Client:
            async def close(self):
                return None
        return _Client()


class _StubDistiller:
    """只回答 identify_roster 的替身：记录 fresh 实参，返回固定名单。"""

    def __init__(self):
        self.calls: list[bool] = []

    def identify_roster(self, text, *, fresh=False):
        self.calls.append(fresh)
        return IdentifyResult(NEW_ROSTER, NEW_TRACE)


@pytest.fixture(autouse=True)
def _clean_identify_cache():
    _IDENTIFY_CACHE.clear()
    yield
    _IDENTIFY_CACHE.clear()


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


@pytest.fixture
async def uid(store):
    user_id = f"u_{uuid.uuid4().hex[:12]}"
    await store.create_user(user_id, f"n_{uuid.uuid4().hex[:12]}", "x")
    return user_id


async def _seed_text(store, uid, content=CONTENT) -> str:
    text_id = f"txt_{uuid.uuid4().hex}"
    await store.save_text(text_id, "src.txt", content, user_id=uid)
    return text_id


async def _seed_roster(store, tid, roster=OLD_ROSTER, trace=None):
    await store.save_characters(
        tid, roster, version=Distiller.IDENTIFY_VERSION, trace=trace)


async def _read_columns(store, text_id):
    async with await store._connect() as conn:
        row = await conn.fetchrow(
            "SELECT characters_json, characters_trace FROM texts WHERE id = $1",
            text_id)
    return (row[0], row[1]) if row else (None, None)


async def _clear_db_cache(store, text_id):
    async with await store._connect() as conn:
        await conn.execute(
            "UPDATE texts SET characters_json = NULL, characters_version = NULL, "
            "characters_trace = NULL WHERE id = $1", text_id)


def _distiller(llm: _IdentifyLLM) -> Distiller:
    d = Distiller(llm)
    d._chunk_size = CHUNK_SIZE
    return d


async def test_default_hits_db_cache_without_llm(store, uid):
    """默认 force=False：库里有当前版本名单就原样返回，不调 LLM。"""
    tid = await _seed_text(store, uid)
    await _seed_roster(store, tid)

    llm = _IdentifyLLM()
    got = await resolve_characters(store, _distiller(llm), tid, uid, CONTENT)

    assert got == OLD_ROSTER
    assert llm.chunk_calls == 0 and llm.judge_calls == 0


async def test_force_reidentifies_and_persists_new_roster(store, uid):
    tid = await _seed_text(store, uid)
    await _seed_roster(store, tid)

    llm = _IdentifyLLM()
    got = await resolve_characters(
        store, _distiller(llm), tid, uid, CONTENT, force=True)

    assert llm.chunk_calls > 0, "force=True 必须调 LLM"
    assert got != OLD_ROSTER
    roster_json, trace_json = await _read_columns(store, tid)
    assert json.loads(roster_json) == json.loads(json.dumps(got)), "新名单必须落库"
    assert trace_json is not None and json.loads(trace_json)["per_chunk"] == CHUNKS


async def test_force_bypasses_in_process_cache(store, uid):
    tid = await _seed_text(store, uid)
    llm = _IdentifyLLM()
    d = _distiller(llm)
    await resolve_characters(store, d, tid, uid, CONTENT)
    first_calls = llm.chunk_calls
    assert first_calls > 0, "首次识别应调 LLM"

    await _clear_db_cache(store, tid)
    await resolve_characters(store, d, tid, uid, CONTENT, force=True)

    assert llm.chunk_calls > first_calls, "进程内缓存命中时 force=True 仍要调 LLM"


async def test_force_failure_keeps_old_roster(store, uid):
    tid = await _seed_text(store, uid)
    await _seed_roster(store, tid)

    with pytest.raises(DistillError):
        await resolve_characters(
            store, _distiller(_IdentifyLLM(fail_chunk="乙")), tid, uid, CONTENT,
            force=True)

    roster_json, _trace_json = await _read_columns(store, tid)
    assert json.loads(roster_json) == OLD_ROSTER


def _client(store, user_id) -> TestClient:
    app = FastAPI()
    app.include_router(distill_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "username": "testuser", "role": "user"}
    return TestClient(app)


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_identify_route_forwards_force(tmp_path, monkeypatch):
    store = SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))
    uid = f"user_{uuid.uuid4().hex[:8]}"
    tid = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", "content", user_id=uid))
    _run_async(store.save_characters(
        tid, OLD_ROSTER, version=Distiller.IDENTIFY_VERSION))

    stub = _StubDistiller()

    async def _fake_user_llm(*args, **kwargs):
        return object()

    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_distiller", lambda *a, **kw: stub)

    r = _client(store, uid).post(
        "/api/distill/identify", json={"text_id": tid, "force": True})

    assert r.status_code == 200, r.text
    assert stub.calls == [True], "force=True 没透到 identify_roster"
    assert r.json()["characters"] == NEW_ROSTER
    stored = _run_async(store.get_characters_owned(
        tid, uid, version=Distiller.IDENTIFY_VERSION))
    assert stored == NEW_ROSTER
