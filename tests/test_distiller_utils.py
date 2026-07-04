"""Tests for Distiller pure functions: _extract_json, _split_chunks, _split_chunks_chat."""

from core.distiller import Distiller


# ── _extract_json ────────────────────────────────────────────────────────────

class TestExtractJson:
    def test_plain_json(self):
        assert Distiller._extract_json('{"a": 1}') == '{"a": 1}'

    def test_markdown_json(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert Distiller._extract_json(raw) == '{"a": 1}'

    def test_markdown_no_tag(self):
        raw = "```\n{\"a\": 1}\n```"
        assert Distiller._extract_json(raw) == '{\"a\": 1}'

    def test_prefix_suffix_text(self):
        raw = "前面文字{\"a\": 1}后面文字"
        assert Distiller._extract_json(raw) == '{"a": 1}'

    def test_multiple_braces(self):
        """Only the outermost braces are captured."""
        raw = '{"outer": {"inner": 1}}'
        assert Distiller._extract_json(raw) == '{"outer": {"inner": 1}}'

    def test_no_braces_returns_original(self):
        raw = "不是JSON"
        assert Distiller._extract_json(raw) == "不是JSON"

    def test_empty_string(self):
        assert Distiller._extract_json("") == ""

    def test_markdown_preferred_over_brace_fallback(self):
        """If both markdown block and raw braces exist, markdown wins."""
        raw = "```\n{\"from\": \"block\"}\n```\n{\"from\": \"brace\"}"
        assert Distiller._extract_json(raw) == '{"from": "block"}'


# ── _split_chunks ────────────────────────────────────────────────────────────

class TestSplitChunks:
    def test_normal_paragraphs(self):
        """Multiple paragraphs merged until chunk_size is exceeded."""
        text = "\n\n".join(["A" * 5, "B" * 5, "C" * 5, "D" * 5])
        result = Distiller._split_chunks(text, 20)
        # 5 + \\n\\n + 5 + \\n\\n + 5 = 19 ≤ 20; adding "D"*5 would exceed
        assert result == ["A" * 5 + "\n\n" + "B" * 5 + "\n\n" + "C" * 5, "D" * 5]

    def test_single_short_paragraph(self):
        text = "Hello World"
        result = Distiller._split_chunks(text, 100)
        assert result == [text]

    def test_empty_text(self):
        result = Distiller._split_chunks("", 100)
        assert result == []

    def test_oversize_paragraph_line_split(self):
        """Paragraph exceeding chunk_size: split by newlines first."""
        para = "A" * 30 + "\n" + "B" * 30
        text = "start\n\n" + para + "\n\n" + "end"
        result = Distiller._split_chunks(text, 25)
        # "start" flushed, then "A"*30 split by char, then "B"*30 split by char, then "end"
        assert result == [
            "start",
            "A" * 25,
            "A" * 5,
            "B" * 25,
            "B" * 5,
            "end",
        ]

    def test_oversize_line_char_split(self):
        """Single line exceeding chunk_size: character-level split."""
        line = "X" * 100
        result = Distiller._split_chunks(line, 30)
        assert result == ["X" * 30, "X" * 30, "X" * 30, "X" * 10]

    def test_chunk_boundary_exact(self):
        """Text that exactly fills chunk_size should not split."""
        para = "A" * 18  # 18 chars
        text = para + "\n\n" + "B" * 3  # 18 + 2 + 3 = 23
        result = Distiller._split_chunks(text, 23)
        assert result == [text]


# ── _split_chunks_chat ───────────────────────────────────────────────────────

class TestSplitChunksChat:
    def test_group_by_date(self):
        text = "[2024-01-01] A: hello\n[2024-01-01] B: hi\n[2024-01-02] A: morning"
        result = Distiller._split_chunks_chat(text, 1000)
        assert result == [
            "[2024-01-01] A: hello\n[2024-01-01] B: hi",
            "[2024-01-02] A: morning",
        ]

    def test_oversize_day(self):
        """Single day exceeding chunk_size is split into sub-chunks."""
        lines = "\n".join(f"[2024-01-01] A: msg_{i}" for i in range(20))
        # Each line ~20 chars, total ~400+ chars
        result = Distiller._split_chunks_chat(lines, 100)
        # Should split into multiple chunks without breaking individual lines
        assert len(result) > 1
        # Verify no line is split mid-message
        for chunk in result:
            for line in chunk.split("\n"):
                assert line.startswith("[2024-01-01]") or line == ""
                assert len(line) <= 100

    def test_no_date_prefix(self):
        """Lines without [YYYY-MM-DD] prefix all go to one group."""
        text = "A: hello\nB: world"
        result = Distiller._split_chunks_chat(text, 100)
        assert result == [text]

    def test_mixed_date_and_no_date(self):
        """Lines without date stay in the same day group as preceding dated line."""
        text = (
            "[2024-01-01] A: first\n"
            "B: reply without date\n"
            "[2024-01-02] C: next day"
        )
        result = Distiller._split_chunks_chat(text, 500)
        assert result == [
            "[2024-01-01] A: first\nB: reply without date",
            "[2024-01-02] C: next day",
        ]

    def test_empty_text(self):
        result = Distiller._split_chunks_chat("", 100)
        # Implementation: "".split("\n") → [""] → one empty group
        assert result == [""]

    def test_single_line(self):
        text = "[2024-01-01] A: lone message"
        result = Distiller._split_chunks_chat(text, 100)
        assert result == [text]


# ── identify_characters TTL cache ──────────────────────────────────────

import copy
import json
from unittest.mock import MagicMock

from core.distiller import (
    _IDENTIFY_CACHE,
    IDENTIFY_CACHE_MAX_ENTRIES,
    IDENTIFY_CACHE_TTL_SECONDS,
)


def _make_mock_llm(model="test-model", return_value=None, side_effect=None):
    llm = MagicMock()
    llm.model = model
    llm.chat = MagicMock(return_value=return_value, side_effect=side_effect)
    return llm


SAMPLE_TEXT = "张三是个沉默寡言的人。李四是他唯一的朋友。"
SAMPLE_RESULT = [
    {"name": "张三", "aliases": [], "importance": "主要", "reason": "有言行描写"},
    {"name": "李四", "aliases": [], "importance": "次要", "reason": "有对话"},
]


class TestIdentifyCharactersCache:
    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_same_text_caches_and_reuses(self):
        """同一文本调两次，LLM.chat 只调用 1 次，两次返回相等。"""
        llm = _make_mock_llm(return_value=json.dumps(SAMPLE_RESULT))
        d = Distiller(llm)

        r1 = d.identify_characters(SAMPLE_TEXT)
        r2 = d.identify_characters(SAMPLE_TEXT)

        assert llm.chat.call_count == 1
        assert r1 == r2 == SAMPLE_RESULT

    def test_cache_not_polluted_by_caller_mutation(self):
        """返回值原地修改后再查缓存，缓存内容不受污染。"""
        llm = _make_mock_llm(return_value=json.dumps(SAMPLE_RESULT))
        d = Distiller(llm)

        r1 = d.identify_characters(SAMPLE_TEXT)
        r1.append({"name": "污染", "aliases": []})

        r2 = d.identify_characters(SAMPLE_TEXT)
        assert len(r2) == 2
        assert r2 == SAMPLE_RESULT

    def test_different_text_different_miss(self):
        """不同文本各自独立 miss。"""
        llm = _make_mock_llm(return_value=json.dumps(SAMPLE_RESULT))
        d = Distiller(llm)

        d.identify_characters(SAMPLE_TEXT)
        d.identify_characters("完全不同的文本内容")

        assert llm.chat.call_count == 2

    def test_different_model_different_miss(self):
        """不同 model 各自独立 miss。"""
        text = SAMPLE_TEXT
        llm1 = _make_mock_llm(model="model-a", return_value=json.dumps(SAMPLE_RESULT))
        llm2 = _make_mock_llm(model="model-b", return_value=json.dumps(SAMPLE_RESULT))

        d1 = Distiller(llm1)
        d2 = Distiller(llm2)

        d1.identify_characters(text)
        d2.identify_characters(text)

        assert llm1.chat.call_count == 1
        assert llm2.chat.call_count == 1

    def test_llm_exception_not_cached(self):
        """LLM 抛异常时不写缓存，下次调用重新请求。"""
        llm = _make_mock_llm(side_effect=RuntimeError("LLM down"))
        d = Distiller(llm)

        try:
            d.identify_characters(SAMPLE_TEXT)
        except RuntimeError:
            pass
        try:
            d.identify_characters(SAMPLE_TEXT)
        except RuntimeError:
            pass

        assert llm.chat.call_count == 2

    def test_parse_failure_not_cached(self):
        """解析失败（非 JSON）不写缓存，下次调用重新请求。"""
        llm = _make_mock_llm(return_value="不是 JSON")
        d = Distiller(llm)

        result = d.identify_characters(SAMPLE_TEXT)
        assert result == []
        result2 = d.identify_characters(SAMPLE_TEXT)
        assert result2 == []
        assert llm.chat.call_count >= 2
