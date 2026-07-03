"""Tests for shared embedding cache, Mem0 bridge, and embed-stats."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.embeddings import (
    DashScopeEmbedding,
    Mem0BridgeEmbedder,
    get_embed_stats,
    reset_embed_stats,
    _is_moderation_error,
    _SHARED_CACHE,
    _SHARED_CACHE_LOCK,
)


@pytest.fixture(autouse=True)
def clear_stats():
    """Reset module-level counters and shared cache before each test."""
    reset_embed_stats()
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE.clear()


class _FakeEmbeddingData:
    def __init__(self, index: int, embedding: list[float]):
        self.index = index
        self.embedding = embedding


class _FakeEmbeddingResponse:
    def __init__(self, embeddings: list[list[float]]):
        self.data = [
            _FakeEmbeddingData(i, emb) for i, emb in enumerate(embeddings)
        ]


@pytest.fixture
def mock_openai_client():
    """Patch openai.OpenAI with a mock that returns fake embeddings."""
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


# ── IsModerationError ────────────────────────────────────────


class TestIsModerationError:
    def test_detects_DataInspectionFailed(self):
        assert _is_moderation_error("DataInspectionFailed: content risky")

    def test_detects_contentFilter(self):
        assert _is_moderation_error("contentFilter triggered")

    def test_detects_content_filter(self):
        assert _is_moderation_error("content_filter violation")

    def test_detects_blocked(self):
        assert _is_moderation_error("request blocked by risk control")

    def test_rejects_normal_error(self):
        assert not _is_moderation_error("rate limit exceeded")

    def test_rejects_empty(self):
        assert not _is_moderation_error("")


# ── SharedCache ──────────────────────────────────────────────


class TestSharedCache:
    """Shared module-level cache: same text embedded only once."""

    def test_cache_hit_no_second_api_call(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        text = "今天天气真好"

        vec1 = emb([text])
        assert len(vec1) == 1
        assert len(vec1[0]) == 1024
        assert mock_openai_client.embeddings.create.call_count == 1

        vec2 = emb([text])
        assert len(vec2) == 1
        assert len(vec2[0]) == 1024
        assert mock_openai_client.embeddings.create.call_count == 1  # no increase

    def test_different_text_triggers_api(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")

        emb(["hello"])
        assert mock_openai_client.embeddings.create.call_count == 1

        emb(["world"])
        assert mock_openai_client.embeddings.create.call_count == 2

    def test_cache_shared_across_instances(self, mock_openai_client):
        emb1 = DashScopeEmbedding(api_key="key_a")
        emb2 = DashScopeEmbedding(api_key="key_b")

        emb1(["共享文本"])
        assert mock_openai_client.embeddings.create.call_count == 1

        emb2(["共享文本"])
        assert mock_openai_client.embeddings.create.call_count == 1  # cache hit

    def test_cache_and_mem0_bridge_share_cache(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        bridge = Mem0BridgeEmbedder(api_key="test_key")

        emb(["你好"])
        assert mock_openai_client.embeddings.create.call_count == 1

        bridge.embed("你好")
        assert mock_openai_client.embeddings.create.call_count == 1  # cache hit

    def test_empty_input_raises_chroma_error(self, mock_openai_client):
        """ChromaDB's EmbeddingFunction wrapper rejects empty returns."""
        emb = DashScopeEmbedding(api_key="test_key")
        with pytest.raises(ValueError):
            emb([""])
        assert mock_openai_client.embeddings.create.call_count == 0


# ── Mem0BridgeEmbedder ───────────────────────────────────────


class TestMem0BridgeEmbedder:
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
        bridge = Mem0BridgeEmbedder(api_key="test_key")
        assert bridge._dashscope.source == "mem0"


# ── EmbedStats ───────────────────────────────────────────────


class TestEmbedStats:
    """[embed-stats] counters accumulate correctly."""

    def test_basic_counting(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        assert get_embed_stats()["total"] == 0

        emb(["text_a"])
        stats = get_embed_stats()
        assert stats["total"] == 1
        assert stats["api_calls"] == 1
        assert stats["cache_hits"] == 0
        assert stats["by_source"].get("rag") == 1

    def test_cache_hit_counts_separately(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        emb(["dup"])
        assert get_embed_stats()["api_calls"] == 1
        assert get_embed_stats()["cache_hits"] == 0

        emb(["dup"])  # cache hit
        stats = get_embed_stats()
        assert stats["api_calls"] == 1  # unchanged
        assert stats["cache_hits"] == 1  # incremented
        assert stats["total"] == 2

    def test_hit_rate(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        emb(["a"])
        emb(["a"])  # cache hit
        emb(["b"])
        emb(["b"])  # cache hit
        emb(["c"])

        stats = get_embed_stats()
        assert stats["total"] == 5
        assert stats["api_calls"] == 3
        assert stats["cache_hits"] == 2
        assert stats["hit_rate"] == 40.0  # 2/5 = 40%

    def test_rag_and_mem0_separate_buckets(self, mock_openai_client):
        emb = DashScopeEmbedding(api_key="test_key")
        bridge = Mem0BridgeEmbedder(api_key="test_key")

        emb(["rag_text"])
        bridge.embed("mem0_text")

        stats = get_embed_stats()
        assert stats["by_source"]["rag"] == 1
        assert stats["by_source"]["mem0"] == 1
        assert stats["api_calls"] == 2

    def test_moderation_blocked_initial_zero(self):
        assert get_embed_stats()["moderation_blocked"] == 0


# ── Moderation Interception ──────────────────────────────────


class TestModerationInterception:
    """DashScope content moderation: blocked chunk skipped, rest continue."""

    def test_moderation_blocked_logged_and_counted(self, mock_openai_client):
        """Single text triggers moderation → counted and zero-vector returned."""
        fake_vec = [0.1] * 1024

        def _side_effect(*, model, input, dimensions, **_kwargs):
            texts = input if isinstance(input, list) else [input]
            if any("blocked" in t for t in texts):
                raise Exception("DataInspectionFailed: content risk detected")
            return _FakeEmbeddingResponse([fake_vec] * len(texts))

        mock_openai_client.embeddings.create.side_effect = _side_effect

        emb = DashScopeEmbedding(api_key="test_key")
        result = emb(["blocked_text"])

        assert get_embed_stats()["moderation_blocked"] == 1
        # batch(1) fails → per-item(1) fails → blocked
        assert mock_openai_client.embeddings.create.call_count == 2

    def test_moderation_skips_only_blocked_item(self, mock_openai_client):
        """Batch with moderation: blocked item gets zero vector, rest succeed."""
        fake_vec = [0.1] * 1024

        def _side_effect(*, model, input, dimensions, **_kwargs):
            texts = input if isinstance(input, list) else [input]
            if any("bad" in t for t in texts):
                raise Exception("DataInspectionFailed: content risk")
            return _FakeEmbeddingResponse([fake_vec] * len(texts))

        mock_openai_client.embeddings.create.side_effect = _side_effect

        emb = DashScopeEmbedding(api_key="test_key")
        texts = ["safe1", "bad", "safe2"]
        result = emb(texts)

        assert len(result) == 3
        # "bad" gets zero vector (result is numpy array from ChromaDB wrapper)
        assert all(v == 0.0 for v in result[1])
        # safe texts get normal vectors
        assert result[0][0] == 0.1
        assert result[2][0] == 0.1

        assert get_embed_stats()["moderation_blocked"] == 1
        # batch(3 fails) → per-item: safe1 ✓, bad ✗, safe2 ✓ = 1+3 = 4
        assert mock_openai_client.embeddings.create.call_count == 4
