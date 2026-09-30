"""Mem0 长期记忆管理器。"""

from __future__ import annotations

import logging

import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.llm_adapter import LLMAdapter
from core import concurrency as C  # 派生与上下文传播（ctx_thread）
from core.utils import try_record_usage

logger = logging.getLogger(__name__)

# ── 加权检索常量（两级门控: base = α·rel + β·rec + γ·imp, final = base × (1+λ·emo)）──
RERANK_ALPHA = 0.60    # 语义相关性权重（含原 DELTA 并入）
RERANK_BETA  = 0.15    # 时间新近度权重
RERANK_GAMMA = 0.25    # 重要性权重
RERANK_LAMBDA = 0.25   # 情绪提亮乘性增益上界（final 最多放大 1.25×, 语义无关项不被抬高）
RECENCY_TAU_HOURS = 72 # 指数衰减时间常数（小时）
REFLECTION_THRESHOLD = 30   # 累计重要性达此值（且满足双条件）触发反思
REFLECTION_MIN_ROUNDS = 8   # 距上次反思最少轮数，防高频触发
REFLECTION_MIN_QUALITY = 3  # 触发反思最少需要的高质量（importance>=7）原始记忆条数

# ── 情绪极性关键词（子串匹配，覆盖 LLM 丰富情绪词）──
_POSITIVE_KEYWORDS = [
    "开心", "喜悦", "高兴", "快乐", "幸福", "甜蜜", "心动", "期待", "好奇",
    "温柔", "安心", "放心", "欣慰", "感激", "感动", "满足", "骄傲", "自豪",
    "兴奋", "放松", "惬意", "释然", "窃喜", "喜欢", "上头", "心软",
]
_NEGATIVE_KEYWORDS = [
    "烦乱", "愤怒", "暴怒", "生气", "恼火", "烦躁", "焦虑", "不安", "紧张",
    "委屈", "吃味", "嫉妒", "失落", "伤心", "难过", "悲伤", "痛苦", "绝望",
    "恐惧", "害怕", "防备", "警觉", "厌恶", "嫌弃", "无奈", "疲惫", "冷淡",
    "疏离", "不屑", "心碎", "背叛", "刺痛", "又气", "心痛", "心如刀绞",
    "自毁", "恨",
]

# ── VAD 情绪映射表（valence∈[-1,1], arousal∈[0,1], 子串命中聚合｜零模型）──
_VAD_MAP: dict[str, tuple[float, float]] = {
    # 正面
    "开心": (0.80, 0.70), "喜悦": (0.90, 0.60), "高兴": (0.80, 0.60),
    "快乐": (0.85, 0.60), "幸福": (0.90, 0.40), "甜蜜": (0.80, 0.30),
    "心动": (0.70, 0.80), "期待": (0.50, 0.70), "好奇": (0.40, 0.60),
    "温柔": (0.70, 0.20), "安心": (0.60, 0.20), "放心": (0.60, 0.15),
    "欣慰": (0.70, 0.30), "感激": (0.80, 0.50), "感动": (0.80, 0.60),
    "满足": (0.70, 0.30), "骄傲": (0.60, 0.50), "自豪": (0.60, 0.50),
    "兴奋": (0.80, 0.90), "放松": (0.70, 0.10), "惬意": (0.70, 0.15),
    "释然": (0.50, 0.10), "窃喜": (0.60, 0.40), "喜欢": (0.80, 0.60), "上头": (0.60, 0.90),
    "心软": (0.40, 0.30),
    # 负面
    "烦乱": (-0.50, 0.70), "愤怒": (-0.90, 0.90), "暴怒": (-0.95, 0.95),
    "生气": (-0.70, 0.70), "恼火": (-0.60, 0.70), "烦躁": (-0.50, 0.70),
    "焦虑": (-0.50, 0.80), "不安": (-0.40, 0.60), "紧张": (-0.30, 0.70),
    "委屈": (-0.50, 0.30), "吃味": (-0.30, 0.40), "嫉妒": (-0.50, 0.60),
    "失落": (-0.50, 0.20), "伤心": (-0.70, 0.30), "难过": (-0.60, 0.30),
    "悲伤": (-0.80, 0.30), "痛苦": (-0.90, 0.50), "绝望": (-0.95, 0.20),
    "恐惧": (-0.80, 0.90), "害怕": (-0.70, 0.80), "防备": (-0.20, 0.60),
    "警觉": (-0.10, 0.70), "厌恶": (-0.70, 0.60), "嫌弃": (-0.50, 0.40),
    "无奈": (-0.30, 0.20), "疲惫": (-0.40, 0.10), "冷淡": (-0.30, 0.10),
    "疏离": (-0.30, 0.15), "不屑": (-0.40, 0.30), "心碎": (-0.90, 0.40),
    "背叛": (-0.85, 0.60), "刺痛": (-0.60, 0.50), "又气": (-0.50, 0.70),
    "心痛": (-0.70, 0.30), "心如刀绞": (-0.90, 0.60),
    "自毁": (-0.95, 0.30), "恨": (-0.90, 0.70),
}


def _lookup_vad(mood: str) -> tuple[float, float]:
    """子串匹配 VAD 表，返回 (valence, arousal)。零匹配时按极性回退。"""
    if not mood:
        return (0.0, 0.5)
    m = mood.strip()
    vals, arous = [], []
    for kw, (v, a) in _VAD_MAP.items():
        if kw in m:
            vals.append(v)
            arous.append(a)
    if vals:
        return (sum(vals) / len(vals), sum(arous) / len(arous))
    pol = _get_emotion_polarity(mood)
    if pol == 1:
        return (0.5, 0.5)
    if pol == -1:
        return (-0.5, 0.5)
    return (0.0, 0.5)


def _emotion_affinity(current_mood: str | None, memory_mood: str) -> float:
    """VAD 加权欧氏距离情感亲和度 [0,1]。

    valence 差权重为 arousal 的 2 倍（情感色调比激活度更重要）。
    """
    if current_mood is None:
        return 0.5
    cur_v, cur_a = _lookup_vad(current_mood)
    mem_v, mem_a = _lookup_vad(memory_mood)
    d_v = cur_v - mem_v      # [-2, 2]
    d_a = cur_a - mem_a      # [-1, 1]
    # 加权欧氏距离: valence 权重 2, arousal 权重 1
    dist = math.sqrt(0.5 * d_v * d_v + d_a * d_a)
    # 最大可能距离 sqrt(2*1 + 1*1) = sqrt(3)
    sim = 1.0 - dist / math.sqrt(3.0)
    return max(0.0, min(1.0, sim))


def _get_emotion_polarity(mood: str) -> int:
    """返回情绪极性：1=正面, -1=负面, 0=中性/未知。

    使用关键词子串匹配——LLM 产生的情绪词变化繁多（如"暴怒中混杂着被无视的刺痛"），
    精确匹配覆盖率太低。子串匹配按正面/负面关键词命中数多数决，平局则判定为中性。
    """
    if not mood:
        return 0
    m = mood.strip()
    pos_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw in m)
    neg_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in m)
    if pos_hits > neg_hits:
        return 1
    if neg_hits > pos_hits:
        return -1
    return 0

def _emotion_match(current_mood: str | None, memory_mood: str) -> float:
    """情绪契合度：同极性=1.0, 一正一负=0.0, 涉及中性/未知=0.5。"""
    if current_mood is None:
        return 0.5
    cur_p = _get_emotion_polarity(current_mood)
    mem_p = _get_emotion_polarity(memory_mood)
    if cur_p == 0 or mem_p == 0:
        return 0.5
    return 1.0 if cur_p == mem_p else 0.0


# ── mem0 提炼的采样参数 ───────────────────────────────────────────────────
#
# 提炼记忆要和 mem0 原来发的那一套一致：走适配器后就不再看 mem0 的默认值，得自己带。
# 原值出处 `mem0/configs/llms/base.py:19-22`（`BaseLlmConfig` 默认）经
# `mem0/llms/base.py::_get_common_params` 发出；`presence_penalty` 原提供方**不发**，
# 即 API 默认 0。`max_tokens` 不复原：它只是输出上限，越低越容易被截断，而截断会被
# 适配器判成 `IncompleteResponseError`，这条记忆就丢了。
_EXTRACT_TEMPERATURE = 0.1
_EXTRACT_PRESENCE_PENALTY = 0.0
_EXTRACT_TOP_P = 0.1


class MemoryKeyMissing(RuntimeError):
    """底层实例被要求做一件需要模型的事。底层实例只该查看 / 删除 —— 走到这里就是接错了线。"""


class _NoKeyEmbedder:
    """底层实例（不属于任何用户）的 embedder：一调用就炸，绝不拿谁的 key 去出站。"""

    def embed(self, *_a: Any, **_kw: Any) -> Any:
        raise MemoryKeyMissing("底层记忆实例不做向量化：检索 / 写入必须走 for_user() 的用户视图")

    def embed_batch(self, *_a: Any, **_kw: Any) -> Any:
        raise MemoryKeyMissing("底层记忆实例不做向量化：检索 / 写入必须走 for_user() 的用户视图")


class _NoKeyLLM:
    """底层实例的 LLM：一调用就炸（理由同 `_NoKeyEmbedder`）。"""

    def generate_response(self, *_a: Any, **_kw: Any) -> Any:
        raise MemoryKeyMissing("底层记忆实例不提炼记忆：写入必须走 for_user() 的用户视图")


# 底层实例构造 mem0 自带客户端时要一个非空 key（构造完立刻被上面两个替身换掉，从不出站）。
_UNUSED_KEY = "unused-no-outbound"


class MemoryManager:
    """长期记忆的**工厂**：一份共享的向量库，按用户给出绑定其自己 key 的视图。

    口径（`docs/specs/user-own-keys.md`）：提炼用的 LLM 与向量化用的 embedding **都用
    用户自己的 key**，缺一把就不给视图（`for_user` 返回 None）—— 引擎拿到 None 时原有的
    「记忆为空即跳过」分支就是「不提供记忆」的实现。

    为什么是「工厂 + 视图」而不是一个实例：本地 qdrant 同一目录只允许一个客户端（第二个
    直接 `RuntimeError: ... already accessed by another instance`），而每个用户的 key 不同。
    故只开**一个** `QdrantClient`，每个用户一个 `Memory`，经 mem0 官方参数
    `vector_store.config.client` 共用它（mem0 2.0.20 `configs/vector_stores/qdrant.py:13`）。
    视图按「用户 + LLM 凭据指纹 + embedding key 指纹」缓存，与 RAG 的
    `IndexingService._get_or_build_rag` 同一写法 —— 用户换了 key，下次取到的就是新视图。

    `base()` 是不属于任何用户的底层视图：只做查看 / 删除（这两类 mem0 不调模型）。
    它的 embedder / LLM 是一碰就炸的替身，保证它绝不会替谁出站。
    """

    def __init__(self, config: dict[str, Any] | None = None, *, db_dir: str | Path | None = None):
        config = config or {}
        self._enabled = config.get("enabled", True)
        self._search_top_k = config.get("search_top_k", 10)
        self._context_window = config.get("context_window", 30)
        repo_root = Path(__file__).resolve().parent.parent
        self._db_dir = Path(db_dir) if db_dir is not None else repo_root / "data" / "mem0_db"
        self._lock = threading.Lock()
        self._client: Any = None  # qdrant_client.QdrantClient，首次用到时才开（开目录即上锁）
        self._views: dict[str, MemoryView] = {}
        self._base: MemoryView | None = None
        if not self._enabled:
            logger.warning("Mem0 disabled by config (memory.enabled = false)")

    @property
    def enabled(self) -> bool:
        """配置层面开没开。某个用户有没有记忆，看 `for_user` 返回的是不是 None。"""
        return bool(self._enabled)

    def _shared_client(self) -> Any:
        if self._client is None:
            from qdrant_client import QdrantClient

            self._db_dir.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(self._db_dir))
        return self._client

    def _build(self, *, llm: Any, llm_key: str, embedder: Any, embed_key: str) -> Any:
        """官方 `Memory.from_config` + 注入。调用方持锁。"""
        from mem0 import Memory

        mem = Memory.from_config({
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "client": self._shared_client(),
                    "embedding_model_dims": 1024,
                },
            },
            # 这两段只用来把 `Memory` 构造起来（`from_config` 会各建一个客户端），建完
            # 立刻被下面的注入替换掉。
            "llm": {
                "provider": "openai",
                "config": {"model": getattr(llm, "model", "unused"), "api_key": llm_key},
            },
            "embedder": {
                "provider": "openai",
                "config": {"model": "text-embedding-v4", "api_key": embed_key, "embedding_dims": 1024},
            },
            # 显式落在数据卷里：mem0 默认是 `~/.mem0/history.db`，容器里不在 `./data` 卷上，
            # 每次部署都被清空；而提炼时要读它（`get_last_messages`，mem0 main.py:920）。
            "history_db_path": str(self._db_dir / "history.db"),
        })
        mem.llm = llm
        mem.embedding_model = embedder
        return mem

    def base(self) -> MemoryView | None:
        """不属于任何用户的底层视图：只做查看 / 删除。配置关掉时返回 None。"""
        if not self._enabled:
            return None
        with self._lock:
            if self._base is None:
                mem = self._build(llm=_NoKeyLLM(), llm_key=_UNUSED_KEY,
                                  embedder=_NoKeyEmbedder(), embed_key=_UNUSED_KEY)
                self._base = MemoryView(mem, self._search_top_k, self._context_window)
            return self._base

    # ── 不需要 key 的操作：查看 / 删除（mem0 这几条路径不调模型）。没配 key 的用户照样能用，
    # 删用户时的清理也不依赖任何人的 key。

    def get_all(self, card_id: str) -> list[dict[str, Any]]:
        base = self.base()
        return base.get_all(card_id) if base is not None else []

    def delete(self, memory_id: str) -> bool:
        base = self.base()
        return base.delete(memory_id) if base is not None else False

    def delete_all(self, card_id: str) -> bool:
        base = self.base()
        return base.delete_all(card_id) if base is not None else False

    def for_user(
        self,
        *,
        user_id: str,
        llm: LLMAdapter | None,
        embedding_key: str,
        embedding_region: str = "cn",
    ) -> MemoryView | None:
        """这个用户的记忆视图；LLM 或 embedding key 缺一把就返回 None（不提供记忆）。

        *llm* 必须是**用户自己的**适配器（调用方从 `deps.get_user_llm` 取，它不回落全局）。
        提炼用的是从它派生的新实例 —— 同一份凭据，换成 mem0 那套低温采样。
        """
        if not self._enabled:
            return None
        if llm is None or not embedding_key:
            logger.info(
                "Mem0 off for user_id=%s: missing own %s", user_id,
                "LLM key" if llm is None else "embedding key",
            )
            return None
        from core.embeddings import Mem0BridgeEmbedder
        from core.fingerprint import key_fingerprint

        cache_key = (
            f"{user_id}:{llm.credential_fingerprint()}:"
            f"{key_fingerprint(embedding_key)}:{embedding_region}"
        )
        with self._lock:
            view = self._views.get(cache_key)
            if view is not None:
                return view
            extractor = llm.derive(
                # 本地压测把提炼导到 mock（tests/perf/e2e_otel.py）；未设置沿用用户自己的地址。
                base_url=os.environ.get("MEM0_LLM_BASE_URL") or None,
                temperature=_EXTRACT_TEMPERATURE,
                presence_penalty=_EXTRACT_PRESENCE_PENALTY,
                top_p=_EXTRACT_TOP_P,
            )
            from core.mem0_llm import AdapterLLM

            embedder = Mem0BridgeEmbedder(
                api_key=embedding_key,
                region=embedding_region,
                model="text-embedding-v4",
                dimensions=1024,
            )
            mem = self._build(llm=AdapterLLM(extractor), llm_key=_UNUSED_KEY,
                              embedder=embedder, embed_key=_UNUSED_KEY)
            view = MemoryView(mem, self._search_top_k, self._context_window)
            self._views[cache_key] = view
            logger.info("Mem0 view built for user_id=%s (LLM: %s)", user_id, extractor.model)
            return view


class MemoryView:
    """绑定**一个** mem0 `Memory` 实例的记忆读写（用户视图或底层视图）。

    方法体沿用改造前的 `MemoryManager`（逐字搬过来）：引擎侧的调用面不变 ——
    `ChatEngine` / `ContextEngine` / `ReflectionService` / `EventService` 拿到的就是它，
    拿到 None 时照旧跳过。
    """

    def __init__(self, mem: Any, search_top_k: int = 10, context_window: int = 30):
        self._enabled = True
        self._search_top_k = search_top_k
        self._context_window = context_window
        self._mem = mem
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled and self._mem is not None

    @property
    def context_window(self) -> int:
        return self._context_window

    def search(self, query: str, card_id: str, current_mood: str | None = None) -> list[dict[str, Any]]:
        """检索长期记忆，返回结构化 dict 列表（两级门控重排）。

        每条返回: {text, relevance, importance, age_seconds}
        两级门控: base = α·relevance_norm + β·recency + γ·importance_norm,
                  final = base × (1 + λ·emo_affinity)    # 乘性提亮,不翻转排序
        current_mood 为 None 时 emo_affinity=0.5（退化，向后兼容）。
        """
        if not self.enabled:
            return []
        try:
            query_est_tok = len(query) // 2
            print(f"[embed-stats] Mem0 search query_len={len(query)} est_tok={query_est_tok} card={card_id}")
            results = self._mem.search(
                query, filters={"user_id": card_id}, limit=self._search_top_k
            )
            if isinstance(results, dict):
                results = results.get("results", [])
        except Exception as exc:
            logger.warning("Mem0 search failed: %s", exc, exc_info=True)
            return []

        now = datetime.now(timezone.utc)
        scored: list[dict[str, Any]] = []

        for r in results:
            if not isinstance(r, dict):
                continue
            text = r.get("memory", "").strip()
            if not text:
                continue

            relevance = float(r.get("score", 0.5) or 0.5)

            meta = r.get("metadata") or {}
            importance_raw = meta.get("importance", 5) if isinstance(meta, dict) else 5
            importance = max(1, min(10, int(importance_raw)))
            memory_mood = meta.get("mood", "") if isinstance(meta, dict) else ""

            created_str = r.get("created_at", "")
            age_seconds = 0.0
            if created_str:
                try:
                    created = datetime.fromisoformat(str(created_str).replace("Z", "+00:00"))
                    age_seconds = (now - created).total_seconds()
                except (ValueError, TypeError):
                    pass

            scored.append({
                "text": text,
                "relevance": relevance,
                "importance": importance,
                "age_seconds": age_seconds,
                "memory_mood": memory_mood,
            })

        if not scored:
            return []

        # 归一化 relevance 到 0-1（Mem0 score 可能不在这个范围）
        rels = [s["relevance"] for s in scored]
        rel_min, rel_max = min(rels), max(rels)
        rel_range = rel_max - rel_min if rel_max > rel_min else 1.0

        # 两级门控评分: base = α·rel + β·rec + γ·imp, final = base × (1 + λ·emo_affinity)
        for s in scored:
            relevance_norm = (s["relevance"] - rel_min) / rel_range
            age_hours = s["age_seconds"] / 3600.0
            recency = math.exp(-age_hours / RECENCY_TAU_HOURS)
            importance_norm = s["importance"] / 10.0
            emo_aff = _emotion_affinity(current_mood, s["memory_mood"])
            base = (
                RERANK_ALPHA * relevance_norm
                + RERANK_BETA * recency
                + RERANK_GAMMA * importance_norm
            )
            s["final"] = base * (1 + RERANK_LAMBDA * emo_aff)
            s["emo_affinity"] = emo_aff
            s["base"] = base

        scored.sort(key=lambda s: s["final"], reverse=True)
        top = scored[: self._search_top_k]
        if top:
            summary = ", ".join(
                f"imp={m['importance']} base={m['base']:.3f} emo_aff={m['emo_affinity']:.2f} final={m['final']:.3f}" for m in top[:3]
            )
            mood_tag = f"mood={current_mood}" if current_mood else "mood=None"
            print(f"[MemoryManager] search top-{len(top)} ({mood_tag}): {summary}")
        return top

    def add(self, messages: list[dict[str, str]], card_id: str, metadata: dict | None = None) -> None:
        """将对话消息写入长期记忆（后台异步执行）。metadata 写入 Mem0 存储供检索加权。"""
        if not self.enabled:
            return

        print(f"[MemoryManager] add called: card={card_id} msg_count={len(messages)} metadata={metadata}")

        def _do_add():
            try:
                kwargs = {"user_id": card_id}
                if metadata:
                    kwargs["metadata"] = metadata
                total_chars = sum(len(m.get("content", "")) for m in messages if isinstance(m, dict))
                print(f"[embed-stats] Mem0 add est_tok={total_chars // 2} card={card_id}")
                result = self._mem.add(messages, **kwargs)
                # mem0 2.0.20 成功时返回 `{"results": [...]}`，一条都没提炼出来时返回 `[]`
                # （memory/main.py:877 与 :989）—— 两种形态都在，故不能只看一种。
                n_results = len(result.get("results", [])) if isinstance(result, dict) else len(result)
                print(f"[MemoryManager] add OK: card={card_id} result_len={n_results}")
            except Exception as exc:
                # ERROR 不是「更吓人」：写入失败 = 这条记忆丢了且不会自愈，而 WARNING
                # 进不了 GlitchTip 的问题列表 —— 之前 SZ/SG 静默了 48 小时没人知道。
                logger.error("Mem0 add failed: %s", exc, exc_info=True)

        C.ctx_thread(_do_add, daemon=True).start()  # context 传播点：记忆入库线程

    def get_all(self, card_id: str) -> list[dict[str, Any]]:
        """获取某角色的所有记忆。"""
        if not self.enabled:
            return []
        try:
            results = self._mem.get_all(filters={"user_id": card_id})
            if isinstance(results, dict):
                results = results.get("results", [])
            return results
        except Exception as exc:
            logger.warning("Mem0 get_all failed: %s", exc, exc_info=True)
            return []

    def add_manual(self, text: str, card_id: str, metadata: dict | None = None) -> bool:
        """手动添加一条单文本记忆。infer=False 避免 Mem0 LLM 提炼丢弃。

        metadata 可选，用于标记反思记忆 is_reflection=True 等。
        """
        if not self.enabled:
            return False
        try:
            kwargs = {"user_id": card_id, "infer": False}
            if metadata:
                kwargs["metadata"] = metadata
            print(f"[embed-stats] Mem0 add_manual est_tok={len(text) // 2} card={card_id}")
            result = self._mem.add(text, **kwargs)
            print(f"[MemoryManager] manual add result: {result}")
            return True
        except Exception as exc:
            logger.error("Mem0 manual add failed: %s", exc, exc_info=True)
            return False

    def reflect(self, card_id: str, llm, recent_memories: list[dict], char_name: str,
                storage=None) -> None:
        """把近期高重要性记忆综合成 1-2 条高阶洞察，后台写回。

        recent_memories: 已过滤的非反思记忆，每项含 text/importance/mood 等。
        llm: 复用 engine 的 LLM client（llm.chat(sp, [msg])）。
        storage: 记账落库用的依赖 —— 反思跑在后台线程、调用方无法在事后补记，
        故必须由调用方传入，在线程内紧跟调用落账。**归属不走这个参数**：线程经
        `ctx_thread` 派生，身份自己跟着上下文过去（缺陷 83）。
        """
        if not self.enabled:
            return
        if not recent_memories or llm is None:
            return

        def _do_reflect():
            try:
                mem_texts = "\n".join(
                    f"- [{m.get('importance', '?')}分] {m['text'][:200]}"
                    for m in recent_memories[:10]
                )
                prompt = (
                    f"你是{char_name}。请以第一人称回顾以下近期的重要对话记忆，"
                    f"提炼出 1-2 条关于「你和对方的关系变化」「你对对方的深层感受」"
                    f"或「你自己的成长」的高阶洞察。\n\n"
                    f"记忆列表：\n{mem_texts}\n\n"
                    f"要求：\n"
                    f"1. 每条洞察一句话，不要复述事实，要总结趋势或深层感悟\n"
                    f"2. 用{char_name}的第一人称口吻\n"
                    f"3. 只输出洞察本身，每条一行，不要编号、不要解释\n"
                    f"4. 如果记忆不足以形成洞察，输出空行"
                )
                reply = llm.chat(
                    "你是一个善于反思和内省的AI角色。",
                    [{"role": "user", "content": prompt}],
                )
                try_record_usage(storage, llm, action="memory_reflect", source="MemoryManager")
                print(f"[Reflection] LLM reply ({len(reply)} chars): {reply[:300]}")

                insights = [
                    line.strip() for line in reply.split("\n")
                    if line.strip() and not line.strip().startswith("#")
                ]
                for insight in insights[:2]:
                    if len(insight) < 6:
                        continue
                    ok = self.add_manual(
                        insight, card_id,
                        metadata={"is_reflection": True, "importance": 8},
                    )
                    print(f"[Reflection] wrote insight (ok={ok}): {insight[:120]}")
            except Exception as exc:
                logger.warning("Reflection insight write failed: %s", exc, exc_info=True)

        C.ctx_thread(_do_reflect, daemon=True).start()  # context 传播点：反思线程

    def update(self, memory_id: str, text: str) -> bool:
        """更新一条记忆的内容。"""
        if not self.enabled:
            return False
        try:
            self._mem.update(memory_id=memory_id, data=text)
            return True
        except Exception as exc:
            logger.warning("Mem0 update failed: %s", exc, exc_info=True)
            return False

    def delete(self, memory_id: str) -> bool:
        """删除单条记忆。"""
        if not self.enabled:
            return False
        try:
            self._mem.delete(memory_id)
            return True
        except Exception as exc:
            logger.warning("Mem0 delete failed: %s", exc, exc_info=True)
            return False

    def delete_all(self, card_id: str) -> bool:
        """清空某角色的全部记忆。"""
        if not self.enabled:
            return False
        try:
            self._mem.delete_all(user_id=card_id)
            return True
        except Exception as exc:
            logger.warning("Mem0 delete_all failed: %s", exc, exc_info=True)
            return False
