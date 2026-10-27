"""目标检查：场景注入按固定 token 预算填块（fill-to-budget），不再按条数截断。

依据 2601.14123 §2 / §3 F4：固定注入总 token 才能在不同块大小间公平比较。
测的是「达成目标」：

① 同一组重排结果，累计 token ≤ 预算，下一块会超就停（不跳过、不截断块）；
② 块小的切法返回的块数多于块大的切法，两者 ``injected_tokens`` 都 ≤ 预算；
③ 第一块单独就超预算时只返回这一块（结果不为空）；
④ 重排失败退回向量顺序时，同样按预算填；
⑤ 评测判命中用的正是 ``scene_hits`` 返回的那组块（且 ``scene_hits`` 按预算取，不按条数）。

夹具带省略号、中文引号、换行 —— token 数用产品的 ``count_tokens`` 现算，不手填。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

import core.rag as rag_mod  # noqa: E402
from core import reranker  # noqa: E402
from core.rag import EvidenceHits  # noqa: E402
from core.schema import scene_evidence  # noqa: E402
from core.tokens import count_tokens  # noqa: E402

BUDGET = 2500  # 独立写死：目标检查先于实现存在，不从被测常量取期望值

pytestmark = pytest.mark.xfail(strict=True, reason="目标检查先红：fill-to-budget 尚未实现")

_SENTENCE = "“你还记得满月宴那天吗……”他在廊下低声问，\n她没有回头。"


def _doc(i: int, repeat: int) -> str:
    return f"第{i}段：" + _SENTENCE * repeat


class _Collection:
    def __init__(self, docs: list[str]):
        self._rows = [(f"scene_{i}", d, {"characters": "甲,乙"}) for i, d in enumerate(docs)]

    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        rows = self._rows[:n_results]
        return {
            "ids": [[r[0] for r in rows]],
            "documents": [[r[1] for r in rows]],
            "distances": [[0.1 * i for i in range(len(rows))]],
            "metadatas": [[r[2] for r in rows]],
        }


def _engine(docs: list[str]):
    eng = object.__new__(rag_mod.RAGEngine)
    eng._client = None
    eng._embedding_function = None
    eng._collection_name = "rag_fake_default"
    eng.collection = _Collection(docs)
    eng.collection_name = "scenes_c1"
    eng._chunk_size = 500
    eng._chunk_overlap = 50
    eng._top_k = 3
    eng._embedding_key = "test-key"
    eng._embedding_region = "cn"
    return eng


def _reverse_rerank(monkeypatch):
    """重排替身：把向量顺序倒过来，确保填块跟的是重排顺序而不是向量顺序。"""
    def fake(query, docs, *, api_key, region):
        n = len(docs)
        return [(i, float(i) / n) for i in reversed(range(n))]
    monkeypatch.setattr(reranker, "rerank", fake)


def _failing_rerank(monkeypatch):
    def fake(query, docs, *, api_key, region):
        raise reranker.RerankUnavailable("timeout")
    monkeypatch.setattr(reranker, "rerank", fake)


def _expected_fill(texts_in_order: list[str]) -> list[str]:
    out, used = [], 0
    for t in texts_in_order:
        n = count_tokens(t)
        if out and used + n > BUDGET:
            break
        out.append(t)
        used += n
        if used > BUDGET:
            break
    return out


def _texts(hits) -> list[str]:
    return [h.text for h in hits]


# ① 累计 ≤ 预算，下一块会超就停
def test_fills_reranked_chunks_up_to_budget_and_stops_before_overflow(monkeypatch):
    _reverse_rerank(monkeypatch)
    docs = [_doc(i, 12) for i in range(20)]
    hits = _engine(docs).query_scenes("q", token_budget=BUDGET)

    reranked = list(reversed(docs))
    got = _texts(hits)
    assert got == reranked[: len(got)], "必须按重排顺序连续取块，不跳过"
    total = sum(count_tokens(t) for t in got)
    assert total <= BUDGET
    assert total + count_tokens(reranked[len(got)]) > BUDGET, "下一块放得下却停了"
    assert hits.injected_tokens == total
    assert got == _expected_fill(reranked)
    assert all(t in docs for t in got), "块不许被截断"


# ② 块小的切法返回更多块，两者都 ≤ 预算
def test_smaller_chunks_return_more_blocks_both_within_budget(monkeypatch):
    _reverse_rerank(monkeypatch)
    small = _engine([_doc(i, 6) for i in range(40)]).query_scenes("q", token_budget=BUDGET)
    large = _engine([_doc(i, 30) for i in range(40)]).query_scenes("q", token_budget=BUDGET)
    assert len(small) > len(large) >= 1
    assert small.injected_tokens <= BUDGET
    assert large.injected_tokens <= BUDGET
    assert small.injected_tokens == sum(count_tokens(t) for t in _texts(small))
    assert large.injected_tokens == sum(count_tokens(t) for t in _texts(large))


# ③ 第一块单独超预算：只返回这一块
def test_first_chunk_over_budget_returns_only_that_chunk(monkeypatch):
    _reverse_rerank(monkeypatch)
    docs = [_doc(0, 5), _doc(1, 5), _doc(2, 400)]  # 重排后第一块是 _doc(2, 400)
    assert count_tokens(docs[2]) > BUDGET
    hits = _engine(docs).query_scenes("q", token_budget=BUDGET)
    assert _texts(hits) == [docs[2]]
    assert hits.injected_tokens == count_tokens(docs[2])


# ④ 重排失败退回向量顺序，同样按预算填
def test_rerank_fallback_fills_budget_in_vector_order(monkeypatch):
    _failing_rerank(monkeypatch)
    docs = [_doc(i, 12) for i in range(20)]
    hits = _engine(docs).query_scenes("q", token_budget=BUDGET)
    assert hits.rerank_fallback is True
    got = _texts(hits)
    assert got == _expected_fill(docs)
    assert len(got) > 3, "预算填块不该再被旧的条数上限卡住"
    assert hits.injected_tokens == sum(count_tokens(t) for t in got) <= BUDGET


# ⑤ 评测判命中用的就是 scene_hits 返回的那组块；scene_hits 按预算取
def test_scene_hits_passes_token_budget_not_top_k():
    import core.scene_retrieval as sr

    class _FakeRag:
        seen: dict = {}

        def query_scenes(self, query, **kwargs):
            _FakeRag.seen = kwargs
            return EvidenceHits([])

    sr.scene_hits(_FakeRag(), "q", character_name="甲", window=None)
    assert _FakeRag.seen.get("token_budget") == BUDGET
    assert "top_k" not in _FakeRag.seen
    assert sr.SCENE_TOKEN_BUDGET == BUDGET


def test_eval_scores_exactly_the_blocks_scene_hits_returned(monkeypatch):
    import core.scene_retrieval as sr
    import scripts.eval_recall as ev

    source = "满月宴那天祈灼在廊下听见了那支曲子后来他独自去了后山没有告诉任何人"
    items = [
        scene_evidence(text=t, rerank_score=None, vector_rank=i, chunk_id=f"c{i}", chapter=None)
        for i, t in enumerate(["满月宴那天", "后来他独自", "没有告诉"])
    ]
    returned = EvidenceHits(items, injected_tokens=37)
    monkeypatch.setattr(sr, "scene_hits", lambda *a, **k: returned)

    class _Rag:
        def chunk_spans(self, n):
            return {"c0": (0, 17), "c1": (17, 33), "c2": (33, n)}

    cases = [{"id": "q1", "phase": None, "query": "你还记得吗",
              "should": ["满月宴那天祈灼在廊下听见"], "should_not": []}]
    rows = ev.run_eval_no_phase(source, "祈灼", cases, _Rag())
    assert rows[0]["top3_chunk_ids"] == ["c0", "c1", "c2"]
    assert rows[0]["hit"] is True
    assert rows[0]["injected_tokens"] == 37
