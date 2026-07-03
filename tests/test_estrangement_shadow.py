"""Tests for P1 影子模式 — Active Estrangement shadow judgment (record-only)."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from core.evaluation_pipeline import EvalResult
from core.affinity_service import AffinityService, calc_stage
from core.chat_engine import ChatEngine
from core.schema import CharacterCard, PsycheProfile


def _make_engine() -> ChatEngine:
    """Minimal engine with a character that has triggers defined."""
    card = CharacterCard(
        name="测试角色",
        identity="一个温柔的人",
        psyche=PsycheProfile(triggers=["被无视", "被欺骗"]),
    )
    llm = MagicMock()
    llm.chat.return_value = '{"affinity":50,"trust":30,"mood":"平静","guard":70,"inner_voice":"。","mood_emoji":"😊","importance":5}'
    llm.last_usage = {}
    rag = MagicMock()
    engine = ChatEngine(llm=llm, rag=rag, card=card)
    engine._session_id = "test-shadow"
    engine._storage = MagicMock()
    engine._affinity_service.clear_delta_ring()
    return engine


def _set_stage(engine: ChatEngine, stage: str, affinity: int) -> None:
    engine._affinity_service.affinity = affinity
    engine._affinity_service.stage = stage
    engine._affinity_service.stage_emoji = calc_stage(affinity)[1]


def _run_eval(engine: ChatEngine, data_override: dict | None = None) -> None:
    """Run a fake evaluation turn, filling ring buffer."""
    data = {
        "affinity": engine._affinity,
        "trust": engine._trust,
        "mood": "平静",
        "guard": engine._guard,
        "inner_voice": "。",
        "mood_emoji": "😊",
        "importance": 5,
        "in_character": 80,
        "assertion_confidence": 50,
        "trigger_hit": False,
        "in_story_conflict": False,
        "repair_signal": "",
    }
    if data_override:
        data.update(data_override)
    old_stage = engine._stage
    engine._affinity_service.apply_evaluation(data, old_stage)


# ─────────────────────────────────────────────
# EvalResult 向后兼容
# ─────────────────────────────────────────────


class TestEvalResultCompat:
    """EvalResult 新字段默认值兼容旧构造。"""

    def test_default_trigger_hit_is_false(self):
        result = EvalResult()
        assert result.trigger_hit is False

    def test_default_in_story_conflict_is_false(self):
        result = EvalResult()
        assert result.in_story_conflict is False

    def test_default_repair_signal_is_empty(self):
        result = EvalResult()
        assert result.repair_signal == ""

    def test_old_construction_still_works(self):
        """只传旧字段，新字段取默认值。"""
        result = EvalResult(importance=7, in_character=90, applied=True)
        assert result.importance == 7
        assert result.in_character == 90
        assert result.applied is True
        assert result.trigger_hit is False
        assert result.repair_signal == ""


# ─────────────────────────────────────────────
# 环形缓冲
# ─────────────────────────────────────────────


class TestDeltaRing:
    """8 轮环形缓冲滑窗正确。"""

    def test_max_8_entries(self):
        svc = AffinityService()
        for i in range(10):
            svc._record_delta_ring(
                affinity_delta=-i, trust_delta=0, guard_delta=0,
                trigger_hit=False, in_story_conflict=False, repair_signal="",
            )
        assert len(svc.get_estrangement_window()) == 8

    def test_fifo_eviction(self):
        svc = AffinityService()
        for i in range(10):
            svc._record_delta_ring(
                affinity_delta=i, trust_delta=0, guard_delta=0,
                trigger_hit=False, in_story_conflict=False, repair_signal="",
            )
        window = svc.get_estrangement_window()
        # First 2 entries (0,1) evicted, remaining are 2..9
        assert window[0]["affinity_delta"] == 2
        assert window[-1]["affinity_delta"] == 9

    def test_clear_on_load(self):
        svc = AffinityService()
        svc._record_delta_ring(
            affinity_delta=-3, trust_delta=0, guard_delta=0,
            trigger_hit=False, in_story_conflict=False, repair_signal="",
        )
        assert len(svc.get_estrangement_window()) == 1
        svc.load({"affinity": 50, "trust": 30, "mood": "平静", "guard": 70, "reason": ""})
        assert len(svc.get_estrangement_window()) == 0

    def test_empty_window_returns_empty_list(self):
        svc = AffinityService()
        assert svc.get_estrangement_window() == []

    def test_negative_delta_in_window(self):
        svc = AffinityService()
        svc._record_delta_ring(
            affinity_delta=-5, trust_delta=-3, guard_delta=2,
            trigger_hit=True, in_story_conflict=False, repair_signal="apology",
        )
        entries = svc.get_estrangement_window()
        assert len(entries) == 1
        assert entries[0]["affinity_delta"] == -5
        assert entries[0]["trigger_hit"] is True
        assert entries[0]["repair_signal"] == "apology"


# ─────────────────────────────────────────────
# 影子判定：三条件
# ─────────────────────────────────────────────


class TestShadowJudgment:
    """_shadow_estrangement_check 三条件各一用例。"""

    def test_cumulative_condition(self, capsys):
        """累计条件：负向轮次≥4 且累计降幅≥12。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)
        engine._guard = 50  # moderate guard, not triggering sharp_drop

        # 5 rounds of negative delta
        for _ in range(5):
            _run_eval(engine, {"affinity": engine._affinity - 3})
            engine._affinity -= 3  # sync

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "would_enter=active" in captured
        assert "cumulative" in captured
        assert "neg_rounds=5" in captured
        assert "neg_sum=" in captured

    def test_trigger_condition(self, capsys):
        """雷点命中条件：trigger_hits≥2 且角色有 triggers 定义。"""
        engine = _make_engine()  # has psyche__triggers=["被无视", "被欺骗"]
        _set_stage(engine, "亲近", 80)

        # 2 trigger hits, 0 negative delta so cumulative won't fire
        for _ in range(2):
            _run_eval(engine, {
                "affinity": engine._affinity,
                "trigger_hit": True,
                "in_story_conflict": False,
            })

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "would_enter=active" in captured
        assert "trigger" in captured
        assert "trigger_hits=2" in captured
        assert "in_story=0" in captured

    def test_story_conflict_excluded_from_trigger_count(self, capsys):
        """剧情冲突标记不计入 trigger_hits 计数。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)

        # 2 triggers but 1 is in_story → only 1 real trigger → not enough
        for i in range(2):
            _run_eval(engine, {
                "affinity": engine._affinity,
                "trigger_hit": True,
                "in_story_conflict": bool(i == 0),  # first one is story
            })

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "would_enter=none" in captured or "would_enter=brewing" in captured
        assert "trigger_hits=1" in captured

    def test_sharp_drop_condition(self, capsys):
        """急降条件：单轮 clamp 到 -8 且 guard≥75。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)
        engine._guard = 80

        # Single sharp drop
        _run_eval(engine, {"affinity": engine._affinity - 8, "guard": 80})
        engine._affinity -= 8

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "would_enter=active" in captured
        assert "sharp_drop" in captured

    def test_brewing_half_threshold(self, capsys):
        """酝酿态：半阈值触发（负向轮次≥2 或降幅≥6）。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)
        engine._guard = 50

        # 2 rounds of moderate negative
        for _ in range(2):
            _run_eval(engine, {"affinity": engine._affinity - 3})
            engine._affinity -= 3

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        # 2 neg rounds >= 2 → brewing
        assert "would_enter=brewing" in captured

    def test_no_condition_returns_none(self, capsys):
        """无一条件触发 → would_enter=none。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)

        # Single neutral eval
        _run_eval(engine, {"affinity": engine._affinity + 1})
        engine._affinity += 1

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "would_enter=none" in captured


class TestShadowRepairSignal:
    """修复信号在日志中体现。"""

    def test_repair_signal_logged(self, capsys):
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)

        _run_eval(engine, {
            "affinity": engine._affinity,
            "repair_signal": "apology",
        })

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "repair=apology" in captured


class TestApplyEvaluationRecordsRing:
    """apply_evaluation 自动记录环形缓冲。"""

    def test_ring_recorded_after_apply(self):
        svc = AffinityService()
        svc.affinity = 60
        data = {
            "affinity": 55, "trust": 30, "mood": "平静", "guard": 70,
            "inner_voice": "嗯", "mood_emoji": "😊", "importance": 5,
            "in_character": 80, "assertion_confidence": 50,
            "trigger_hit": True, "in_story_conflict": False, "repair_signal": "",
        }
        svc.apply_evaluation(data, "朋友")
        window = svc.get_estrangement_window()
        assert len(window) == 1
        assert window[0]["affinity_delta"] == -5  # 55 - 60
        assert window[0]["trigger_hit"] is True

    def test_missing_keys_tolerated(self):
        """评估 JSON 缺 trigger_hit/in_story_conflict/repair_signal → 不报错。"""
        svc = AffinityService()
        svc.affinity = 50
        data = {
            "affinity": 50, "trust": 30, "mood": "平静", "guard": 70,
            "inner_voice": "嗯", "mood_emoji": "😊", "importance": 5,
            "in_character": 80, "assertion_confidence": 50,
            # Intentionally missing trigger_hit, in_story_conflict, repair_signal
        }
        svc.apply_evaluation(data, "陌生")  # must not raise
        window = svc.get_estrangement_window()
        assert len(window) == 1
        assert window[0]["trigger_hit"] is False
        assert window[0]["in_story_conflict"] is False
        assert window[0]["repair_signal"] == ""


class TestShadowNoPsycheTriggers:
    """角色没有 triggers 定义时，trigger_hits 不触发条件 2。"""

    def test_no_triggers_no_trigger_condition(self, capsys):
        card = CharacterCard(name="无雷角色", identity="温和", psyche=PsycheProfile())
        llm = MagicMock()
        llm.chat.return_value = '{"affinity":50,"trust":30,"mood":"平静","guard":70,"inner_voice":"。","mood_emoji":"😊","importance":5}'
        llm.last_usage = {}
        engine = ChatEngine(llm=llm, rag=MagicMock(), card=card)
        engine._session_id = "test-no-trigger"
        engine._storage = MagicMock()
        engine._affinity_service.clear_delta_ring()

        _set_stage(engine, "亲近", 80)

        for _ in range(2):
            _run_eval(engine, {
                "affinity": engine._affinity,
                "trigger_hit": True,
                "in_story_conflict": False,
            })

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        # trigger_hits=2 but has_triggers=False → condition not met
        assert "would_enter=none" in captured
