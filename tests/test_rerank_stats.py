"""重排耗时与 token 带出来（spec S4：延迟中位 / p95 要量得出）。

- ``rerank()`` 返回值不变（仍是按相关度降序的 ``(下标, 分)``），成功时记一条 INFO：
  ``n_docs`` / ``elapsed_ms`` / ``attempts`` / ``total_tokens``（响应没有 usage 记 None）。
- ``EvidenceHits`` 带 ``rerank_ms`` / ``rerank_tokens``：成功填实数；退回向量顺序时
  ``rerank_ms`` 是已花的时间、``rerank_tokens`` 为 None（不编数）；没调重排两者都 None。
- 评测行带这两个字段，``aggregate_suite`` 顶层给 p50 / p95 / token 合计。

httpx 替身照生产：耗时超过 timeout 抛 ``httpx.TimeoutException``；时钟是假的。
"""
from __future__ import annotations

import logging

import httpx
import pytest

import core.rag as rag_mod
import core.reranker as rr
from core import recall_eval
from core.scene_retrieval import SCENE_TOKEN_BUDGET  # noqa: E402
from core.embeddings import embed_deadline
from core.rag import EvidenceHits


@pytest.fixture(autouse=True)
def _scene_rerank_on(monkeypatch):
    """本文件测重排开着时的行为；生产默认关（SCENE_RERANK 未设），这里显式打开。"""
    monkeypatch.setenv("SCENE_RERANK", "1")


class _Collection:
    def __init__(self, n: int = 3):
        self.n = n

    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        n = min(self.n, n_results)
        return {
            "ids": [[f"scene_{i}" for i in range(n)]],
            "documents": [[f"原文片段 {i}" for i in range(n)]],
            "distances": [[0.1 * i for i in range(n)]],
            "metadatas": [[{"characters": "甲", "scene_index": str(i)} for i in range(n)]],
        }


def _engine(n: int = 3):
    eng = object.__new__(rag_mod.RAGEngine)
    eng._client = None
    eng._embedding_function = None
    eng._collection_name = "rag_fake_default"
    eng.collection = _Collection(n)
    eng.collection_name = "scenes_c1"
    eng._chunk_size = 500
    eng._chunk_overlap = 50
    eng._top_k = 3
    eng._embedding_key = "test-key"
    eng._embedding_region = "cn"
    return eng


def _install(monkeypatch, *, cost_s: float, usage: dict | None = None, statuses=(200,)):
    """假时钟 + httpx 替身：每次请求耗 ``cost_s``；按 ``statuses`` 依次回状态码。"""
    now = [1000.0]
    monkeypatch.setattr(rr.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(rr.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    queue = list(statuses)

    class _Resp:
        headers: dict = {}

        def __init__(self, status):
            self.status_code = status

        def json(self):
            body = {"output": {"results": [{"index": i, "relevance_score": 1.0 - i / 10}
                                           for i in range(3)]}}
            if usage is not None:
                body["usage"] = usage
            return body

    class _Client:
        """照生产：长期客户端，超时按请求传（``post(timeout=)``）。"""

        def __init__(self, *a, **kw):
            pass

        def post(self, *a, timeout=None, **k):
            if timeout is not None and cost_s > timeout:
                now[0] += timeout
                raise httpx.TimeoutException("read timeout")
            now[0] += cost_s
            return _Resp(queue.pop(0) if len(queue) > 1 else queue[0])

    monkeypatch.setattr(rr.httpx, "Client", _Client)
    return now


# ── rerank()：返回值不变 + INFO 日志 ─────────────────────────────


def test_rerank_return_value_unchanged_and_logs_stats(monkeypatch, caplog):
    _install(monkeypatch, cost_s=0.25, usage={"total_tokens": 321}, statuses=(503, 200))
    with caplog.at_level(logging.INFO, logger="core.reranker"):
        got = rr.rerank("q", ["a", "b", "c"], api_key="k", region="cn")

    assert got == [(0, 1.0), (1, 0.9), (2, 0.8)]
    msgs = [r.getMessage() for r in caplog.records
            if r.name == "core.reranker" and r.levelno == logging.INFO]
    assert len(msgs) == 1, msgs
    msg = msgs[0]
    for needle in ("n_docs=3", "elapsed_ms=800", "attempts=2", "total_tokens=321"):
        assert needle in msg, msg


def test_rerank_without_usage_logs_none(monkeypatch, caplog):
    _install(monkeypatch, cost_s=0.1, usage=None)
    with caplog.at_level(logging.INFO, logger="core.reranker"):
        rr.rerank("q", ["a", "b", "c"], api_key="k", region="cn")
    assert any("total_tokens=None" in r.getMessage() for r in caplog.records)


# ── EvidenceHits 带耗时 / token ───────────────────────────────


def test_evidence_hits_stats_default_none():
    hits = EvidenceHits([])
    assert hits.rerank_ms is None and hits.rerank_tokens is None


def test_query_scenes_fills_stats_on_success(monkeypatch):
    _install(monkeypatch, cost_s=0.6, usage={"total_tokens": 28000})
    hits = _engine().query_scenes("q", token_budget=SCENE_TOKEN_BUDGET)
    assert hits.rerank_fallback is False
    assert hits.rerank_ms == 600
    assert hits.rerank_tokens == 28000


def test_query_scenes_fallback_keeps_spent_time_and_no_tokens(monkeypatch):
    _install(monkeypatch, cost_s=10.0, usage={"total_tokens": 1})
    with embed_deadline(1000.0 + 4.0):
        hits = _engine().query_scenes("q", token_budget=SCENE_TOKEN_BUDGET)
    assert hits.rerank_fallback is True
    assert hits.rerank_ms == 3900  # 唯一一次尝试 timeout = 4.0 − margin 0.1
    assert hits.rerank_tokens is None


def test_query_scenes_without_candidates_has_no_stats(monkeypatch):
    _install(monkeypatch, cost_s=0.1, usage={"total_tokens": 1})
    hits = _engine(0).query_scenes("q", token_budget=SCENE_TOKEN_BUDGET)
    assert hits.rerank_ms is None and hits.rerank_tokens is None


# ── 评测：行字段 + 汇总 ───────────────────────────────────────

CASE = {"id": "q", "should": [], "should_not": []}


def test_score_case_carries_rerank_stats():
    hits = EvidenceHits([], rerank_ms=812, rerank_tokens=27500)
    row = recall_eval.score_case("", CASE, hits, {})
    assert row["rerank_ms"] == 812 and row["rerank_tokens"] == 27500


def test_score_case_plain_iterable_has_no_stats():
    row = recall_eval.score_case("", CASE, [], {})
    assert row["rerank_ms"] is None and row["rerank_tokens"] is None


def _row(ms, tokens):
    return {"hit": False, "leak": False, "rerank_fallback": False,
            "rerank_ms": ms, "rerank_tokens": tokens}


def test_aggregate_suite_latency_and_tokens():
    rows = {
        "书/甲": [_row(ms, 100) for ms in range(100, 1100, 100)],  # 100..1000
        "书/乙": [_row(None, None), _row(2000, None)],
    }
    out = recall_eval.aggregate_suite(rows)
    assert out["rerank_ms_p50"] == 600
    assert out["rerank_ms_p95"] == pytest.approx(1500)
    assert out["rerank_tokens_total"] == 1000


def test_aggregate_suite_without_stats_is_none():
    out = recall_eval.aggregate_suite({"书/甲": [_row(None, None)]})
    assert out["rerank_ms_p50"] is None
    assert out["rerank_ms_p95"] is None
    assert out["rerank_tokens_total"] is None
