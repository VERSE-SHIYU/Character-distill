# -*- coding: utf-8 -*-
"""目标检查：群聊本场记录（⑤ 最后一项，spec `docs/specs/group-session-record.md`）。

目标：群里每个 AI 人物在这个群里有一份自己的本场记录，效果和一对一一致 ——
隔离、恢复后接得上、用户看不到、删群跟着删。抽取 / 校验 / 注入全部复用
`core/session_record.py`，这里只验「接到群聊上」这一层。

替身与工具沿用一对一的目标检查（`tests/test_session_record_goal.py`），不另写一份。
"""
from __future__ import annotations

import asyncio
import json

from conftest import TEST_DATABASE_URL, main_loop_registered
from core import session_record as rec
from core.group_session import GroupSession
from storage.postgres_store import PostgresStore
from test_session_record_goal import (
    CHAR_SAYS, USER_SAYS, _add, _engine, _FailingEvalLLM, _one, _pg, _ScriptLLM, _seed,
    _uid, _verdict,
)

_KNOWN = {rec.FIELD_OPS: [_add("known", "在杭州读研究生", "杭州读研究生")]}


async def _store() -> PostgresStore:
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    return store


@_pg
async def test_G1_a_members_record_is_its_own():
    """群里 A 的一轮只写进（群, A）：同群的 B 没有，同一张卡的一对一存档也没有；A 的注入块有，B 没有。"""
    store = await _store()
    try:
        _, card_a, session_a, _ = await _seed(store)
        _, card_b, _, _ = await _seed(store)
        gid = _uid("g")
        llm = _ScriptLLM(_verdict(), record=_KNOWN)
        eng_a, eng_b = _engine(store, llm, card_a, group_id=gid), _engine(store, llm, card_b, group_id=gid)
        async with main_loop_registered():
            await asyncio.to_thread(eng_a.post_stream_process, USER_SAYS, CHAR_SAYS)
        assert len(llm.record_calls) == 1, "群里一轮应有一次抽取调用"
        assert "在杭州读研究生" in await store.load_group_session_record_unscoped(gid, card_a)
        assert await store.load_group_session_record_unscoped(gid, card_b) == ""
        assert await store.load_session_record_unscoped(session_a) == "", "群里的记录漏进了一对一存档"
        assert "在杭州读研究生" in eng_a._build_all_enhancements()
        assert rec.BLOCK_TITLE not in eng_b._build_all_enhancements()
    finally:
        await store.close()


class _GroupLLM(_ScriptLLM):
    """群聊回复走 achat：记下拼好的系统提示词，看恢复后注入块在不在里面。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.systems: list[str] = []

    async def achat(self, system, messages, **_kw):
        self.systems.append(system)
        return "回复"


@_pg
async def test_G2_a_rebuilt_group_still_has_the_record():
    """服务重启后群引擎是新造的：群会话发消息前装回本场记录，提示词里有它（单发、广播两条路）。"""
    store = await _store()
    try:
        _, card_a, _, _ = await _seed(store)
        gid = _uid("g")
        await store.save_group_session_record(
            gid, card_a, _one("known", "在杭州读研究生", "杭州读研究生").to_persist())
        for path in ("send", "broadcast"):
            llm = _GroupLLM(_verdict())
            group = GroupSession(gid, {card_a: _engine(store, llm, card_a, group_id=gid)},
                                 storage=None, user_id="u")
            group._storage = store  # 只装回不评估：评估是后台任务，这里不等它
            async with main_loop_registered():
                if path == "send":
                    await group.send(card_a, "你好")
                else:
                    await group.broadcast("你好", [card_a])
            assert llm.systems and "在杭州读研究生" in llm.systems[-1], path
    finally:
        await store.close()


@_pg
async def test_G3_a_reaction_only_round_extracts_nothing():
    """点赞路径评估时回复是空串（`web/routers/group.py:259`）：没有这一轮的回复就不抽取。"""
    store = await _store()
    try:
        _, card_a, _, _ = await _seed(store)
        gid = _uid("g")
        llm = _ScriptLLM(_verdict(), record=_KNOWN)
        eng = _engine(store, llm, card_a, group_id=gid)
        async with main_loop_registered():
            await asyncio.to_thread(eng._evaluate_affinity, USER_SAYS, "")
        assert llm.record_calls == []
        assert await store.load_group_session_record_unscoped(gid, card_a) == ""
    finally:
        await store.close()


@_pg
async def test_G4_hard_deleting_the_group_deletes_its_records():
    store = await _store()
    try:
        _, card_a, _, _ = await _seed(store)
        gid = _uid("g")
        await store.save_group_session_record(
            gid, card_a, _one("known", "在杭州读研究生", "杭州读研究生").to_persist())
        await store.hard_delete_group_session(gid)
        assert await store.load_group_session_record_unscoped(gid, card_a) == ""
    finally:
        await store.close()


@_pg
async def test_G5_the_user_never_sees_it_and_no_fake_affinity():
    """好感评估失败、本场记录照常存：群好感读出来仍是「没评估过」，不伪造 50；读出的好感也不带记录。"""
    store = await _store()
    try:
        _, card_a, _, _ = await _seed(store)
        gid = _uid("g")
        llm = _FailingEvalLLM(_verdict(), record=_KNOWN)
        async with main_loop_registered():
            await asyncio.to_thread(_engine(store, llm, card_a, group_id=gid).post_stream_process,
                                    USER_SAYS, CHAR_SAYS)
        assert "在杭州读研究生" in await store.load_group_session_record_unscoped(gid, card_a), "前提：存进去了"
        assert await store.get_group_affinity(gid, card_a) is None

        ok = _ScriptLLM(_verdict(), record={rec.FIELD_OPS: []})
        async with main_loop_registered():
            await asyncio.to_thread(_engine(store, ok, card_a, group_id=gid).post_stream_process,
                                    USER_SAYS, CHAR_SAYS)
        text = json.dumps(await store.get_group_affinity(gid, card_a), ensure_ascii=False, default=str)
        assert "在杭州读研究生" not in text and "record" not in text
    finally:
        await store.close()


@_pg
async def test_G6_restore_entry_reads_the_group_record():
    """群聊的恢复入口和一对一的 `restore_engine_state` 并列，装回好感和本场记录。"""
    store = await _store()
    try:
        _, card_a, _, _ = await _seed(store)
        gid = _uid("g")
        await store.save_group_session_record(
            gid, card_a, _one("known", "在杭州读研究生", "杭州读研究生").to_persist())
        await store.update_group_affinity(gid, card_a, 61, 40, "平静", 60, "")
        from core.session_restore import restore_group_engine_state

        fresh = _engine(store, _ScriptLLM(_verdict()), card_a, group_id=gid)
        await restore_group_engine_state(fresh, gid, card_a, store)
        assert fresh._affinity == 61
        assert "在杭州读研究生" in fresh._build_all_enhancements()
    finally:
        await store.close()
