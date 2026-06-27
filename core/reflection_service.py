"""反思触发与执行：双条件（轮数 + 高质量素材）触发，避免高频低质反思。"""

from __future__ import annotations

from typing import Any


class ReflectionService:
    """管理反思累计器，在双条件 + 高质量素材均满足时触发记忆反思。

    状态
    ----
    _importance_acc : int
        本轮对话累计重要性，达阈值且满足双条件时触发反思后归零。
    _rounds_since_reflect : int
        距上次反思的对话轮数，防高频触发。

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
        self._rounds_since_reflect: int = 0

    def maybe_reflect(self, importance: int, llm, card_name: str) -> None:
        """双条件（累计 + 轮数）触发反思，高质量素材不足时 defer 不归零。"""
        from core.memory_manager import REFLECTION_THRESHOLD, REFLECTION_MIN_ROUNDS, REFLECTION_MIN_QUALITY

        self._importance_acc += importance
        self._rounds_since_reflect += 1

        # ── 双条件前置校验：任一不满足则不触发，累计持续 ──
        if not (self._importance_acc >= REFLECTION_THRESHOLD
                and self._rounds_since_reflect >= REFLECTION_MIN_ROUNDS):
            return
        if not self._memory or not self._memory.enabled or not self._card_id:
            return
        if not llm:
            return

        print(f"[Reflection] Triggered: acc={self._importance_acc} >= {REFLECTION_THRESHOLD}, "
              f"rounds={self._rounds_since_reflect} >= {REFLECTION_MIN_ROUNDS}")

        try:
            all_memories = self._memory.get_all(self._card_id)
        except Exception as exc:
            print(f"[Reflection] get_all failed: {exc}")
            return

        # 过滤：排除已有反思记忆，低可信不计入
        raw = []
        for m in all_memories:
            if not isinstance(m, dict):
                continue
            meta = m.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("is_reflection"):
                continue
            conf = meta.get("assertion_confidence", 50) if isinstance(meta, dict) else 50
            if conf < 40:
                continue
            text = m.get("memory", "").strip()
            if not text:
                continue
            imp = meta.get("importance", 5) if isinstance(meta, dict) else 5
            raw.append({"text": text, "importance": int(imp), "mood": meta.get("mood", "") if isinstance(meta, dict) else ""})

        # 高质量素材校验：至少 REFLECTION_MIN_QUALITY 条 importance>=7
        high_quality = [r for r in raw if r["importance"] >= 7]
        if len(high_quality) < REFLECTION_MIN_QUALITY:
            print(f"[Reflection] Deferred: only {len(high_quality)} high-quality items "
                  f"(need {REFLECTION_MIN_QUALITY})")
            return  # 不归零累加器，等素材攒够

        # 按 importance 降序取 top-10
        raw.sort(key=lambda x: x["importance"], reverse=True)
        recent = raw[:10]

        if not recent:
            print("[Reflection] No raw memories to reflect on.")
            return

        # 真正执行反思 → 累加器归零
        self._importance_acc = 0
        self._rounds_since_reflect = 0

        print(f"[Reflection] {len(raw)} raw memories, top-10 importance range: "
              f"{recent[-1]['importance']}-{recent[0]['importance']}")
        self._memory.reflect(self._card_id, llm, recent, card_name)
