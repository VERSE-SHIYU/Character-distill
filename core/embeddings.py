import os
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar

from chromadb.api.types import EmbeddingFunction

from core import telemetry as T  # OTel 埋点（OTEL_ENABLED 关时装饰器原样返回，零开销）

# ── Active embed deadline（D2：线程/上下文级，杜绝跨请求污染）────────────────
# 挂死的 embed 发生在 mem0/chroma 库内部（它们各自调 embedding_model.embed /
# EmbeddingFunction.__call__），函数参数传不进第三方库 —— scope 是唯一能夹逼库内
# embed 的机制。必须是 ContextVar 而非模块级全局：tools.execute 每个 tool 一个 executor
# 线程、异步侧还有并发，全局会串，不同请求的 deadline 互相污染。
# 约定：embed 调用所在线程（检索 handler 线程 = mem0/chroma embed 同线程）里开 scope，
# _call_api 读当前值；无 scope / None → 嵌入行为与 D2 前逐字节一致。
_EMBED_DEADLINE: ContextVar[float | None] = ContextVar("embed_deadline", default=None)


@contextmanager
def embed_deadline(deadline: float | None):
    """把当前上下文嵌入调用的截止时刻设为 deadline（time.monotonic() 刻度，绝对值）。

    None 等价于不设 —— 调用方想显式声明"此路径不夹逼"时传 None。
    """
    if deadline is None:
        yield
        return
    token = _EMBED_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _EMBED_DEADLINE.reset(token)


def current_embed_deadline() -> float | None:
    """当前上下文的嵌入截止时刻（无则 None）。供 _call_api 与测试/假 ctx 读取。"""
    return _EMBED_DEADLINE.get()


# ── D2 bounded 单次调用上限组 ──────────────────────────────────
# ceiling 8.0 = 旧 client timeout 默认值（保留非工具路径 ~24s 上限）；env 可覆盖，默认不变。
# margin/min 只服务 bounded 循环收尾：剩余不足撑一次有效 attempt 则拒发，不做会把
# timeout=0/负 变成 no-timeout 假请求的死亡窗 create。
_EMBED_ATTEMPT_S = max(float(os.getenv("EMBED_ATTEMPT_S", "8.0")), 0.1)
_EMBED_MARGIN_S = 0.1
_EMBED_MIN_S = 0.1
_EMBED_WINDOW_S = _EMBED_MARGIN_S + _EMBED_MIN_S
_EMBED_ATTEMPTS = 3  # bounded 路径总尝试上限（对齐旧 SDK max_retries=2 → ≤3 次 HTTP）


def _retry_after_secs(exc: Exception) -> float | None:
    """429 响应里的 Retry-After 秒数（夹到 5s，防 user key 侧无限重打），无则 None。"""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if not headers:
        return None
    val = headers.get("Retry-After", "")
    if val and val.isdigit():
        return min(float(val), 5.0)
    return None


def _is_retryable_embed(exc: Exception) -> bool:
    """bounded 路径重试分类：429 / 5xx / 408 / 409 / 连接与读超时 → 重试；
    400/401/403/其他 → 立即抛。可重试是受 deadline 约束的显式重试（不是把瞬时抖动吞成
    空检索，也不是把用户配置/内容错误当抖动反复打）。
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 429) or status >= 500
    name = type(exc).__name__
    mod = type(exc).__module__ or ""
    if "openai" in mod:
        return name in ("APIConnectionError", "APITimeoutError")
    return name in ("TimeoutError", "ConnectionError")

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

        # EMBEDDING_BASE_URL 覆盖（②④ Step 2：本地压测剥离 DashScope 网络延迟）；
        # 未设置时行为与 region 硬编码表逐字节一致。
        base_url = os.environ.get("EMBEDDING_BASE_URL") or {
            "cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        }[region]
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=8.0, max_retries=2
        )
        # D2：bounded 路径用 max_retries=0 克隆 —— SDK 内部重试会复用同一 per-request
        # timeout（挂死=3×timeout，严格吃不到 deadline），必须撤掉黑盒重试，改由
        # _call_api 自己做受 deadline 约束的显式重试。with_options 共享同一 httpx 连接池。
        self._client_no_retry = self._client.with_options(max_retries=0)
        self._model = model
        self._dimensions = dimensions

    MAX_BATCH = 10  # 百炼 text-embedding-v4 单次最多 10 条
    MAX_CHARS = 3000  # 单条安全长度（~8192 token 的保守字符数）
    STRATEGY = "batch=10, truncate=3000"

    @T.spanned("embed.api", op="embeddings",
               finalize=lambda sp, self, res, exc: T.set_attr(sp, "model", self._model))
    def _call_api(self, texts: list[str], *, deadline: float | None = None) -> list[list[float]]:
        """Single batch API call. Returns embeddings in input order.

        deadline（time.monotonic() 刻度，绝对值）：非 None 时本调用被严格夹逼 ——
        per-attempt timeout=min(ceiling, 剩余−margin)，次数(≤_EMBED_ATTEMPTS)与时限
        (deadline)二维先到先弃；429(可 honor Retry-After)/5xx/连接与读超时在窗口内显式重试，
        400/401/403 立即抛。deadline=None 且无活跃 embed_deadline scope → 走 _client
        (timeout=8.0, max_retries=2)，行为与 D2 前逐字节一致（非工具路径 ~24s 上限保留）。
        """
        effective = deadline if deadline is not None else _EMBED_DEADLINE.get()
        if effective is None:
            resp = self._client.embeddings.create(
                model=self._model, input=texts, dimensions=self._dimensions,
            )
            return [resp.data[i].embedding for i in range(len(texts))]
        return self._call_api_bounded(texts, effective)

    def _call_api_bounded(self, texts: list[str], deadline: float) -> list[list[float]]:
        """受 deadline 夹逼的单批调用：次数与时限两维分开管，先到先弃。"""
        attempts = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining - _EMBED_MARGIN_S < _EMBED_MIN_S:
                raise RuntimeError(
                    f"embedding no time for an attempt: remaining {remaining:.2f}s "
                    f"< window {_EMBED_WINDOW_S:.2f}s (deadline hit)"
                )
            timeout = min(_EMBED_ATTEMPT_S, remaining - _EMBED_MARGIN_S)
            try:
                resp = self._client_no_retry.embeddings.create(
                    model=self._model, input=texts, dimensions=self._dimensions,
                    timeout=timeout,
                )
                return [resp.data[i].embedding for i in range(len(texts))]
            except Exception as exc:
                attempts += 1
                if attempts >= _EMBED_ATTEMPTS or not _is_retryable_embed(exc):
                    raise
                wait = _retry_after_secs(exc) or 0.3
                rem = deadline - time.monotonic()
                max_wait = rem - _EMBED_WINDOW_S  # 睡满还留 min 窗给下一次有效 attempt
                if wait > max_wait:
                    wait = max(0.0, max_wait)
                if wait > 0:
                    time.sleep(wait)

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
