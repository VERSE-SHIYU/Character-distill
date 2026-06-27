"""回归测试：VAD 情感亲和度 + 两级门控评分结构。"""

from __future__ import annotations

import pytest

from core.memory_manager import (
    _lookup_vad, _emotion_affinity, _emotion_match, _get_emotion_polarity,
    RERANK_ALPHA, RERANK_BETA, RERANK_GAMMA, RERANK_LAMBDA,
)


class TestLookupVAD:
    """VAD 查表纯函数回归。"""

    def test_known_word(self):
        v, a = _lookup_vad("开心")
        assert v == pytest.approx(0.8, abs=0.01)
        assert a == pytest.approx(0.7, abs=0.01)

    def test_compound_mood_average(self):
        """复合情绪取匹配词的平均 VAD。"""
        v, a = _lookup_vad("开心又有点生气")
        # 开心(0.8,0.7) + 生气(-0.7,0.7) → 平均
        assert v == pytest.approx(0.05, abs=0.01)
        assert a == pytest.approx(0.70, abs=0.01)

    def test_empty_mood(self):
        assert _lookup_vad("") == (0.0, 0.5)
        assert _lookup_vad(None) == (0.0, 0.5)

    def test_vad_lookup_known_word(self):
        """VAD 表中已知词直接返回映射值。"""
        v, a = _lookup_vad("窃喜")
        assert v == pytest.approx(0.6, abs=0.01)
        assert a == pytest.approx(0.4, abs=0.01)

    def test_fallback_when_no_vad_match(self):
        """不在 VAD 表的词按极性回退。"""
        v, a = _lookup_vad("wtf")  # 无任何子串匹配,极性中性(0)→(0.0,0.5)
        assert v == pytest.approx(0.0, abs=0.01)
        assert a == pytest.approx(0.5, abs=0.01)


class TestEmotionAffinity:
    """VAD 情感亲和度回归。"""

    def test_same_mood_is_1(self):
        assert _emotion_affinity("开心", "开心") == pytest.approx(1.0, abs=0.01)

    def test_opposite_moods_low(self):
        aff = _emotion_affinity("开心", "愤怒")
        assert aff < 0.5  # 明显不同

    def test_none_mood_returns_0_5(self):
        assert _emotion_affinity(None, "开心") == 0.5
        assert _emotion_affinity(None, "") == 0.5

    def test_vad_distinguishes_shiran_vs_qiexi(self):
        """VAD 区分释然(0.5,0.1) 与 窃喜(0.6,0.4)。"""
        same = _emotion_affinity("释然", "释然")
        diff = _emotion_affinity("释然", "窃喜")
        assert same > diff  # 同词比异词高
        # 两者 valence 相近(0.5 vs 0.6)，arousal 不同(0.1 vs 0.4)
        assert diff < 0.95  # 有区分度

    def test_arousal_difference_matters(self):
        """相同极性但唤醒度不同时亲和度不同。"""
        # 兴奋(0.8,0.9) vs 释然(0.5,0.1)：arousal 差距大
        aff_low = _emotion_affinity("释然", "兴奋")
        # 兴奋(0.8,0.9) vs 开心(0.8,0.7)：较接近
        aff_high = _emotion_affinity("开心", "兴奋")
        assert aff_low < aff_high

    def test_memory_mood_empty(self):
        """记忆 mood 为空时空字符串，不影响。"""
        aff = _emotion_affinity("开心", "")
        # 空→(0,0.5) vs 开心→(0.8,0.7)
        assert 0.2 < aff < 0.8


class TestMultiplicativeGate:
    """两级门控评分结构验证。"""

    def test_irrelevant_memory_not_boosted_by_emotion(self):
        """语义无关(rel≈0)的记忆不会被情绪抬高。"""
        base = RERANK_ALPHA * 0.0 + RERANK_BETA * 0.5 + RERANK_GAMMA * 0.5
        # 即使情绪极度匹配
        final_high = base * (1 + RERANK_LAMBDA * 1.0)
        final_low = base * (1 + RERANK_LAMBDA * 0.0)
        # 差距很小: 1.25× vs 1.0×, 绝对值仍低
        assert final_high <= 0.25  # base=0.2, 1.25×=0.25, 仍被压住

    def test_semantic_relevant_emotion_highlight(self):
        """语义相关(rel高)且情绪匹配的排在情绪不匹配的前面。"""
        rel_norm = 0.9
        base = RERANK_ALPHA * rel_norm + RERANK_BETA * 0.5 + RERANK_GAMMA * 0.5

        final_match = base * (1 + RERANK_LAMBDA * 1.0)
        final_mismatch = base * (1 + RERANK_LAMBDA * 0.0)

        assert final_match > final_mismatch  # 情绪匹配的更高
        assert final_match <= base * 1.25  # 有界

    def test_lambda_bounded_amplification(self):
        """情绪乘性增益有界: final ≤ base × 1.25。"""
        base = 0.5
        max_final = base * (1 + RERANK_LAMBDA * 1.0)
        min_final = base * (1 + RERANK_LAMBDA * 0.0)
        assert max_final == base * 1.25
        assert min_final == base
        assert 0.0 <= _emotion_affinity(None, "开心") <= 1.0  # emo_affinity 有界

    def test_emotion_affinity_bounded(self):
        for mood in ["开心", "愤怒", "释然", "窃喜", "心碎", "兴奋"]:
            aff = _emotion_affinity(mood, mood)
            assert 0.0 <= aff <= 1.0
        aff = _emotion_affinity("开心", "愤怒")
        assert 0.0 <= aff <= 1.0
