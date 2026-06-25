"""点赞信号暂存与转译：接收外部点赞信号，消费为 OCC 归因文本。"""

from __future__ import annotations

from typing import Any


class ReactionService:
    """管理待消化的点赞信号队列，按需转译为 affinity prompt 文本。

    状态
    ----
    _pending : list[dict]
        尚未被 affinity 评估消费的点赞信号。
        每条结构: {emoji: str, msg_content: str}。

    不依赖任何外部服务——纯内存队列，无构造参数。
    """

    def __init__(self) -> None:
        self._pending: list[dict] = []

    def ingest(self, signals: list[dict]) -> None:
        """接收待消化的点赞信号。signals: [{emoji, msg_content}, ...]
        单聊/群聊在触发 affinity 评估前调用。空列表则无操作。"""
        if signals:
            self._pending.extend(signals)

    def build_appraisal(self) -> str:
        """把待消化点赞转成 OCC 归因文本注入 affinity prompt。消费后清空。"""
        if not self._pending:
            return ""
        lines = []
        for s in self._pending:
            emoji = s.get("emoji", "")
            content = (s.get("msg_content", "") or "")[:60]
            lines.append(f'  - 对方对你说过的「{content}」点了一个 {emoji}')
        self._pending = []  # 消费即清空，防重复
        joined = "\n".join(lines)
        return (
            f"\n对方刚刚还对你的话做了这些非言语回应（点赞）：\n{joined}\n"
            "这是对方对你的在意/认可，请按你的性格去体会：被点赞的是哪句话、"
            "用的什么表情，会在你心里激起什么（傲娇会嘴硬但心软，"
            "暴躁可能不屑但偷偷在意）。让它自然影响你下面的内心想法和情绪。\n\n"
        )
