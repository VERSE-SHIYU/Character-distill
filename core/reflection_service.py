"""反思触发与执行：累计重要性，达阈值时综合高重要性原始记忆为高阶洞察。"""

from __future__ import annotations

from typing import Any


class ReflectionService:
    """管理反思累计器，在重要性累计达阈值时触发记忆反思。

    状态
    ----
    _importance_acc : int
        本轮对话累计重要性，达阈值触发反思后归零。

    外部依赖（构造注入）
    ------------------
    memory : 只读调用 memory.get_all() / 写调用 memory.reflect()
    card_id : 当前角色卡 ID 字符串

    方法参数
    --------
    maybe_reflect 每次调用传入 importance、llm、card_name，
    因为这些是调用方（ChatEngine）的共享状态，不属反思服务。
    """

    def __init__(self, memory, card_id: str) -> None:
        self._memory = memory
        self._card_id = card_id
        self._importance_acc: int = 0

    def maybe_reflect(self, importance: int, llm, card_name: str) -> None:
        """累计重要性达阈值时触发反思，综合高重要性原始记忆为高阶洞察。"""
        from core.memory_manager import REFLECTION_THRESHOLD

        self._importance_acc += importance
        if self._importance_acc < REFLECTION_THRESHOLD:
            return
        if not self._memory or not self._memory.enabled or not self._card_id:
            return
        if not llm:
            return

        print(f"[Reflection] Triggered: acc={self._importance_acc} >= {REFLECTION_THRESHOLD}")
        self._importance_acc = 0

        try:
            all_memories = self._memory.get_all(self._card_id)
        except Exception as exc:
            print(f"[Reflection] get_all failed: {exc}")
            return

        # 过滤：排除已有反思记忆，只要原始记忆
        raw = []
        for m in all_memories:
            if not isinstance(m, dict):
                continue
            meta = m.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("is_reflection"):
                continue
            # 低可信记忆不进反思，避免反话/假设/角色扮演内容被固化
            conf = meta.get("assertion_confidence", 50) if isinstance(meta, dict) else 50
            if conf < 40:
                continue
            text = m.get("memory", "").strip()
            if not text:
                continue
            imp = meta.get("importance", 5) if isinstance(meta, dict) else 5
            raw.append({"text": text, "importance": int(imp), "mood": meta.get("mood", "") if isinstance(meta, dict) else ""})

        # 按 importance 降序取 top-10
        raw.sort(key=lambda x: x["importance"], reverse=True)
        recent = raw[:10]

        if not recent:
            print("[Reflection] No raw memories to reflect on.")
            return

        print(f"[Reflection] {len(raw)} raw memories, top-10 importance range: "
              f"{recent[-1]['importance']}-{recent[0]['importance']}")
        self._memory.reflect(self._card_id, llm, recent, card_name)
