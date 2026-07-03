"""Tests for 队列6 — 口癖磨损与感染 (catchword extraction + injection)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from core.chat_engine import ChatEngine
from core.affinity_service import AffinityService
from core.schema import CharacterCard


def _make_engine() -> ChatEngine:
    """Minimal engine with mocked deps."""
    card = CharacterCard(name="测试角色", identity="一个温柔的人")
    llm = MagicMock()
    llm.chat.return_value = "好的。"
    llm.last_usage = {}
    rag = MagicMock()
    engine = ChatEngine(llm=llm, rag=rag, card=card)
    engine._session_id = "test-session"
    engine._storage = MagicMock()
    return engine


def _fill_history(engine: ChatEngine, user_msgs: list[str]) -> None:
    """Fill engine history with alternating user/assistant messages."""
    engine.history.clear()
    for i, msg in enumerate(user_msgs):
        engine.history.append({"role": "user", "content": msg})
        engine.history.append({"role": "assistant", "content": f"resp_{i}"})


def _set_stage(engine: ChatEngine, stage: str, affinity: int) -> None:
    """Set affinity stage directly."""
    engine._affinity_service.affinity = affinity
    engine._affinity_service.stage = stage
    from core.affinity_service import calc_stage
    engine._affinity_service.stage_emoji = calc_stage(affinity)[1]


class TestCatchwordExtraction:
    """_extract_catchwords() — 中文分词 + 停用词过滤 + 频次阈值 + top N。"""

    def test_single_char_filtered(self):
        """单字词不提取（len<2）。"""
        engine = _make_engine()
        msgs = ["好", "的", "了"] * 10
        _fill_history(engine, msgs)
        engine._extract_catchwords()
        assert engine._affinity_service.user_catchwords == []

    def test_stop_words_filtered(self):
        """停用词表中的词（含双字虚词）不提取。"""
        engine = _make_engine()
        msgs = ["什么"] * 5 + ["怎么"] * 5 + ["但是"] * 5
        _fill_history(engine, msgs)
        engine._extract_catchwords()
        assert engine._affinity_service.user_catchwords == []

    def test_chinese_catchphrase_extracted(self):
        """连续中文句中 jieba 正确切出"真的"等口头禅。"""
        engine = _make_engine()
        # jieba.lcut('真的假的你也太牛了真的服了')
        # → ['真的假', '的', '你', '也', '太', '牛', '了', '真的', '服', '了']
        # "真的" appears as standalone word once per message
        msg = "真的假的你也太牛了真的服了"
        _fill_history(engine, [msg] * 5)
        engine._extract_catchwords()
        assert "真的" in engine._affinity_service.user_catchwords

    def test_continuous_text_does_not_become_token(self):
        """整句连续中文不被当作一个词，jieba 正确切分。"""
        engine = _make_engine()
        # jieba.lcut('没事没事真的没事')
        # → ['没事', '没事', '真的', '没事']
        msg = "没事没事真的没事"
        _fill_history(engine, [msg] * 5)
        engine._extract_catchwords()
        # "没事" should appear as a word, and it has count >= 4
        assert "没事" in engine._affinity_service.user_catchwords
        # The full sentence should NOT appear as a catchword
        assert msg not in engine._affinity_service.user_catchwords

    def test_below_threshold_not_extracted(self):
        """频次 <4 → 不提取。"""
        engine = _make_engine()
        msg = "真的麻烦你了"
        _fill_history(engine, [msg] * 3)
        engine._extract_catchwords()
        assert engine._affinity_service.user_catchwords == []

    def test_above_threshold_extracted(self):
        """刚好 4 次 → 提取。"""
        engine = _make_engine()
        msg = "真的麻烦你了"
        _fill_history(engine, [msg] * 4)
        engine._extract_catchwords()
        assert "真的" in engine._affinity_service.user_catchwords or \
               "麻烦" in engine._affinity_service.user_catchwords

    def test_only_top_2_words(self):
        """最多返回 top 2。"""
        engine = _make_engine()
        msgs = (["真的假的"] * 5 + ["没事没事"] * 5 + ["谢谢谢谢"] * 5 +
                ["知道知道"] * 5 + ["不行不行"] * 5)
        _fill_history(engine, msgs)
        engine._extract_catchwords()
        assert len(engine._affinity_service.user_catchwords) <= 2

    def test_pure_numbers_excluded(self):
        """纯数字 token 如 "666666" 不提取。"""
        engine = _make_engine()
        msgs = ["666666"] * 5 + ["888888"] * 5
        _fill_history(engine, msgs)
        engine._extract_catchwords()
        assert engine._affinity_service.user_catchwords == []

    def test_english_tokens_preserved(self):
        """英文词保持提取能力。"""
        engine = _make_engine()
        msgs = ["lol"] * 5
        _fill_history(engine, msgs)
        engine._extract_catchwords()
        assert "lol" in engine._affinity_service.user_catchwords

    def test_old_words_beyond_20_not_counted(self):
        """超出最近 20 条 user 消息的历史词不被计入。"""
        engine = _make_engine()
        history = []
        for i in range(10):
            history.append({"role": "user", "content": "旧词"})
            history.append({"role": "assistant", "content": f"r{i}"})
        for i in range(20):
            word = "不行" if i < 4 else "放心"  # jieba keeps both as 2-char words
            history.append({"role": "user", "content": word})
            history.append({"role": "assistant", "content": f"r{i+10}"})
        engine.history = history
        engine._extract_catchwords()
        # "旧词" appeared 10 times but only in first 10 msgs (outside 20-window)
        assert "旧词" not in engine._affinity_service.user_catchwords
        # "不行" appeared 4 times in last 20
        assert "不行" in engine._affinity_service.user_catchwords

    def test_mixed_chinese_english(self):
        """中英混合消息中各自正确分词。"""
        engine = _make_engine()
        msgs = ["lol 真的服了"] * 5
        _fill_history(engine, msgs)
        engine._extract_catchwords()
        assert "lol" in engine._affinity_service.user_catchwords
        assert "真的" in engine._affinity_service.user_catchwords


class TestCatchphraseBlock:
    """_build_catchphrase_block() — 档位门控 + 有词注入。"""

    def test_low_stage_no_injection(self):
        """陌生/认识/熟悉 → 不注入。"""
        engine = _make_engine()
        engine._affinity_service.user_catchwords = ["好的"]
        for stage, aff in [("陌生", 10), ("认识", 25), ("熟悉", 45)]:
            _set_stage(engine, stage, aff)
            block = engine._build_catchphrase_block()
            assert block == "", f"Stage {stage} should not inject"

    def test_friendly_stage_no_injection(self):
        """朋友档（低于亲近）→ 不注入。"""
        engine = _make_engine()
        _set_stage(engine, "朋友", 65)
        engine._affinity_service.user_catchwords = ["好的"]
        assert engine._build_catchphrase_block() == ""

    def test_close_stage_injects(self):
        """亲近档 + 有词 → 注入。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)
        engine._affinity_service.user_catchwords = ["好的"]
        block = engine._build_catchphrase_block()
        assert "【口癖渗透】" in block
        assert "好的" in block
        assert "十次里最多一次" in block

    def test_intimate_stage_injects(self):
        """心意相通档 + 有词 → 注入。"""
        engine = _make_engine()
        _set_stage(engine, "心意相通", 95)
        engine._affinity_service.user_catchwords = ["真的", "没事"]
        block = engine._build_catchphrase_block()
        assert "【口癖渗透】" in block
        assert "真的" in block
        assert "没事" in block

    def test_empty_catchwords_no_injection(self):
        """有档位但 catchwords 为空 → 不注入。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)
        engine._affinity_service.user_catchwords = []
        assert engine._build_catchphrase_block() == ""

    def test_injected_in_enhancements(self):
        """_build_all_enhancements 包含口癖块（亲近档+有词）。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)
        engine._affinity_service.user_catchwords = ["好的"]
        enhancements = engine._build_all_enhancements()
        assert "【口癖渗透】" in enhancements
        assert "好的" in enhancements


class TestAffinityCompat:
    """旧存档无 user_catchwords 字段不报错。"""

    def test_load_without_catchwords(self):
        """旧数据缺 user_catchwords → 默认空列表。"""
        svc = AffinityService()
        svc.load({
            "affinity": 60,
            "trust": 40,
            "mood": "开心",
            "guard": 30,
            "reason": json.dumps({
                "inner_voice": "很开心",
                "mood_emoji": "😊",
                "mood_word": "开心",
                "stage": "朋友",
                "stage_emoji": "😄",
            }),
        })
        assert svc.user_catchwords == []

    def test_load_with_catchwords(self):
        """新数据含 user_catchwords → 正确加载。"""
        svc = AffinityService()
        svc.load({
            "affinity": 80,
            "trust": 70,
            "mood": "亲近",
            "guard": 15,
            "reason": json.dumps({
                "inner_voice": "好亲近",
                "mood_emoji": "🥰",
                "mood_word": "亲近",
                "stage": "亲近",
                "stage_emoji": "🥰",
                "user_catchwords": ["好的", "没事"],
            }),
        })
        assert svc.user_catchwords == ["好的", "没事"]

    def test_get_includes_catchwords(self):
        """get() 返回 user_catchwords。"""
        svc = AffinityService()
        svc.user_catchwords = ["真的"]
        data = svc.get()
        assert data.get("user_catchwords") == ["真的"]

    def test_apply_evaluation_serializes_catchwords(self):
        """apply_evaluation 生成的 affinity_reason 含 user_catchwords。"""
        svc = AffinityService()
        svc.user_catchwords = ["好的"]
        data = {
            "affinity": 60, "trust": 50, "mood": "开心",
            "guard": 30, "inner_voice": "不错", "mood_emoji": "😊",
            "importance": 5,
        }
        svc.apply_evaluation(data, "朋友")
        parsed = json.loads(svc.affinity_reason)
        assert parsed.get("user_catchwords") == ["好的"]

    def test_plain_text_reason_compat(self):
        """旧格式纯文本 reason（非 JSON）→ user_catchwords 默认空列表。"""
        svc = AffinityService()
        svc.load({
            "affinity": 50, "trust": 30, "mood": "平静", "guard": 70,
            "reason": "纯文本旧数据，没有 JSON 扩展字段",
        })
        assert svc.user_catchwords == []


class TestReflectionReturnType:
    """maybe_reflect 返回 bool 兼容性。"""

    def test_maybe_reflect_returns_bool(self):
        """ReflectionService.maybe_reflect 返回 bool 值。"""
        from core.reflection_service import ReflectionService
        svc = ReflectionService(None, "")
        result = svc.maybe_reflect(1, None, "")
        assert isinstance(result, bool)
        # Without memory and proper setup, should return False
        assert result is False
