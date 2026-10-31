"""D22 目标检查：重逢问候不拿空串做场景/记忆检索。

线上日志（10-31 22:26:40 UTC）：``Mem0 search query_len=0`` →
``Invalid query: cannot be empty or whitespace-only``。根因是
``generate_reunion_greeting`` 调 ``ContextEngine.build("", ...)`` 时没关动态区，
空串并行跑了两条检索。本文件的判据是「两条检索一次都不发生」，不是「问候没报错」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from core.chat_engine import ChatEngine, _reunion_dates
from core.context_engine import ContextEngine, RetrievalResult
from core.schema import CharacterCard


@pytest.fixture(autouse=True)
def clear_reunion_dates():
    _reunion_dates.clear()


@pytest.fixture
def retrieval_calls(monkeypatch):
    """记录两条检索的调用次数；生产实现不被执行（本用例只数次数）。"""
    calls = {"scene": 0, "memory": 0}

    def scene(self, query):
        calls["scene"] += 1
        return RetrievalResult(source="scene", block="", items=[], status="empty")

    def memory(self, query, current_mood=None):
        calls["memory"] += 1
        return RetrievalResult(source="memory", block="", items=[], status="empty")

    monkeypatch.setattr(ContextEngine, "_retrieve_scenes_ex", scene)
    monkeypatch.setattr(ContextEngine, "_retrieve_memories_ex", memory)
    return calls


def _make_engine() -> ChatEngine:
    card = CharacterCard(name="测试角色", identity="一个温柔的人")
    llm = MagicMock()
    llm.chat.return_value = "你总算回来了。"
    llm.last_usage = {}
    engine = ChatEngine(
        llm=llm, rag=MagicMock(), card=card, storage=MagicMock(),
        session_id="test-session", is_new_session=True,
    )
    engine.history = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "嗨"},
    ]
    engine._build_interval_note = MagicMock(return_value="你们已经很久没联系了。")
    return engine


def _old_session_data(hours_ago: int = 12) -> dict:
    return {
        "updated_at": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    }


def test_reunion_greeting_runs_no_empty_query_retrieval(retrieval_calls):
    engine = _make_engine()

    greeting = engine.generate_reunion_greeting(session_data=_old_session_data())

    assert greeting == "你总算回来了。"
    assert retrieval_calls == {"scene": 0, "memory": 0}, (
        f"重逢问候仍拿空串检索：{retrieval_calls}"
    )


def test_friend_stage_reunion_memory_still_reaches_llm(monkeypatch, retrieval_calls):
    engine = _make_engine()
    engine._scoped = MagicMock()
    engine._scoped.enabled = True
    engine._scoped.get_all.return_value = [
        {"memory": "对方说喜欢下雨天", "metadata": {"importance": 9}},
    ]
    engine._affinity_service.affinity = 60
    engine._affinity_service.stage = "朋友"
    engine._affinity_service.stage_emoji = "😄"

    invoked: list[bool] = []
    original = ChatEngine._build_reunion_memories

    def spy(self):
        invoked.append(True)
        return original(self)

    monkeypatch.setattr(ChatEngine, "_build_reunion_memories", spy)

    greeting = engine.generate_reunion_greeting(session_data=_old_session_data())

    assert greeting == "你总算回来了。"
    assert invoked == [True], "朋友档没有取重逢记忆"
    instruction = engine.llm.chat.call_args[0][1][0]["content"]
    assert "对方说喜欢下雨天" in instruction, "重逢记忆没进到发给 LLM 的提示里"
    assert retrieval_calls == {"scene": 0, "memory": 0}, (
        f"重逢问候仍拿空串检索：{retrieval_calls}"
    )
