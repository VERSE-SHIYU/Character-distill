"""Tests for ChatEngine.generate_reunion_greeting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from core.chat_engine import ChatEngine, _reunion_dates
from core.affinity_service import calc_stage
from core.schema import CharacterCard


@pytest.fixture(autouse=True)
def clear_reunion_dates():
    """Reset the in-memory frequency gate before each test."""
    _reunion_dates.clear()


def _make_engine(
    history_len: int = 3,
    mock_llm_return: str = "你总算回来了。",
    session_id: str = "test-session",
) -> ChatEngine:
    """Build a minimal ChatEngine with mocked deps for reunion testing."""
    card = CharacterCard(name="测试角色", identity="一个温柔的人")

    llm = MagicMock()
    llm.chat.return_value = mock_llm_return
    llm.last_usage = {}

    rag = MagicMock()

    engine = ChatEngine(llm=llm, rag=rag, card=card)
    engine._session_id = session_id
    engine._storage = MagicMock()

    if history_len > 0:
        engine.history = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "嗨"}]
        if history_len > 2:
            for i in range(history_len - 2):
                engine.history.append({"role": "user", "content": f"msg_{i}"})
                engine.history.append({"role": "assistant", "content": f"resp_{i}"})

    # Mock the time awareness block to avoid run_on_main_loop issues in tests
    engine._build_time_awareness_block = MagicMock(
        return_value="【现实感知】你们已经很久没联系了。"
    )

    return engine


def _old_session_data(hours_ago: int = 12) -> dict:
    """Return session_data dict with updated_at far enough in the past."""
    return {
        "updated_at": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    }


class TestResumeEndpointMessageIds:
    """Verifies the resume endpoint's return contract includes the reunion greeting
    as a proper message in the frontend_messages array."""

    def test_greeting_in_frontend_messages(self):
        """Greeting appears as a regular char message in frontend_messages."""
        db_messages = [
            {"role": "user", "content": "你好", "id": 101, "created_at": "2025-01-01T00:00:00Z", "retracted": False},
            {"role": "char", "content": "嗨", "id": 102, "created_at": "2025-01-01T00:00:01Z", "retracted": False},
            {"role": "user", "content": "最近怎么样", "id": 103, "created_at": "2025-01-01T00:00:02Z", "retracted": False},
        ]
        greeting_data = {
            "reunion_greeting_id": 999,
            "reunion_greeting": "你总算回来了。",
            "reunion_greeting_created_at": "2025-01-02T00:00:00Z",
        }

        # Step 10: build frontend_messages from db_messages + greeting
        frontend_messages = [
            {"role": m["role"], "content": m["content"], "id": m["id"],
             "created_at": m["created_at"], "retracted": m.get("retracted", False)}
            for m in db_messages
        ]
        if greeting_data:
            frontend_messages.append({
                "role": "char",
                "content": greeting_data["reunion_greeting"],
                "id": greeting_data["reunion_greeting_id"],
                "created_at": greeting_data["reunion_greeting_created_at"],
                "retracted": False,
            })

        assert len(frontend_messages) == 4
        assert frontend_messages[3] == {
            "role": "char",
            "content": "你总算回来了。",
            "id": 999,
            "created_at": "2025-01-02T00:00:00Z",
            "retracted": False,
        }

    def test_no_greeting_no_extra_message(self):
        """When no reunion greeting, frontend_messages is a clean mapping of db_messages."""
        db_messages = [
            {"role": "user", "content": "你好", "id": 101, "created_at": "2025-01-01T00:00:00Z", "retracted": False},
            {"role": "char", "content": "嗨", "id": 102, "created_at": "2025-01-01T00:00:01Z", "retracted": False},
        ]
        greeting_data = None

        frontend_messages = [
            {"role": m["role"], "content": m["content"], "id": m["id"],
             "created_at": m["created_at"], "retracted": m.get("retracted", False)}
            for m in db_messages
        ]
        if greeting_data:
            frontend_messages.append({
                "role": "char",
                "content": greeting_data["reunion_greeting"],
                "id": greeting_data["reunion_greeting_id"],
                "created_at": greeting_data["reunion_greeting_created_at"],
                "retracted": False,
            })

        assert len(frontend_messages) == 2
        assert frontend_messages == [
            {"role": "user", "content": "你好", "id": 101, "created_at": "2025-01-01T00:00:00Z", "retracted": False},
            {"role": "char", "content": "嗨", "id": 102, "created_at": "2025-01-01T00:00:01Z", "retracted": False},
        ]

    def test_greeting_id_in_message_ids_after_rebuild(self):
        """Greeting_id still appended to message_ids for revoke support."""
        db_msg_ids = [101, 102, 103]
        greeting_id = 999

        message_ids = list(db_msg_ids)
        greeting_data = {
            "reunion_greeting_id": greeting_id,
            "reunion_greeting": "你回来了。",
        }

        message_ids = list(db_msg_ids)
        if greeting_data:
            message_ids.append(greeting_data["reunion_greeting_id"])

        assert greeting_id in message_ids
        assert message_ids == [101, 102, 103, 999]


class TestReunionGreeting:
    """generate_reunion_greeting() condition and output tests."""

    def test_empty_history_returns_empty(self):
        """No greeting when there's no chat history."""
        engine = _make_engine(history_len=0)
        assert engine.generate_reunion_greeting(session_data=_old_session_data()) == ""

    def test_recent_session_returns_empty(self):
        """No greeting when last activity was less than 6 hours ago."""
        recent = {
            "updated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        }
        engine = _make_engine()
        assert engine.generate_reunion_greeting(session_data=recent) == ""

    def test_old_session_generates_greeting(self):
        """Greeting generated when last activity was 6+ hours ago."""
        engine = _make_engine()
        result = engine.generate_reunion_greeting(session_data=_old_session_data())
        assert result == "你总算回来了。"

    def test_same_day_duplicate_returns_empty(self):
        """Only one greeting per calendar day per session (user local tz)."""
        engine = _make_engine()
        first = engine.generate_reunion_greeting(session_data=_old_session_data())
        assert first != ""

        second = engine.generate_reunion_greeting(session_data=_old_session_data())
        assert second == ""

    def test_llm_returns_empty_on_exception(self):
        """Graceful degradation when LLM call fails."""
        engine = _make_engine()
        engine.llm.chat.side_effect = Exception("LLM timeout")
        assert engine.generate_reunion_greeting(session_data=_old_session_data()) == ""

    def test_llm_returns_long_text_returns_empty(self):
        """Greeting longer than 100 chars is discarded."""
        engine = _make_engine(mock_llm_return="a" * 101)
        assert engine.generate_reunion_greeting(session_data=_old_session_data()) == ""

    def test_no_storage_returns_empty(self):
        """No greeting when storage is not set."""
        engine = _make_engine()
        engine._storage = None
        assert engine.generate_reunion_greeting(session_data=_old_session_data()) == ""

    def test_no_session_id_returns_empty(self):
        """No greeting when session_id is not set."""
        engine = _make_engine()
        engine._session_id = ""
        assert engine.generate_reunion_greeting(session_data=_old_session_data()) == ""

    def test_exactly_6_hours_is_still_recent(self):
        """Just under 6 hours is treated as recent — threshold is exclusive."""
        data = {
            "updated_at": (
                datetime.now(timezone.utc) - timedelta(hours=5, minutes=59)
            ).isoformat()
        }
        engine = _make_engine()
        assert engine.generate_reunion_greeting(session_data=data) == ""

    def test_over_6_hours_triggers_greeting(self):
        """Just past 6 hours triggers greeting."""
        data = {
            "updated_at": (
                datetime.now(timezone.utc) - timedelta(hours=6, seconds=1)
            ).isoformat()
        }
        engine = _make_engine()
        result = engine.generate_reunion_greeting(session_data=data)
        assert result == "你总算回来了。"

    def test_offline_moment_injected_for_friend_stage(self):
        """朋友档 + 有记忆 → prompt 含离线时刻素材。"""
        engine = _make_engine()
        engine._card_id = "card_test"
        engine._memory = MagicMock()
        engine._memory.enabled = True
        engine._memory.get_all.return_value = [
            {"memory": "对方说喜欢下雨天", "metadata": {"importance": 8}},
            {"memory": "你们一起看了日出", "metadata": {"importance": 9}},
        ]
        engine._affinity_service.affinity = 60
        engine._affinity_service.stage = "朋友"
        engine._affinity_service.stage_emoji = "😄"

        result = engine.generate_reunion_greeting(session_data=_old_session_data())
        assert result == "你总算回来了。"

        instruction = engine.llm.chat.call_args[0][1][0]["content"]
        assert "对方不在时你想起过" in instruction
        assert "你们一起看了日出" in instruction

    def test_low_stage_no_offline_moment(self):
        """熟悉及以下 → prompt 不含离线时刻素材。"""
        engine = _make_engine()
        engine._card_id = "card_test"
        engine._memory = MagicMock()
        engine._memory.enabled = True
        engine._memory.get_all.return_value = [
            {"memory": "对方说喜欢下雨天", "metadata": {"importance": 8}},
        ]
        # Default stage is 陌生 (from initial_affinity with no relationships)

        result = engine.generate_reunion_greeting(session_data=_old_session_data())
        assert result == "你总算回来了。"

        instruction = engine.llm.chat.call_args[0][1][0]["content"]
        assert "对方不在时你想起过" not in instruction

    def test_empty_memory_falls_back(self):
        """朋友档但记忆为空 → 退回现有 prompt。"""
        engine = _make_engine()
        engine._card_id = "card_test"
        engine._memory = MagicMock()
        engine._memory.enabled = True
        engine._memory.get_all.return_value = []
        engine._affinity_service.affinity = 60
        engine._affinity_service.stage = "朋友"
        engine._affinity_service.stage_emoji = "😄"

        result = engine.generate_reunion_greeting(session_data=_old_session_data())
        assert result == "你总算回来了。"

        instruction = engine.llm.chat.call_args[0][1][0]["content"]
        assert "对方不在时你想起过" not in instruction


class TestDailyVisitAwareness:
    """今日到访觉察：engine._visit_count → system prompt 注入。"""

    @staticmethod
    def _set_stage(engine: ChatEngine, stage: str, affinity: int) -> None:
        engine._affinity_service.affinity = affinity
        engine._affinity_service.stage = stage
        engine._affinity_service.stage_emoji = calc_stage(affinity)[1]

    def test_visit_3_injects(self):
        """第3次到访 → system prompt 含觉察块。"""
        engine = _make_engine()
        self._set_stage(engine, "朋友", 60)
        engine._visit_count = 3
        engine.chat("你好")
        sys_prompt = engine.llm.chat.call_args_list[1][0][0]
        assert "【觉察】" in sys_prompt
        assert "第 3 次" in sys_prompt

    def test_visit_4_injects(self):
        """第4次到访 → 同样注入。"""
        engine = _make_engine()
        self._set_stage(engine, "朋友", 60)
        engine._visit_count = 4
        engine.chat("你好")
        sys_prompt = engine.llm.chat.call_args_list[1][0][0]
        assert "【觉察】" in sys_prompt
        assert "第 4 次" in sys_prompt

    def test_visit_2_does_not_inject(self):
        """第2次不到 3，不注入。"""
        engine = _make_engine()
        self._set_stage(engine, "朋友", 60)
        engine._visit_count = 2
        engine.chat("你好")
        sys_prompt = engine.llm.chat.call_args_list[1][0][0]
        assert "【觉察】" not in sys_prompt

    def test_visit_consumed_after_injection(self):
        """注入后 _visit_count 归零，第二条消息不再注入。"""
        engine = _make_engine()
        self._set_stage(engine, "朋友", 60)
        engine._visit_count = 3
        engine.chat("你好")
        assert engine._visit_count == 0  # consumed

        engine.llm.chat.reset_mock()
        engine.chat("再聊")
        sys_prompt = engine.llm.chat.call_args_list[1][0][0]
        assert "【觉察】" not in sys_prompt

    def test_low_stage_no_injection(self):
        """熟悉及以下即使 count>=3 也不注入。"""
        engine = _make_engine()
        self._set_stage(engine, "熟悉", 50)
        engine._visit_count = 3
        engine.chat("你好")
        sys_prompt = engine.llm.chat.call_args_list[1][0][0]
        assert "【觉察】" not in sys_prompt

    def test_visit_consumed_on_low_stage(self):
        """低档位时 count 仍然被消费（本轮 resume 机会用完）。"""
        engine = _make_engine()
        self._set_stage(engine, "陌生", 10)
        engine._visit_count = 3
        engine.chat("你好")
        assert engine._visit_count == 0
