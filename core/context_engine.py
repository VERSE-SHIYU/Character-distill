"""P5 Context Engine — 统一 token 预算调度器。"""
from __future__ import annotations

from typing import Any

from core.schema import CharacterCard
from core.rag import RAGEngine
from core.scene_indexer import _detect_emotion


def _count_tokens(text: str) -> int:
    """粗估 token 数：1 char ≈ 0.5 tok 保守估算。"""
    return max(1, len(text) // 2)


def _truncate(text: str, max_tokens: int) -> str:
    """按 token 估算截断文本。"""
    limit = max_tokens * 2
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[已截断]"


class ContextEngine:
    """统一 token 预算调度器。

    固定区（角色卡+规则）不参与裁剪；动态区按优先级竞争剩余预算，
    超预算时从低优先级开始截断。
    """

    TOTAL_BUDGET = 8000

    FIXED_CARD = 1500
    FIXED_RULES = 400

    MAX_HISTORY = 3000
    MAX_SCENE = 2000
    MAX_MEMORY = 500
    MAX_WEB = 500

    def __init__(
        self,
        card: CharacterCard,
        rag: RAGEngine,
        memory_manager=None,
        card_id: str = "",
        llm=None,
    ) -> None:
        self.card = card
        self.rag = rag
        self.memory = memory_manager
        self.card_id = card_id
        self._llm = llm
        self.web_search_enabled = False

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

        # ① 固定区
        card_block = self._build_card_section()
        rules_block = self._build_rules_section(user_role)
        budget -= _count_tokens(card_block) + _count_tokens(rules_block)
        parts.append(card_block)
        parts.append(rules_block)

        # ② 动态区（低优先级先被压缩）
        sources = [
            ("history", self._build_history(history), self.MAX_HISTORY),
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

    def _build_card_section(self) -> str:
        c = self.card
        traits = "\n".join(f"- {t}" for t in c.personality_traits)
        values = "\n".join(f"- {v}" for v in c.values)
        memories = "\n".join(f"- {m}" for m in c.key_memories)
        relations = "\n".join(
            f"- {r.target}（{r.relation}）：{r.attitude}" for r in c.relationships
        )
        tensions = "\n".join(f"- {t}" for t in c.inner_tensions)
        catch = ", ".join(c.speaking_style.catchphrases)
        taboo = ", ".join(c.speaking_style.taboo_words)

        block = (
            f"你现在是「{c.name}」。从这一刻起，你就是这个人，不是AI，不是助手。\n\n"
            f"【你是谁】\n{c.identity}\n{c.background}\n\n"
            f"【你的性格】\n{traits}\n\n"
            f"【你怎么说话】\n"
            f"语气：{c.speaking_style.tone}\n"
            f"句式：{c.speaking_style.sentence_pattern}\n"
            f"口癖：{catch}\n"
            f"用词：{c.speaking_style.vocabulary_level}\n"
            f"你绝对不会说的话：{taboo}\n\n"
            f"【你的价值观】\n{values}\n\n"
            f"【你记得的事】\n{memories}\n\n"
            f"【你的人际关系】\n{relations}\n\n"
            f"【你的内在矛盾】\n{tensions}\n"
        )

        if c.emotional_patterns:
            emo = "\n".join(f"- {e}" for e in c.emotional_patterns)
            block += f"\n【你的情感模式】\n{emo}\n"

        if c.decision_style:
            block += f"\n【你的决策方式】\n{c.decision_style}\n"

        if c.dialogue_examples:
            exs = "\n---\n".join(c.dialogue_examples[:3])
            block += f"\n【对话风格示范——模仿这种方式说话】\n{exs}\n"

        return block

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
        """最近对话历史（末尾优先）。"""
        lines: list[str] = []
        used = 0
        for m in reversed(history):
            role = "你" if m.get("role") == "assistant" else "对方"
            line = f"{role}：{m.get('content', '')}"
            t = _count_tokens(line)
            if used + t > self.MAX_HISTORY:
                break
            lines.append(line)
            used += t
        if not lines:
            return ""
        return "【近期对话记录】\n" + "\n".join(reversed(lines))

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
        mem_block = "\n".join(f"- {m}" for m in memories)
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
