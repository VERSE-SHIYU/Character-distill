"""Tests for P1 影子模式 — Active Estrangement shadow judgment (record-only)."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from core.evaluation_pipeline import EvalResult
from core.schema import PsycheProfile
from affinity_verdict import verdict
from core.affinity_service import AffinityService, TurnSignal, calc_stage
from core.chat_engine import ChatEngine
from core.schema import CharacterCard, PsycheProfile


def _trigger_turn() -> dict:
    """一轮「触到雷点」的评估输出（生效事件是 trigger）。"""
    return {"affinity_event": "trigger", "affinity_tier": "medium", "affinity_delta": 4}


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
    engine = ChatEngine(llm=llm, rag=rag, card=card, storage=MagicMock(),
                        session_id="test-shadow", is_new_session=True)
    engine._affinity_service.clear_delta_ring()
    return engine


def _set_stage(engine: ChatEngine, stage: str, affinity: int) -> None:
    engine._affinity_service.affinity = affinity
    engine._affinity_service.stage = stage
    engine._affinity_service.stage_emoji = calc_stage(affinity)[1]


def _run_eval(engine: ChatEngine, data_override: dict | None = None) -> None:
    """Run a fake evaluation turn, filling ring buffer."""
    data = {
        **verdict(0),
        "trust": engine._trust,
        "mood": "平静",
        "guard": engine._guard,
        "inner_voice": "。",
        "mood_emoji": "😊",
        "importance": 5,
        "in_character": 80,
        "assertion_confidence": 50,
    }
    if data_override:
        override = dict(data_override)
        if "affinity" in override:      # 用例写的是「好感变成多少」，翻成评估协议里的事件
            data.update(verdict(override.pop("affinity") - engine._affinity))
        data.update(override)
    old_stage = engine._stage
    engine._affinity_service.apply_evaluation(data, old_stage, engine.card.psyche)


# ─────────────────────────────────────────────
# EvalResult：旧的影子字段已删
# ─────────────────────────────────────────────


class TestEvalResultNoOldFields:
    """三个平行字段已删除，EvalResult 上不再有它们。"""

    def test_old_parallel_fields_are_gone(self):
        result = EvalResult()
        for field in ("trigger_hit", "in_story_conflict", "repair_signal"):
            assert not hasattr(result, field), f"EvalResult 不该再有 {field}"


# ─────────────────────────────────────────────
# 环形缓冲
# ─────────────────────────────────────────────


class TestDeltaRing:
    """8 轮环形缓冲滑窗正确。"""

    def test_max_8_entries(self):
        svc = AffinityService()
        for i in range(10):
            svc._record_delta_ring(TurnSignal.of(
                affinity_delta=-i, trust_delta=0, guard_delta=0, event=""))
        assert len(svc.get_estrangement_window()) == 8

    def test_fifo_eviction(self):
        svc = AffinityService()
        for i in range(10):
            svc._record_delta_ring(TurnSignal.of(
                affinity_delta=i, trust_delta=0, guard_delta=0, event=""))
        window = svc.get_estrangement_window()
        # First 2 entries (0,1) evicted, remaining are 2..9
        assert window[0].affinity_delta == 2
        assert window[-1].affinity_delta == 9

    def test_clear_on_load(self):
        svc = AffinityService()
        svc._record_delta_ring(TurnSignal.of(
            affinity_delta=-3, trust_delta=0, guard_delta=0, event=""))
        assert len(svc.get_estrangement_window()) == 1
        svc.load({"affinity": 50, "trust": 30, "mood": "平静", "guard": 70, "reason": ""})
        assert len(svc.get_estrangement_window()) == 0

    def test_empty_window_returns_empty_list(self):
        svc = AffinityService()
        assert svc.get_estrangement_window() == []

    def test_negative_delta_in_window(self):
        svc = AffinityService()
        svc._record_delta_ring(TurnSignal.of(
            affinity_delta=-5, trust_delta=-3, guard_delta=2,
            event="repair", repair_kind="apology"))
        entries = svc.get_estrangement_window()
        assert len(entries) == 1
        assert entries[0].affinity_delta == -5
        assert entries[0].is_trigger is False
        assert entries[0].repair_kind == "apology"


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
        """雷点命中条件：两轮生效的 trigger 事件 ≥2 且角色有 triggers 定义。"""
        engine = _make_engine()  # has psyche__triggers=["被无视", "被欺骗"]
        _set_stage(engine, "亲近", 80)

        for _ in range(2):
            _run_eval(engine, _trigger_turn())

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "would_enter=active" in captured
        assert "trigger_hits=2" in captured
        assert "trigger(2hits)" in captured

    def test_story_conflict_excluded_from_trigger_count(self, capsys):
        """剧情冲突按 neutral 报 → 不计入 trigger_hits。"""
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)

        for _ in range(2):
            _run_eval(engine, {"affinity_event": "neutral"})

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "trigger_hits=0" in captured

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


class TestShadowRepairKind:
    """修复细分在日志中体现，且只在真有修复时记。"""

    def test_repair_kind_logged(self, capsys):
        engine = _make_engine()
        _set_stage(engine, "亲近", 80)

        _run_eval(engine, {"affinity": engine._affinity - 4})          # 先冒犯
        _run_eval(engine, {"affinity_event": "repair", "affinity_tier": "small",
                           "affinity_delta": 2, "repair_kind": "apology"})

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        assert "repair=apology" in captured


class TestApplyEvaluationRecordsRing:
    """apply_evaluation 自动记录环形缓冲。"""

    def test_ring_recorded_after_apply(self):
        svc = AffinityService()
        svc.affinity = 60
        data = {
            **verdict(-5), "trust": 30, "mood": "平静", "guard": 70,
            "inner_voice": "嗯", "mood_emoji": "😊", "importance": 5,
            "in_character": 80, "assertion_confidence": 50,
        }
        svc.apply_evaluation(data, "朋友", PsycheProfile())
        window = svc.get_estrangement_window()
        assert len(window) == 1
        assert window[0].affinity_delta == -5  # 55 - 60
        assert window[0].event == "offended"

    def test_missing_repair_kind_tolerated(self):
        """评估 JSON 缺 repair_kind → 记 ""，不报错。"""
        svc = AffinityService()
        svc.affinity = 50
        data = {
            "affinity_event": "neutral", "trust": 30, "mood": "平静", "guard": 70,
            "inner_voice": "嗯", "mood_emoji": "😊", "importance": 5,
            "in_character": 80, "assertion_confidence": 50,
            # 故意没有 repair_kind
        }
        svc.apply_evaluation(data, "陌生", PsycheProfile())  # must not raise
        window = svc.get_estrangement_window()
        assert len(window) == 1
        assert window[0].event == "neutral"
        assert window[0].repair_kind == ""


class TestRingEventFromEffectiveVerdict:
    """缓冲记生效事件：不合法的一轮记 ""，合法的一轮记事件名。"""

    def test_unknown_event_records_empty_event(self):
        svc = AffinityService()
        svc.apply_evaluation({"affinity_event": "bogus"}, "陌生", PsycheProfile())
        assert svc.get_estrangement_window()[-1].event == ""

    def test_valid_trigger_event_is_recorded(self):
        svc = AffinityService()
        svc.apply_evaluation(_trigger_turn(), "陌生", PsycheProfile())
        assert svc.get_estrangement_window()[-1].event == "trigger"


class TestTurnSignal:
    """TurnSignal 里的两条判断：repair_kind 只跟 repair；is_trigger 只认 trigger。"""

    def test_repair_kind_kept_only_for_repair(self):
        assert TurnSignal.of(affinity_delta=0, trust_delta=0, guard_delta=0,
                             event="repair", repair_kind="apology").repair_kind == "apology"
        assert TurnSignal.of(affinity_delta=0, trust_delta=0, guard_delta=0,
                             event="friendly", repair_kind="apology").repair_kind == ""

    def test_is_trigger_only_for_trigger(self):
        assert TurnSignal.of(affinity_delta=0, trust_delta=0, guard_delta=0,
                             event="trigger").is_trigger is True
        assert TurnSignal.of(affinity_delta=0, trust_delta=0, guard_delta=0,
                             event="offended").is_trigger is False


class TestShadowNoPsycheTriggers:
    """角色没有 triggers 定义时，触雷轮数再多也不触发条件 2。"""

    def test_no_triggers_no_trigger_condition(self, capsys):
        card = CharacterCard(name="无雷角色", identity="温和", psyche=PsycheProfile())
        llm = MagicMock()
        llm.chat.return_value = '{"affinity":50,"trust":30,"mood":"平静","guard":70,"inner_voice":"。","mood_emoji":"😊","importance":5}'
        llm.last_usage = {}
        engine = ChatEngine(llm=llm, rag=MagicMock(), card=card, storage=MagicMock(),
                            session_id="test-no-trigger", is_new_session=True)
        engine._affinity_service.clear_delta_ring()

        _set_stage(engine, "亲近", 80)

        for _ in range(2):
            _run_eval(engine, _trigger_turn())

        engine._shadow_estrangement_check()
        captured = capsys.readouterr().out
        # trigger_hits=2 但角色没定义 triggers → 条件 2 不成立
        assert "trigger_hits=2" in captured
        assert "trigger(2hits)" not in captured
