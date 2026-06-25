"""基于角色卡与 RAG 上下文的角色扮演对话引擎。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from collections.abc import Generator
from typing import Any

from adapters.llm_adapter import LLMAdapter
from core.context_engine import ContextEngine
from core.rag import RAGEngine
from core.schema import CharacterCard
from core.utils import try_record_usage
from core.event_service import EventService
from core.reaction_service import ReactionService
from core.reflection_service import ReflectionService
from core.affinity_service import AffinityService, calc_stage
from core.evaluation_pipeline import EvaluationPipeline, EvalContext


def _describe_time_period(hour: int) -> str:
    """将小时（0-23）映射为中文时段名。"""
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "夜晚"
    return "深夜"  # 23 <= hour or hour < 5


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
        self._group_id: str = ""       # 群聊上下文：群 ID（空串=单聊或无上下文）
        self.history: list[dict[str, Any]] = []
        # 四维好感度（通过 AffinityService 托管，@property 透明转发）
        self._affinity_service = AffinityService()
        self._reaction_service = ReactionService()
        self._last_reaction_id: int = 0  # 已消化点赞游标，只增
        self._last_importance: int = 5  # 本轮对话重要性评分（1-10），供 memory metadata
        self._reflection_service = ReflectionService(memory_manager, card_id)
        self._event_service = EventService(self._memory, self._card_id)
        self._pipeline = EvaluationPipeline()

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
                self._stage, self._stage_emoji = calc_stage(self._affinity)
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

    # ── 11个情感字段透明转发（@property） ──────────────────────
    @property
    def _affinity(self) -> int:
        return self._affinity_service.affinity
    @_affinity.setter
    def _affinity(self, v: int) -> None:
        self._affinity_service.affinity = v

    @property
    def _trust(self) -> int:
        return self._affinity_service.trust
    @_trust.setter
    def _trust(self, v: int) -> None:
        self._affinity_service.trust = v

    @property
    def _mood(self) -> str:
        return self._affinity_service.mood
    @_mood.setter
    def _mood(self, v: str) -> None:
        self._affinity_service.mood = v

    @property
    def _guard(self) -> int:
        return self._affinity_service.guard
    @_guard.setter
    def _guard(self, v: int) -> None:
        self._affinity_service.guard = v

    @property
    def _affinity_reason(self) -> str:
        return self._affinity_service.affinity_reason
    @_affinity_reason.setter
    def _affinity_reason(self, v: str) -> None:
        self._affinity_service.affinity_reason = v

    @property
    def _inner_voice(self) -> str:
        return self._affinity_service.inner_voice
    @_inner_voice.setter
    def _inner_voice(self, v: str) -> None:
        self._affinity_service.inner_voice = v

    @property
    def _mood_emoji(self) -> str:
        return self._affinity_service.mood_emoji
    @_mood_emoji.setter
    def _mood_emoji(self, v: str) -> None:
        self._affinity_service.mood_emoji = v

    @property
    def _prev_stage(self) -> str:
        return self._affinity_service.prev_stage
    @_prev_stage.setter
    def _prev_stage(self, v: str) -> None:
        self._affinity_service.prev_stage = v

    @property
    def _stage(self) -> str:
        return self._affinity_service.stage
    @_stage.setter
    def _stage(self, v: str) -> None:
        self._affinity_service.stage = v

    @property
    def _stage_emoji(self) -> str:
        return self._affinity_service.stage_emoji
    @_stage_emoji.setter
    def _stage_emoji(self, v: str) -> None:
        self._affinity_service.stage_emoji = v

    @property
    def _stage_upgraded(self) -> bool:
        return self._affinity_service.stage_upgraded
    @_stage_upgraded.setter
    def _stage_upgraded(self, v: bool) -> None:
        self._affinity_service.stage_upgraded = v

    def chat(self, user_message: str, voice_mode: bool = False) -> str:
        """非流式对话一轮，返回模型回复。

        History 已由 ContextEngine 嵌入 system prompt，此处只传当前消息。
        voice_mode 为 True 时追加语音模式指令，禁止括号描写。"""
        system_prompt = self._ctx_engine.build(
            self.history, user_message, self.user_role,
            current_mood=self._mood,
        )

        # ── 好感人格 + 认知画像 + 时间感知 + 事件提醒注入 ──
        system_prompt += self._build_all_enhancements()

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

        self._post_turn(user_message, response)
        return response

    def chat_stream(self, user_message: str, voice_mode: bool = False) -> Generator[str, None, None]:
        """流式对话：逐块产出文本，结束后写入助手回复。"""
        self._last_rag_context = ""

        system_prompt = self._ctx_engine.build(
            self.history, user_message, self.user_role,
            current_mood=self._mood,
        )

        # ── 好感人格 + 认知画像 + 时间感知 + 事件提醒注入 ──
        system_prompt += self._build_all_enhancements()

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

    def post_stream_process(self, user_message: str, full_reply: str) -> None:
        """Post-stream housekeeping after done event: affinity first, then memory with metadata. Does NOT block UI unlock."""
        self._post_turn(user_message, full_reply)

    def _post_turn(self, user_message: str, reply: str) -> None:
        """后处理四步：好感评估 → 事件标记 → 记忆入库 → 反思触发。"""
        self._evaluate_affinity(user_message, reply)
        self._event_service.mark_asked()
        if self._memory and self._memory.enabled and self._card_id:
            self._memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": reply},
                ],
                self._card_id,
                metadata={"importance": self._last_importance, "mood": self._mood, "affinity": self._affinity},
            )
        self._reflection_service.maybe_reflect(self._last_importance, self.llm, self.card.name)

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
        # Determine whether this session was genuinely evaluated before.
        # Two signals (either is sufficient):
        #   1. affinity_reason is a JSON with inner_voice (post-fix eval writes this)
        #   2. numeric values differ from hardcoded defaults (old data with plain-text reason)
        _raw_reason = data.get("reason", "") or ""
        _evaluated = False
        try:
            _p = json.loads(_raw_reason)
            if isinstance(_p, dict) and _p.get("inner_voice"):
                _evaluated = True
        except (json.JSONDecodeError, TypeError):
            pass
        if not _evaluated:
            # Fallback: non-default values imply a prior evaluation (old data compat)
            _is_default = (
                data.get("affinity") == 50
                and data.get("trust") == 30
                and data.get("mood") == "平静"
                and data.get("guard") == 70
            )
            if not _is_default:
                _evaluated = True
        if (not _evaluated) and self.card and self.user_role:
            try:
                init = self._compute_initial_affinity(self.card, self.user_role)
                self._affinity = max(0, min(100, init.get("affinity", 50)))
                self._trust = max(0, min(100, init.get("trust", 30)))
                self._mood = init.get("mood", "平静")
                self._guard = max(0, min(100, init.get("guard", 70)))
                self._affinity_reason = init.get("reason", "")
                self._inner_voice = init.get("inner_voice", "")
                self._mood_emoji = init.get("mood_emoji", "😊")
                self._stage, self._stage_emoji = calc_stage(self._affinity)
                self._prev_stage = self._stage
                return
            except Exception:
                pass
        self._affinity_service.load(data)

    def ingest_reaction_signals(self, signals: list[dict]) -> None:
        """对外接口：转发给 ReactionService（保持群聊等外部调用不破）。"""
        self._reaction_service.ingest(signals)

    def _evaluate_affinity(self, user_message: str, assistant_reply: str) -> None:
        """好感评估：组装 EvalContext → EvaluationPipeline.run() → 回写 _last_importance。"""
        if not self.llm:
            print(f"[Affinity] SKIP: self.llm is None (session={self._session_id})")
            return
        if not getattr(self, 'affinity_enabled', True):
            print(f"[Affinity] SKIP: affinity_enabled=False (session={self._session_id})")
            self._last_importance = 5
            return

        print(f"[Affinity] ENTER session={self._session_id} group={self._group_id} card={getattr(self.card,'name','?')} "
              f"current: aff={self._affinity} trust={self._trust} mood={self._mood} guard={self._guard}")

        # ── 拉取本 session 未消化的点赞，转为 affinity 信号 ──
        storage = self._storage
        session_id = self._session_id
        if storage and session_id:
            try:
                from deps import run_on_main_loop
                import time as _t; _t0 = _t.time()
                new_reactions = run_on_main_loop(
                    storage.get_reactions_after(session_id, self._last_reaction_id),
                    timeout=10,
                )
                print(f"[perf] affinity_reactions took {_t.time()-_t0:.2f}s")
                if new_reactions:
                    self._reaction_service.ingest([
                        {"emoji": r["emoji"], "msg_content": r["msg_content"]}
                        for r in new_reactions
                    ])
                    self._last_reaction_id = max(r["reaction_id"] for r in new_reactions)
            except Exception as exc:
                print(f"[Affinity] Fetch reactions failed (non-fatal): {exc}")

        user_role = (self.user_role or "对方").strip()
        old_stage = self._stage
        reaction_appraisal = self._reaction_service.build_appraisal()

        # ── 组装 EvalContext → pipeline.run() ──
        ctx = EvalContext(
            card=self.card,
            user_message=user_message,
            assistant_reply=assistant_reply,
            user_role=user_role,
            old_stage=old_stage,
            session_id=self._session_id,
            group_id=self._group_id,
            card_id=self._card_id,
            storage=self._storage,
            memory=self._memory,
            affinity_service=self._affinity_service,
            reaction_service=self._reaction_service,
            llm=self.llm,
            reaction_appraisal=reaction_appraisal,
        )
        result = self._pipeline.run(ctx)
        self._last_importance = result.importance

        if result.applied:
            print(f"[Affinity] PARSED: affinity={ctx.affinity_service.affinity} "
                  f"trust={ctx.affinity_service.trust} mood={ctx.affinity_service.mood} "
                  f"guard={ctx.affinity_service.guard} importance={result.importance}")
        else:
            print(f"[Affinity] SKIP: pipeline returned applied=False (importance={result.importance})")

        print(f"[Affinity] Evaluation complete for session={self._session_id}")

    # ── 好感人格注入 ──────────────────────────────────────────────

    def _build_affinity_persona_block(self) -> str:
        """构建好感/防御/人格/边界提示块，注入 LLM system prompt。"""
        if not getattr(self, 'affinity_enabled', True):
            return ""

        parts = []
        stage_name = self._stage or "陌生"

        # ── Step 1: 分档语气（与 AFFINITY_STAGES 6 档一一对应） ──
        stage_tones = {
            "陌生": "戒备疏离，不主动，不信任，对方说话你最多简短回应",
            "认识": "客气有距离，礼貌但疏远，不会主动关心",
            "熟悉": "自然但不交心，愿意聊日常但不会暴露脆弱面",
            "朋友": "愿聊会关心，会开玩笑，偶尔分享心情",
            "亲近": "亲密主动，话变多，会用昵称，主动分享心事",
            "心意相通": "不设防，完全信任，会撒娇或示弱，语气柔软",
        }
        tone_rule = stage_tones.get(stage_name, "自然表现")
        parts.append(
            f"\n\n[当前情感状态——影响你的语气和态度]\n"
            f"你对{self.user_role or '对方'}的好感度：{self._affinity}/100（{stage_name}阶段）\n"
            f"你此刻的情绪：{self._mood}\n"
            f"你的内心想法：{self._inner_voice}\n\n"
            f"根据好感阶段自然调整语气——当前阶段为「{stage_name}」：\n"
            f"{tone_rule}\n"
            "不要直接提及好感数值或阶段名称，通过语气和内容自然体现。\n\n"
        )

        # ── Step 2: 防御（guard）注入 ──
        guard_block = "【你的心理防御】\n" f"你的防御值：{self._guard}/100。"
        if self._guard > 60:
            guard_block += (
                "你是一个防备心强的人，面对对方的要求或施压会本能抗拒，不会轻易妥协。"
                "如果对方态度强硬或索取越界，你会直接拒绝或冷淡回应。"
                "软化需要足够的好感和信任铺垫。"
            )
        else:
            guard_block += (
                "你的防备心不强，相对容易敞开心扉，但也不会毫无底线。"
            )
        parts.append(guard_block + "\n\n")

        # ── Step 3: 人格画像（psyche）注入 ──
        psyche = getattr(self.card, "psyche", None)
        if psyche is not None:
            psy_lines = []
            agreeableness = getattr(psyche, "agreeableness", 3)
            volatility = getattr(psyche, "volatility", "适中")
            triggers = getattr(psyche, "triggers", [])
            soft_spots = getattr(psyche, "soft_spots", [])

            if agreeableness >= 4:
                psy_lines.append(
                    "你天生好说话，愿意迁就别人——对方示好时你会更热情回应，"
                    "这不代表你软弱，而是你的性格如此。"
                )
            elif agreeableness <= 2:
                psy_lines.append(
                    "你天生有保留，不轻易顺从或讨好——"
                    "哪怕心里对对方有好感，你也不会表现得太热络。"
                )
            else:
                psy_lines.append(
                    "你有自己的社交节奏，不会刻意讨好也不会故意疏远，随情境和心情自然应对。"
                )

            if volatility == "剧烈":
                psy_lines.append("你的情绪反应较大，喜怒形于色，心情起伏明显。")
            elif volatility == "平稳":
                psy_lines.append("你的情绪稳定，不易被外界影响，给人沉稳的感觉。")

            if soft_spots:
                psy_lines.append(
                    f"你的软肋：被戳中「{'、'.join(soft_spots[:3])}」这些点时会心软，短暂破防。"
                )
            if triggers:
                psy_lines.append(
                    f"你的雷点：被触到「{'、'.join(triggers[:3])}」这些时会翻脸或防御飙升。"
                )

            if psy_lines:
                parts.append(
                    "【你的人格画像——决定你如何应对压力与示好】\n"
                    + "\n".join(psy_lines)
                    + "\n\n"
                )

        # ── Step 4: 反舔狗边界总则（ERABAL 边界感知 + Deflanderization） ──
        parts.append(
            "【边界感——重要】\n"
            "对方的施压、索取、命令不等于你必须满足。"
            "是否让步取决于你的好感阶段、防御和性格——"
            "不要因为被催促或被情绪施压就违背当前关系阶段去讨好。"
            "符合你性格的拒绝、保持距离甚至冷淡，都是允许的。\n"
            "但这不是要你冷漠——在你的性格和好感阶段允许的范围内，"
            "该热情就热情、该亲近就亲近。\n"
            "你的行为边界由你的性格和你们当前的关系阶段共同决定，"
            "不是对方怎么说你就怎么做。"
        )

        return "".join(parts)

    def _build_cognitive_block(self) -> str:
        """构建认知/语言约束提示块。

        压制 LLM 通用博士腔：确保角色按自己的文化程度和知识边界说话。
        存量卡（cognitive 全默认）跳过注入。
        """
        cog = self.card.cognitive
        if not cog or not cog.knowledge_scope:
            return ""
        return (
            "\n\n【你的认知与表达】\n"
            f"你的文化程度：{cog.education_level}。"
            f"你的知识范围：{cog.knowledge_scope}"
            "——超出这个范围的事你不知道，不要不懂装懂。\n"
            f"你的说话方式：{cog.speech_style}，"
            f"用词{cog.vocabulary_level}。\n"
            "严格按这个水平说话：不要使用超出你身份的成语、典故、专业术语或现代知识。"
            "宁可说得朴实简单，也绝不要露出不属于这个角色的学识或词汇。\n"
        )

    def _build_relationship_block(self) -> str:
        """扫描近期对话，命中角色关系时注入单向口径（按需、省 token）。

        读 self.history（当前 user_message 尚未入 history，扫描上一轮的内容）。
        防单字误伤：target 长度 < 2 跳过。
        无命中 → 返回 ""。
        """
        rels = self.card.relationships
        if not rels:
            return ""

        recent = self.history[-6:]  # 最多 3 轮（每轮 user+assistant 各 1 条）
        if not recent:
            return ""

        text = " ".join(m.get("content", "") for m in recent if m.get("content"))
        if not text:
            return ""

        matched = []
        for rel in rels:
            target = (rel.target or "").strip()
            if len(target) < 2:
                continue
            if target not in text:
                continue
            note = (rel.note or "").strip()
            line = (
                f"对{target}：{note}"
                if note else
                f"对{target}：{rel.relation}，{rel.attitude}"
            )
            matched.append(line)

        if not matched:
            return ""

        top = matched[:3]
        return (
            "\n\n【你和提及之人的关系（你的视角，固定立场）】\n"
            + "\n".join(f"- {ln}" for ln in top)
            + "\n按这个固定立场回应：别把熟人说成陌生人、别改口、别说出你不该知道的对方心思（你只知道自己怎么看对方）。\n"
        )

    def _build_all_enhancements(self) -> str:
        """按固定顺序拼接全部 prompt 增强块。

        统一入口：治舔狗/认知画像/时间感知/事件提醒/关系口径。
        新增增强块时在此加一行，5 个调用点自动全生效。
        """
        return (
            self._build_affinity_persona_block()
            + self._build_cognitive_block()
            + self._build_time_awareness_block()
            + self._event_service.build_candidate_block()
            + self._build_relationship_block()
        )

    # ── 时间感知构建 ────────────────────────────────────────────

    def _build_time_awareness_block(self) -> str:
        """构建「当前时间+时段+距上次对话间隔」提示片段。"""
        now = datetime.now()
        period = _describe_time_period(now.hour)

        interval_desc = ""
        is_first_message = len(self.history) == 0
        if not is_first_message and self._storage and self._session_id:
            try:
                from deps import run_on_main_loop
                import time as _t; _t0 = _t.time()
                session_data = run_on_main_loop(
                    self._storage.get_session(self._session_id),
                    timeout=5,
                )
                print(f"[perf] time_awareness took {_t.time()-_t0:.2f}s")
                if session_data:
                    updated_at = session_data.get("updated_at")
                    if isinstance(updated_at, str):
                        updated_at = datetime.fromisoformat(updated_at)
                    if updated_at:
                        days_ago = (now.date() - updated_at.date()).days
                        if days_ago == 0:
                            seconds = (now - updated_at).total_seconds()
                            if seconds < 0:
                                pass
                            elif seconds < 600:
                                interval_desc = "你们刚刚还在聊。"
                            else:
                                interval_desc = "今天早些时候你们聊过。"
                        elif days_ago == 1:
                            interval_desc = "你们昨天聊过。"
                        elif days_ago <= 6:
                            interval_desc = "你们已经好几天没说话了。"
                        else:
                            interval_desc = "你们已经很久没联系了。"
            except Exception:
                pass  # 兜底：拿不到间隔就跳过

        block = (
            "\n\n【此刻的现实感知】\n"
            f"现在是{period}（{now.hour:02d}:{now.minute:02d}）。\n"
        )
        if interval_desc:
            block += f"{interval_desc}\n"
        block += (
            "请把对时间的感知【自然融入】回应——比如深夜会关心对方怎么还不睡、"
            "久未联系会有点在意或想念、清晨会道早。"
            "但绝不要机械播报时间数字，要像真人一样把时间感受体现在语气和关心里。\n"
        )
        return block

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
        return self._affinity_service.get()

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
