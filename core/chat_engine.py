"""基于角色卡与 RAG 上下文的角色扮演对话引擎。"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Generator
from typing import Any

from adapters.llm_adapter import LLMAdapter
from core.context_engine import ContextEngine
from core.rag import RAGEngine
from core.schema import CharacterCard
from core.utils import try_record_usage


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

        # 新会话：动态计算初始好感度（load_affinity 会在恢复旧会话时覆盖）
        if not self._session_id:
            try:
                init_data = self._compute_initial_affinity(card, user_role)
                self._affinity = max(0, min(100, init_data.get("affinity", 50)))
                self._trust = max(0, min(100, init_data.get("trust", 30)))
                self._mood = init_data.get("mood", "平静")
                self._guard = max(0, min(100, init_data.get("guard", 70)))
                self._affinity_reason = init_data.get("reason", "")
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
                return
            except Exception:
                pass
        self._affinity = data.get("affinity", 50)
        self._trust = data.get("trust", 30)
        self._mood = data.get("mood", "平静")
        self._guard = data.get("guard", 70)
        self._affinity_reason = data.get("reason", "")

    def _evaluate_affinity(self, user_message: str, assistant_reply: str) -> None:
        """异步评估四维好感度变化。"""
        if not self.llm:
            return
        if not getattr(self, 'affinity_enabled', True):
            return

        user_role = (self.user_role or "对方").strip()

        prompt = (
            "你是情感分析专家。根据以下角色设定和对话，评估角色对对方的情感状态变化。\n\n"
            f"角色：{self.card.name}\n"
            f"价值观：{', '.join(self.card.values[:3])}\n"
            f"内在矛盾：{', '.join(self.card.inner_tensions[:2])}\n"
            f"人际关系中对话者的位置：{user_role}\n\n"
            f"当前状态：好感={self._affinity}, 信任={self._trust}, 情绪={self._mood}, 防御={self._guard}\n\n"
            f"对方说：{user_message}\n"
            f"角色回复：{assistant_reply}\n\n"
            "根据角色性格，输出 JSON（只输出 JSON）：\n"
            '{"affinity": 数字0-100, "trust": 数字0-100, "mood": "平静/开心/悲伤/愤怒/紧张/甜蜜", '
            '"guard": 数字0-100, "reason": "一句话原因"}\n'
            "变化规则：\n"
            "- 单次变化幅度不超过 ±10\n"
            "- 踩到角色雷点（背叛、谎言、触及旧伤）→ 好感-5~-10，防御+10\n"
            "- 温柔关心但不逼迫 → 好感+3~5，防御-3~5\n"
            "- 表白/深情告白 → 好感±看角色性格，防御+5（害怕）\n"
            "- 日常闲聊 → 变化 ±1~2"
        )

        storage = self._storage
        session_id = self._session_id

        def _do():
            try:
                reply = self.llm.chat(
                    "你是精确的JSON输出器，只输出JSON。",
                    [{"role": "user", "content": prompt}],
                )
                m = re.search(r'\{.*\}', reply, re.DOTALL)
                if not m:
                    return
                data = json.loads(m.group())
                self._affinity = max(0, min(100, data.get("affinity", self._affinity)))
                self._trust = max(0, min(100, data.get("trust", self._trust)))
                self._mood = data.get("mood", self._mood)
                self._guard = max(0, min(100, data.get("guard", self._guard)))
                self._affinity_reason = data.get("reason", "")
                if storage and session_id:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(
                        storage.update_session_affinity(
                            session_id, self._affinity, self._trust,
                            self._mood, self._guard, self._affinity_reason,
                        )
                    )
                    loop.close()
            except Exception as exc:
                print(f"[ChatEngine] Affinity eval failed: {exc}")

        threading.Thread(target=_do, daemon=True).start()

    def _compute_initial_affinity(
        self,
        card: CharacterCard,
        user_role: str,
    ) -> dict[str, Any]:
        """根据角色卡人际关系 + 用户扮演身份，动态计算初始四维好感度。

        关系判定优先级：亲密度 > 对立度 > 普通相识 > 陌生人。
        """
        user = (user_role or "").strip()

        # 无身份：默认陌生人
        if not user:
            return {
                "affinity": 50, "trust": 30, "mood": "平静", "guard": 70,
                "reason": f"{card.name} 对陌生人保持中立",
            }

        # 遍历角色卡人际关系列表，找到匹配的用户身份
        for rel in (card.relationships or []):
            target = (rel.target or "").strip()
            if not target:
                continue
            # Guard: require ≥2 chars for substring match to avoid single-char
            # false positives (e.g. "明" matching "明朝学生")
            if target != user and not (
                len(target) >= 2 and len(user) >= 2
                and (target in user or user in target)
            ):
                continue
            relation = (rel.relation or "").lower()
            attitude = (rel.attitude or "").lower()

            # 1) 亲密关系
            _close = ["朋友", "兄弟", "姐妹", "挚友", "搭档", "队友",
                       "恋人", "情侣", "夫妻", "家人", "亲人",
                       "父子", "父女", "母子", "母女"]
            if any(w in relation for w in _close):
                has_conflict = any(w in attitude for w in ["矛盾", "复杂", "爱恨", "疏远", "冷战"])
                if has_conflict:
                    return {
                        "affinity": 70, "trust": 55, "mood": "紧张", "guard": 68,
                        "reason": f"{card.name} 与 {target}（{rel.relation}）关系复杂，心存芥蒂",
                    }
                return {
                    "affinity": 82, "trust": 72, "mood": "开心", "guard": 48,
                    "reason": f"{card.name} 视 {target} 为{rel.relation}",
                }

            # 2) 对立关系
            _hostile = ["敌人", "仇人", "对手", "情敌", "死敌"]
            if any(w in relation for w in _hostile):
                return {
                    "affinity": 22, "trust": 15, "mood": "紧张", "guard": 92,
                    "reason": f"{card.name} 视 {target} 为{rel.relation}，充满敌意",
                }

            # 3) 普通相识
            _acquaintance = ["同学", "同事", "邻居", "认识", "普通", "路人", "同行"]
            if any(w in relation for w in _acquaintance):
                return {
                    "affinity": 60, "trust": 42, "mood": "平静", "guard": 62,
                    "reason": f"{card.name} 认识 {target}（{rel.relation}），关系普通",
                }

            # 4) 其他已知关系（兜底）
            return {
                "affinity": 55, "trust": 40, "mood": "平静", "guard": 65,
                "reason": f"{card.name} 与 {target} 是{rel.relation}",
            }

        # 未匹配到任何关系 → 陌生人。根据 user_role 语义微调
        _fan_words = ["粉丝", "歌迷", "影迷", "书迷"]
        _neutral = ["路人", "陌生人", "顾客", "记者", "学生"]
        if any(w in user for w in _fan_words):
            return {
                "affinity": 65, "trust": 40, "mood": "开心", "guard": 55,
                "reason": f"{card.name} 对{user}保持友好但有所保留",
            }
        if any(w in user for w in _neutral) or "路" in user:
            return {
                "affinity": 45, "trust": 25, "mood": "平静", "guard": 82,
                "reason": f"{card.name} 对陌生人{user}保持警惕",
            }

        # 完全未知身份
        return {
            "affinity": 48, "trust": 28, "mood": "平静", "guard": 75,
            "reason": f"{card.name} 不认识{user}，态度谨慎",
        }

    def get_affinity(self) -> dict[str, Any]:
        return {
            "affinity": self._affinity,
            "trust": self._trust,
            "mood": self._mood,
            "guard": self._guard,
            "reason": self._affinity_reason,
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
