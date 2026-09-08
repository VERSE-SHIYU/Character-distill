"""Tests for auto_review_card, auto_review_split and _flatten_card."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.moderation.auto_review import _flatten_card, auto_review_card, auto_review_split


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


class TestAutoReviewSplit:
    """auto_review_split is the two-channel review used at market publish.

    Content channel keeps fail-open semantics; injection channel must fail
    *to the manual queue* (error=True) rather than silently pass.
    """

    async def test_both_pass(self):
        mock_llm = AsyncMock()
        mock_llm.achat.return_value = '{"content_pass": true, "content_reason": "", "injection_pass": true, "injection_reason": ""}'
        result = await auto_review_split({"name": "合规角色"}, llm=mock_llm)
        assert result["content"]["pass"] is True
        assert result["injection"]["pass"] is True
        assert result["injection"]["error"] is False

    async def test_injection_detected(self):
        mock_llm = AsyncMock()
        mock_llm.achat.return_value = '{"content_pass": true, "content_reason": "", "injection_pass": false, "injection_reason": "包含忽略以上设定"}'
        result = await auto_review_split({"name": "注入卡"}, llm=mock_llm)
        assert result["content"]["pass"] is True
        assert result["injection"]["pass"] is False
        assert "忽略以上设定" in result["injection"]["reason"]

    async def test_content_violation(self):
        mock_llm = AsyncMock()
        mock_llm.achat.return_value = '{"content_pass": false, "content_reason": "色情", "injection_pass": true, "injection_reason": ""}'
        result = await auto_review_split({"name": "违规卡"}, llm=mock_llm)
        assert result["content"]["pass"] is False
        assert result["injection"]["pass"] is True

    async def test_llm_error_fails_content_open_but_flags_injection(self):
        mock_llm = AsyncMock()
        mock_llm.achat.side_effect = RuntimeError("LLM unavailable")
        result = await auto_review_split({"name": "测试"}, llm=mock_llm)
        # content: fail-open (unchanged)
        assert result["content"]["pass"] is True
        # injection: fail-to-flag (never silent pass)
        assert result["injection"]["error"] is True
        assert result["injection"]["pass"] is False

    async def test_bad_json_flags_injection(self):
        mock_llm = AsyncMock()
        mock_llm.achat.return_value = "not json"
        result = await auto_review_split({"name": "测试"}, llm=mock_llm)
        assert result["content"]["pass"] is True
        assert result["injection"]["error"] is True

    async def test_llm_none_flags_injection(self):
        with patch("deps.get_llm", return_value=None):
            result = await auto_review_split({"name": "测试"}, llm=None)
        assert result["content"]["pass"] is True
        assert result["injection"]["pass"] is False
        assert result["injection"]["error"] is True
