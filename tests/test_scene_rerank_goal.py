"""目标检查：向量取 RERANK_POOL 候选 → qwen3-rerank 打分 → 前 top_k；出错退回向量顺序。

第一个提交全部 ``xfail(strict=True)``；实现完成后去掉 marker 转绿。
"""

from __future__ import annotations

import inspect
import logging

import pytest

import core.rag as rag_mod
import core.scene_indexer as scene_indexer_mod
from core.embeddings import embed_deadline
from core.schema import SceneMeta

POOL = 50  # 独立写死：目标检查先于实现存在，不能从被测常量取期望值


class _Collection:
    def __init__(self, n: int = 60):
        self.calls = 0
        self._rows = [
            (
                f"scene_{i}",
                f"原文片段 {i}",
                {"characters": "甲,乙", "scene_index": str(i)},
            )
            for i in range(n)
        ]

    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        self.calls += 1
        rows = self._rows[:n_results]
        return {
            "ids": [[r[0] for r in rows]],
            "documents": [[r[1] for r in rows]],
            "distances": [[0.1 * i for i in range(len(rows))]],
            "metadatas": [[r[2] for r in rows]],
        }


def _engine(n: int = 60, *, key="test-key", region="cn", name="scenes_c1"):
    eng = object.__new__(rag_mod.RAGEngine)
    eng._client = None
    eng._embedding_function = None
    eng._collection_name = "rag_fake_default"
    eng.collection = _Collection(n)
    eng.collection_name = name
    eng._chunk_size = 500
    eng._chunk_overlap = 50
    eng._top_k = 3
    eng._embedding_key = key
    eng._embedding_region = region
    return eng


def _install_rerank(monkeypatch, *, scores=None, exc=None, sink=None):
    """重排替身：只回答「候选 → (index, score)」，不碰网络。"""
    from core import reranker

    def fake(query, docs, *, api_key, region):
        if sink is not None:
            sink.append(
                {"query": query, "docs": list(docs), "api_key": api_key, "region": region}
            )
        if exc is not None:
            raise exc
        if scores is None:
            return [(i, 1.0 - i / max(len(docs), 1)) for i in range(len(docs))]
        pairs = [(i, scores.get(i, 0.0)) for i in range(len(docs))]
        return sorted(pairs, key=lambda p: p[1], reverse=True)

    monkeypatch.setattr(reranker, "rerank", fake)
    return fake


# 1. 候选池
def test_pool_caps_candidates_with_and_without_character_filter(monkeypatch):
    sink = []
    _install_rerank(monkeypatch, sink=sink)
    eng = _engine(60)

    eng.query_scenes("q", top_k=3)
    assert len(sink[0]["docs"]) == POOL

    sink.clear()
    eng.query_scenes("q", character_name="甲", top_k=3)
    assert len(sink[0]["docs"]) == POOL


def test_short_pool_is_reported_as_is(monkeypatch):
    sink = []
    _install_rerank(monkeypatch, sink=sink)
    hits = _engine(4).query_scenes("q", top_k=10)
    assert len(sink[0]["docs"]) == 4
    assert len(hits) == 4


# 2. 前 3 = 重排分数最高的 3 条（按 index 映射回候选）
def test_top3_follow_rerank_scores_not_vector_order(monkeypatch):
    sink = []
    _install_rerank(
        monkeypatch, scores={0: 0.1, 1: 0.9, 2: 0.5, 3: 0.99, 4: 0.2}, sink=sink
    )
    hits = _engine(5).query_scenes("q", top_k=3)

    assert [h.text for h in hits] == ["原文片段 3", "原文片段 1", "原文片段 2"]
    assert [h.meta["vector_rank"] for h in hits] == [3, 1, 2]
    assert [h.meta["rerank_score"] for h in hits] == pytest.approx([0.99, 0.9, 0.5])
    assert [h.score for h in hits] == pytest.approx([0.99, 0.9, 0.5])


# 3/4. 重排失败退回向量顺序，WARNING 写明类别与集合名
@pytest.mark.parametrize(
    "kind,status,needle",
    [
        ("timeout", None, "超时"),
        ("rate_limit", 429, "限流"),
        ("http", 500, "其他"),
        ("http", 401, "401"),
    ],
)
def test_rerank_failure_falls_back_to_vector_order(
    monkeypatch, caplog, kind, status, needle
):
    from core.reranker import RerankUnavailable

    exc = RerankUnavailable(kind) if status is None else RerankUnavailable(kind, status)
    _install_rerank(monkeypatch, exc=exc)
    eng = _engine(10)

    with caplog.at_level(logging.WARNING):
        hits = eng.query_scenes("q", top_k=3)

    assert [h.text for h in hits] == ["原文片段 0", "原文片段 1", "原文片段 2"]
    assert hits.rerank_fallback is True
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("scenes_c1" in m and needle in m for m in msgs), msgs


# 4b. deadline：预算不够不发；够发时 timeout 不超过剩余预算
def test_deadline_without_room_sends_nothing(monkeypatch, caplog):
    import core.reranker as rr

    now = [100.0]
    monkeypatch.setattr(rr.time, "monotonic", lambda: now[0])
    sent = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            sent.append(k)
            raise AssertionError("预算不够还发了重排请求")

    monkeypatch.setattr(rr.httpx, "Client", _Client)

    with embed_deadline(100.05), caplog.at_level(logging.WARNING):
        hits = _engine(10).query_scenes("q", top_k=3)

    assert sent == []
    assert hits.rerank_fallback is True
    assert [h.text for h in hits] == ["原文片段 0", "原文片段 1", "原文片段 2"]
    assert any("deadline" in r.getMessage().lower() for r in caplog.records)


def test_deadline_caps_request_timeout(monkeypatch):
    import core.reranker as rr

    now = [100.0]
    monkeypatch.setattr(rr.time, "monotonic", lambda: now[0])
    seen = []

    class _Resp:
        status_code = 200
        headers: dict = {}

        def json(self):
            return {
                "output": {"results": [{"index": 0, "relevance_score": 0.5}]},
                "usage": {"total_tokens": 1},
            }

    class _Client:
        def __init__(self, timeout=None, **kw):
            seen.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            now[0] += 1.0
            return _Resp()

    monkeypatch.setattr(rr.httpx, "Client", _Client)

    with embed_deadline(110.0):
        hits = _engine(3).query_scenes("q", top_k=1)

    assert seen and max(seen) <= 10.0 - 0.1
    assert hits.rerank_fallback is False
    assert [h.text for h in hits] == ["原文片段 0"]


# 5. key / region 只来自构造它的引擎，重排函数不接收用户身份
def test_key_and_region_come_from_the_engine(monkeypatch):
    sink = []
    _install_rerank(monkeypatch, sink=sink)
    _engine(5, key="user-key", region="intl").query_scenes("q", top_k=1)

    assert sink[0]["api_key"] == "user-key"
    assert sink[0]["region"] == "intl"

    from core import reranker

    params = set(inspect.signature(reranker.rerank).parameters)
    assert params == {"query", "docs", "api_key", "region"}


# 6. 情绪排序整体删除
def test_emotion_ranking_is_gone():
    import core.scene_retrieval as sr

    assert not hasattr(scene_indexer_mod, "_detect_emotion")
    assert not hasattr(scene_indexer_mod, "_EMOTION_KEYWORDS")
    assert not hasattr(rag_mod.RAGEngine, "_rank_with_emotion")
    assert not hasattr(rag_mod.RAGEngine, "query_with_emotion_ex")
    assert "emotion" not in inspect.getsource(sr).lower()


# 7. SceneMeta 契约改为 rerank_score / vector_rank
def test_scene_meta_exposes_rerank_fields(monkeypatch):
    _install_rerank(monkeypatch, scores={0: 0.2, 1: 0.9})
    hits = _engine(2).query_scenes("q", top_k=2)

    assert set(SceneMeta.__annotations__) == {
        "chapter", "chunk_id", "rerank_score", "vector_rank",
    }
    for h in hits:
        assert set(h.meta) == set(SceneMeta.__annotations__)
    assert hits[0].meta["rerank_score"] == pytest.approx(0.9)
    assert hits[0].score == pytest.approx(0.9)


def test_fallback_keeps_vector_order_and_no_rerank_score(monkeypatch):
    from core.reranker import RerankUnavailable

    _install_rerank(monkeypatch, exc=RerankUnavailable("timeout"))
    hits = _engine(3).query_scenes("q", top_k=3)

    assert [h.meta["vector_rank"] for h in hits] == [0, 1, 2]
    assert all(h.meta["rerank_score"] is None for h in hits)
    assert all(h.score is None for h in hits)
