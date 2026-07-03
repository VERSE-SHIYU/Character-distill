"""Tests for shared embedding cache and Mem0 bridge."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.embeddings import (
    DashScopeEmbedding,
    Mem0BridgeEmbedder,
    get_embed_stats,
    reset_embed_stats,
)


@pytest.fixture(autouse=True)
def clear_stats():
    """Reset module-level counters before each test."""
    reset_embed_stats()


class _FakeEmbeddingData:
    """Mimics the structure of an OpenAI embedding response item."""

    def __init__(self, index: int, embedding: list[float]):
        self.index = index
        self.embedding = embedding


class _FakeEmbeddingResponse:
    """Mimics the structure of an OpenAI embeddings.create response."""

    def __init__(self, embeddings: list[list[float]]):
        self.data = [
            _FakeEmbeddingData(i, emb) for i, emb in enumerate(embeddings)
        ]


@pytest.fixture
def mock_openai_client():
    """Patch DashScopeEmbedding to use a mock OpenAI client.

    Returns the mock so tests can inspect/assert call counts.
    """
    fake_vec = [0.1] * 1024

    def _fake_create(*, model, input, dimensions, **_kwargs):
        if isinstance(input, str):
            input = [input]
        return _FakeEmbeddingResponse([fake_vec] * len(input))

    with patch("openai.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = _fake_create
        mock_openai.return_value = mock_client
        yield mock_client


class TestSharedCache:
    """Shared module-level cache: same text embedded only once."""

    def test_cache_hit_no_second_api_call(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        text = "今天天气真好"

        # First call — should hit API
        vec1 = emb([text])
        assert len(vec1) == 1
        assert len(vec1[0]) == 1024
        assert mock_openai_client.embeddings.create.call_count == 1

        # Second call with same text — cache hit, no API call
        vec2 = emb([text])
        assert len(vec2) == 1
        assert len(vec2[0]) == 1024
        assert mock_openai_client.embeddings.create.call_count == 1  # no increase

    def test_different_text_triggers_api(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")

        emb(["hello"])
        assert mock_openai_client.embeddings.create.call_count == 1

        emb(["world"])  # different → API call
        assert mock_openai_client.embeddings.create.call_count == 2

    def test_cache_shared_across_instances(self, mock_openai_client):
        """Two instances share the same cache."""
        emb1 = DashScopeEmbedding(api_key="key_a")
        emb2 = DashScopeEmbedding(api_key="key_b")

        emb1(["共享文本"])
        assert mock_openai_client.embeddings.create.call_count == 1

        emb2(["共享文本"])  # same text, different instance → cache hit
        assert mock_openai_client.embeddings.create.call_count == 1  # no increase

    def test_cache_and_mem0_bridge_share_cache(self, mock_openai_client):
        """RAG DashScopeEmbedding and Mem0 bridge share the same cache."""
        emb = DashScopeEmbedding(api_key="test_key")
        bridge = Mem0BridgeEmbedder(api_key="test_key")

        emb(["你好"])
        assert mock_openai_client.embeddings.create.call_count == 1

        bridge.embed("你好")  # same text via Mem0 path → cache hit
        assert mock_openai_client.embeddings.create.call_count == 1  # no increase

    def test_empty_input_raises_chroma_error(self, mock_openai_client):
        """ChromaDB's EmbeddingFunction wrapper rejects empty returns."""
        emb = DashScopeEmbedding(api_key="test_key")
        with pytest.raises(ValueError):
            emb([""])
        assert mock_openai_client.embeddings.create.call_count == 0


class TestMem0BridgeEmbedder:
    """Mem0BridgeEmbedder implements the Mem0 duck-typed protocol."""

    def test_embed_returns_correct_dimensions(self, mock_openai_client):
        bridge = Mem0BridgeEmbedder(api_key="test_key")
        vec = bridge.embed("测试文本")
        assert isinstance(vec, list), f"expected list, got {type(vec)}"
        assert len(vec) == 1024
        assert all(isinstance(v, float) for v in vec), "not all floats"

    def test_embed_batch_returns_list(self, mock_openai_client):
        bridge = Mem0BridgeEmbedder(api_key="test_key")
        texts = ["第一段", "第二段", "第三段"]
        vecs = bridge.embed_batch(texts)
        assert isinstance(vecs, list)
        assert len(vecs) == 3
        assert all(len(v) == 1024 for v in vecs)

    def test_source_is_mem0(self, mock_openai_client):
        """Bridge sets source='mem0' for stats bucketing."""
        bridge = Mem0BridgeEmbedder(api_key="test_key")
        assert bridge._dashscope.source == "mem0"


class TestEmbedStats:
    """[embed-stats] counters accumulate correctly."""

    def test_basic_counting(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        assert get_embed_stats()["calls"] == 0

        emb(["text_a"])
        stats = get_embed_stats()
        assert stats["calls"] == 1
        assert stats["by_source"].get("rag") == 1

    def test_rag_and_mem0_separate_buckets(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        bridge = Mem0BridgeEmbedder(api_key="test_key")

        emb(["rag_text"])
        bridge.embed("mem0_text")  # cache miss → calls API

        stats = get_embed_stats()
        assert stats["by_source"]["rag"] == 1
        assert stats["by_source"]["mem0"] == 1

    def test_cache_hit_does_not_double_count(self, mock_openai_client):
        """Stats count API-bound texts, not cache hits."""
        emb = DashScopeEmbedding(api_key="test_key")
        emb(["once"])
        assert get_embed_stats()["calls"] == 1

        emb(["once"])  # cache hit — still counted in stats
        assert get_embed_stats()["calls"] == 2  # the __call__ was made, even if cache
