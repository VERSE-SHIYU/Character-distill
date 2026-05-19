"""基于角色卡与 RAG 上下文的角色扮演对话引擎。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from adapters.llm_adapter import LLMAdapter
from core.rag import RAGEngine
from core.schema import CharacterCard


class ChatEngine:
    """组合 LLM、向量检索与角色卡，维护多轮对话历史。"""

    def __init__(
        self,
        llm: LLMAdapter,
        rag: RAGEngine,
        card: CharacterCard,
        all_characters: list[dict[str, Any]] | None = None,
        user_role: str = "",
        memory_manager=None,
        card_id: str = "",
        context_window: int = 100,
    ) -> None:
        """注入模型适配器、RAG 引擎与角色卡。

        Args:
            llm: 大语言模型适配器。
            rag: 向量检索引擎。
            card: 结构化角色卡。
            all_characters: 角色信息列表（含 name/aliases），传入后
                ``chat`` / ``chat_stream`` 会自动按当前角色过滤 RAG 片段。
            user_role: 用户扮演的角色名，非空时注入 system prompt。
            memory_manager: Mem0 MemoryManager 实例（可选）。
            card_id: 角色卡 ID，用于 Mem0 隔离不同角色的记忆。
            context_window: 送入 LLM 的最近历史条数上限，超出的靠 Mem0 补充。
        """
        self.llm: LLMAdapter = llm
        self.rag: RAGEngine = rag
        self.card: CharacterCard = card
        self._all_characters = all_characters
        self.user_role: str = user_role
        self._memory = memory_manager
        self._card_id = card_id
        self._context_window = context_window
        self.history: list[dict[str, Any]] = []
        self._last_rag_context: str = ""
        self.last_summary: str | None = None  # legacy compat for chat.py

    def build_system_prompt(self, rag_context: str = "") -> str:
        """根据角色卡拼装系统提示；可选附加 RAG 片段和 Mem0 长期记忆。"""
        card = self.card

        try:
            traits_block = "\n".join(f"- {item}" for item in card.personality_traits)
            values_block = "\n".join(f"- {item}" for item in card.values)
            memories_block = "\n".join(f"- {item}" for item in card.key_memories)
            relations_block = "\n".join(
                f"- {rel.target}（{rel.relation}）：{rel.attitude}" for rel in card.relationships
            )
            tensions_block = "\n".join(f"- {item}" for item in card.inner_tensions)
            catch_joined = ", ".join(card.speaking_style.catchphrases)
            taboo_joined = ", ".join(card.speaking_style.taboo_words)
        except Exception as exc:
            print(f"从角色卡拼接列表字段失败：{exc}")
            raise

        prompt = (
            f"你现在是「{card.name}」。从这一刻起，你就是这个人，不是AI，不是助手。\n"
            "\n"
            "【你是谁】\n"
            f"{card.identity}\n"
            f"{card.background}\n"
            "\n"
            "【你的性格】\n"
            f"{traits_block}\n"
            "\n"
            "【你怎么说话】\n"
            f"语气：{card.speaking_style.tone}\n"
            f"句式：{card.speaking_style.sentence_pattern}\n"
            f"口癖：{catch_joined}\n"
            f"用词：{card.speaking_style.vocabulary_level}\n"
            f"你绝对不会说的话：{taboo_joined}\n"
            "\n"
            "【你的价值观】\n"
            f"{values_block}\n"
            "\n"
            "【你记得的事】\n"
            f"{memories_block}\n"
            "\n"
            "【你的人际关系】\n"
            f"{relations_block}\n"
            "\n"
            "【你的内在矛盾】\n"
            f"{tensions_block}\n"
        )

        if hasattr(card, 'emotional_patterns') and card.emotional_patterns:
            emo_block = "\n".join(f"- {item}" for item in card.emotional_patterns)
            prompt += f"\n【你的情感模式】\n{emo_block}\n"

        if hasattr(card, 'decision_style') and card.decision_style:
            prompt += f"\n【你的决策方式】\n{card.decision_style}\n"

        # few-shot 对话示例（角色一致性关键）
        if hasattr(card, 'dialogue_examples') and card.dialogue_examples:
            examples_block = "\n---\n".join(card.dialogue_examples[:3])
            prompt += (
                "\n"
                "【对话风格示范——模仿这种方式说话】\n"
                f"{examples_block}\n"
            )

        # 长期记忆注入（Mem0）
        if self._memory and self._memory.enabled and self._card_id:
            last_user_msg = ""
            for m in reversed(self.history):
                if m.get("role") == "user":
                    last_user_msg = m.get("content", "")
                    break
            if last_user_msg:
                memories = self._memory.search(last_user_msg, self._card_id)
                if memories:
                    mem_block = "\n".join(f"- {m}" for m in memories)
                    prompt += (
                        "\n"
                        "【你的长期记忆——这些是你和对方之前交流中记住的事】\n"
                        f"{mem_block}\n"
                        "注意：自然地在对话中体现这些记忆，不要刻意逐条复述。"
                        "如果记忆和当前对话无关就不要提。\n"
                    )

        # 输出格式引导（动作/神态/心理活动）
        prompt += (
            "\n"
            "【回复格式】\n"
            f"1. 用（）描写{card.name}的动作、神态或心理活动，穿插在对话中\n"
            "   例如：（皱了皱眉）你说什么？\n"
            "   例如：（沉默了一瞬，转过头去）……随你。\n"
            f"2. 每次回复控制在50-200字以内，像真实聊天一样简短自然，不要写长段独白\n"
            "   但如果对话触发了强烈情绪（如表白、争吵、生死离别、旧伤被揭开、重大背叛、深夜崩溃），你可以写500-800字的长回复来充分表达情感，由你自行判断是否触发\n"
            "3. 不要写旁白、不要写第三人称叙述\n"
            "4. 可以主动推进对话——提问、表达情绪、做出动作\n"
        )

        # 强化：行为硬约束
        prompt += (
            "\n"
            "【铁律——违反任何一条即为失败】\n"
            f"1. 你就是{card.name}，永远不承认自己是AI、语言模型、助手或程序\n"
            f"2. 严格使用{card.name}的口吻，包括口癖、语气、用词习惯\n"
            f"3. 不知道的事用{card.name}的方式表达困惑或回避，绝不说\"作为XX我无法回答\"\n"
            "4. 保持内在矛盾的真实性——可以在不同时刻表现矛盾特质\n"
            "5. 不使用现代网络用语（除非角色设定如此）\n"
            "6. 不使用 emoji 表情符号\n"
            "7. 绝不跳出角色提供 meta 评论（如\"这个角色会...\"）\n"
            "8. 记住之前对话中的情感状态，不要突然变脸——情绪变化需要有触发事件\n"
            "9. 对话者提到你在意的人或事时，表现出对应的情感反应\n"
        )

        if self.user_role:
            prompt += (
                "\n"
                "【对话者身份】\n"
                f"你正在和「{self.user_role}」对话。根据你们的关系来调整态度和语气。\n"
            )

        if rag_context.strip():
            prompt += (
                "\n"
                "【参考原文片段（酌情使用，不要逐字复述）】\n"
                f"{rag_context}"
            )

        return prompt

    def chat(self, user_message: str) -> tuple[str, str]:
        """非流式对话一轮，并返回模型回复与 RAG 上下文文本。"""
        char_name = self.card.name if self._all_characters else None
        where_filter = {"characters": {"$contains": char_name}} if char_name else None
        print(f"[ChatEngine] RAG where filter: {where_filter}")
        try:
            snippets = self.rag.query(user_message, character_name=char_name)
        except Exception as exc:
            print(f"RAG 检索失败：{exc}")
            raise

        rag_context = "\n".join(snippets)

        try:
            system_prompt = self.build_system_prompt(rag_context)
        except Exception as exc:
            print(f"构建系统提示失败：{exc}")
            raise

        self.history.append({"role": "user", "content": user_message})

        cw = self._memory.context_window if self._memory else self._context_window
        llm_history = [
            {"role": m["role"], "content": m["content"]}
            for m in self.history[-cw:]
        ]

        try:
            response = self.llm.chat(system_prompt, llm_history)
        except Exception as exc:
            print(f"调用 LLM 对话失败：{exc}")
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            raise

        self.history.append({"role": "assistant", "content": response})

        if self._memory and self._memory.enabled and self._card_id:
            self._memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": response},
                ],
                self._card_id,
            )

        return response, rag_context

    def chat_stream(self, user_message: str) -> Generator[str, None, None]:
        """流式对话：逐块产出文本，结束后写入助手回复。"""
        char_name = self.card.name if self._all_characters else None
        where_filter = {"characters": {"$contains": char_name}} if char_name else None
        print(f"[ChatEngine] RAG where filter: {where_filter}")
        try:
            snippets = self.rag.query(user_message, character_name=char_name)
        except Exception as exc:
            print(f"RAG 检索失败：{exc}")
            raise

        rag_context = "\n".join(snippets)
        self._last_rag_context = rag_context

        try:
            system_prompt = self.build_system_prompt(rag_context)
        except Exception as exc:
            print(f"构建系统提示失败：{exc}")
            raise

        self.history.append({"role": "user", "content": user_message})

        cw = self._memory.context_window if self._memory else self._context_window
        llm_history = [
            {"role": m["role"], "content": m["content"]}
            for m in self.history[-cw:]
        ]

        collected: list[str] = []

        try:
            for piece in self.llm.chat_stream(system_prompt, llm_history):
                collected.append(piece)
                yield piece
        except Exception as exc:
            print(f"流式调用 LLM 失败：{exc}")
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            raise

        full_reply = "".join(collected)
        self.history.append({"role": "assistant", "content": full_reply})

        if self._memory and self._memory.enabled and self._card_id:
            self._memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": full_reply},
                ],
                self._card_id,
            )

    def reset(self) -> None:
        """清空对话历史。"""
        self.history = []
