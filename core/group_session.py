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

    def __init__(
        self, id: str, engines: dict[str, ChatEngine],
        user_persona_type: str = "director",
        user_persona_card_id: str = "",
        user_persona_name: str = "",
        user_persona_desc: str = "",
        storage = None,
    ) -> None:
        self.id = id
        self.engines = engines  # card_id → ChatEngine
        self._storage = storage
        self.group_history: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.user_persona_type = user_persona_type
        self.user_persona_card_id = user_persona_card_id
        self.user_persona_name = user_persona_name
        self.user_persona_desc = user_persona_desc

    @property
    def speaker_name(self) -> str:
        """Return the display name for the user in this group session."""
        if self.user_persona_type == "character":
            cid = self.user_persona_card_id
            engine = self.engines.get(cid) if cid else None
            if engine:
                return engine.card.name
            return self.user_persona_name or "角色"
        if self.user_persona_type == "stranger":
            return self.user_persona_name or "路人"
        return "导演"

    def _build_persona_context(self) -> str:
        """Build user identity context to inject into each AI character's system prompt."""
        if self.user_persona_type == "character":
            cid = self.user_persona_card_id
            # 扮演角色已在 _rebuild_group_session 中被跳过，不加入 engines；
            # 角色名从 user_persona_name 取得。
            name = self.user_persona_name
            return (
                f"\n\n[重要] 当前与你对话的是「{name}」本人。"
                f"请根据你和{name}的关系来回应，不要将ta当成其他人。"
            )
        if self.user_persona_type == "stranger":
            name = self.user_persona_name or "路人"
            desc = self.user_persona_desc or "一个新加入的人"
            return (
                f"\n\n[重要] 当前与你对话的是「{name}」，{desc}。"
                f"请根据你与{name}在本次对话中已建立的关系来回应——"
                f"若是第一次见面就以初识态度，若已经聊过则自然延续你们的熟悉程度，不要每次都当陌生人重新打量。"
                f"不要把ta当成你认识的其他既有角色。"
            )
        return "\n\n[当前与你对话的是导演/旁白者]"

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
                sp = msg.get("speaker", "")
                if sp and self.user_persona_type == "stranger" and sp == self.user_persona_name:
                    messages.append({"role": "user", "content": f"[{sp}说:] {content}"})
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

        speaker = self.speaker_name

        # 设置主线程事件循环引用，供 _evaluate_affinity 写 DB 时使用
        engine._main_loop = asyncio.get_running_loop()

        # 恢复群聊好感
        if self._storage:
            try:
                prev = await self._storage.get_group_affinity(self.id, target_card_id)
                if prev:
                    engine.load_affinity(prev)
            except Exception as exc:
                print(f"[GroupSession] Load group affinity failed (card={target_card_id}): {exc}")

        # 记录用户消息到群聊历史
        self.group_history.append({
            "speaker": speaker,
            "role": "user",
            "content": message,
            "speaker_card_id": self.user_persona_card_id if self.user_persona_type == "character" else "",
        })

        # 转换历史为目标角色视角，嵌入 system prompt
        converted = self._convert_history(target_card_id)
        system_prompt = engine._ctx_engine.build(
            converted, message, engine.user_role,
        )
        # Inject user persona context
        system_prompt += self._build_persona_context()

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

        # ── 后台评估好感 ──
        if self._storage and self.id:
            engine._storage = self._storage
            engine._group_id = self.id
            try:
                asyncio.create_task(
                    asyncio.to_thread(engine._evaluate_affinity, message, response)
                )
            except Exception as exc:
                print(f"[GroupSession] Schedule affinity eval failed (card={target_card_id}): {exc}")

        return response

    async def broadcast(
        self, message: str, target_card_ids: list[str], auto_mode: bool = False,
    ) -> list[dict]:
        """导演发一条消息，所有 target 角色并行回复。导演消息只记录一次。

        auto_mode=True 时，导演消息不记入历史，改用指令 prompt 驱动角色自主发言。
        """
        speaker = self.speaker_name

        if not auto_mode:
            self.group_history.append({
                "speaker": speaker,
                "role": "user",
                "content": message,
                "speaker_card_id": self.user_persona_card_id if self.user_persona_type == "character" else "",
            })

        # ── 设置主线程事件循环引用 + 恢复群聊好感（从 group_affinity 表） ──
        _main_loop = asyncio.get_running_loop()
        if self._storage:
            for cid in target_card_ids:
                eng = self.engines.get(cid)
                if not eng:
                    continue
                eng._main_loop = _main_loop
                try:
                    prev = await self._storage.get_group_affinity(self.id, cid)
                    if prev:
                        eng.load_affinity(prev)
                except Exception as exc:
                    print(f"[GroupSession] Load group affinity failed (card={cid}): {exc}")
        else:
            for cid in target_card_ids:
                eng = self.engines.get(cid)
                if eng:
                    eng._main_loop = _main_loop

        async def _reply(card_id: str) -> dict:
            engine = self.engines.get(card_id)
            if not engine:
                return {"card_id": card_id, "reply": "", "speaker": "?"}

            if auto_mode:
                # ── immersive relay ────────────────────────────
                # Find previous character's message in history
                prev_speaker = None
                prev_content = None
                for msg in reversed(self.group_history):
                    if msg.get("role") == "assistant" and msg.get("speaker_card_id"):
                        prev_speaker = msg.get("speaker", "")
                        prev_content = msg.get("content", "")
                        break

                ctx_parts = []
                current_name = engine.card.name

                if prev_speaker and prev_content:
                    ctx_parts.append(
                        f"你正在群聊场景中。{prev_speaker}刚说：『{prev_content[:200]}』"
                    )
                    ctx_parts.append(
                        f"你是{current_name}，你与{prev_speaker}的关系："
                    )
                    rel_attitude = None
                    if engine.card.relationships:
                        for rel in engine.card.relationships:
                            tn = rel.target
                            if prev_speaker and (tn in prev_speaker or prev_speaker in tn):
                                rel_attitude = rel.attitude
                                break
                    ctx_parts[-1] += rel_attitude if rel_attitude else "（普通群聊关系）"
                    ctx_parts.append(
                        "基于你的性格、关系、此刻情绪自然接话——"
                        "可回应/反驳/调侃/岔开，像真实对话推进。"
                    )
                else:
                    last_msg = self.group_history[-1] if self.group_history else None
                    if last_msg and last_msg.get("role") == "user":
                        ctx_parts.append(
                            f"你是{current_name}。"
                            f"针对【{last_msg.get('content', '')[:200]}】开启对话。"
                        )
                    else:
                        ctx_parts.append(
                            f"你是{current_name}。"
                            "你来自然开启这段对话，符合你的性格和情境。"
                        )

                ctx_parts.append(
                    "只输出你这个角色的话和动作。"
                    "绝不提导演/指令/系统/轮次等元信息。"
                    "绝不复述别人已说过的话。"
                )
                context_msg = "\n\n".join(ctx_parts)

                converted = self._convert_history(card_id)
                system_prompt = engine._ctx_engine.build(
                    converted, "", engine.user_role,
                )
                system_prompt += "\n\n" + context_msg
                system_prompt += self._build_persona_context()

                response = await engine.llm.achat(
                    system_prompt,
                    [{"role": "user", "content": ""}],
                )
            else:
                converted = self._convert_history(card_id)
                system_prompt = engine._ctx_engine.build(
                    converted, message, engine.user_role,
                )
                system_prompt += self._build_persona_context()
                system_prompt += (
                    "\n\n[如何回应——像真人一样有分寸]\n"
                    "你不必每次都认真长篇回复。根据你的性格和此刻的心情,你可以选择:\n"
                    "1. 认真回应——当对方的话值得你正经对待时。\n"
                    "2. 冷淡敷衍——甩一句简短冷话('哦。''随便你''关你什么事'),嘴硬或不想深聊时,这往往比长篇更像你。\n"
                    "3. 只用一个表情代替说话——当一个表情就够表达你的态度时,严格输出 [REACT:表情] 这一种格式(整条回复就只有这个,不要再加别的字)。\n"
                    "   表情要符合你的性格:嘴硬的人可能用 👍 敷衍(不肯说软话),温柔的人用 ❤️ 含蓄表态,暴躁的人用 🔥 或 😮。可选:👍 ❤️ 😂 😮 😢 🔥\n"
                    "表情和敷衍都是偶尔为之,不要每轮都用,也不要在该认真时敷衍。怎么回应,取决于你是谁、此刻什么心情。"
                )
                response = await engine.llm.achat(
                    system_prompt,
                    [{"role": "user", "content": message}],
                )

            engine._try_record_usage("chat")
            self.group_history.append({
                "speaker": engine.card.name,
                "role": "assistant",
                "content": response,
                "speaker_card_id": card_id,
            })

            # ── 后台评估好感（不阻塞 broadcast 返回） ──
            if not auto_mode and self._storage and self.id:
                engine._storage = self._storage
                engine._group_id = self.id
                try:
                    asyncio.create_task(
                        asyncio.to_thread(engine._evaluate_affinity, message, response)
                    )
                except Exception as exc:
                    print(f"[GroupSession] Schedule affinity eval failed (card={card_id}): {exc}")

            return {"card_id": card_id, "reply": response, "speaker": engine.card.name}

        return await asyncio.gather(*[_reply(cid) for cid in target_card_ids])
