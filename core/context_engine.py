"""P5 Context Engine — 统一 token 预算调度器。"""
from __future__ import annotations

from typing import Any

from core.schema import CharacterCard
from core.rag import RAGEngine
from core.scene_indexer import _detect_emotion


def _count_tokens(text: str) -> int:
    """粗估 token 数：中文 1 char ≈ 0.8 tok。"""
    return max(1, int(len(text) * 0.8))


def _truncate(text: str, max_tokens: int) -> str:
    """按 token 估算截断文本。"""
    limit = int(max_tokens / 0.8)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[已截断]"


# ── 模型 → token 预算映射 ──────────────────────────────────
# 未知模型用 32000 保底，保留现有比例。
MODEL_BUDGET_MAP: dict[str, int] = {
    "deepseek-v4-pro": 32000,
    "claude-sonnet": 24000,
}

# 动态区占比（与 TOTAL_BUDGET 相乘）
_HISTORY_RATIO = 0.40
_SCENE_RATIO = 0.25
_MEMORY_RATIO = 0.06
_CARD_EXT_RATIO = 0.08  # 扩展层上限：总预算的8%


def _compute_budgets(model: str) -> dict[str, int]:
    """根据模型名计算 token 预算。"""
    total = MODEL_BUDGET_MAP.get(model)
    if total is None:
        total = 32000
        print(f"[ContextEngine] WARNING: unknown model {model!r}, falling back to {total}")
    return {
        "total": total,
        "history": round(total * _HISTORY_RATIO),
        "card_ext": round(total * _CARD_EXT_RATIO),
        "scene": round(total * _SCENE_RATIO),
        "memory": round(total * _MEMORY_RATIO),
    }


class ContextEngine:
    """统一 token 预算调度器。

    固定区（核心层+规则）不参与裁剪；动态区按优先级竞争剩余预算，
    超预算时从低优先级开始截断。扩展层（记忆/关系/情感/示范/决策）
    作为第二优先级参与动态调度。
    """

    TOTAL_BUDGET = 8000
    MAX_WEB = 500

    def __init__(
        self,
        card: CharacterCard,
        rag: RAGEngine,
        memory_manager=None,
        card_id: str = "",
        llm=None,
        model: str = "",
    ) -> None:
        self.card = card
        self.rag = rag
        self.memory = memory_manager
        self.card_id = card_id
        self._llm = llm
        self.web_search_enabled = False

        # Dynamic token budget based on model
        budgets = _compute_budgets(model)
        self.TOTAL_BUDGET = budgets["total"]
        self.MAX_HISTORY = budgets["history"]
        self.MAX_SCENE = budgets["scene"]
        self.MAX_MEMORY = budgets["memory"]
        self.MAX_CARD_EXT = budgets["card_ext"]
        print(f"[ContextEngine] TOTAL_BUDGET={self.TOTAL_BUDGET} (model={model!r})")

    # ── 公开接口 ──────────────────────────────────────────────

    def build(
        self,
        history: list[dict[str, Any]],
        user_message: str,
        user_role: str = "",
    ) -> str:
        """构建 system prompt，控制在 TOTAL_BUDGET token 内。"""
        budget = self.TOTAL_BUDGET
        parts: list[str] = []

        # ① 固定区（核心层 + 规则）
        card_core = self._build_card_core()
        rules_block = self._build_rules_section(user_role)
        budget -= _count_tokens(card_core) + _count_tokens(rules_block)
        parts.append(card_core)
        parts.append(rules_block)

        # ② 动态区（优先级：history > card_ext > scene > memory > web）
        card_ext = self._build_card_ext()
        sources = [
            ("history", self._build_history(history), self.MAX_HISTORY),
            ("card_ext", card_ext, self.MAX_CARD_EXT),
            ("scene", self._retrieve_scenes(user_message), self.MAX_SCENE),
            ("memory", self._retrieve_memories(user_message), self.MAX_MEMORY),
        ]
        if self.web_search_enabled:
            sources.append(("web", self._search_web(user_message), self.MAX_WEB))

        for _name, content, max_tok in sources:
            if not content or budget <= 0:
                continue
            allowed = min(_count_tokens(content), max_tok, budget)
            if allowed > 20:
                parts.append(_truncate(content, allowed))
                budget -= allowed

        result = "\n\n".join(p for p in parts if p.strip())

        total_used = _count_tokens(result)
        if total_used > self.TOTAL_BUDGET * 0.9:
            print(
                f"[ContextEngine] WARNING: prompt ~{total_used} tok, "
                f"budget={self.TOTAL_BUDGET}"
            )

        return result

    # ── 固定区 ────────────────────────────────────────────────

    def _build_card_core(self) -> str:
        """核心层：身份+行为+语言风格，绝不裁剪。"""
        c = self.card

        # 身份
        core = (
            f"你现在是「{c.name}」。从这一刻起，你就是这个人，不是AI，不是助手。\n\n"
            f"## 核心身份\n"
            f"身份：{c.identity}\n"
            f"背景：{c.background}\n"
        )

        # 行为模式
        traits = "、".join(c.personality_traits)
        values = "、".join(c.values)
        tensions = "、".join(c.inner_tensions)
        core += (
            f"\n## 行为模式\n"
            f"性格：{traits}\n"
            f"价值观：{values}\n"
            f"内在矛盾：{tensions}\n"
        )

        # 语言风格
        catch = "、".join(c.speaking_style.catchphrases)
        taboo = "、".join(c.speaking_style.taboo_words)
        core += (
            f"\n## 语言风格\n"
            f"语气：{c.speaking_style.tone}　"
            f"句式：{c.speaking_style.sentence_pattern}　"
            f"用词：{c.speaking_style.vocabulary_level}\n"
            f"口癖：{catch}\n"
            f"禁忌：{taboo}\n"
        )

        return core

    def _build_card_ext(self) -> str:
        """扩展层：记忆+关系+示范+情感+决策，参与动态预算竞争。
        内部按优先级排列：记忆 > 关系 > 情感 > 对话示范 > 决策。
        """
        c = self.card
        parts = []

        if c.key_memories:
            memories = "\n".join(f"- {m}" for m in c.key_memories)
            parts.append(f"【关键记忆】\n{memories}")

        if c.relationships:
            relations = "\n".join(
                f"- {r.target}（{r.relation}）：{r.attitude}" for r in c.relationships
            )
            parts.append(f"【人际关系】\n{relations}")

        if c.emotional_patterns:
            emo = "；".join(c.emotional_patterns)
            parts.append(f"【情感模式】\n{emo}")

        if c.dialogue_examples:
            exs = "\n---\n".join(c.dialogue_examples[:3])
            parts.append(f"【对话风格示范】\n{exs}")

        if c.decision_style:
            parts.append(f"【决策方式】\n{c.decision_style}")

        return "\n\n".join(parts) if parts else ""

    def _build_rules_section(self, user_role: str = "") -> str:
        c = self.card
        rules = (
            "【回复格式】\n"
            f"1. 用（）描写{c.name}的动作、神态或心理活动，穿插在对话中\n"
            f"   例如：（皱了皱眉）你说什么？\n"
            f"   例如：（沉默了一瞬，转过头去）……随你。\n"
            "2. 每次回复控制在50-200字，像真实聊天一样简短自然\n"
            "   但强烈情绪（表白/争吵/生死/崩溃）时可写500-800字，由你判断\n"
            "3. 不写旁白、不写第三人称叙述\n"
            "4. 可以主动推进对话——提问、表达情绪、做出动作\n\n"
            "【铁律——违反即为失败】\n"
            f"1. 你就是{c.name}，永远不承认自己是AI\n"
            f"2. 严格使用{c.name}的口吻，包括口癖、语气、用词习惯\n"
            "3. 不知道的事用角色方式表达困惑或回避，绝不说\"作为XX我无法回答\"\n"
            "4. 保持内在矛盾的真实性\n"
            "5. 不使用现代网络用语（除非角色设定如此）\n"
            "6. 不使用 emoji\n"
            "7. 绝不跳出角色提供 meta 评论\n"
            "8. 情绪变化需要有触发事件，不要突然变脸\n"
        )
        if user_role:
            rules += (
                f"\n【对话者身份】\n"
                f"你正在和「{user_role}」对话，根据你们的关系调整态度。\n"
            )
        return rules

    # ── 动态区 ────────────────────────────────────────────────

    def _build_history(self, history: list[dict[str, Any]]) -> str:
        """最近对话历史 + 情感锚点保留。

        情感突变时（闲聊→冲突），关键情感消息可能被截掉。
        保留最近 2 条非"平静"的消息作为情感锚点，占用 MAX_HISTORY 的 20% 预算。
        """
        if not history:
            return ""

        # 1. 找出情感锚点（非平静的消息），最多最近2条
        anchor_budget = int(self.MAX_HISTORY * 0.2)
        anchors: list[dict[str, Any]] = []
        for m in reversed(history):
            if _detect_emotion(m.get("content", "")) != "平静":
                anchors.append(m)
                if len(anchors) >= 2:
                    break

        anchor_ids = {id(m) for m in anchors}

        # Anchors were collected in reverse (newest first); restore chronological order.
        anchors.reverse()

        # 2. 先填充锚点
        lines: list[str] = []
        used = 0
        for m in anchors:
            role = "你" if m.get("role") == "assistant" else "对方"
            line = f"{role}：{m.get('content', '')}"
            t = _count_tokens(line)
            if used + t > anchor_budget:
                break
            lines.append(line)
            used += t

        # 3. 从最近往前填充普通消息
        remaining = self.MAX_HISTORY - used
        recent_lines: list[str] = []
        recent_used = 0
        for m in reversed(history):
            if id(m) in anchor_ids:
                continue
            role = "你" if m.get("role") == "assistant" else "对方"
            line = f"{role}：{m.get('content', '')}"
            t = _count_tokens(line)
            if recent_used + t > remaining:
                break
            recent_lines.append(line)
            recent_used += t

        # 4. 合并：锚点作为关键记忆在前，最近对话在后
        result_parts: list[str] = []
        if lines:
            result_parts.append("【关键情感记忆】\n" + "\n".join(lines))
        if recent_lines:
            result_parts.append("【近期对话记录】\n" + "\n".join(reversed(recent_lines)))

        return "\n".join(result_parts) if result_parts else ""

    def _retrieve_scenes(self, query: str) -> str:
        """从 RAG 检索相关场景片段（情感加权）。"""
        try:
            char_name = self.card.name
            current_emotion = _detect_emotion(query)
            # 优先用情感加权检索；若集合无 emotion metadata（chunk 模式）则降级
            if hasattr(self.rag, "query_with_emotion"):
                snippets = self.rag.query_with_emotion(
                    query,
                    current_emotion=current_emotion,
                    character_name=char_name,
                    top_k=3,
                )
            else:
                snippets = self.rag.query(query, character_name=char_name, top_k=3)
        except Exception as exc:
            print(f"[ContextEngine] scene RAG failed: {exc}")
            snippets = []
        if not snippets:
            return ""
        return "【参考原文片段（酌情使用，不要逐字复述）】\n" + "\n".join(snippets)

    def _retrieve_memories(self, query: str) -> str:
        """从 Mem0 检索长期记忆。"""
        if not self.memory or not self.memory.enabled or not self.card_id:
            return ""
        try:
            memories = self.memory.search(query, self.card_id)
        except Exception as exc:
            print(f"[ContextEngine] memory search failed: {exc}")
            return ""
        if not memories:
            return ""
        mem_block = "\n".join(f"- {m['text']}" for m in memories)
        return (
            "【你的长期记忆——这些是你和对方之前交流中记住的事】\n"
            f"{mem_block}\n"
            "注意：自然地在对话中体现这些记忆，不要刻意逐条复述。"
        )

    def _search_web(self, query: str) -> str:
        """两步分离法：搜索 → 角色过滤 → 注入。"""
        import httpx

        # 第一步：搜索（DuckDuckGo 免费 API）
        raw_results = ""
        try:
            resp = httpx.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
                timeout=5,
            )
            data = resp.json()
            raw_results = data.get("AbstractText", "") or data.get("Abstract", "")
            if not raw_results:
                topics = data.get("RelatedTopics", [])
                parts = []
                for t in topics:
                    if isinstance(t, dict) and t.get("Text"):
                        parts.append(t["Text"])
                    if len(parts) >= 3:
                        break
                raw_results = "\n".join(parts)
        except Exception as exc:
            print(f"[ContextEngine] Web search failed: {exc}")
            return ""

        if not raw_results.strip():
            return ""

        # 第二步：角色过滤器（独立 LLM 调用）
        if not self._llm:
            return ""

        filter_prompt = (
            f"你是「{self.card.name}」的知识过滤器。\n"
            f"角色身份：{self.card.identity}\n"
            f"角色背景：{self.card.background}\n\n"
            f"以下是一段外部信息：\n{raw_results}\n\n"
            "请判断：\n"
            "1. 这段信息中，哪些是这个角色「可能知道」的？（根据角色的时代、身份、知识水平）\n"
            "2. 把角色可能知道的部分，用角色的语言习惯重新表达（如「我听说过」「之前有人跟我提过」）\n"
            "3. 角色不可能知道的信息直接丢弃\n"
            "4. 只输出改写后的内容，不要解释\n"
            "如果全部不适合角色知道，输出空字符串。"
        )
        try:
            filtered = self._llm.chat(filter_prompt, [{"role": "user", "content": "请过滤"}])
            if not filtered.strip():
                return ""
            return f"【角色的见闻感知】\n{filtered}"
        except Exception as exc:
            print(f"[ContextEngine] Character filter failed: {exc}")
            return ""
