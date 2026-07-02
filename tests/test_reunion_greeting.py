"""Tests for ChatEngine.generate_reunion_greeting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from core.chat_engine import ChatEngine, _reunion_dates
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
