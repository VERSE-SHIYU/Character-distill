"""回归测试：ReflectionService 低可信记忆过滤。"""

from __future__ import annotations

from core.reflection_service import ReflectionService


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
            _make_mem(importance=8, assertion_confidence=30),  # 应被排除
            _make_mem(importance=6, assertion_confidence=70),  # 应进入
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")

        # 触发反思（阈值默认 8）
        from core.memory_manager import REFLECTION_THRESHOLD
        svc._importance_acc = REFLECTION_THRESHOLD - 1
        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        assert memory.reflect_captured is not None
        texts = [m["text"] for m in memory.reflect_captured]
        assert "test_imp8_conf30" not in texts  # 低可信被排除
        assert "test_imp6_conf70" in texts      # 正常进入

    def test_default_confidence_not_excluded(self):
        """存量记忆无 assertion_confidence 字段（默认 50）不被误杀。"""
        memories = [
            # 无 assertion_confidence 字段的存量数据
            {"memory": "old memory", "metadata": {"importance": 7}},
            # 明确 50 分
            _make_mem(importance=5, assertion_confidence=50),
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")

        from core.memory_manager import REFLECTION_THRESHOLD
        svc._importance_acc = REFLECTION_THRESHOLD - 1
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

        from core.memory_manager import REFLECTION_THRESHOLD
        svc._importance_acc = REFLECTION_THRESHOLD - 1
        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        # 没有可反思的记忆 → reflect 不会被调用（captured 保持 None）
        assert memory.reflect_captured is None

    def test_high_confidence_maintains_importance_order(self):
        """过滤后 importance 排序保持正确。"""
        memories = [
            _make_mem(importance=9, assertion_confidence=35),  # 低可信，排除
            _make_mem(importance=8, assertion_confidence=80),  # 正常
            _make_mem(importance=6, assertion_confidence=75),  # 正常
            _make_mem(importance=7, assertion_confidence=20),  # 低可信，排除
        ]
        memory = FakeMemory(memories)
        svc = ReflectionService(memory, "card_test")

        from core.memory_manager import REFLECTION_THRESHOLD
        svc._importance_acc = REFLECTION_THRESHOLD - 1
        svc.maybe_reflect(1, FakeLLM(), "测试角色")

        assert memory.reflect_captured is not None
        # 应只有 2 条，按 importance 降序
        assert len(memory.reflect_captured) == 2
        assert memory.reflect_captured[0]["text"] == "test_imp8_conf80"
        assert memory.reflect_captured[1]["text"] == "test_imp6_conf75"
