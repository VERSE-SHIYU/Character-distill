from collections import OrderedDict
import threading

from chromadb.api.types import EmbeddingFunction

# ── Factory cache: region:api_key → DashScopeEmbedding singleton ──
_cache: dict[str, "DashScopeEmbedding"] = {}

# ── Module-level shared embedding cache (thread-safe) ─────────
# Both RAG (via DashScopeEmbedding) and Mem0 (via Mem0BridgeEmbedder)
# use this cache so identical texts across the two pipelines are
# only embedded once.
_SHARED_CACHE: OrderedDict[str, list[float]] = OrderedDict()
_SHARED_CACHE_LOCK = threading.Lock()
_SHARED_CACHE_MAX = 512

# ── Module-level [embed-stats] counters (thread-safe) ────────
_STATS_LOCK = threading.Lock()
_STATS_API_CALLS = 0       # texts that actually called the API
_STATS_CACHE_HITS = 0      # texts served from shared cache
_STATS_TOKENS = 0          # estimated tokens of all texts processed
_STATS_BY_SOURCE: dict[str, int] = {}
_STATS_MODERATION_BLOCKED = 0
_STATS_LAST_LOG_THRESHOLD = 0

_MODERATION_KEYWORDS = ("DataInspectionFailed", "contentFilter", "content_filter",
                        "blocked", "inappropriate", "risk control")


def _is_moderation_error(msg: str) -> bool:
    """Check if an exception message indicates DashScope content moderation."""
    lower = msg.lower()
    return any(kw.lower() in lower for kw in _MODERATION_KEYWORDS)


def _update_stats(
    api_calls: int, cache_hits: int, moderation_blocked: int,
    tokens: int, source: str,
) -> None:
    global _STATS_API_CALLS, _STATS_CACHE_HITS, _STATS_TOKENS, _STATS_MODERATION_BLOCKED, _STATS_LAST_LOG_THRESHOLD
    with _STATS_LOCK:
        _STATS_API_CALLS += api_calls
        _STATS_CACHE_HITS += cache_hits
        _STATS_TOKENS += tokens
        _STATS_MODERATION_BLOCKED += moderation_blocked
        _STATS_BY_SOURCE[source] = _STATS_BY_SOURCE.get(source, 0) + api_calls + cache_hits

        total = _STATS_API_CALLS + _STATS_CACHE_HITS
        threshold = (total // 100) * 100
        if threshold >= 100 and threshold > _STATS_LAST_LOG_THRESHOLD:
            _STATS_LAST_LOG_THRESHOLD = threshold
            hit_rate = _STATS_CACHE_HITS / total * 100 if total > 0 else 0.0
            by_source = ", ".join(
                f"{k}={v}" for k, v in sorted(_STATS_BY_SOURCE.items())
            )
            extra = ""
            if _STATS_MODERATION_BLOCKED:
                extra = f" moderation_blocked={_STATS_MODERATION_BLOCKED}"
            print(
                f"[embed-stats] SUMMARY: total={total} api={_STATS_API_CALLS}"
                f" hits={_STATS_CACHE_HITS} hit_rate={hit_rate:.1f}%"
                f" tokens≈{_STATS_TOKENS}"
                f" by_source=[{by_source}]"
                f"{extra}"
            )


def _shared_cache_get(text: str) -> list[float] | None:
    """Thread-safe lookup in the shared embedding cache."""
    with _SHARED_CACHE_LOCK:
        cached = _SHARED_CACHE.get(text)
        if cached is not None:
            _SHARED_CACHE.move_to_end(text)
        return cached


def _shared_cache_put(text: str, embedding: list[float]) -> None:
    """Thread-safe insert into the shared embedding cache."""
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE[text] = embedding
        if len(_SHARED_CACHE) > _SHARED_CACHE_MAX:
            _SHARED_CACHE.popitem(last=False)


class DashScopeEmbedding(EmbeddingFunction):
    """Aliyun Bailian text-embedding-v4 via OpenAI-compatible API.

    Uses a module-level shared LRU cache so that identical text across
    RAG and Mem0 pipelines is only embedded once.

    ``source`` is used for [embed-stats] per-source bucketing:
    set to ``"rag"`` (default) or ``"mem0"`` via the ``Mem0BridgeEmbedder``.
    """

    source: str = "rag"

    def __init__(
        self,
        api_key: str,
        region: str = "cn",
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
    ):
        from openai import OpenAI

        base_url = {
            "cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        }[region]
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=8.0, max_retries=2
        )
        self._model = model
        self._dimensions = dimensions

    MAX_BATCH = 10  # 百炼 text-embedding-v4 单次最多 10 条
    MAX_CHARS = 3000  # 单条安全长度（~8192 token 的保守字符数）
    STRATEGY = "batch=10, truncate=3000"

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """Single batch API call. Returns embeddings in input order."""
        resp = self._client.embeddings.create(
            model=self._model, input=texts, dimensions=self._dimensions,
        )
        return [resp.data[i].embedding for i in range(len(texts))]

    def _embed_impl(self, input: list[str]) -> list[list[float]]:
        """Raw embedding logic — returns pure Python list[list[float]].

        Used by both ``__call__`` (ChromaDB interface) and
        ``Mem0BridgeEmbedder`` (which must bypass ChromaDB's numpy
        normalization wrapper).
        """
        # 过滤空串并记录偏移，保持返回顺序对齐
        non_empty: list[tuple[int, str]] = [
            (i, t) for i, t in enumerate(input) if t.strip()
        ]
        if not non_empty:
            return []

        # 单条长度截断
        cleaned: list[tuple[int, str]] = []
        for idx, text in non_empty:
            if len(text) > self.MAX_CHARS:
                text = text[: self.MAX_CHARS]
            cleaned.append((idx, text))

        # 查共享缓存
        all_results: list[tuple[int, list[float]]] = []
        uncached: list[tuple[int, str]] = []
        for idx, text in cleaned:
            cached = _shared_cache_get(text)
            if cached is not None:
                all_results.append((idx, cached))
            else:
                uncached.append((idx, text))

        cache_hits = len(cleaned) - len(uncached)
        api_calls = 0
        moderation_blocked = 0

        # 未命中的调 API
        batch_size = self.MAX_BATCH
        for batch_start in range(0, len(uncached), batch_size):
            batch = uncached[batch_start : batch_start + batch_size]
            texts = [t for _, t in batch]
            try:
                embeddings = self._call_api(texts)
                api_calls += len(texts)
                for (orig_idx, orig_text), emb in zip(batch, embeddings):
                    _shared_cache_put(orig_text, emb)
                    all_results.append((orig_idx, emb))
            except Exception as exc:
                exc_str = str(exc)
                if _is_moderation_error(exc_str):
                    # 内容审核拦截：降级为逐条重试，跳过违规段
                    print(
                        f"[embed-stats] moderation detected in batch, "
                        f"falling back to per-item retry: {exc_str[:120]}"
                    )
                    for orig_idx, orig_text in batch:
                        try:
                            single_emb = self._call_api([orig_text])
                            api_calls += 1
                            _shared_cache_put(orig_text, single_emb[0])
                            all_results.append((orig_idx, single_emb[0]))
                        except Exception as inner_exc:
                            if _is_moderation_error(str(inner_exc)):
                                moderation_blocked += 1
                                print(
                                    f"[embed-stats] moderation_blocked: "
                                    f"len={len(orig_text)} source={self.source}"
                                )
                                # 零向量占位，保持 ChromaDB 对齐
                                all_results.append(
                                    (orig_idx, [0.0] * self._dimensions)
                                )
                            else:
                                raise
                else:
                    lengths = [len(t) for t in texts]
                    raise RuntimeError(
                        f"百炼 embedding 失败：第{batch_start // batch_size + 1}批，"
                        f"{len(texts)}条，单条长度{lengths}。"
                        f"可能超长或超限: {exc}"
                    ) from exc

        # 统计
        total_tokens = sum(len(t) for _, t in cleaned) // 2
        _update_stats(api_calls, cache_hits, moderation_blocked, total_tokens, self.source)

        # 按原始顺序恢复
        all_results.sort(key=lambda x: x[0])
        return [emb for _, emb in all_results]

    def __call__(self, input: list[str]) -> list[list[float]]:
        """ChromaDB EmbeddingFunction interface — delegates to _embed_impl."""
        return self._embed_impl(input)


class Mem0BridgeEmbedder:
    """Bridges DashScopeEmbedding to Mem0's EmbeddingBase interface.

    Shares the module-level LRU cache so identical texts across RAG and
    Mem0 pipelines are only embedded once.  Implements the same duck-typed
    protocol that Mem0's ``Memory.embedding_model`` expects:
    ``embed(text)`` and ``embed_batch(texts)``.
    """

    def __init__(
        self,
        api_key: str,
        region: str = "cn",
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
    ):
        self._dashscope = DashScopeEmbedding(api_key, region, model, dimensions)
        self._dashscope.source = "mem0"
        self._dimensions = dimensions

    def embed(
        self, text: str, memory_action: str | None = None
    ) -> list[float]:
        # Use _embed_impl directly to bypass ChromaDB's numpy normalization
        return self._dashscope._embed_impl([text])[0]

    def embed_batch(
        self, texts: list[str], memory_action: str = "add"
    ) -> list[list[float]]:
        return self._dashscope._embed_impl(texts)


def create_safe_embedding_fn(
    api_key: str = "",
    region: str = "cn",
) -> EmbeddingFunction:
    """Create a DashScope embedding function. Requires api_key to be configured."""
    if not api_key:
        raise RuntimeError(
            "未配置向量检索 API Key，请在设置页填写阿里云百炼 API Key"
        )
    cache_key = f"dashscope:{region}:{api_key[:8]}"
    if cache_key not in _cache:
        _cache[cache_key] = DashScopeEmbedding(api_key, region)
    return _cache[cache_key]


def get_embed_stats() -> dict:
    """Return current [embed-stats] counters (for tests / monitoring)."""
    with _STATS_LOCK:
        total = _STATS_API_CALLS + _STATS_CACHE_HITS
        hit_rate = _STATS_CACHE_HITS / total * 100 if total > 0 else 0.0
        return {
            "total": total,
            "api_calls": _STATS_API_CALLS,
            "cache_hits": _STATS_CACHE_HITS,
            "hit_rate": round(hit_rate, 1),
            "tokens": _STATS_TOKENS,
            "by_source": dict(_STATS_BY_SOURCE),
            "moderation_blocked": _STATS_MODERATION_BLOCKED,
        }


def reset_embed_stats() -> None:
    """Reset [embed-stats] counters (for tests)."""
    global _STATS_API_CALLS, _STATS_CACHE_HITS, _STATS_TOKENS, _STATS_BY_SOURCE
    global _STATS_MODERATION_BLOCKED, _STATS_LAST_LOG_THRESHOLD
    with _STATS_LOCK:
        _STATS_API_CALLS = 0
        _STATS_CACHE_HITS = 0
        _STATS_TOKENS = 0
        _STATS_BY_SOURCE = {}
        _STATS_MODERATION_BLOCKED = 0
        _STATS_LAST_LOG_THRESHOLD = 0
