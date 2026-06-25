"""情感状态容器：11个亲密度字段 + 读写 + 阶段计算。"""

from __future__ import annotations

from typing import Any


# IOS₁₁ (Scientific Reports 2024) 11级亲密度 → Bogardus 7级社会距离映射
AFFINITY_STAGES = [
    (0, 18, "陌生", "🫥"),      # IOS 1-2, Bogardus 6-7
    (18, 36, "认识", "🙂"),     # IOS 3-4, Bogardus 4-5
    (36, 55, "熟悉", "😊"),     # IOS 5-6, Bogardus 3
    (55, 73, "朋友", "😄"),     # IOS 7-8, Bogardus 2
    (73, 91, "亲近", "🥰"),     # IOS 9-10, Bogardus 1-2
    (91, 101, "心意相通", "💕"), # IOS 11, Bogardus 1
]


def calc_stage(affinity: int) -> tuple[str, str]:
    for lo, hi, name, emoji in AFFINITY_STAGES:
        if lo <= affinity < hi:
            return name, emoji
    return "陌生", "🫥"


class AffinityService:
    """11个情感状态字段的容器 + load/get 方法。

    ChatEngine 通过 @property 透明转发，所有现有代码的
    self._affinity / self._mood / ... 读写自动路由到此容器，
    无需改动调用点。
    """

    def __init__(self) -> None:
        self.affinity: int = 50
        self.trust: int = 30
        self.mood: str = "平静"
        self.guard: int = 70
        self.affinity_reason: str = ""
        self.inner_voice: str = ""
        self.mood_emoji: str = "😊"
        self.prev_stage: str = ""
        self.stage: str = ""
        self.stage_emoji: str = ""
        self.stage_upgraded: bool = False

    def load(self, data: dict[str, Any]) -> None:
        """从存档 dict 加载11个情感字段。

        *注意*：仅设置情感状态字段，不涉及 affinity_enabled 等开关。
        """
        if not data:
            return
        self.affinity = data.get("affinity", 50)
        self.trust = data.get("trust", 30)
        self.mood = data.get("mood", "平静")
        self.guard = data.get("guard", 70)
        self.affinity_reason = data.get("reason", "")
        # 尝试 JSON 解析扩展数据（兼容旧格式纯文本 reason）
        _reason = self.affinity_reason or ""
        try:
            import json
            _parsed = json.loads(_reason)
            self.inner_voice = _parsed.get("inner_voice", "")
            self.mood_emoji = _parsed.get("mood_emoji", "😊")
        except (json.JSONDecodeError, TypeError):
            self.inner_voice = _reason
            self.mood_emoji = "😊"
        self.stage, self.stage_emoji = calc_stage(self.affinity)
        self.prev_stage = self.stage

    def get(self) -> dict[str, Any]:
        """返回 get_affinity 所需的字段结构。"""
        return {
            "affinity": self.affinity,
            "trust": self.trust,
            "mood": self.mood,
            "guard": self.guard,
            "reason": self.affinity_reason,
            "inner_voice": self.inner_voice,
            "mood_emoji": self.mood_emoji,
            "stage": self.stage,
            "stage_emoji": self.stage_emoji,
        }
