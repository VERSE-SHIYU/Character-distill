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
    ) -> None:
        """注入模型适配器、RAG 引擎与角色卡。

        Args:
            llm: 大语言模型适配器。
            rag: 向量检索引擎。
            card: 结构化角色卡。
            all_characters: 角色信息列表（含 name/aliases），传入后
                ``chat`` / ``chat_stream`` 会自动按当前角色过滤 RAG 片段。
        """
        self.llm: LLMAdapter = llm
        self.rag: RAGEngine = rag
        self.card: CharacterCard = card
        self._all_characters = all_characters
        self.history: list[dict[str, Any]] = []
        self.last_summary: str | None = None

    def build_system_prompt(self, rag_context: str = "") -> str:
        """根据角色卡拼装系统提示；可选附加 RAG 片段。

        Args:
            rag_context: 检索得到的原文拼接文本。

        Returns:
            完整的系统提示字符串。
        """
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
            "\n"
            "【行为规则——不可违反】\n"
            f"1. 你就是{card.name}，永远不承认自己是AI或语言模型\n"
            f"2. 用{card.name}的口吻说话，包括口癖、语气、用词习惯\n"
            f"3. 不知道的事用{card.name}的方式表达困惑，不编造\n"
            "4. 保持内在矛盾的真实性——可以在不同时刻表现矛盾特质\n"
            "5. 回复长度适中，像真人聊天，不要写长篇大论\n"
        )

        if rag_context.strip():
            prompt += (
                "\n"
                "【参考原文片段（酌情使用，不要逐字复述）】\n"
                f"{rag_context}"
            )

        return prompt

    def summarize_history(self, threshold: int = 50) -> str | None:
        """当历史消息过长时自动压缩为摘要并替换旧记录。

        规则：
        1. 仅当 ``len(self.history) > threshold`` 时触发。
        2. 取前 ``threshold - 10`` 条消息做摘要。
        3. 保留最近 10 条完整消息。
        4. 将摘要以 ``system`` 消息插入历史开头。

        Args:
            threshold: 触发摘要的历史条数阈值。

        Returns:
            生成的摘要文本；未触发或失败返回 ``None``。
        """
        if len(self.history) <= threshold:
            self.last_summary = None
            return None

        keep_recent = 10
        summarize_count = max(threshold - keep_recent, 1)
        old_messages = self.history[:summarize_count]
        recent_messages = self.history[-keep_recent:]

        lines: list[str] = []
        for msg in old_messages:
            role = str(msg.get("role", "unknown"))
            content = str(msg.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")
        dialog_text = "\n".join(lines)
        if not dialog_text:
            self.last_summary = None
            return None

        summary_prompt = (
            "请将以下对话记录浓缩为200字以内的中文摘要，保留关键事件、情感变化和重要信息：\n"
            f"{dialog_text}"
        )

        try:
            summary_text = self.llm.chat(
                "你是一个中文对话摘要助手。",
                [{"role": "user", "content": summary_prompt}],
            ).strip()
        except Exception as exc:
            print(f"生成对话摘要失败：{exc}")
            self.last_summary = None
            return None

        if not summary_text:
            self.last_summary = None
            return None

        summary_message = {
            "role": "system",
            "content": f"历史摘要：{summary_text}",
        }
        self.history = [summary_message, *recent_messages]
        self.last_summary = summary_text
        return summary_text

    def chat(self, user_message: str) -> tuple[str, str]:
        """非流式对话一轮，并返回模型回复与 RAG 上下文文本。

        Args:
            user_message: 用户输入。

        Returns:
            (模型回复, RAG 拼接上下文)。
        """
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

        try:
            response = self.llm.chat(system_prompt, self.history)
        except Exception as exc:
            print(f"调用 LLM 对话失败：{exc}")
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            raise

        self.history.append({"role": "assistant", "content": response})
        self.summarize_history()
        return response, rag_context

    def chat_stream(self, user_message: str) -> Generator[str, None, None]:
        """流式对话：逐块产出文本，结束后写入助手回复。

        Args:
            user_message: 用户输入。

        Yields:
            模型增量文本片段。
        """
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

        collected: list[str] = []

        try:
            for piece in self.llm.chat_stream(system_prompt, self.history):
                collected.append(piece)
                yield piece
        except Exception as exc:
            print(f"流式调用 LLM 失败：{exc}")
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            raise

        self.history.append({"role": "assistant", "content": "".join(collected)})
        self.summarize_history()

    def reset(self) -> None:
        """清空对话历史。"""
        self.history = []
        self.last_summary = None
