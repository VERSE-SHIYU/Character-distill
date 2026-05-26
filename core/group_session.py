"""Multi-character group chat with director mode."""

from __future__ import annotations

import asyncio
from typing import Any

from core.chat_engine import ChatEngine
from core.context_engine import _count_tokens

MAX_HISTORY_TOKENS = 2000


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

        # Token truncation: drop oldest messages until under MAX_HISTORY_TOKENS
        while messages:
            total = sum(_count_tokens(m["content"]) for m in messages)
            if total <= MAX_HISTORY_TOKENS:
                break
            messages.pop(0)

        return messages

    async def send(self, target_card_id: str, message: str) -> str:
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
        response = await engine.llm.achat(
            system_prompt,
            [{"role": "user", "content": message}],
        )
        engine._try_record_usage("chat")

        # 记录角色回复到群聊历史
        self.group_history.append({
            "speaker": engine.card.name,
            "role": "assistant",
            "content": response,
            "speaker_card_id": target_card_id,
        })

        return response

    async def broadcast(
        self, message: str, target_card_ids: list[str], auto_mode: bool = False,
    ) -> list[dict]:
        """导演发一条消息，所有 target 角色并行回复。导演消息只记录一次。

        auto_mode=True 时，导演消息不记入历史，改用指令 prompt 驱动角色自主发言。
        """
        if not auto_mode:
            self.group_history.append({
                "speaker": "导演",
                "role": "user",
                "content": message,
                "speaker_card_id": "",
            })

        async def _reply(card_id: str) -> dict:
            engine = self.engines.get(card_id)
            if not engine:
                return {"card_id": card_id, "reply": "", "speaker": "?"}
            user_msg = (
                f"（导演指令：请{engine.card.name}根据当前对话情境，"
                f"自主说一句话或做出反应，推进剧情。）"
                if auto_mode else message
            )
            converted = self._convert_history(card_id)
            system_prompt = engine._ctx_engine.build(
                converted, user_msg, engine.user_role,
            )
            response = await engine.llm.achat(
                system_prompt,
                [{"role": "user", "content": user_msg}],
            )
            engine._try_record_usage("chat")
            self.group_history.append({
                "speaker": engine.card.name,
                "role": "assistant",
                "content": response,
                "speaker_card_id": card_id,
            })
            return {"card_id": card_id, "reply": response, "speaker": engine.card.name}

        return await asyncio.gather(*[_reply(cid) for cid in target_card_ids])
