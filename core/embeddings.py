from chromadb.api.types import EmbeddingFunction

_cache: dict[str, "DashScopeEmbedding"] = {}


class DashScopeEmbedding(EmbeddingFunction):
    """Aliyun Bailian text-embedding-v4 via OpenAI-compatible API."""

    def __init__(self, api_key: str, region: str = "cn", model: str = "text-embedding-v4", dimensions: int = 1024):
        from openai import OpenAI
        base_url = {
            "cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        }[region]
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._dimensions = dimensions

    MAX_BATCH = 10        # 百炼 text-embedding-v4 单次最多 10 条
    MAX_CHARS = 3000       # 单条安全长度（~8192 token 的保守字符数）
    STRATEGY = "batch=10, truncate=3000"

    def __call__(self, input: list[str]) -> list[list[float]]:
        # 过滤空串并记录偏移，保持返回顺序对齐
        non_empty: list[tuple[int, str]] = [(i, t) for i, t in enumerate(input) if t.strip()]
        if not non_empty:
            return []

        # 单条长度截断
        cleaned: list[tuple[int, str]] = []
        for idx, text in non_empty:
            if len(text) > self.MAX_CHARS:
                text = text[:self.MAX_CHARS]
            cleaned.append((idx, text))

        # 分批
        all_results: list[tuple[int, list[float]]] = []
        batch_size = self.MAX_BATCH
        for batch_start in range(0, len(cleaned), batch_size):
            batch = cleaned[batch_start:batch_start + batch_size]
            texts = [t for _, t in batch]
            try:
                resp = self._client.embeddings.create(
                    model=self._model, input=texts, dimensions=self._dimensions,
                )
            except Exception as exc:
                lengths = [len(t) for t in texts]
                raise RuntimeError(
                    f"百炼 embedding 失败：第{batch_start // batch_size + 1}批，"
                    f"{len(texts)}条，单条长度{lengths}。"
                    f"可能超长或超限: {exc}"
                ) from exc
            for item in resp.data:
                all_results.append((batch[item.index][0], item.embedding))

        # 按原始顺序恢复
        all_results.sort(key=lambda x: x[0])
        return [emb for _, emb in all_results]


def create_safe_embedding_fn(
    api_key: str = "",
    region: str = "cn",
) -> EmbeddingFunction:
    """Create a DashScope embedding function. Requires api_key to be configured."""
    if not api_key:
        raise RuntimeError("未配置向量检索 API Key，请在设置页填写阿里云百炼 API Key")
    cache_key = f"dashscope:{region}:{api_key[:8]}"
    if cache_key not in _cache:
        _cache[cache_key] = DashScopeEmbedding(api_key, region)
    return _cache[cache_key]
