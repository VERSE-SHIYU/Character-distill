"""评测与聊天同一时限：``eval_recall`` 每条查询在 ``SCENE_TOOL_BUDGET_S`` 内检索。

聊天里场景检索由 ``AgentToolkit.execute`` 开 ``embed_deadline(now + SCENE_TOOL_BUDGET_S)``，
查询嵌入与重排共用这段预算。评测若不开同一个 deadline，重排可按非工具路径一直等，
``rerank_fallbacks = 0`` 就说明不了聊天里来不来得及。deadline 每条查询现算，不跨查询累计。

重排用例的替身只换网络：真 ``reranker.rerank`` + 真 ``RAGEngine.query_scenes``；httpx 客户端
替身照生产行为 —— 长期客户端、超时按请求传，请求耗时超过 ``timeout`` 就抛
``httpx.TimeoutException``；时钟是假的，不真睡。
"""
from __future__ import annotations

import sys
from pathlib import Path

import time

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import core.rag as rag_mod  # noqa: E402
import core.reranker as rr  # noqa: E402
import eval_recall  # noqa: E402
from core.agent.tools import SCENE_TOOL_BUDGET_S  # noqa: E402
from core.embeddings import current_embed_deadline  # noqa: E402


@pytest.fixture(autouse=True)
def _scene_rerank_on(monkeypatch):
    """本文件测重排开着时的行为；生产默认关（SCENE_RERANK 未设），这里显式打开。"""
    monkeypatch.setenv("SCENE_RERANK", "1")

FRAGMENT = "丙丁戊己庚辛壬癸子丑"  # 10 字，全书唯一
SOURCE = "乙" * 50 + FRAGMENT + "乙" * 50
CASES = [{"id": "q01", "phase": None, "query": "你当时在场吗？", "should": [FRAGMENT]}]


class _Collection:
    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        n = 3
        return {
            "ids": [[f"scene_{i}" for i in range(n)]],
            "documents": [[f"原文片段 {i}" for i in range(n)]],
            "distances": [[0.1 * i for i in range(n)]],
            "metadatas": [[{"characters": "甲", "scene_index": str(i)} for i in range(n)]],
        }


def _engine():
    eng = object.__new__(rag_mod.RAGEngine)
    eng._client = None
    eng._embedding_function = None
    eng._collection_name = "rag_fake_default"
    eng.collection = _Collection()
    eng.collection_name = "scenes_eval"
    eng._chunk_size = 500
    eng._chunk_overlap = 50
    eng._top_k = 3
    eng._embedding_key = "test-key"
    eng._embedding_region = "cn"
    eng.chunk_spans = lambda _n: {}
    return eng


def _install_network(monkeypatch, cost_s: float):
    """假时钟 + 照生产行为的 httpx 替身：耗时 ``cost_s``，超过 timeout 即超时。"""
    now = [1000.0]
    monkeypatch.setattr(rr.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(rr.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))

    class _Resp:
        status_code = 200
        headers: dict = {}

        def json(self):
            return {"output": {"results": [{"index": i, "relevance_score": 1.0 - i / 10}
                                           for i in range(3)]},
                    "usage": {"total_tokens": 123}}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def post(self, *a, timeout=None, **k):
            if timeout is not None and cost_s > timeout:
                now[0] += timeout
                raise httpx.TimeoutException("read timeout")
            now[0] += cost_s
            return _Resp()

    monkeypatch.setattr(rr.httpx, "Client", _Client)


@pytest.mark.parametrize(
    ("cost_s", "fallback"),
    [(SCENE_TOOL_BUDGET_S + 1.0, True), (SCENE_TOOL_BUDGET_S / 4, False)],
    ids=["slower_than_budget", "within_budget"],
)
def test_rerank_fallback_follows_chat_budget(monkeypatch, cost_s, fallback):
    _install_network(monkeypatch, cost_s)
    rows = eval_recall.run_eval_no_phase(SOURCE, "甲", CASES, _engine())
    assert [r["rerank_fallback"] for r in rows] == [fallback]


def _deadline_recorder(monkeypatch):
    seen: list[float | None] = []

    def recorder(rag, query, *, character_name, window):
        seen.append(current_embed_deadline() - time.monotonic()
                    if current_embed_deadline() is not None else None)
        return rag_mod.EvidenceHits([])

    monkeypatch.setattr(eval_recall.scene_retrieval, "scene_hits", recorder)
    return seen


def test_no_phase_path_opens_budget_per_query(monkeypatch):
    seen = _deadline_recorder(monkeypatch)
    eval_recall.run_eval_no_phase(SOURCE, "甲", CASES * 2, _engine())
    assert len(seen) == 2
    assert all(s is not None and 0 < s <= SCENE_TOOL_BUDGET_S for s in seen)


def test_card_path_opens_budget_per_query(monkeypatch):
    from core.arc_view import arc_view
    from core.schema import ArcPhase, CharacterArc, CharacterCard

    card = CharacterCard(name="甲", character_arc=CharacterArc(
        phases=[ArcPhase(start=0)], source_fingerprint="fp-test"))
    assert arc_view(card, 0) is not None
    seen = _deadline_recorder(monkeypatch)
    cases = [dict(CASES[0], phase=0)] * 2
    eval_recall.run_eval(SOURCE, card, cases, _engine())
    assert len(seen) == 2
    assert all(s is not None and 0 < s <= SCENE_TOOL_BUDGET_S for s in seen)
