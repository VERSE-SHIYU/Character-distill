# -*- coding: utf-8 -*-
"""D24 ①b 第 1 步目标检查：决定名单的模型输出与名单同一次落库、可原样重算。

名单错并（云汐玥 / 云肆野 / 昭华公主并进云绮，楚临并进楚翊）查不出是哪一步出的错，
因为逐片识别结果只在内存、判定结果只有一条 WARNING 日志。本文件锁的正是「留证」：

  1. ``texts.characters_trace`` 与 ``characters_json`` 同一次写入，逐片条目按分片序原样；
  2. 判定 5 份原始输出（含解析失败那份的原文）、票数结果都在 trace 里；
  3. ``replay_roster(trace)`` 用纯函数重算出与库内名单逐项相等的名单；
  4. 一份判定样本非 JSON → 原文保留、其余合法样本照常计票；
  5. 任一分片失败 → 抛 ``DistillError``，名单与留证都不写（对照，改前已成立）；
  6. 库内已有当前版本名单 → 不调 LLM，留证不变（对照，改前已成立）；
  7. 单片路径 → ``per_chunk`` 长 1、``judge`` 为 null，replay 原样返回。

前四行与第 7 行在实现前是 ``xfail(strict=True)``。期望值全部是字面量，不从被测模块
import —— 从源码 import 就自证自明，变异杀不掉。
"""

from __future__ import annotations

import json
import uuid

import pytest

from conftest import TEST_DATABASE_URL
from core.character_roster import resolve_characters
from core.distiller import DistillError, Distiller, _IDENTIFY_CACHE
from storage.postgres_store import PostgresStore

CHUNK_SIZE = 3000
MERGE_REPLY = '{"merge": [["甲", "乙"]], "impersonal": []}'
NOT_JSON = "这不是 JSON"

CHUNKS = [
    [{"name": "甲", "aliases": ["阿甲"], "spoke": True, "reason": "甲说了话"}],
    [{"name": "乙", "aliases": ["阿乙"], "spoke": False, "reason": "乙只被提到"}],
    [{"name": "丙", "aliases": ["阿丙"], "spoke": True, "reason": "丙说了话"}],
]
CONTENT = "\n\n".join(["甲" * 2500, "乙" * 2500, "丙" * 2500])
SINGLE_REPLY = json.dumps([{"name": "甲", "aliases": ["阿甲"], "spoke": True,
                            "reason": "甲说了话"}], ensure_ascii=False)


class _IdentifyLLM:
    """识别用的假 LLM：逐片按首字回固定条目，判定按调用序号回固定样本。"""

    def __init__(self, *, judge_replies=None, fail_chunk=None, single_reply=None):
        self.model = "test-model"
        self.last_usage = None
        self.learned_map_concurrency = 4
        self.judge_replies = judge_replies or [MERGE_REPLY] * 5
        self.fail_chunk = fail_chunk
        self.single_reply = single_reply
        self.chunk_calls = 0
        self.judge_calls = 0

    def _usage(self):
        return {"prompt_tokens": 10, "completion_tokens": 5}

    async def async_chat(self, system, messages, client=None, gate=None, **kw):
        content = messages[0]["content"]
        if "merge" in system and "impersonal" in system:
            idx = self.judge_calls
            self.judge_calls += 1
            return self.judge_replies[idx], self._usage()
        self.chunk_calls += 1
        if self.fail_chunk and content.startswith(self.fail_chunk):
            raise RuntimeError("connection timeout")
        for items in CHUNKS:
            if content.startswith(items[0]["name"]):
                return json.dumps(items, ensure_ascii=False), self._usage()
        return "[]", self._usage()

    def chat(self, system, messages, max_tokens=None, **kw):
        return self.single_reply

    def _make_async_client(self):
        class _Client:
            async def close(self):
                return None
        return _Client()


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


async def _read_columns(store, text_id):
    """读名单与留证两列；实现前 trace 列不存在时返回 None（对照用例照样能跑）。"""
    async with await store._connect() as conn:
        has_trace = await conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'texts' AND column_name = 'characters_trace'")
        if not has_trace:
            row = await conn.fetchrow(
                "SELECT characters_json FROM texts WHERE id = $1", text_id)
            return (row[0] if row else None), None
        row = await conn.fetchrow(
            "SELECT characters_json, characters_trace FROM texts WHERE id = $1", text_id)
    return (row[0], row[1]) if row else (None, None)


def _distiller(llm: _IdentifyLLM) -> Distiller:
    d = Distiller(llm)
    d._chunk_size = CHUNK_SIZE
    return d


async def test_per_chunk_outputs_and_meta_are_persisted(store, uid):
    tid = await _seed_text(store, uid)
    await resolve_characters(store, _distiller(_IdentifyLLM()), tid, uid, CONTENT)

    _roster, trace_json = await _read_columns(store, tid)
    assert trace_json is not None, "名单落了库，留证没落"
    trace = json.loads(trace_json)

    assert trace["per_chunk"] == CHUNKS
    assert trace["chunk_size"] == CHUNK_SIZE
    assert trace["chunk_count"] == 3
    assert trace["identify_version"] == 4


async def test_judge_samples_and_tally_are_persisted(store, uid):
    tid = await _seed_text(store, uid)
    await resolve_characters(store, _distiller(_IdentifyLLM()), tid, uid, CONTENT)

    _roster, trace_json = await _read_columns(store, tid)
    trace = json.loads(trace_json)

    assert trace["judge"]["raw"] == [MERGE_REPLY] * 5
    assert trace["judge"]["failed"] == []
    assert trace["judge"]["pairs"] == [["乙", "甲"]], "tally 按名排序组对"
    assert trace["judge"]["impersonal"] == []


async def test_replay_roster_matches_stored_roster(store, uid):
    from core.roster_aggregate import replay_roster

    tid = await _seed_text(store, uid)
    await resolve_characters(store, _distiller(_IdentifyLLM()), tid, uid, CONTENT)

    roster_json, trace_json = await _read_columns(store, tid)
    assert replay_roster(json.loads(trace_json)) == json.loads(roster_json)


async def test_unparseable_judge_sample_keeps_raw_text(store, uid):
    tid = await _seed_text(store, uid)
    llm = _IdentifyLLM(judge_replies=[NOT_JSON] + [MERGE_REPLY] * 4)
    await resolve_characters(store, _distiller(llm), tid, uid, CONTENT)

    _roster, trace_json = await _read_columns(store, tid)
    judge = json.loads(trace_json)["judge"]

    assert judge["raw"] == [NOT_JSON] + [MERGE_REPLY] * 4
    assert judge["failed"] == []
    assert judge["pairs"] == [["乙", "甲"]], "4 份合法样本仍够 3 票"


async def test_chunk_failure_writes_neither_roster_nor_trace(store, uid):
    tid = await _seed_text(store, uid)
    with pytest.raises(DistillError):
        await resolve_characters(
            store, _distiller(_IdentifyLLM(fail_chunk="乙")), tid, uid, CONTENT)

    roster_json, trace_json = await _read_columns(store, tid)
    assert roster_json is None
    assert trace_json is None


async def test_existing_roster_is_returned_without_trace_rewrite(store, uid):
    tid = await _seed_text(store, uid)
    await store.save_characters(
        tid, [{"name": "甲", "aliases": []}], version=Distiller.IDENTIFY_VERSION)

    llm = _IdentifyLLM()
    got = await resolve_characters(store, _distiller(llm), tid, uid, CONTENT)

    assert got == [{"name": "甲", "aliases": []}]
    assert llm.chunk_calls == 0 and llm.judge_calls == 0, "命中缓存不许调 LLM"
    roster_json, trace_json = await _read_columns(store, tid)
    assert trace_json is None, "命中缓存不许改动留证"


async def test_single_chunk_trace_has_no_judge(store, uid):
    from core.roster_aggregate import replay_roster

    tid = await _seed_text(store, uid, "短文本" * 100)
    llm = _IdentifyLLM(single_reply=SINGLE_REPLY)
    await resolve_characters(store, _distiller(llm), tid, uid, "短文本" * 100)

    roster_json, trace_json = await _read_columns(store, tid)
    trace = json.loads(trace_json)

    assert len(trace["per_chunk"]) == 1
    assert trace["per_chunk"][0] == json.loads(SINGLE_REPLY)
    assert trace["judge"] is None
    assert replay_roster(trace) == json.loads(roster_json)
