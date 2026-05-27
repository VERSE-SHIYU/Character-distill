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
