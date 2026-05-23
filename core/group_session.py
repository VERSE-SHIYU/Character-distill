"""Multi-character group chat with director mode."""

from __future__ import annotations

import asyncio
from typing import Any

from core.chat_engine import ChatEngine


class GroupSession:
    """多角色群聊导演模式。

    持有多个 ChatEngine（每个角色一个），共享群聊历史。
    send(target_card_id, message) 将历史转换为目标角色的视角后调用 LLM。
    """

    def __init__(self, id: str, engines: dict[str, ChatEngine]) -> None:
        self.id = id
        self.engines = engines  # card_id → ChatEngine
        self.group_history: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()

    def _convert_history(self, target_card_id: str) -> list[dict[str, str]]:
        """将群聊历史转换为目标角色的视角。

        - 目标角色自己的历史发言 → assistant（"你"）
        - 其他角色的发言 → user（"对方[名字说:]"）
        - 导演（无 speaker_card_id）的发言 → user
        """
        messages = []
        for msg in self.group_history:
            speaker_card_id = msg.get("speaker_card_id", "")
            content = msg.get("content", "")

            if speaker_card_id == target_card_id:
                messages.append({"role": "assistant", "content": content})
            elif speaker_card_id:
                name = msg.get("speaker", "未知")
                messages.append({"role": "user", "content": f"[{name}说:] {content}"})
            else:
                messages.append({"role": "user", "content": content})
        return messages

    def send(self, target_card_id: str, message: str) -> str:
        """向群聊中指定角色发消息，返回该角色的回复。"""
        engine = self.engines.get(target_card_id)
        if not engine:
            raise ValueError(f"Character {target_card_id} not in this group")

        # 记录导演消息到群聊历史
        self.group_history.append({
            "speaker": "导演",
            "role": "user",
            "content": message,
            "speaker_card_id": "",
        })

        # 转换历史为目标角色视角，嵌入 system prompt
        converted = self._convert_history(target_card_id)
        system_prompt = engine._ctx_engine.build(
            converted, message, engine.user_role,
        )

        # 直接调用 LLM，不走 engine.chat() 以免污染单聊历史
        response = engine.llm.chat(
            system_prompt,
            [{"role": "user", "content": message}],
        )

        # 记录角色回复到群聊历史
        self.group_history.append({
            "speaker": engine.card.name,
            "role": "assistant",
            "content": response,
            "speaker_card_id": target_card_id,
        })

        return response
