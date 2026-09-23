"""P5 Context Engine — 统一 token 预算调度器。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Literal, NamedTuple
from urllib.parse import urlparse

from core.schema import (
    CharacterCard,
    EvidenceItem,
    EvidenceKind,
    SourceTrace,
    memory_evidence,
    web_evidence,
)
from core.rag import RAGEngine
from core.scene_indexer import _detect_emotion
from core.utils import try_record_usage
from core import telemetry as T  # OTel 埋点（OTEL_ENABLED 关时装饰器原样返回，零开销）
from core import concurrency as C  # 派生与上下文传播


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


# ── 检索来源：三源共用的产出/渲染机制 ──────────────────────────────
# 一次检索 = produce（怎么产出 items）+ fmt（块标题/行格式）。三源共同的「异常降级 /
# 空结果 / 块渲染 / 两出口的委托」只在 _retrieve_via 一份 —— 三者差异仅此两点。

RetrievalStatus = Literal["hit", "empty", "failed"]


class RetrievalResult(NamedTuple):
    """一次检索的产出：来源（source）+ 结构（items）+ 视图（block）+ 三态。

    ``source`` 是**对外的来源词汇**（``EvidenceKind``：scene/memory/web），不是工具名
    （``search_scenes``/``search_memory``/``web_search``）—— 工具名是 agent 的调用
    词汇，来源是证据的词汇，两者不许混。

    ``block`` 是 prompt 侧用的字符串（渲染自 items；web 例外，见 ``_web_items``）。
    ``status`` 三值而非 bool：本仓最大的盲点是「失败被吞成正常返回」，而这里天然三态 ——
    命中 / 真无匹配 / 失败。bool 只分两态，调用方得靠「ok=True 且 items 空」自己推
    「这是真无」，那又是靠散文约定而不是字段。
    """
    source: EvidenceKind
    block: str
    items: list[EvidenceItem]
    status: RetrievalStatus

    def trace(self) -> SourceTrace:
        """跨边界投影：只带 source/status/items，**不带 block**（block 是 prompt 侧）。"""
        return SourceTrace(source=self.source, status=self.status, items=list(self.items))


class BuildResult(NamedTuple):
    """``build_ex`` 的产出：prompt 字符串 + 动态区各源的追溯记录。

    prompt 是喂给模型的（prompt 侧）；traces 是检索层事实（证据侧）。两者**不许互相
    取材** —— traces 只由 ``RetrievalResult.trace()`` 投影而来，绝不从 parts 里回捞。
    traces 顺序 = 运行顺序 [scene, memory, web]（web 开关关时无末项），按源码顺序确定。
    """
    prompt: str
    traces: list[SourceTrace]


class _BlockFmt(NamedTuple):
    """块模板：标题 + 行前缀 + 尾注。

    三个模板字面量在本文件各只出现一次 —— 由
    ``tests/test_context_engine_evidence.py`` 的源码级唯一出处锁守住（防将来新增第四条
    源自己手拼一块）。
    """
    title: str
    line_prefix: str = ""
    trailer: str = ""


_SCENE_FMT = _BlockFmt(title="【参考原文片段（酌情使用，不要逐字复述）】")
_MEMORY_FMT = _BlockFmt(
    title="【你的长期记忆——这些是你和对方之前交流中记住的事】",
    line_prefix="- ",
    trailer="\n注意：自然地在对话中体现这些记忆，不要刻意逐条复述。",
)
_WEB_FMT = _BlockFmt(title="【角色的见闻感知】")


def _render_block(fmt: _BlockFmt, body: str) -> str:
    """块字符串的**唯一**渲染出口。body 为空 → 空块（不产出光秃秃的标题壳）。"""
    if not body:
        return ""
    return f"{fmt.title}\n{body}{fmt.trailer}"


def _ddg_items(data: dict, fetched_at: str) -> tuple[list[EvidenceItem], str]:
    """把 DDG Instant Answer 的 JSON 拆成 ``(原始片段 items, 拼接文本)``。

    拼接文本就是旧实现的 ``raw_results``（喂给改写阶段的那段），**逐字节相同** ——
    prompt 侧不许动。items 的 ``text`` 是 DDG 原文，**不是**改写结果：本线只宣称
    「检索到了什么」。

    url：Abstract 取 AbstractURL，topics 取 FirstURL，**取不到就是 None**（None 与 ""
    是两回事，不许填空串假装有）。source：Abstract 用 AbstractSource，topics 用 url 的
    netloc。fetched_at 是一次请求一个时刻，逐条同值。

    只认**顶层**带 Text 的 RelatedTopics 条目、不下钻嵌套 Topics —— 下钻会改变拼接
    文本（prompt 字节），本轮硬约束是 prompt 侧不变；要不要放开是单独的判定。
    """
    abstract = data.get("AbstractText", "") or data.get("Abstract", "")
    if abstract:
        return [web_evidence(
            text=abstract,
            url=data.get("AbstractURL") or None,
            source=data.get("AbstractSource") or None,
            fetched_at=fetched_at,
        )], abstract

    items: list[EvidenceItem] = []
    parts: list[str] = []
    for t in data.get("RelatedTopics", []):
        if not (isinstance(t, dict) and t.get("Text")):
            continue
        url = t.get("FirstURL") or None
        items.append(web_evidence(
            text=t["Text"],
            url=url,
            source=urlparse(url).netloc if url else None,
            fetched_at=fetched_at,
        ))
        parts.append(t["Text"])
        if len(parts) >= 3:
            break
    return items, "\n".join(parts)


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
        *,
        storage: Any,
    ) -> None:
        self.card = card
        self.rag = rag
        self.memory = memory_manager
        self.card_id = card_id
        self._llm = llm
        self._storage = storage
        self.web_search_enabled = False
        self._apply_budgets(model)

    def _apply_budgets(self, model: str) -> None:
        """按模型重算五档 token 预算 —— 构造与换连接共用这一处，两条路不会分叉。"""
        budgets = _compute_budgets(model)
        self.TOTAL_BUDGET = budgets["total"]
        self.MAX_HISTORY = budgets["history"]
        self.MAX_SCENE = budgets["scene"]
        self.MAX_MEMORY = budgets["memory"]
        self.MAX_CARD_EXT = budgets["card_ext"]
        print(f"[ContextEngine] TOTAL_BUDGET={self.TOTAL_BUDGET} (model={model!r})")

    def set_llm(self, llm: Any) -> None:
        """换到新连接，并按**新模型**重算预算。

        只影响**下一次**出站：已经在飞的那一轮由调用方在发起前把实例取到本地
        （见 ``ChatEngine._try_record_usage`` 的 ``llm`` 形参），不在这里补。

        预算是按模型算的，所以换模型必须重算 —— 否则 32k 换成 24k 之后上下文仍按 32k
        装填，超出的部分被上游截断，表现为角色忽然健忘。这五档全挂 ``self``，读者是
        ``ChatEngine``（``_build_llm_messages``）与本类自己的 ``build_ex``，就地重算即全站生效。
        """
        self._llm = llm
        self._apply_budgets(getattr(llm, "model", ""))

    # ── 公开接口 ──────────────────────────────────────────────

    def build(
        self,
        user_message: str,
        user_role: str = "",
        current_mood: str | None = None,
        include_dynamic: bool = True,
    ) -> str:
        """system prompt 的字符串出口 = ``build_ex`` 的 prompt（薄委托，不复制逻辑）。"""
        return self.build_ex(
            user_message,
            user_role=user_role,
            current_mood=current_mood,
            include_dynamic=include_dynamic,
        ).prompt

    @T.spanned("context.build")
    def build_ex(
        self,
        user_message: str,
        user_role: str = "",
        current_mood: str | None = None,
        include_dynamic: bool = True,
    ) -> BuildResult:
        """构建 system prompt，控制在 TOTAL_BUDGET token 内；另带动态区 traces。

        对话历史已从 system prompt 中移出，改为通过 messages 数组传递
        （见 ChatEngine._build_llm_messages），由 role 字段天然区分说话人。

        include_dynamic=False 时只输出 card_core + rules + card_ext，
        跳过 RAG 场景检索／记忆检索／web 搜索（agent 模式下由工具按需调用），
        traces 随之为空。

        返回 BuildResult 而非裸字符串：prompt 逐字节与旧版相同，traces 是新增的
        证据侧出口（不参与下方 parts 拼接）。
        """
        budget = self.TOTAL_BUDGET
        parts: list[str] = []
        traces: list[SourceTrace] = []

        # ① 固定区（核心层 + 规则）
        card_core = self._build_card_core()
        rules_block = self._build_rules_section(user_role)
        budget -= _count_tokens(card_core) + _count_tokens(rules_block)
        parts.append(card_core)
        parts.append(rules_block)

        # ② card_ext 始终参与
        card_ext = self._build_card_ext()
        sources: list[tuple[str, str, int]] = [("card_ext", card_ext, self.MAX_CARD_EXT)]

        # ③ 动态区（仅 include_dynamic=True 时执行）
        if include_dynamic:
            with ThreadPoolExecutor(max_workers=2) as pool:
                # D2：这里检索刻意不开 embed deadline scope —— 下方 result() 无 timeout，
                # 调用方阻塞等待而非弃船：没有 force-abandon 就没有"线程残留到 embed 放弃"
                # 的泄漏（区别于 tools.execute 的 fut.result(timeout) 弃船路径）。加了反而
                # 夹逼慢 embed、把正常检索误杀成空结果，净退化。工具模式的检索由
                # tools.execute 的 budget scope 管。
                # context 传播点：submit 不拷贝 contextvar，用 ctx_submit 包装，
                # 否则 worker 里的检索/embed span 会成孤儿。
                f_scene = C.ctx_submit(pool, self._retrieve_scenes_ex, user_message)
                f_memory = C.ctx_submit(
                    pool, self._retrieve_memories_ex, user_message, current_mood=current_mood
                )
                scene = f_scene.result()
                memory = f_memory.result()
            results = [scene, memory]
            sources.append(("scene", scene.block, self.MAX_SCENE))
            sources.append(("memory", memory.block, self.MAX_MEMORY))
            if self.web_search_enabled:
                web = self._search_web_ex(user_message)
                results.append(web)
                sources.append(("web", web.block, self.MAX_WEB))
            traces = [r.trace() for r in results]

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

        return BuildResult(prompt=result, traces=traces)

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
            "9. 如果对方在反复说同样或相似的话，看你们的对话历史，绝不要一字不差重复你上一条回复——那会让你像坏掉的机器。按你自己的性格，对这种重复给出真实、有变化的反应：可能不耐烦、可能心软、可能起疑、可能担心、也可能干脆懒得理——取决于你是谁、以及此刻你和对方的关系。\n"
        )
        if user_role:
            rules += (
                f"\n【对话者身份】\n"
                f"你正在和「{user_role}」对话，根据你们的关系调整态度。\n"
            )
        return rules

    # ── 动态区 ────────────────────────────────────────────────

    def _retrieve_via(
        self,
        name: str,
        source: EvidenceKind,
        produce: Callable[[], tuple[list[EvidenceItem], str | None]],
        fmt: _BlockFmt,
    ) -> RetrievalResult:
        """三源共用的机制：跑 produce、异常降级、判空、渲染块 —— 只此一份。

        ``source`` 由调用方传入（本函数不认识自己跑的是哪一源，只负责盖章）：它可以传
        ``EvidenceKind`` 字面量，但**不许**从 ``name`` 解析 —— 中文日志名与对外词汇是
        两回事，解析就是在造第二份映射表。

        ``produce() -> (items, body)``：``body=None`` 表示块体由 items 渲染（scene /
        memory）。web 的块体是**改写结果**，不能用 items 原文渲染（那会把检索原文灌进
        prompt，改动 prompt 字节），故它显式给 body。
        """
        try:
            items, body = produce()
        except Exception as exc:
            print(f"[ContextEngine] {name} failed: {exc}")
            return RetrievalResult(source=source, block="", items=[], status="failed")
        if not items:
            return RetrievalResult(source=source, block="", items=[], status="empty")
        if body is None:
            body = "\n".join(f"{fmt.line_prefix}{it.text}" for it in items)
        return RetrievalResult(
            source=source, block=_render_block(fmt, body), items=list(items), status="hit"
        )

    def _retrieve_scenes(self, query: str) -> str:
        """从 RAG 检索相关场景片段（情感加权）。字符串出口 = 结构出口的 block。"""
        return self._retrieve_scenes_ex(query).block

    def _retrieve_scenes_ex(self, query: str) -> RetrievalResult:
        return self._retrieve_via(
            "scene RAG", "scene", lambda: self._scene_items(query), _SCENE_FMT
        )

    def _retrieve_memories(self, query: str, current_mood: str | None = None) -> str:
        """从 Mem0 检索长期记忆（含情感加权）。"""
        return self._retrieve_memories_ex(query, current_mood=current_mood).block

    def _retrieve_memories_ex(
        self, query: str, current_mood: str | None = None
    ) -> RetrievalResult:
        return self._retrieve_via(
            "memory search", "memory", lambda: self._memory_items(query, current_mood), _MEMORY_FMT
        )

    def _search_web(self, query: str) -> str:
        """两步分离法：搜索 → 角色过滤 → 注入。"""
        return self._search_web_ex(query).block

    def _search_web_ex(self, query: str) -> RetrievalResult:
        return self._retrieve_via("Web search", "web", lambda: self._web_items(query), _WEB_FMT)

    # ── 三源各自的 produce（差异点之一） ──────────────────────

    def _scene_items(self, query: str) -> tuple[list[EvidenceItem], str | None]:
        """rag 的结构化出口。rag 未配置 → 空（真无，不是失败）。"""
        if self.rag is None:
            return [], None
        hits = self.rag.query_with_emotion_ex(
            query,
            current_emotion=_detect_emotion(query),
            character_name=self.card.name,
            top_k=3,
        )
        return list(hits), None

    def _memory_items(
        self, query: str, current_mood: str | None
    ) -> tuple[list[EvidenceItem], str | None]:
        if not self.memory or not self.memory.enabled or not self.card_id:
            return [], None
        memories = self.memory.search(query, self.card_id, current_mood=current_mood)
        return [
            memory_evidence(
                text=m["text"],
                relevance=m["relevance"],
                importance=m["importance"],
                age_seconds=m["age_seconds"],
                memory_mood=m["memory_mood"],
                emo_affinity=m["emo_affinity"],
                final=m["final"],
            )
            for m in memories
        ], None

    def _web_items(self, query: str) -> tuple[list[EvidenceItem], str | None]:
        """web 是两阶段（检索 → 改写）。第一阶段失败上抛（→ failed）；第二阶段失败只
        降级 body，**items 保留**（检索确实发生过，抹平更绕）。"""
        import httpx

        # 第一步：搜索（DuckDuckGo 免费 API）
        resp = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1},
            timeout=5,
        )
        data = resp.json()
        items, raw_results = _ddg_items(data, datetime.now(timezone.utc).isoformat())
        if not raw_results.strip():
            return [], None
        if not self._llm:
            return items, ""

        # 第二步：角色过滤器（独立 LLM 调用）
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
            try_record_usage(self._storage, self._llm, action="chat_web_filter", source="ContextEngine")
        except Exception as exc:
            print(f"[ContextEngine] Character filter failed: {exc}")
            return items, ""
        # 过滤器判定「全不适合」→ prompt 侧无输出、块为空，但来源确实检索到了：items
        # 照出（这一态的上层表达是 commit 3 的事，这里只保证不丢）。
        return items, (filtered if filtered.strip() else "")
