"""基于角色卡与 RAG 上下文的角色扮演对话引擎。"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Generator
from typing import Any

from adapters.llm_adapter import LLMAdapter
from core.context_engine import ContextEngine
from core.rag import RAGEngine
from core.schema import CharacterCard
from core.utils import try_record_usage


# IOS₁₁ (Scientific Reports 2024) 11级亲密度 → Bogardus 7级社会距离映射
AFFINITY_STAGES = [
    (0, 18, "陌生", "🫥"),      # IOS 1-2, Bogardus 6-7
    (18, 36, "认识", "🙂"),     # IOS 3-4, Bogardus 4-5
    (36, 55, "熟悉", "😊"),     # IOS 5-6, Bogardus 3
    (55, 73, "朋友", "😄"),     # IOS 7-8, Bogardus 2
    (73, 91, "亲近", "🥰"),     # IOS 9-10, Bogardus 1-2
    (91, 101, "心意相通", "💕"), # IOS 11, Bogardus 1
]


def _calc_stage(affinity: int) -> tuple[str, str]:
    for lo, hi, name, emoji in AFFINITY_STAGES:
        if lo <= affinity < hi:
            return name, emoji
    return "陌生", "🫥"


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
        """注入模型适配器、RAG 引擎与角色卡。"""
        self.llm: LLMAdapter = llm
        self.rag: RAGEngine = rag
        self.card: CharacterCard = card
        self._all_characters = all_characters
        self.user_role: str = user_role
        self._memory = memory_manager
        self._card_id = card_id
        self._context_window = context_window
        self._storage = None
        self._user_id: str = ""
        self._session_id: str = ""
        self.history: list[dict[str, Any]] = []
        # 四维好感度
        self._affinity: int = 50
        self._trust: int = 30
        self._mood: str = "平静"
        self._guard: int = 70
        self._affinity_reason: str = ""
        self._inner_voice: str = ""
        self._mood_emoji: str = "😊"
        self._prev_stage: str = ""
        self._stage: str = ""
        self._stage_emoji: str = ""
        self._stage_upgraded: bool = False

        # 新会话：动态计算初始好感度（load_affinity 会在恢复旧会话时覆盖）
        if not self._session_id:
            try:
                init_data = self._compute_initial_affinity(card, user_role)
                self._affinity = max(0, min(100, init_data.get("affinity", 50)))
                self._trust = max(0, min(100, init_data.get("trust", 30)))
                self._mood = init_data.get("mood", "平静")
                self._guard = max(0, min(100, init_data.get("guard", 70)))
                self._affinity_reason = init_data.get("reason", "")
                self._inner_voice = init_data.get("inner_voice", "")
                self._mood_emoji = init_data.get("mood_emoji", "😊")
                self._stage, self._stage_emoji = _calc_stage(self._affinity)
                self._prev_stage = self._stage
            except Exception as exc:
                print(f"[ChatEngine] Initial affinity calc failed, using defaults: {exc}")

        self._last_rag_context: str = ""
        self.last_summary: str | None = None  # legacy compat for chat.py
        self._ctx_engine = ContextEngine(
            card=card,
            rag=rag,
            memory_manager=memory_manager,
            card_id=card_id,
            llm=llm,
            model=getattr(llm, "model", ""),
        )

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

        if hasattr(card, 'character_arc') and card.character_arc:
            arc_block = "\n".join(f"- {item}" for item in card.character_arc)
            prompt += f"\n【你的成长轨迹】\n{arc_block}\n"

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
            "10. 用户可能会用 emoji 表达情绪，请自然理解并回应，不要解释 emoji 含义。\n"
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

        # 情感状态注入（iPET ACL 2025 情绪渗透 + Kohne & Montag CHB 2026 行为模式）
        if getattr(self, 'affinity_enabled', True):
            prompt += (
                f"\n\n[当前情感状态——影响你的语气和态度]\n"
                f"你对{self.user_role or '对方'}的好感度：{self._affinity}/100\n"
                f"你此刻的情绪：{self._mood}\n"
                f"你的内心想法：{self._inner_voice}\n\n"
                "根据以上状态自然调整你的说话方式：\n"
                "- 好感<30时：冷淡、简短、不主动展开话题\n"
                "- 好感30-55时：礼貌但保持距离，偶尔敷衍\n"
                "- 好感55-73时：愿意聊，会开玩笑，偶尔关心对方\n"
                "- 好感>73时：话变多、语气亲近、会用昵称、主动分享心事\n"
                "- 好感>91时：完全信任，说话不设防，会撒娇或示弱\n"
                "不要直接提及数值，通过语气和内容自然体现。"
            )

        return prompt

    def chat(self, user_message: str, voice_mode: bool = False) -> str:
        """非流式对话一轮，返回模型回复。

        History 已由 ContextEngine 嵌入 system prompt，此处只传当前消息。
        voice_mode 为 True 时追加语音模式指令，禁止括号描写。"""
        system_prompt = self._ctx_engine.build(
            self.history, user_message, self.user_role,
        )

        if voice_mode:
            system_prompt += (
                "\n\n【语音模式——重要】\n"
                "当前为语音模式。严格遵循：\n"
                "1. 禁止使用任何括号（包括（）和「」等）描写动作、神态、心理活动或旁白\n"
                "2. 禁止输出任何非对话内容，只输出角色直接说出口的话语\n"
                "3. 不要添加任何舞台指示、动作描写或表情描写\n"
                "4. 直接说出角色想说的话，就像在真实语音通话中一样"
            )

        self.history.append({"role": "user", "content": user_message})

        try:
            response = self.llm.chat(
                system_prompt, [{"role": "user", "content": user_message}]
            )
        except Exception as exc:
            print(f"调用 LLM 对话失败：{exc}")
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            raise

        self._try_record_usage("chat", self.llm.last_usage)

        self.history.append({"role": "assistant", "content": response})

        if self._memory and self._memory.enabled and self._card_id:
            self._memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": response},
                ],
                self._card_id,
            )

        self._evaluate_affinity(user_message, response)

        return response

    def chat_stream(self, user_message: str, voice_mode: bool = False) -> Generator[str, None, None]:
        """流式对话：逐块产出文本，结束后写入助手回复。"""
        self._last_rag_context = ""

        system_prompt = self._ctx_engine.build(
            self.history, user_message, self.user_role,
        )

        if voice_mode:
            system_prompt += (
                "\n\n【语音模式——重要】\n"
                "当前为语音模式。严格遵循：\n"
                "1. 禁止使用任何括号（包括（）和「」等）描写动作、神态、心理活动或旁白\n"
                "2. 禁止输出任何非对话内容，只输出角色直接说出口的话语\n"
                "3. 不要添加任何舞台指示、动作描写或表情描写\n"
                "4. 直接说出角色想说的话，就像在真实语音通话中一样"
            )

        self.history.append({"role": "user", "content": user_message})

        collected: list[str] = []

        try:
            for piece in self.llm.chat_stream(
                system_prompt, [{"role": "user", "content": user_message}]
            ):
                collected.append(piece)
                yield piece
        except Exception as exc:
            print(f"流式调用 LLM 失败：{exc}")
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            raise

        self._try_record_usage("chat", self.llm.last_usage)

        full_reply = "".join(collected)
        if not full_reply.strip():
            print(f"[chat_stream] WARNING: LLM returned empty response (history={len(self.history)} messages, sp_len={len(system_prompt)} chars)")
        self.history.append({"role": "assistant", "content": full_reply})

        if self._memory and self._memory.enabled and self._card_id:
            self._memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": full_reply},
                ],
                self._card_id,
            )

        self._evaluate_affinity(user_message, full_reply)

    def _try_record_usage(self, action: str = "chat", usage: dict | None = None) -> None:
        try_record_usage(
            storage=self._storage,
            user_id=self._user_id,
            llm=self.llm,
            action=action,
            usage=usage,
            source="ChatEngine",
        )

    def load_affinity(self, data: dict[str, Any]) -> None:
        if not data:
            return
        # If DB still holds the hardcoded defaults, this session was never
        # evaluated — recompute from the current card's relationship settings
        # in case the card has been updated (e.g. "stranger" → "friend").
        is_default = (
            data.get("affinity") == 50
            and data.get("trust") == 30
            and data.get("mood") == "平静"
            and data.get("guard") == 70
        )
        if is_default and self.card and self.user_role:
            try:
                init = self._compute_initial_affinity(self.card, self.user_role)
                self._affinity = max(0, min(100, init.get("affinity", 50)))
                self._trust = max(0, min(100, init.get("trust", 30)))
                self._mood = init.get("mood", "平静")
                self._guard = max(0, min(100, init.get("guard", 70)))
                self._affinity_reason = init.get("reason", "")
                self._inner_voice = init.get("inner_voice", "")
                self._mood_emoji = init.get("mood_emoji", "😊")
                self._stage, self._stage_emoji = _calc_stage(self._affinity)
                self._prev_stage = self._stage
                return
            except Exception:
                pass
        self._affinity = data.get("affinity", 50)
        self._trust = data.get("trust", 30)
        self._mood = data.get("mood", "平静")
        self._guard = data.get("guard", 70)
        self._affinity_reason = data.get("reason", "")
        # 尝试 JSON 解析扩展数据（兼容旧格式纯文本 reason）
        _reason = self._affinity_reason or ""
        try:
            _parsed = json.loads(_reason)
            self._inner_voice = _parsed.get("inner_voice", "")
            self._mood_emoji = _parsed.get("mood_emoji", "😊")
        except (json.JSONDecodeError, TypeError):
            self._inner_voice = _reason
            self._mood_emoji = "😊"
        self._stage, self._stage_emoji = _calc_stage(self._affinity)
        self._prev_stage = self._stage

    def _evaluate_affinity(self, user_message: str, assistant_reply: str) -> None:
        """异步评估好感度变化：角色第一人称内心独白 + 情绪惯性 + 关系阶段。"""
        if not self.llm:
            print(f"[Affinity] SKIP: self.llm is None (session={self._session_id})")
            return
        if not getattr(self, 'affinity_enabled', True):
            print(f"[Affinity] SKIP: affinity_enabled=False (session={self._session_id})")
            return

        print(f"[Affinity] ENTER session={self._session_id} card={getattr(self.card,'name','?')} "
              f"current: aff={self._affinity} trust={self._trust} mood={self._mood} guard={self._guard}")

        user_role = (self.user_role or "对方").strip()
        # 记录旧阶段用于检测阶段变化
        old_stage = self._stage
        _values = getattr(self.card, 'values', []) or []
        _tensions = getattr(self.card, 'inner_tensions', []) or []

        prompt = (
            f"你现在就是{self.card.name}本人。\n"
            f"性格特征：{', '.join(_values[:3])}\n"
            f"内在矛盾：{', '.join(_tensions[:2])}\n"
            f"对话者身份：{user_role}\n\n"
            f"当前情感状态：好感={self._affinity}, 信任={self._trust}, 情绪={self._mood}, 防御={self._guard}\n"
            f"上一刻的内心想法：{self._inner_voice}\n\n"
            f"对方刚才说：{user_message}\n"
            f"你回复了：{assistant_reply}\n\n"
            "现在，用你自己的口吻写出你此刻真实的内心想法。\n\n"
            "要求：\n"
            "1. 用第一人称，用你的性格说话。傲娇不会直说喜欢，内向会犹豫，暴躁会骂人。\n"
            "2. 写2-3句内心独白，要有情绪的微妙层次，不要笼统的'我觉得还行'。\n"
            "3. 如果对方触碰了你的痛点或雷区，反应要激烈但符合你的性格。\n"
            "4. 如果上一刻你在生气，对方道歉了，你不应该立刻原谅——你需要时间消化。\n\n"
            "情绪惯性规则（依据AnnaAgent ACL 2025情绪动态演化模型）：\n"
            "- 单轮数值变化不超过 ±8\n"
            "- 正面情绪建立慢（+3~5/轮），负面情绪爆发快（-5~8/轮）\n"
            "- 防御值下降速度 = 信任上升速度的0.6倍（信任建立慢，防御松懈更慢）\n"
            "- 情绪有惯性：愤怒→道歉→不是立刻开心，而是'不甘+犹豫'的过渡态\n"
            "- 连续3轮正面互动才能触发阶段性好感跃升\n\n"
            "输出严格JSON格式（只输出JSON，不要任何其他内容）：\n"
            "{\n"
            '  "affinity": 0-100整数,\n'
            '  "trust": 0-100整数,\n'
            '  "mood": "具体情绪词（如释然/微酸/警觉/心软/嘴硬心软/又气又心疼/微微上头）",\n'
            '  "guard": 0-100整数,\n'
            '  "inner_voice": "你的第一人称内心独白2-3句",\n'
            '  "mood_emoji": "一个最贴合此刻情绪的emoji"\n'
            "}"
        )

        storage = self._storage
        session_id = self._session_id

        def _do():
            try:
                print(f"[Affinity] Calling LLM...")
                reply = self.llm.chat(
                    "你是精确的JSON输出器，只输出JSON。",
                    [{"role": "user", "content": prompt}],
                )
                print(f"[Affinity] LLM reply ({len(reply)} chars): {reply[:300]}")
                m = re.search(r'\{.*\}', reply, re.DOTALL)
                if not m:
                    print(f"[Affinity] FAIL: no JSON object found in LLM reply")
                    return
                print(f"[Affinity] JSON match: {m.group()[:200]}")
                data = json.loads(m.group())
                print(f"[Affinity] PARSED: affinity={data.get('affinity')} trust={data.get('trust')} "
                      f"mood={data.get('mood')} guard={data.get('guard')} "
                      f"inner_voice={str(data.get('inner_voice',''))[:80]}")
                self._affinity = max(0, min(100, data.get("affinity", self._affinity)))
                self._trust = max(0, min(100, data.get("trust", self._trust)))
                self._mood = data.get("mood", self._mood)
                self._guard = max(0, min(100, data.get("guard", self._guard)))
                self._inner_voice = data.get("inner_voice", self._inner_voice)
                self._mood_emoji = data.get("mood_emoji", self._mood_emoji)
                self._stage, self._stage_emoji = _calc_stage(self._affinity)
                self._stage_upgraded = self._stage != old_stage
                self._prev_stage = old_stage
                # 阶段升级时在内心独白末尾追加祝贺
                if self._stage_upgraded:
                    self._inner_voice += f"\n（我们的关系似乎更近了…现在是「{self._stage}」阶段）"
                # 扩展数据序列化存入 affinity_reason
                extended = {
                    "inner_voice": self._inner_voice,
                    "mood_emoji": self._mood_emoji,
                    "mood_word": self._mood,
                    "stage": self._stage,
                    "stage_emoji": self._stage_emoji,
                }
                self._affinity_reason = json.dumps(extended, ensure_ascii=False)
                print(f"[Affinity] UPDATED in-memory: aff={self._affinity} trust={self._trust} "
                      f"mood={self._mood} guard={self._guard}")
                if storage and session_id:
                    import sqlite3
                    try:
                        print(f"[Affinity] Saving to DB: {session_id}")
                        conn = sqlite3.connect(str(storage.db_path))
                        conn.execute(
                            "UPDATE sessions SET affinity=?, trust=?, mood=?, guard=?, affinity_reason=? WHERE id=?",
                            (self._affinity, self._trust, self._mood, self._guard, self._affinity_reason, session_id),
                        )
                        conn.commit()
                        conn.close()
                        print(f"[Affinity] DB save OK")
                    except Exception as db_exc:
                        print(f"[ChatEngine] Affinity DB save failed: {db_exc}")
            except Exception as exc:
                print(f"[ChatEngine] Affinity eval failed: {exc}")
                import traceback
                traceback.print_exc()

        # 同步执行（移除 threading，确保 fetchAffinity 能拿到最新值）
        _do()
        print(f"[Affinity] Evaluation complete for session={self._session_id}")

    def _compute_initial_affinity(
        self,
        card: CharacterCard,
        user_role: str,
    ) -> dict[str, Any]:
        """依据 IOS₁₁ (Scientific Reports 2024) + Trust Revisited (JSPR 2025) 计算初始值。"""
        user = (user_role or "").strip()

        # 无身份：陌生人（IOS₁₁ 1-2级）
        if not user:
            return {
                "affinity": 15, "trust": 10, "mood": "警觉", "guard": 85,
                "inner_voice": "谁？不认识。先看看什么情况。",
                "mood_emoji": "🫥",
                "reason": f"{card.name} 对陌生人保持高度警惕",
            }

        # 遍历角色卡人际关系列表
        for rel in (card.relationships or []):
            target = (rel.target or "").strip()
            if not target:
                continue
            if target != user and not (
                len(target) >= 2 and len(user) >= 2
                and (target in user or user in target)
            ):
                continue
            relation = (rel.relation or "").lower()
            attitude = (rel.attitude or "").lower()

            # 亲密关系（IOS₁₁ 9-10级，Bogardus 1级）
            _close = ["朋友", "兄弟", "姐妹", "挚友", "搭档", "队友",
                       "恋人", "情侣", "夫妻", "家人", "亲人",
                       "父子", "父女", "母子", "母女"]
            if any(w in relation for w in _close):
                has_conflict = any(w in attitude for w in ["矛盾", "复杂", "爱恨", "疏远", "冷战"])
                if has_conflict:
                    return {
                        "affinity": 68, "trust": 48, "mood": "紧张", "guard": 62,
                        "inner_voice": f"又见到{target}了...心里说不上来什么感觉，明明那么熟悉，却好像隔了什么。",
                        "mood_emoji": "😔",
                        "reason": f"{card.name} 与 {target}（{rel.relation}）关系复杂，心存芥蒂",
                    }
                return {
                    "affinity": 82, "trust": 72, "mood": "开心", "guard": 25,
                    "inner_voice": f"{target}来了，看到{target}心情就会好起来。",
                    "mood_emoji": "😊",
                    "reason": f"{card.name} 视 {target} 为{rel.relation}",
                }

            # 对立关系（IOS₁₁ 1级，Bogardus 7级）
            _hostile = ["敌人", "仇人", "对手", "情敌", "死敌"]
            if any(w in relation for w in _hostile):
                return {
                    "affinity": 10, "trust": 5, "mood": "敌意", "guard": 95,
                    "inner_voice": f"{target}...看到这个名字就来气。",
                    "mood_emoji": "😤",
                    "reason": f"{card.name} 视 {target} 为{rel.relation}，充满敌意",
                }

            # 普通相识（IOS₁₁ 5-6级，Bogardus 3-4级）
            _acquaintance = ["同学", "同事", "邻居", "认识", "普通", "路人", "同行"]
            if any(w in relation for w in _acquaintance):
                return {
                    "affinity": 50, "trust": 35, "mood": "平静", "guard": 58,
                    "inner_voice": f"是{target}啊，还行吧，不算陌生也不算多熟。",
                    "mood_emoji": "🙂",
                    "reason": f"{card.name} 认识 {target}（{rel.relation}），关系普通",
                }

            # 兜底
            return {
                "affinity": 40, "trust": 28, "mood": "平静", "guard": 60,
                "inner_voice": f"嗯，{target}来了，好好相处吧。",
                "mood_emoji": "🙂",
                "reason": f"{card.name} 与 {target} 是{rel.relation}",
            }

        # 未匹配关系 → 按 user_role 语义微调
        _fan_words = ["粉丝", "歌迷", "影迷", "书迷"]
        if any(w in user for w in _fan_words):
            return {
                "affinity": 38, "trust": 15, "mood": "平静", "guard": 68,
                "inner_voice": f"又一个{user}...客气点就好，保持距离。",
                "mood_emoji": "🙂",
                "reason": f"{card.name} 对{user}保持友好但有所保留",
            }

        # 完全陌生人（IOS₁₁ 1-2级，Bogardus 6-7级）
        return {
            "affinity": 15, "trust": 10, "mood": "警觉", "guard": 85,
            "inner_voice": "谁？不认识。先看看什么情况。",
            "mood_emoji": "🫥",
            "reason": f"{card.name} 不认识{user}，态度谨慎",
        }

    def get_affinity(self) -> dict[str, Any]:
        return {
            "affinity": self._affinity,
            "trust": self._trust,
            "mood": self._mood,
            "guard": self._guard,
            "reason": self._affinity_reason,
            "inner_voice": self._inner_voice,
            "mood_emoji": self._mood_emoji,
            "stage": self._stage,
            "stage_emoji": self._stage_emoji,
        }

    def reset(self) -> None:
        """清空对话历史。"""
        self.history = []

    def _should_retract(self, reply: str) -> bool:
        """轻量判断：角色是否会后悔说这句话。"""
        if not self.llm or not self.card:
            return False
        prompt = (
            f"你是「{self.card.name}」，性格：{self.card.identity}\n"
            f"你刚才说了：「{reply}」\n"
            f"根据你的性格，你会后悔说这句话并想撤回吗？"
            f"只回答 true 或 false，不要解释。"
        )
        try:
            result = self.llm.chat(prompt, [{"role": "user", "content": "请判断"}])
            return "true" in result.strip().lower()
        except Exception:
            return False
