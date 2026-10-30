"""目标检查：按顺序填块到 2,500 token；重排不可用时按向量顺序（#209 起；第 3 轮接回重排）。

#209 时重排暂缓、断言「不发重排请求」；第 3 轮（连接复用后 50 块 p50 878 ms）接回重排，
那条断言随之改为：重排不可用 → 退回向量顺序填块、``rerank_fallback=True``。

① 重排不可用（网络不通）→ 按向量顺序填块，结果带 ``rerank_fallback``；
② 按顺序填块：累计 token ≤ 2,500，下一块会超就停，第一块总收；
③ 评测遇 ``CollectionUnusableError``（查询嵌入超时也走这里，见 ``core/rag.py``
   ``_fetch_candidates``）：整套跑完，该题按「注入为空」计分、记 ``retrieval_failed``，
   其余题正常。

token 数用产品的 ``core.tokens.count_tokens`` 现算，不手填。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo / "scripts"))
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

import core.rag as rag_mod  # noqa: E402
import eval_recall  # noqa: E402
from core import recall_eval  # noqa: E402
from core.rag import CollectionUnusableError, EvidenceHits  # noqa: E402
from core.tokens import count_tokens  # noqa: E402


@pytest.fixture(autouse=True)
def _scene_rerank_on(monkeypatch):
    """本文件测重排开着时的行为；生产默认关（SCENE_RERANK 未设），这里显式打开。"""
    monkeypatch.setenv("SCENE_RERANK", "1")

BUDGET = 2500  # 独立写死：目标检查不从被测常量取期望值

_SENTENCE = "“你还记得满月宴那天吗……”他在廊下低声问，\n她没有回头。"


def _doc(i: int, repeat: int) -> str:
    return f"第{i}段：" + _SENTENCE * repeat


class _Collection:
    """照 chroma 行为：按向量顺序返回前 n_results 条。"""

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
    eng._embedding_key = "test-key"  # 有 key：重排会真发请求（被 http_calls 断网）
    eng._embedding_region = "cn"
    return eng


@pytest.fixture
def http_calls(monkeypatch):
    """HTTP 替身：记录每次发出的请求，并照网络不通的生产行为抛 ``TransportError``。

    重试之间的等待替成不睡（``core.reranker`` 的 ``time.sleep``）：网络不通时照生产重试 3 次。
    """
    from core import reranker

    calls: list[str] = []

    def send(self, request, *a, **kw):
        calls.append(str(request.url))
        raise httpx.ConnectError("offline in tests", request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(reranker.time, "sleep", lambda s: None)
    return calls


# ① 重排不可用 → 退回向量顺序
def test_rerank_unavailable_falls_back_to_vector_order(http_calls):
    docs = [_doc(i, 12) for i in range(20)]
    hits = _engine(docs).query_scenes("q", token_budget=BUDGET)
    assert http_calls, "有 key 就该发重排请求"
    assert hits.rerank_fallback is True
    assert [h.text for h in hits] == docs[: len(hits)]


# ② 按向量顺序填块
def test_fills_in_vector_order_up_to_budget(http_calls):
    docs = [_doc(i, 12) for i in range(20)]
    hits = _engine(docs).query_scenes("q", token_budget=BUDGET)
    got = [h.text for h in hits]
    assert got == docs[: len(got)], "必须按向量顺序连续取块，不跳过、不截断"
    total = sum(count_tokens(t) for t in got)
    assert total <= BUDGET
    assert total + count_tokens(docs[len(got)]) > BUDGET, "下一块放得下却停了"
    assert hits.injected_tokens == total
    assert [h.meta["vector_rank"] for h in hits] == list(range(len(got)))


def test_first_chunk_over_budget_is_still_returned(http_calls):
    docs = [_doc(0, 400), _doc(1, 5)]
    assert count_tokens(docs[0]) > BUDGET
    hits = _engine(docs).query_scenes("q", token_budget=BUDGET)
    assert [h.text for h in hits] == [docs[0]]
    assert hits.injected_tokens == count_tokens(docs[0])


# ③ 评测一题检索失败不中止整套
FRAGMENTS = ["甲乙丙丁戊己庚辛壬癸", "子丑寅卯辰巳午未申酉", "金木水火土日月星辰风"]
SOURCE = "。".join(f"开头{f}结尾" for f in FRAGMENTS)
CASES = [{"id": f"q{i}", "phase": None, "query": f"问题{i}", "should": [f]}
         for i, f in enumerate(FRAGMENTS)]


class _FlakyRag:
    """第 2 题抛 ``CollectionUnusableError``（生产里查询嵌入超时就包成它）；其余题命中。"""

    def __init__(self):
        self.calls = 0
        self.spans = {f"c{i}": (SOURCE.index(f), SOURCE.index(f) + len(f))
                      for i, f in enumerate(FRAGMENTS)}

    def chunk_spans(self, _n):
        return self.spans

    def query_scenes(self, query, **kw):
        i = self.calls
        self.calls += 1
        if i == 1:
            raise CollectionUnusableError("向量检索查询失败：embedding deadline")
        # score_case 只读每块的 ``meta["chunk_id"]``；块的其余字段与本条无关。
        return EvidenceHits([SimpleNamespace(text=FRAGMENTS[i], meta={"chunk_id": f"c{i}"})])


def test_eval_survives_one_failed_retrieval():
    rag = _FlakyRag()
    rows = eval_recall.run_eval_no_phase(SOURCE, "甲", CASES, rag)

    assert rag.calls == 3, "整套必须跑完"
    assert [r["id"] for r in rows] == ["q0", "q1", "q2"]
    assert rows[1]["hit"] is False and rows[1]["leak"] is False
    assert rows[1]["retrieval_failed"] is True
    assert [r["hit"] for r in (rows[0], rows[2])] == [True, True]
    assert not rows[0].get("retrieval_failed") and not rows[2].get("retrieval_failed")
    assert recall_eval.aggregate_suite({"书/甲": rows})["retrieval_failed"] == 1
