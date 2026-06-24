"""基于角色卡与 RAG 上下文的角色扮演对话引擎。"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime
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
        # 待注入 affinity 评估的点赞信号（结构: [{emoji, msg_content}], 由外部 ingest）
        self._pending_reaction_signals: list[dict] = []
        self._last_reaction_id: int = 0  # 已消化点赞游标，只增
        self._last_importance: int = 5  # 本轮对话重要性评分（1-10），供 memory metadata
        self._reflection_importance_acc: int = 0  # 累计重要性，达阈值触发反思
        self._asked_event_ids: set[str] = set()  # 已问过的事件 ID（会话级）
        self._injected_event_id: str = ""  # 本轮注入的事件 ID（响应后标记为已问）

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

        self._evaluate_affinity(user_message, response)
        self._mark_event_asked()

        if self._memory and self._memory.enabled and self._card_id:
            self._memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": response},
                ],
                self._card_id,
                metadata={"importance": self._last_importance, "mood": self._mood, "affinity": self._affinity},
            )

        self._maybe_reflect()

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
        self._evaluate_affinity(user_message, full_reply)
        self._mark_event_asked()
        if self._memory and self._memory.enabled and self._card_id:
            self._memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": full_reply},
                ],
                self._card_id,
                metadata={"importance": self._last_importance, "mood": self._mood, "affinity": self._affinity},
            )

        self._maybe_reflect()

    def _maybe_reflect(self) -> None:
        """累计重要性达阈值时触发反思，综合高重要性原始记忆为高阶洞察。"""
        from core.memory_manager import REFLECTION_THRESHOLD

        self._reflection_importance_acc += self._last_importance
        if self._reflection_importance_acc < REFLECTION_THRESHOLD:
            return
        if not self._memory or not self._memory.enabled or not self._card_id:
            return
        if not self.llm:
            return

        print(f"[Reflection] Triggered: acc={self._reflection_importance_acc} >= {REFLECTION_THRESHOLD}")
        self._reflection_importance_acc = 0

        try:
            all_memories = self._memory.get_all(self._card_id)
        except Exception as exc:
            print(f"[Reflection] get_all failed: {exc}")
            return

        # 过滤：排除已有反思记忆，只要原始记忆
        raw = []
        for m in all_memories:
            if not isinstance(m, dict):
                continue
            meta = m.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("is_reflection"):
                continue
            text = m.get("memory", "").strip()
            if not text:
                continue
            imp = meta.get("importance", 5) if isinstance(meta, dict) else 5
            raw.append({"text": text, "importance": int(imp), "mood": meta.get("mood", "") if isinstance(meta, dict) else ""})

        # 按 importance 降序取 top-10
        raw.sort(key=lambda x: x["importance"], reverse=True)
        recent = raw[:10]

        if not recent:
            print("[Reflection] No raw memories to reflect on.")
            return

        print(f"[Reflection] {len(raw)} raw memories, top-10 importance range: "
              f"{recent[-1]['importance']}-{recent[0]['importance']}")
        self._memory.reflect(self._card_id, self.llm, recent, self.card.name)

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

    def ingest_reaction_signals(self, signals: list[dict]) -> None:
        """接收待消化的点赞信号。signals: [{emoji, msg_content}, ...]
        单聊/群聊在触发 affinity 评估前调用。空列表则无操作。"""
        if signals:
            self._pending_reaction_signals.extend(signals)

    def _build_reaction_appraisal(self) -> str:
        """把待消化点赞转成 OCC 归因文本注入 affinity prompt。消费后清空。"""
        if not self._pending_reaction_signals:
            return ""
        lines = []
        for s in self._pending_reaction_signals:
            emoji = s.get("emoji", "")
            content = (s.get("msg_content", "") or "")[:60]
            lines.append(f'  - 对方对你说过的「{content}」点了一个 {emoji}')
        self._pending_reaction_signals = []  # 消费即清空，防重复
        joined = "\n".join(lines)
        return (
            f"\n对方刚刚还对你的话做了这些非言语回应（点赞）：\n{joined}\n"
            "这是对方对你的在意/认可，请按你的性格去体会：被点赞的是哪句话、"
            "用的什么表情，会在你心里激起什么（傲娇会嘴硬但心软，"
            "暴躁可能不屑但偷偷在意）。让它自然影响你下面的内心想法和情绪。\n\n"
        )

    def _evaluate_affinity(self, user_message: str, assistant_reply: str) -> None:
        """异步评估好感度变化：角色第一人称内心独白 + 情绪惯性 + 关系阶段。"""
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
                _loop = getattr(self, '_main_loop', None)
                if _loop is not None:
                    new_reactions = asyncio.run_coroutine_threadsafe(
                        storage.get_reactions_after(session_id, self._last_reaction_id),
                        _loop,
                    ).result(timeout=10)
                else:
                    new_reactions = asyncio.run(
                        storage.get_reactions_after(session_id, self._last_reaction_id)
                    )
                if new_reactions:
                    self.ingest_reaction_signals([
                        {"emoji": r["emoji"], "msg_content": r["msg_content"]}
                        for r in new_reactions
                    ])
                    self._last_reaction_id = max(r["reaction_id"] for r in new_reactions)
            except Exception as exc:
                print(f"[Affinity] Fetch reactions failed (non-fatal): {exc}")

        user_role = (self.user_role or "对方").strip()
        # 记录旧阶段用于检测阶段变化
        old_stage = self._stage
        _values = getattr(self.card, 'values', []) or []
        _tensions = getattr(self.card, 'inner_tensions', []) or []
        psyche = self.card.psyche

        # ── 个性化基线规则 vs 通用回退 ──
        has_custom_psyche = (
            psyche.affinity_baseline != 50
            or psyche.volatility != "适中"
            or psyche.grudge_inertia != "一般"
            or bool(psyche.triggers)
            or bool(psyche.soft_spots)
        )
        if has_custom_psyche:
            baseline_rules = (
                f"情绪基线规则（依据Kuppens情感动力学—情感围绕个性化基线波动）：\n"
                f"- 你的关系基线大约在 {psyche.affinity_baseline}（满分100）——当前好感围绕这条基线波动，不会无限攀升，也很难长期大幅低于它；连续多轮真心相待，基线才会慢慢台阶式上移\n"
                f"- 你的情绪波动幅度是【{psyche.volatility}】的（剧烈=容易大起大落，平稳=情感很稳不会轻易起伏）\n"
                f"- 你消化负面情绪的方式是【{psyche.grudge_inertia}】（记仇=好感掉了很难回升，大度=很快回到基线不记仇）\n"
            )
            if psyche.triggers:
                baseline_rules += f"- 以下是你的雷点，被触碰会明显掉好感/防御飙升：{', '.join(psyche.triggers)}\n"
            if psyche.soft_spots:
                baseline_rules += f"- 以下是你的软肋，被戳中会让你心软、好感回升更快：{', '.join(psyche.soft_spots)}\n"
            baseline_rules += (
                "- 好感很难长时间大幅低于基线——除非对方严重背叛或伤害你，普通拌嘴过后会自然回到基线附近\n"
                "- 基线上移要慢、要台阶式；一旦上移，不会因小摩擦轻易回落\n\n"
            )
        else:
            baseline_rules = (
                "情绪基线规则（依据Kuppens情感动力学—情感围绕个性化基线波动）：\n"
                "- 你心里有一条\"关系基线\"，代表你对 ta 长期、稳定的态度，不等于此刻的一时情绪\n"
                "- 当前好感是围绕这条基线的波动：开心时高于基线，闹别扭时低于基线\n"
                "- 好感很难长时间大幅低于基线——除非对方严重背叛或伤害你，普通拌嘴过后会自然回到基线附近\n"
                "- 连续多轮真心相待，会让基线本身慢慢上移（关系真正变深），而不是因为一次拌嘴就退回原点\n"
                "- 基线上移要慢、要台阶式；一旦上移，不会因小摩擦轻易回落\n\n"
            )

        prompt = (
            f"你现在就是{self.card.name}本人。\n"
            f"性格特征：{', '.join(_values[:3])}\n"
            f"内在矛盾：{', '.join(_tensions[:2])}\n"
            f"对话者身份：{user_role}\n\n"
            f"当前情感状态：好感={self._affinity}, 信任={self._trust}, 情绪={self._mood}, 防御={self._guard}\n"
            f"上一刻的内心想法：{self._inner_voice}\n\n"
            f"对方刚才说：{user_message}\n"
            f"你回复了：{assistant_reply}\n\n"
            + self._build_reaction_appraisal()
            + "现在，用你自己的口吻写出你此刻真实的内心想法。\n\n"
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
            + baseline_rules
            + "重要性评分规则（用于判断对话记忆的营养程度）：\n"
            "- 情感强度高/关系转折/承诺/冲突/揭露秘密/告白/决裂：8-10分\n"
            "- 日常寒暄/打招呼/无关痛痒：1-3分\n"
            "- 普通对话/闲聊/一般信息交换：4-6分\n\n"
            "时间事件抽取规则：如果对方刚才提到了一件「未来的、具体的、值得以后关心的事」"
            "（如面试/考试/手术/见人/搬家/旅行/重要会议），"
            "请在 time_event 字段中如实记录。日常琐碎（吃了顿饭、今天好累）填 null。\n"
            "正例：对方说\"我明天下午有个面试\" → "
            '{"event":"面试","when_text":"明天下午","due_at":"2026-06-25T15:00"}\n'
            "正例：对方说\"下周搬家\" → "
            '{"event":"搬家","when_text":"下周","due_at":"2026-06-30"}\n'
            "反例：对方说\"今天吃了火锅\" → null\n"
            "反例：对方说\"今天好累\" → null\n"
            "due_at 尽量归一化成 ISO 时间，模糊时间给粗略日期即可。\n\n"
            "输出严格JSON格式（只输出JSON，不要任何其他内容）：\n"
            "{\n"
            '  "affinity": 0-100整数,\n'
            '  "trust": 0-100整数,\n'
            '  "mood": "具体情绪词（如释然/微酸/警觉/心软/嘴硬心软/又气又心疼/微微上头）",\n'
            '  "guard": 0-100整数,\n'
            '  "inner_voice": "你的第一人称内心独白2-3句",\n'
            '  "mood_emoji": "一个最贴合此刻情绪的emoji",\n'
            '  "importance": 1-10整数,\n'
            '  "time_event": null 或 {"event":"事件名","when_text":"用户原话描述","due_at":"ISO时间"}\n'
            "}"
        )

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

                # ── 时间事件抽取 ──
                time_event = data.get("time_event")
                if time_event and isinstance(time_event, dict) and self._memory and self._memory.enabled and self._card_id:
                    try:
                        evt = time_event.get("event", "")
                        when_text = time_event.get("when_text", "")
                        due_at = time_event.get("due_at", "")
                        event_id = uuid.uuid4().hex[:16]
                        self._memory.add_manual(
                            f"对方提到「{evt}」（{when_text}），大约在{due_at}。",
                            self._card_id,
                            metadata={
                                "type": "time_event",
                                "event_id": event_id,
                                "event": evt,
                                "when_text": when_text,
                                "due_at": due_at,
                            },
                        )
                        print(f"[ChatEngine] Saved time_event: {evt} at {due_at} (id={event_id})")
                    except Exception as exc:
                        print(f"[ChatEngine] Save time_event failed: {exc}")

                self._last_importance = max(1, min(10, int(data.get("importance", 5))))
                print(f"[Affinity] PARSED: affinity={data.get('affinity')} trust={data.get('trust')} "
                      f"mood={data.get('mood')} guard={data.get('guard')} "
                      f"importance={self._last_importance} "
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
                if storage and self._group_id:
                    # 群聊：写 group_affinity 表，key=(group_id, card_id)
                    try:
                        _loop = getattr(self, '_main_loop', None)
                        if _loop is not None:
                            asyncio.run_coroutine_threadsafe(
                                storage.update_group_affinity(
                                    self._group_id, self._card_id, self._affinity, self._trust,
                                    self._mood, self._guard, self._affinity_reason,
                                ),
                                _loop,
                            ).result(timeout=15)
                        else:
                            asyncio.run(storage.update_group_affinity(
                                self._group_id, self._card_id, self._affinity, self._trust,
                                self._mood, self._guard, self._affinity_reason,
                            ))
                    except Exception as db_exc:
                        print(f"[ChatEngine] Affinity DB save failed (group={self._group_id} card={self._card_id}): {db_exc}")
                elif storage and session_id:
                    # 单聊：写 sessions 表（原逻辑不变）
                    try:
                        _loop = getattr(self, '_main_loop', None)
                        if _loop is not None:
                            asyncio.run_coroutine_threadsafe(
                                storage.update_session_affinity(
                                    session_id, self._affinity, self._trust,
                                    self._mood, self._guard, self._affinity_reason,
                                ),
                                _loop,
                            ).result(timeout=15)
                        else:
                            asyncio.run(storage.update_session_affinity(
                                session_id, self._affinity, self._trust,
                                self._mood, self._guard, self._affinity_reason,
                            ))
                    except Exception as db_exc:
                        print(f"[ChatEngine] Affinity DB save failed (session={session_id}): {db_exc}")
            except Exception as exc:
                print(f"[ChatEngine] Affinity eval failed: {exc}")
                self._last_importance = 5
                import traceback
                traceback.print_exc()

        # 同步执行（移除 threading，确保 fetchAffinity 能拿到最新值）
        _do()
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

    def _build_all_enhancements(self) -> str:
        """按固定顺序拼接全部 prompt 增强块。

        统一入口：治舔狗/认知画像/时间感知/事件提醒。
        新增增强块时在此加一行，5 个调用点自动全生效。
        """
        return (
            self._build_affinity_persona_block()
            + self._build_cognitive_block()
            + self._build_time_awareness_block()
            + self._build_event_candidate_block()
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
                _loop = getattr(self, '_main_loop', None)
                if _loop is not None:
                    session_data = asyncio.run_coroutine_threadsafe(
                        self._storage.get_session(self._session_id), _loop
                    ).result(timeout=5)
                else:
                    session_data = asyncio.run(
                        self._storage.get_session(self._session_id)
                    )
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

    # ── 事件级时间感知 ──────────────────────────────────────────

    def _get_due_event(self) -> dict | None:
        """扫描记忆，返回第一个到期的未问时间事件（最接近当前时刻的）。"""
        if not self._memory or not self._memory.enabled or not self._card_id:
            return None
        try:
            memories = self._memory.get_all(self._card_id)
            if not memories:
                return None

            asked_ids = set()
            for m in memories:
                meta = m.get("metadata") or {}
                if isinstance(meta, dict) and meta.get("type") == "time_event_asked":
                    asked_ids.add(meta.get("event_id", ""))

            now = datetime.now()
            best = None
            for m in memories:
                meta = m.get("metadata") or {}
                if not isinstance(meta, dict):
                    continue
                if meta.get("type") != "time_event":
                    continue
                eid = meta.get("event_id", "")
                if not eid or eid in asked_ids or eid in self._asked_event_ids:
                    continue
                due_at_str = meta.get("due_at", "")
                if not due_at_str:
                    continue
                try:
                    due_at = datetime.fromisoformat(due_at_str)
                except (ValueError, TypeError):
                    continue
                if due_at > now:
                    continue
                if best is None or due_at > best["due_at"]:
                    best = {
                        "event": meta.get("event", ""),
                        "when_text": meta.get("when_text", ""),
                        "event_id": eid,
                        "due_at": due_at,
                    }
            return best
        except Exception as exc:
            print(f"[ChatEngine] _get_due_event failed: {exc}")
            return None

    def _build_event_candidate_block(self) -> str:
        """如果记忆中有到期的未问事件，返回可选关心提示片段。"""
        pending = self._get_due_event()
        if not pending:
            self._injected_event_id = ""
            return ""
        self._injected_event_id = pending["event_id"]
        return (
            "\n\n【你或许记着的一件事】\n"
            f"对方之前提到「{pending['event']}」，时间大约在「{pending['when_text']}」，"
            f"现在应该已经发生/到时间了。\n"
            "如果这轮气氛自然，你可以像真人一样【关心地】问起——但要符合你的性格："
            "含蓄的人就旁敲侧击，热情的人就直接问。"
            "绝不要机械复述细节，要带着在意去问。"
            "如果这轮在吵架/对方情绪差/话题不搭，就先不提。\n"
        )

    def _mark_event_asked(self) -> None:
        """将本轮已注入的事件标记为已问（持久化标记）。"""
        eid = self._injected_event_id
        if not eid:
            return
        self._injected_event_id = ""
        self._asked_event_ids.add(eid)
        if self._memory and self._memory.enabled and self._card_id:
            try:
                self._memory.add_manual(
                    "",
                    self._card_id,
                    metadata={"type": "time_event_asked", "event_id": eid},
                )
                print(f"[ChatEngine] Marked event {eid} as asked")
            except Exception as exc:
                print(f"[ChatEngine] Mark event asked failed: {exc}")

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
