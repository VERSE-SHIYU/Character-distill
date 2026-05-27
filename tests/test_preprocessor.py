"""Tests for ChatPreprocessor three-layer pipeline."""

from core.chat_preprocessor import ChatPreprocessor


PREPROC = ChatPreprocessor()


class TestLayer0FormatClean:
    """格式清洗：系统消息、媒体标记、纯标点、短行去重。"""

    def test_remove_system_message(self):
        text = "[2024-01-01] 张三: 你好\n[2024-01-01] 张三: 撤回了一条消息"
        result = PREPROC._layer0_format_clean(text)
        assert "[2024-01-01] 张三: 你好" in result
        assert "撤回了一条消息" not in result

    def test_remove_media_only_line(self):
        text = "[2024-01-01] 张三: [图片]\n[2024-01-01] 李四: 好的"
        result = PREPROC._layer0_format_clean(text)
        assert "[图片]" not in result
        assert "李四: 好的" in result

    def test_strip_media_markers_from_content(self):
        text = "[2024-01-01] 张三: 你看这个[图片]怎么样"
        result = PREPROC._layer0_format_clean(text)
        assert "[图片]" not in result
        assert "张三: 你看这个怎么样" in result

    def test_remove_punctuation_only(self):
        text = "[2024-01-01] 张三: 你好\n。。。\n[2024-01-01] 李四: 在吗"
        result = PREPROC._layer0_format_clean(text)
        assert "你好" in result
        assert "。。。" not in result
        assert "在吗" in result

    def test_remove_short_lines(self):
        text = "[2024-01-01] 张三: 你好\nx\n[2024-01-01] 李四: 在吗"
        result = PREPROC._layer0_format_clean(text)
        assert "你好" in result
        assert "\nx\n" not in result
        assert "在吗" in result

    def test_remove_blank_lines(self):
        text = "[2024-01-01] 张三: 你好\n\n[2024-01-01] 李四: 在吗"
        result = PREPROC._layer0_format_clean(text)
        assert result == "[2024-01-01] 张三: 你好\n[2024-01-01] 李四: 在吗"

    def test_non_standard_format_preserved(self):
        text = "一些杂散文字不属于标准消息格式"
        result = PREPROC._layer0_format_clean(text)
        assert text in result

    def test_empty_input(self):
        assert PREPROC._layer0_format_clean("") == ""


class TestLayer1QualityFilter:
    """质量过滤：保留有人格信息的消息。"""

    def test_long_message_kept(self):
        text = "[2024-01-01] 张三: 我今天去了一个特别好的地方，推荐给你"
        result = PREPROC._layer1_quality_filter(text)
        assert "推荐给你" in result

    def test_short_transactional_removed(self):
        text = "[2024-01-01] 张三: 好的\n[2024-01-01] 李四: 今天天气真好"
        result = PREPROC._layer1_quality_filter(text)
        assert "好的" not in result
        assert "今天天气真好" in result

    def test_opinion_marker_kept(self):
        text = "[2024-01-01] 张三: 我觉得这是个好主意"
        result = PREPROC._layer1_quality_filter(text)
        assert "我觉得" in result

    def test_qa_pair_kept(self):
        text = "[2024-01-01] 李四: 你去吗？\n[2024-01-01] 张三: 去"
        result = PREPROC._layer1_quality_filter(text)
        assert "你去吗" in result
        # "去" is short but kept because it's the next message after a question
        assert "去" in result

    def test_transactional_opinion_overrides(self):
        """Transactional but with opinion marker is still kept."""
        text = "[2024-01-01] 张三: 我觉得好的"  # 4 chars but has opinion
        result = PREPROC._layer1_quality_filter(text)
        assert "好的" in result

    def test_unparseable_line_skipped(self):
        text = "[2024-01-01] 张三: 今天天气真好\n这是一行不标准的格式\n[2024-01-01] 李四: 我也觉得非常好"
        result = PREPROC._layer1_quality_filter(text)
        assert "今天天气真好" in result
        assert "这是一行不标准的格式" not in result
        assert "我也觉得非常好" in result

    def test_empty_input(self):
        assert PREPROC._layer1_quality_filter("") == ""


class TestLayer2CharacterContext:
    """角色上下文提取。"""

    def test_target_own_lines_with_context(self):
        text = (
            "[2024-01-01] A: 第一句\n"
            "[2024-01-01] B: 第二句\n"
            "[2024-01-01] C: 第三句\n"
            "[2024-01-01] 张三: 我是目标\n"
            "[2024-01-01] D: 第五句\n"
            "[2024-01-01] E: 第六句\n"
            "[2024-01-01] F: 第七句"
        )
        result = PREPROC._layer2_character_context(text, "张三")
        # ±3 around index 3 → indices 0-6 all kept
        assert "第一句" in result
        assert "我是目标" in result
        assert "第七句" in result

    def test_others_mentioning_target(self):
        text = (
            "[2024-01-01] A: 今天天气不错\n"
            "[2024-01-01] B: 你见到张三了吗\n"
            "[2024-01-01] C: 见到了"
        )
        result = PREPROC._layer2_character_context(text, "张三")
        # B mentions "张三" → keep B + ±1 context: A and C
        assert "今天天气不错" in result
        assert "你见到张三了吗" in result
        assert "见到了" in result

    def test_empty_target_returns_original(self):
        text = "[2024-01-01] 张三: 你好"
        assert PREPROC._layer2_character_context(text, "") == text

    def test_no_match_returns_empty(self):
        text = "[2024-01-01] A: 你好\n[2024-01-01] B: 在吗"
        result = PREPROC._layer2_character_context(text, "张三")
        assert result == ""

    def test_overlapping_contexts_merged(self):
        """When two relevant lines are close, their contexts should merge."""
        lines = [
            "[2024-01-01] A: filler",
            "[2024-01-01] B: 张三在这",
            "[2024-01-01] C: 中间行",
            "[2024-01-01] 张三: 我也在",
            "[2024-01-01] D: after",
        ]
        text = "\n".join(lines)
        result = PREPROC._layer2_character_context(text, "张三")
        # B mentions target → keep B + ±1 → A, B, C
        # 张三 speaks → keep ±3 → B, C, 张三, D
        # Merged: A, B, C, 张三, D
        assert "filler" in result
        assert "我也在" in result
        assert "after" in result

    def test_non_msg_lines_included_as_context(self):
        """Non-standard lines between messages should still be kept as context."""
        text = (
            "[2024-01-01] A: 前面\n"
            "---场景分割线---\n"
            "[2024-01-01] 张三: 我的发言"
        )
        result = PREPROC._layer2_character_context(text, "张三")
        assert "前面" in result
        assert "---场景分割线---" in result
        assert "我的发言" in result

    def test_similar_speaker_name(self):
        """Speaker '张三丰' (not equal to '张三') is kept via content match rule.

        The line '我是张三丰' contains the substring '张三', so it's kept
        as "others mentioning the target", not as self-speech.
        """
        text = (
            "[2024-01-01] 张三丰: 我是张三丰\n"
            "[2024-01-01] A: 你好"
        )
        result = PREPROC._layer2_character_context(text, "张三")
        # content "我是张三丰" contains substring "张三" → kept
        assert "我是张三丰" in result
