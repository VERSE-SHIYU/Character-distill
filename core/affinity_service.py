"""情感状态容器：11个亲密度字段 + 读写 + 阶段计算 + 评估 prompt 构建。"""

from __future__ import annotations

from typing import Any


# IOS₁₁ (Scientific Reports 2024) 11级亲密度 → Bogardus 7级社会距离映射
AFFINITY_STAGES = [
    (0, 18, "陌生", "🫥"),      # IOS 1-2, Bogardus 6-7
    (18, 36, "认识", "🙂"),     # IOS 3-4, Bogardus 4-5
    (36, 55, "熟悉", "😊"),     # IOS 5-6, Bogardus 3
    (55, 73, "朋友", "😄"),     # IOS 7-8, Bogardus 2
    (73, 91, "亲近", "🥰"),     # IOS 9-10, Bogardus 1-2
    (91, 101, "心意相通", "💕"), # IOS 11, Bogardus 1
]


def calc_stage(affinity: int) -> tuple[str, str]:
    for lo, hi, name, emoji in AFFINITY_STAGES:
        if lo <= affinity < hi:
            return name, emoji
    return "陌生", "🫥"


class AffinityService:
    """11个情感状态字段的容器 + load/get 方法。

    ChatEngine 通过 @property 透明转发，所有现有代码的
    self._affinity / self._mood / ... 读写自动路由到此容器，
    无需改动调用点。
    """

    def __init__(self) -> None:
        self.affinity: int = 50
        self.trust: int = 30
        self.mood: str = "平静"
        self.guard: int = 70
        self.affinity_reason: str = ""
        self.inner_voice: str = ""
        self.mood_emoji: str = "😊"
        self.prev_stage: str = ""
        self.stage: str = ""
        self.stage_emoji: str = ""
        self.stage_upgraded: bool = False

    def load(self, data: dict[str, Any]) -> None:
        """从存档 dict 加载11个情感字段。

        *注意*：仅设置情感状态字段，不涉及 affinity_enabled 等开关。
        """
        if not data:
            return
        self.affinity = data.get("affinity", 50)
        self.trust = data.get("trust", 30)
        self.mood = data.get("mood", "平静")
        self.guard = data.get("guard", 70)
        self.affinity_reason = data.get("reason", "")
        # 尝试 JSON 解析扩展数据（兼容旧格式纯文本 reason）
        _reason = self.affinity_reason or ""
        try:
            import json
            _parsed = json.loads(_reason)
            self.inner_voice = _parsed.get("inner_voice", "")
            self.mood_emoji = _parsed.get("mood_emoji", "😊")
        except (json.JSONDecodeError, TypeError):
            self.inner_voice = _reason
            self.mood_emoji = "😊"
        self.stage, self.stage_emoji = calc_stage(self.affinity)
        self.prev_stage = self.stage

    def get(self) -> dict[str, Any]:
        """返回 get_affinity 所需的字段结构。"""
        return {
            "affinity": self.affinity,
            "trust": self.trust,
            "mood": self.mood,
            "guard": self.guard,
            "reason": self.affinity_reason,
            "inner_voice": self.inner_voice,
            "mood_emoji": self.mood_emoji,
            "stage": self.stage,
            "stage_emoji": self.stage_emoji,
        }

    def build_evaluation_prompt(
        self,
        card: Any,
        user_message: str,
        assistant_reply: str,
        user_role: str,
        reaction_appraisal: str,
    ) -> str:
        """构建情感评估 LLM prompt（纯函数：只读状态，无副作用/IO）。"""
        _values = getattr(card, 'values', []) or []
        _tensions = getattr(card, 'inner_tensions', []) or []
        psyche = card.psyche

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
            f"你现在就是{card.name}本人。\n"
            f"性格特征：{', '.join(_values[:3])}\n"
            f"内在矛盾：{', '.join(_tensions[:2])}\n"
            f"对话者身份：{user_role}\n\n"
            f"当前情感状态：好感={self.affinity}, 信任={self.trust}, 情绪={self.mood}, 防御={self.guard}\n"
            f"上一刻的内心想法：{self.inner_voice}\n\n"
            f"对方刚才说：{user_message}\n"
            f"你回复了：{assistant_reply}\n\n"
            + reaction_appraisal
            + "现在，用你自己的口吻写出你此刻真实的内心想法。\n\n"
            "如果你此刻感觉到对ta的心意悄悄越过了某道坎——比原来更近了一点——"
            "就让这份察觉自然融进内心独白里。"
            "不是宣告'我们关系变了'，而是那种突然发现自己不设防了、"
            "想多说一句了的真实心里一动。用你的性格，一句就好。\n\n"
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
            "出戏判定规则（用于 in_character / ooc_reason 字段）：\n"
            "- 符合人设的冷淡、拒绝、距离感是高分（in_character 高），不是出戏\n"
            "- 出戏特指「违背这张卡片的性格与当前关系阶段，去无原则讨好/迁就对方」\n"
            "- 评判只针对「是否像这个人」，不涉及内容安全（安全另有独立约束）\n\n"
            "事实可信度判定规则（用于 assertion_confidence 字段）：\n"
            "- 判断对方刚才的话作为「关于现实的事实陈述」有多可信\n"
            "- 反讽/反话/假设场景/角色扮演设定/明显玩笑 → 低分（0-40）\n"
            "- 平实陈述自身真实信息（我叫X/我在Y上班/我明天面试）→ 高分（70-100）\n"
            "- 无法判断或介于之间 → 中性（50）\n"
            "- 这是判断「该不该把这句话当事实记进长期记忆」，不是判断好感，也不是判断角色是否出戏\n\n"
            "输出严格JSON格式（只输出JSON，不要任何其他内容）：\n"
            "{\n"
            '  "affinity": 0-100整数,\n'
            '  "trust": 0-100整数,\n'
            '  "mood": "具体情绪词（如释然/微酸/警觉/心软/嘴硬心软/又气又心疼/微微上头）",\n'
            '  "guard": 0-100整数,\n'
            '  "inner_voice": "你的第一人称内心独白2-3句",\n'
            '  "mood_emoji": "一个最贴合此刻情绪的emoji",\n'
            '  "importance": 1-10整数,\n'
            '  "time_event": null 或 {"event":"事件名","when_text":"用户原话描述","due_at":"ISO时间"},\n'
            '  "in_character": 0-100整数,  // 刚才的回复有多符合这张卡片此刻该有的姿态\n'
            '  "ooc_reason": "一句话说明哪里出戏，符合人设则空字符串",\n'
            '  "assertion_confidence": 0-100整数  // 对方刚才的话作为事实陈述有多可信\n'
        )
        return prompt

    def parse_evaluation_reply(self, reply: str) -> dict | None:
        """从LLM原始回复提取并解析JSON。无JSON返回None；坏JSON让json.loads抛出(由调用方try/except兜底)。"""
        import re as _re, json as _json
        m = _re.search(r'\{.*\}', reply, _re.DOTALL)
        if not m:
            return None
        return _json.loads(m.group())

    def apply_evaluation(self, data: dict, old_stage: str) -> int:
        """把解析结果回写11个情感字段，返回importance。纯状态计算，无IO。"""
        import json as _json
        importance = max(1, min(10, int(data.get("importance", 5))))
        self.affinity = max(0, min(100, data.get("affinity", self.affinity)))
        self.trust = max(0, min(100, data.get("trust", self.trust)))
        self.mood = data.get("mood", self.mood)
        self.guard = max(0, min(100, data.get("guard", self.guard)))
        self.inner_voice = data.get("inner_voice", self.inner_voice)
        self.mood_emoji = data.get("mood_emoji", self.mood_emoji)
        self.stage, self.stage_emoji = calc_stage(self.affinity)
        self.stage_upgraded = self.stage != old_stage
        self.prev_stage = old_stage
        extended = {
            "inner_voice": self.inner_voice,
            "mood_emoji": self.mood_emoji,
            "mood_word": self.mood,
            "stage": self.stage,
            "stage_emoji": self.stage_emoji,
        }
        self.affinity_reason = _json.dumps(extended, ensure_ascii=False)
        return importance
