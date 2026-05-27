"""Tests for auto_review_card and _flatten_card."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.moderation.auto_review import _flatten_card, auto_review_card


class TestFlattenCard:
    """_flatten_card converts card JSON dict to reviewable text."""

    def test_flatten_basic_fields(self):
        card = {
            "name": "张三",
            "identity": "武侠",
            "background": "生于武林世家",
            "personality": "豪爽",
        }
        text = _flatten_card(card)
        assert "name: 张三" in text
        assert "identity: 武侠" in text
        assert "background: 生于武林世家" in text
        assert "personality: 豪爽" in text

    def test_flatten_list_fields(self):
        card = {
            "name": "李四",
            "personality_traits": ["勇敢", "正直"],
            "values": ["正义", "忠诚"],
        }
        text = _flatten_card(card)
        assert "勇敢" in text
        assert "正直" in text
        assert "正义" in text

    def test_flatten_dict_fields(self):
        card = {
            "name": "王五",
            "inner_tensions": {"conflict": "善恶之间", "resolution": "未解"},
        }
        text = _flatten_card(card)
        assert "conflict" in text
        assert "善恶之间" in text

    def test_flatten_empty_card(self):
        text = _flatten_card({})
        assert text == "{}"

    def test_flatten_partial(self):
        card = {"name": "赵六"}
        text = _flatten_card(card)
        assert "name: 赵六" in text


class TestAutoReviewCard:
    """auto_review_card calls LLM and parses the JSON response."""

    async def test_pass(self):
        mock_llm = AsyncMock()
        mock_llm.achat.return_value = '{"pass": true, "reason": ""}'
        result = await auto_review_card(
            {"name": "合规角色", "personality": "友善"},
            llm=mock_llm,
        )
        assert result["pass"] is True
        assert result["reason"] == ""

    async def test_reject(self):
        mock_llm = AsyncMock()
        mock_llm.achat.return_value = '{"pass": false, "reason": "包含暴力内容"}'
        result = await auto_review_card(
            {"name": "暴力角色", "personality": "残忍"},
            llm=mock_llm,
        )
        assert result["pass"] is False
        assert "暴力" in result["reason"]

    async def test_fails_open_on_llm_error(self):
        mock_llm = AsyncMock()
        mock_llm.achat.side_effect = RuntimeError("LLM unavailable")
        result = await auto_review_card(
            {"name": "测试"},
            llm=mock_llm,
        )
        assert result["pass"] is True
        assert result["reason"] == ""

    async def test_fails_open_on_bad_json(self):
        mock_llm = AsyncMock()
        mock_llm.achat.return_value = "not json at all"
        result = await auto_review_card(
            {"name": "测试"},
            llm=mock_llm,
        )
        assert result["pass"] is True
        assert result["reason"] == ""

    async def test_fails_open_when_llm_is_none(self):
        result = await auto_review_card(
            {"name": "测试"},
            llm=None,
        )
        assert result["pass"] is True
        assert result["reason"] == ""

    async def test_llm_passed_flattened_text(self):
        mock_llm = AsyncMock()
        mock_llm.achat.return_value = '{"pass": true, "reason": ""}'
        await auto_review_card(
            {"name": "测试角色", "identity": "法师", "background": "来自魔法世界"},
            llm=mock_llm,
        )
        # Verify the LLM received the flattened card
        call_args = mock_llm.achat.call_args
        assert call_args is not None
        messages = call_args[0][1]
        user_content = messages[0]["content"]
        assert "测试角色" in user_content
        assert "法师" in user_content
        assert "魔法世界" in user_content
