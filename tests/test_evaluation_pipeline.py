"""回归测试：EvaluationPipeline CORE/SIDE-EFFECT 隔离 + AffinityService 纯函数。"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from core.affinity_service import AffinityService, calc_stage
from core.evaluation_pipeline import EvaluationPipeline, EvalContext, EvalResult


# ── 辅助 fake 对象 ─────────────────────────────────────────────

class FakePsyche:
    affinity_baseline = 50
    volatility = "适中"
    grudge_inertia = "一般"
    triggers: list[str] = []
    soft_spots: list[str] = []


class FakeCard:
    name = "测试角色"
    values: list[str] = ["真诚", "直率"]
    inner_tensions: list[str] = ["独立 vs 依赖"]
    psyche = FakePsyche()


class FakeLLM:
    def __init__(self, reply: str | None = None, raise_on_call: bool = False):
        self.reply = reply
        self.raise_on_call = raise_on_call
        self.call_count = 0

    def chat(self, system: str, messages: list[dict]) -> str:
        self.call_count += 1
        if self.raise_on_call:
            raise RuntimeError("LLM call failed (fake)")
        if self.reply is None:
            return '{"affinity":65,"trust":45,"mood":"开心","guard":35,"inner_voice":"不错","mood_emoji":"😊","importance":7,"time_event":null}'
        return self.reply


class FakeMemory:
    def __init__(self, raise_on_add_manual: bool = False):
        self.enabled = True
        self.raise_on_add_manual = raise_on_add_manual
        self.added: list[tuple] = []

    def add_manual(self, text: str, card_id: str, metadata: dict | None = None) -> None:
        if self.raise_on_add_manual:
            raise RuntimeError("Memory add_manual failed (fake)")
        self.added.append((text, card_id, metadata))


class FakeStorage:
    def __init__(self, raise_on_update: bool = False):
        self.raise_on_update = raise_on_update
        self.captured: dict | None = None

    async def update_session_affinity(
        self, session_id: str, affinity: int, trust: int,
        mood: str, guard: int, reason: str,
    ) -> None:
        if self.raise_on_update:
            raise RuntimeError("Storage update failed (fake)")
        self.captured = {
            "session_id": session_id,
            "affinity": affinity,
            "trust": trust,
            "mood": mood,
            "guard": guard,
            "reason": reason,
        }

    async def update_group_affinity(
        self, group_id: str, card_id: str, affinity: int, trust: int,
        mood: str, guard: int, reason: str,
    ) -> None:
        if self.raise_on_update:
            raise RuntimeError("Storage update failed (fake)")
        self.captured = {
            "group_id": group_id,
            "card_id": card_id,
            "affinity": affinity,
            "trust": trust,
            "mood": mood,
            "guard": guard,
            "reason": reason,
        }


def _build_context(
    affinity_service: AffinityService | None = None,
    llm: FakeLLM | None = None,
    storage: FakeStorage | None = None,
    memory: FakeMemory | None = None,
    user_message: str = "你好",
    assistant_reply: str = "你好，有什么事吗？",
    old_stage: str = "陌生",
    session_id: str = "sess_test",
    group_id: str = "",
    card_id: str = "card_test",
    reaction_appraisal: str = "",
) -> EvalContext:
    return EvalContext(
        card=FakeCard(),
        user_message=user_message,
        assistant_reply=assistant_reply,
        user_role="对方",
        old_stage=old_stage,
        session_id=session_id,
        group_id=group_id,
        card_id=card_id,
        storage=storage,
        memory=memory,
        affinity_service=affinity_service or AffinityService(),
        reaction_service=object(),  # not used by pipeline
        llm=llm or FakeLLM(),
        reaction_appraisal=reaction_appraisal,
    )


class TestParseEvaluationReply:
    """AffinityService.parse_evaluation_reply 纯函数回归。"""

    def test_valid_json_with_prefix_suffix(self):
        """合法前后缀提取：LLM 回复包裹了 markdown JSON block。"""
        svc = AffinityService()
        reply = "让我想想。。。\n```json\n{\"affinity\": 65, \"trust\": 45}\n```\n好了。"
        result = svc.parse_evaluation_reply(reply)
        assert result is not None
        assert result["affinity"] == 65
        assert result["trust"] == 45

    def test_no_json_returns_none(self):
        """无 JSON 内容时返回 None。"""
        svc = AffinityService()
        result = svc.parse_evaluation_reply("好的，我会认真思考你刚才说的话。")
        assert result is None

    def test_bad_json_raises(self):
        """格式错误的 JSON 抛出 JSONDecodeError。"""
        svc = AffinityService()
        with pytest.raises(json.JSONDecodeError):
            svc.parse_evaluation_reply('{"affinity": 65, "trust": }')

    def test_missing_fields_fallback(self):
        """json 中缺字段时，apply_evaluation 用当前值兜底。"""
        svc = AffinityService()
        svc.affinity = 50
        svc.trust = 30
        importance = svc.apply_evaluation({"affinity": 75}, "陌生")
        assert importance == 5  # importance 缺省默认为 5
        assert svc.affinity == 75
        assert svc.trust == 30  # 未提供，保持原值


class TestEvaluationPipeline:
    """EvaluationPipeline 三层隔离回归。"""

    def test_full_success(self, monkeypatch):
        monkeypatch.setattr("deps.run_on_main_loop", lambda coro, timeout=600: (coro.close(), None))
        """正常流程：CORE 层执行 → side-effect 层执行 → 结果正确。"""
        memory = FakeMemory()
        storage = FakeStorage()
        svc = AffinityService()
        ctx = _build_context(
            affinity_service=svc,
            storage=storage,
            memory=memory,
        )
        pipeline = EvaluationPipeline()
        result = pipeline.run(ctx)

        assert result.applied is True
        assert result.importance == 7
        assert result.affinity == 65
        assert result.in_character == 80  # default when LLM doesn't provide it
        assert result.ooc_reason == ""
        assert result.assertion_confidence == 50  # default when LLM doesn't provide it
        assert svc.affinity == 65
        assert svc.trust == 45
        assert svc.mood == "开心"
        assert svc.guard == 35
        # stage_upgraded: 65 → 认识阶段（默认 50 → 65 跨档）
        assert result.stage_upgraded is True

    def test_in_character_propagated(self, monkeypatch):
        """in_character 字段从 LLM data → EvalResult → ChatEngine 正确传播。"""
        monkeypatch.setattr("deps.run_on_main_loop", lambda coro, timeout=600: (coro.close(), None))
        llm_reply = (
            '{"affinity":55,"trust":40,"mood":"平静","guard":50,'
            '"inner_voice":"还行","mood_emoji":"😐","importance":6,'
            '"time_event":null,'
            '"in_character":35,"ooc_reason":"对方施压后立刻妥协，不符合性格"}'
        )
        llm = FakeLLM(reply=llm_reply)
        svc = AffinityService()
        ctx = _build_context(affinity_service=svc, llm=llm)
        pipeline = EvaluationPipeline()
        result = pipeline.run(ctx)

        assert result.applied is True
        assert result.in_character == 35
        assert result.ooc_reason == "对方施压后立刻妥协，不符合性格"
        # in_character 不影响好感数值
        assert svc.affinity == 55
        assert svc.trust == 40

    def test_assertion_confidence_propagated(self, monkeypatch):
        """assertion_confidence 字段从 LLM data → EvalResult 正确传播，且与 in_character 正交。"""
        monkeypatch.setattr("deps.run_on_main_loop", lambda coro, timeout=600: (coro.close(), None))
        llm_reply = (
            '{"affinity":60,"trust":35,"mood":"平静","guard":55,'
            '"inner_voice":"嗯","mood_emoji":"😐","importance":4,'
            '"time_event":null,"in_character":85,"ooc_reason":"",'
            '"assertion_confidence":30}'
        )
        llm = FakeLLM(reply=llm_reply)
        svc = AffinityService()
        ctx = _build_context(affinity_service=svc, llm=llm)
        pipeline = EvaluationPipeline()
        result = pipeline.run(ctx)

        assert result.applied is True
        assert result.assertion_confidence == 30  # 低可信
        assert result.in_character == 85           # 高 in_character，正交
        # assertion_confidence 不影响好感数值
        assert svc.affinity == 60
        assert svc.trust == 35

    def test_core_layer_failure_returns_applied_false(self):
        """CORE 层 LLM 抛异常 → applied=False, importance=5，不触碰持久化。"""
        llm = FakeLLM(raise_on_call=True)
        memory = FakeMemory()
        storage = FakeStorage()
        svc = AffinityService()
        svc.affinity = 50  # baseline

        ctx = _build_context(
            affinity_service=svc,
            llm=llm,
            storage=storage,
            memory=memory,
        )
        pipeline = EvaluationPipeline()
        result = pipeline.run(ctx)

        assert result.applied is False
        assert result.importance == 5
        # 状态未变
        assert svc.affinity == 50
        assert svc.trust == 30
        assert memory.added == []     # 时间事件未持久化
        assert storage.captured is None  # 好感未持久化

    def test_time_event_side_effect_failure_still_applies_core(self, monkeypatch):
        """守门员：时间事件存库爆炸 → CORE 状态仍然落定。"""
        monkeypatch.setattr("deps.run_on_main_loop", lambda coro, timeout=600: (coro.close(), None))
        llm_reply = (
            '{"affinity":72,"trust":55,"mood":"开心","guard":28,'
            '"inner_voice":"不错","mood_emoji":"😊","importance":8,'
            '"time_event":{"event":"明天有面试","when_text":"明天","due_at":"2026-06-26T10:00"}}'
        )
        llm = FakeLLM(reply=llm_reply)
        memory = FakeMemory(raise_on_add_manual=True)  # ← add_manual 必抛
        svc = AffinityService()
        svc.affinity = 40

        ctx = _build_context(
            affinity_service=svc,
            llm=llm,
            memory=memory,
            storage=FakeStorage(),
        )
        pipeline = EvaluationPipeline()
        result = pipeline.run(ctx)

        # CORE 已落定
        assert result.applied is True
        assert result.importance == 8
        assert result.affinity == 72
        assert svc.affinity == 72
        assert svc.trust == 55
        assert svc.mood == "开心"
        assert svc.guard == 28
        # time_event 虽然没写进去，但 CORE 状态不受影响
        assert len(memory.added) == 0  # 果然没写进去（raise_on_add_manual）

    @pytest.mark.asyncio
    async def test_affinity_persist_side_effect_failure_still_applies_core(self, monkeypatch):
        """守门员：好感存库爆炸 → CORE 状态仍然落定。"""
        monkeypatch.setattr("deps.run_on_main_loop", lambda coro, timeout=600: (coro.close(), None))
        llm = FakeLLM()
        svc = AffinityService()
        storage = FakeStorage(raise_on_update=True)
        memory = FakeMemory()

        ctx = _build_context(
            affinity_service=svc,
            llm=llm,
            storage=storage,
            memory=memory,
        )

        pipeline = EvaluationPipeline()
        result = pipeline.run(ctx)

        assert result.applied is True
        assert result.importance == 7
        assert svc.affinity == 65
        # 存储虽然炸了，但 storage.captured 不会被设置（因为 raise_on_update）
        assert storage.captured is None

    def test_parse_no_json_returns_applied_false(self):
        """LLM 回复无 JSON → applied=False。"""
        llm = FakeLLM(reply="好的，我明白了，我会注意的。")
        svc = AffinityService()
        ctx = _build_context(affinity_service=svc, llm=llm)
        pipeline = EvaluationPipeline()
        result = pipeline.run(ctx)

        assert result.applied is False
        assert result.importance == 5
        assert svc.affinity == 50  # 默认值，未变化
