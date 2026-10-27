"""目标检查：#209 改为「删情绪排序 + 按预算填块」，重排暂缓。

依据：SG 实测（2026-10-27）「查询嵌入 + 重排」p95 在候选 20 / 30 / 50、A / B 两种块长下
没有一格 ≤ 聊天场景工具预算 4 s（最好 A-20：p95 6.4 s）；原样上线每次检索都会把 4 s
耗满再退回向量顺序。故本 PR 不发重排请求，按向量顺序填块。

① ``query_scenes`` 不发任何重排请求（替身记 HTTP 调用次数 = 0）；
② 按向量顺序填块：累计 token ≤ 2,500，下一块会超就停，第一块总收；
③ 评测遇 ``CollectionUnusableError``（查询嵌入超时也走这里，见 ``core/rag.py``
   ``_fetch_candidates``）：整套跑完，该题按「注入为空」计分、记 ``retrieval_failed``，
   其余题正常 —— 每题 4 s 时限是本 PR 加的，「一题失败整套中止」也就是本 PR 引入的。

token 数用产品的 ``count_tokens`` 现算，不手填。
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
    eng._embedding_key = "test-key"  # 有 key：若代码还想重排，必然发请求
    eng._embedding_region = "cn"
    return eng


@pytest.fixture
def http_calls(monkeypatch):
    """HTTP 替身：记录每次发出的请求，并照网络不通的生产行为抛 ``TransportError``。"""
    calls: list[str] = []

    def send(self, request, *a, **kw):
        calls.append(str(request.url))
        raise httpx.ConnectError("offline in tests", request=request)

    async def asend(self, request, *a, **kw):
        calls.append(str(request.url))
        raise httpx.ConnectError("offline in tests", request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "send", asend)
    return calls


# ① 不发任何重排请求
def test_query_scenes_sends_no_rerank_request(http_calls):
    hits = _engine([_doc(i, 12) for i in range(20)]).query_scenes("q", token_budget=BUDGET)
    assert len(hits) >= 1
    assert http_calls == []


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
