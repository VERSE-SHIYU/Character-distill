"""回归测试：ReflectionService 低可信记忆过滤 + 双条件触发。"""

from __future__ import annotations

from core.reflection_service import ReflectionService
from core.memory_manager import REFLECTION_THRESHOLD, REFLECTION_MIN_ROUNDS, REFLECTION_MIN_QUALITY


class FakeLLM:
    def chat(self, system: str, messages: list[dict]) -> str:
        return "done"


class FakeMemory:
    """可控记忆存储：get_all 返回预设列表，reflect 捕获传入的 recent_memories。"""

    def __init__(self, memories: list[dict] | None = None):
        self.enabled = True
        self._memories = memories or []
        self.reflect_captured: list[dict] | None = None

    def get_all(self, card_id: str) -> list[dict]:
        return self._memories

    def reflect(self, card_id: str, llm, recent_memories: list[dict], char_name: str) -> None:
        self.reflect_captured = recent_memories


def _make_mem(importance: int = 5, assertion_confidence: int = 50, is_reflection: bool = False) -> dict:
    meta: dict = {"importance": importance, "assertion_confidence": assertion_confidence}
    if is_reflection:
        meta["is_reflection"] = True
    return {"memory": f"test_imp{importance}_conf{assertion_confidence}", "metadata": meta}


class TestReflectionLowConfidenceFilter:
    """断言 assertion_confidence < 40 的记忆被排除在反思之外。"""

    def test_low_confidence_excluded(self):
        """低可信(30)记忆被过滤，正常(70)记忆进入反思。"""
        memories = [
            _make_mem(importance=8, assertion_confidence=30),  # 应被排除（低可信）
            _make_mem(importance=7, assertion_confidence=70),  # 高质量
            _make_mem(importance=9, assertion_confidence=80),  # 高质量
            _make_mem(importance=8, assertion_confidence=75),  # 高质量
            _make_mem(importance=6, assertion_confidence=70),  # 正常
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")
        svc._importance_acc = REFLECTION_THRESHOLD
        svc._rounds_since_reflect = REFLECTION_MIN_ROUNDS

        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        assert memory.reflect_captured is not None
        texts = [m["text"] for m in memory.reflect_captured]
        assert "test_imp8_conf30" not in texts  # 低可信被排除
        assert "test_imp6_conf70" in texts      # 正常进入

    def test_default_confidence_not_excluded(self):
        """存量记忆无 assertion_confidence 字段（默认 50）不被误杀。"""
        memories = [
            {"memory": "old memory", "metadata": {"importance": 7}},
            _make_mem(importance=5, assertion_confidence=50),
            _make_mem(importance=8, assertion_confidence=70),
            _make_mem(importance=7, assertion_confidence=65),
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")
        svc._importance_acc = REFLECTION_THRESHOLD
        svc._rounds_since_reflect = REFLECTION_MIN_ROUNDS

        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        assert memory.reflect_captured is not None
        texts = [m["text"] for m in memory.reflect_captured]
        assert "old memory" in texts
        assert "test_imp5_conf50" in texts

    def test_only_low_confidence_all_excluded(self):
        """所有记忆都是低可信 → reflect 不会被调用。"""
        memories = [
            _make_mem(importance=9, assertion_confidence=20),
            _make_mem(importance=7, assertion_confidence=10),
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")
        svc._importance_acc = REFLECTION_THRESHOLD
        svc._rounds_since_reflect = REFLECTION_MIN_ROUNDS

        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        assert memory.reflect_captured is None

    def test_high_confidence_maintains_importance_order(self):
        """过滤后 importance 排序保持正确。"""
        memories = [
            _make_mem(importance=9, assertion_confidence=35),  # 低可信，排除
            _make_mem(importance=8, assertion_confidence=80),  # 正常
            _make_mem(importance=6, assertion_confidence=75),  # 正常
            _make_mem(importance=7, assertion_confidence=20),  # 低可信，排除
            _make_mem(importance=9, assertion_confidence=85),  # 正常，高质量
            _make_mem(importance=7, assertion_confidence=90),  # 正常，高质量
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")
        svc._importance_acc = REFLECTION_THRESHOLD
        svc._rounds_since_reflect = REFLECTION_MIN_ROUNDS

        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        assert memory.reflect_captured is not None
        # 应只有 4 条（2 条低可信被排除），按 importance 降序
        assert len(memory.reflect_captured) == 4
        assert memory.reflect_captured[0]["text"] == "test_imp9_conf85"
        assert memory.reflect_captured[1]["text"] == "test_imp8_conf80"
        assert memory.reflect_captured[2]["text"] == "test_imp7_conf90"
        assert memory.reflect_captured[3]["text"] == "test_imp6_conf75"


class TestReflectionDualCondition:
    """双条件触发 + 高质量素材校验。"""

    def test_rounds_condition_not_met(self):
        """重要性达标但轮数不足 → 不触发，累加器不归零。"""
        svc = ReflectionService(None, "card_test")
        svc._importance_acc = REFLECTION_THRESHOLD  # 重要性已达标
        svc._rounds_since_reflect = 0               # 轮数不足

        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        # 累加器均未归零（内部+1后仍然不达轮数条件）
        assert svc._importance_acc == REFLECTION_THRESHOLD + 1
        assert svc._rounds_since_reflect == 1

    def test_quality_deferred_does_not_zero(self):
        """双条件满足但高质量素材不足 → defer，累加器不归零。"""
        memories = [
            _make_mem(importance=8, assertion_confidence=30),  # 低可信，排除
            _make_mem(importance=7, assertion_confidence=70),  # 高质量①
            _make_mem(importance=7, assertion_confidence=80),  # 高质量② 仅2条<3
            _make_mem(importance=5, assertion_confidence=70),  # 普通
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")
        svc._importance_acc = REFLECTION_THRESHOLD
        svc._rounds_since_reflect = REFLECTION_MIN_ROUNDS

        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        # defer：不归零
        assert svc._importance_acc > 0
        assert svc._rounds_since_reflect > 0
        assert memory.reflect_captured is None

    def test_dual_condition_and_quality_met(self):
        """双条件 + 高质量素材均满足 → 正常触发并归零。"""
        memories = [
            _make_mem(importance=8, assertion_confidence=70),
            _make_mem(importance=7, assertion_confidence=65),
            _make_mem(importance=9, assertion_confidence=80),
            _make_mem(importance=6, assertion_confidence=75),
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")
        svc._importance_acc = REFLECTION_THRESHOLD
        svc._rounds_since_reflect = REFLECTION_MIN_ROUNDS

        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        # 已触发反思
        assert memory.reflect_captured is not None
        # 累加器归零
        assert svc._importance_acc == 0
        assert svc._rounds_since_reflect == 0
