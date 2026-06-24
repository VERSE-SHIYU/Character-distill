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

    def __call__(self, input: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=input, dimensions=self._dimensions)
        return [item.embedding for item in resp.data]


def create_safe_embedding_fn(
    model_name: str = "all-MiniLM-L6-v2",
    api_key: str = "",
    region: str = "cn",
) -> EmbeddingFunction:
    """Create a DashScope embedding function. Requires api_key to be configured."""
    if not api_key:
        raise RuntimeError("未配置向量检索 API Key，请在设置页填写阿里云百炼 API Key")
    cache_key = f"dashscope:{region}"
    if cache_key not in _cache:
        _cache[cache_key] = DashScopeEmbedding(api_key, region)
    return _cache[cache_key]
