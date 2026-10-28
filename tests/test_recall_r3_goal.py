"""目标检查：召回第 3 轮 —— 多轮指代改写 + 改口 + 重排接回（连接复用）+ 评测走路由。

依据（Shiyu 10-28 定）：
- SemEval-2026 Task 8 第 3 名（2606.28352）表 2：LLM 结合历史改写成独立问题 nDCG@5
  0.3457 → 0.4144；``最后一句 | 改写`` 拼接 0.4150；2610.15034：只替换指代 / 省略、不加内容；
  MemRevise（2610.24672）图 3：改口轮套到纠正后的对象、不提被否定的对象。
- 重排慢的根因是每次新建 ``httpx.Client``（建连 p50 684 ms）；复用连接池后 50 块 p50 878 ms。

① 场景工具收到用户原句与路由 query：不同 → 检索词 = ``原句 | query``；相同或 query 空 → 原句。
② 场景工具的 query 说明与路由规则写明「独立问题、只替换指代与省略、不添加内容」与改口规则。
③ 连续两次重排只建一次连接；不同 api_key 共用同一地域的客户端，请求头各自正确。
④ 重排在 deadline 不够时退回向量顺序（``rerank_fallback=True``），不抛到聊天。
⑤ 评测 ``--via-router``：history + query 交给真实 ``AgentLoop`` + ``AgentToolkit``（替身 LLM），
   路由调 search_scenes 时用它的 query 检索；不调 → 该题未命中、``router_called=False``。

期望值独立写死，不从被测常量取。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo / "scripts"))
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

import core.rag as rag_mod  # noqa: E402
from core.agent import agent_loop, tools  # noqa: E402
from core.context_engine import RetrievalResult  # noqa: E402
from core.embeddings import embed_deadline  # noqa: E402
from core.rag import EvidenceHits  # noqa: E402

GOAL = pytest.mark.xfail(strict=True, reason="目标检查先红：第 3 轮未实现")

USER = "他那次为什么没来？"
ROUTER_Q = "祈灼满月宴那次为什么没来？"


# ── ① 检索词 = 原句 | 路由改写 ─────────────────────────────────────────


class _Ctx:
    """ContextEngine 替身：只记场景检索收到的查询；照生产，空查询也照常返回空态。"""

    web_search_enabled = False

    def __init__(self):
        self.scene_queries: list[str] = []

    def _retrieve_scenes_ex(self, query):
        self.scene_queries.append(query)
        return RetrievalResult(source="scene", block="", items=[], status="empty")

    def _retrieve_memories_ex(self, query, current_mood=None):
        return RetrievalResult(source="memory", block="", items=[], status="empty")


@pytest.mark.parametrize(
    ("router_query", "expected"),
    [
        (ROUTER_Q, f"{USER} | {ROUTER_Q}"),
        (USER, USER),
        ("", USER),
    ],
)
def test_scene_query_joins_user_sentence_and_rewrite(router_query, expected):
    ctx = _Ctx()
    toolkit = tools.AgentToolkit(ctx, user_message=USER)
    toolkit._call_search_scenes(router_query)
    assert ctx.scene_queries == [expected]


def test_scene_query_without_user_sentence_uses_router_query():
    """MCP / 旧评测脚本不传原句：只用路由 query（与改前一致）。"""
    ctx = _Ctx()
    tools.AgentToolkit(ctx).execute("search_scenes", {"query": ROUTER_Q})
    assert ctx.scene_queries == [ROUTER_Q]


# ── ② 改写与改口规则写进路由与工具说明 ─────────────────────────────────


def _scene_query_description() -> str:
    schema = next(s for s in tools.ALL_SCHEMAS if s["function"]["name"] == "search_scenes")
    return schema["function"]["parameters"]["properties"]["query"]["description"]


@pytest.mark.parametrize("text_of", [_scene_query_description,
                                     lambda: agent_loop.ROUTER_SYSTEM_PROMPT])
def test_rewrite_rule_is_stated(text_of):
    text = text_of()
    for phrase in ("独立问题", "代词", "省略", "不添加", "改口", "纠正后的对象", "被否定的对象"):
        assert phrase in text, f"缺「{phrase}」"


def test_scene_query_is_no_longer_keywords():
    assert "搜索关键词" not in _scene_query_description()


def test_router_routes_correction_to_scenes():
    prompt = agent_loop.ROUTER_SYSTEM_PROMPT
    i = prompt.index("改口")
    assert "search_scenes" in prompt[i:i + 40], "改口 / 纠正上一问 → search_scenes"


# ── ③ 重排复用连接 ─────────────────────────────────────────────────────


class _ClientDouble:
    """``httpx.Client`` 替身：每构造一次 = 建一次连接池；照生产返回重排接口的 JSON。"""

    created = 0

    def __init__(self, *a, **kw):
        type(self).created += 1
        self.requests: list[dict] = []
        _ClientDouble.last = self

    def post(self, url, *, json=None, headers=None, timeout=None, **kw):
        self.requests.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        n = len(json["input"]["documents"])
        body = {"output": {"results": [{"index": i, "relevance_score": 1.0 - i / n}
                                       for i in range(n)]},
                "usage": {"total_tokens": 7}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def client_double(monkeypatch):
    from core import reranker

    _ClientDouble.created = 0
    monkeypatch.setattr(reranker.httpx, "Client", _ClientDouble)
    return _ClientDouble


def test_two_reranks_open_one_connection(client_double):
    from core import reranker

    reranker.rerank("q1", ["a", "b"], api_key="k1", region="cn")
    reranker.rerank("q2", ["a", "b"], api_key="k1", region="cn")
    assert client_double.created == 1


def test_keys_share_region_client_with_their_own_headers(client_double):
    from core import reranker

    reranker.rerank("q", ["a"], api_key="key-A", region="cn")
    reranker.rerank("q", ["a"], api_key="key-B", region="cn")
    assert client_double.created == 1
    auth = [r["headers"]["Authorization"] for r in client_double.last.requests]
    assert auth == ["Bearer key-A", "Bearer key-B"]
    assert all(r["timeout"] is not None for r in client_double.last.requests), "超时按请求传"


# ── ④ deadline 不够 → 退回向量顺序 ───────────────────────────────────


class _Collection:
    def __init__(self, n: int):
        self._rows = [(f"scene_{i}", f"原文片段 {i}", {"characters": "甲"}) for i in range(n)]

    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        rows = self._rows[:n_results]
        return {"ids": [[r[0] for r in rows]], "documents": [[r[1] for r in rows]],
                "distances": [[0.1 * i for i in range(len(rows))]],
                "metadatas": [[r[2] for r in rows]]}


def _engine(n: int):
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


def test_rerank_without_deadline_room_falls_back_to_vector_order(client_double):
    with embed_deadline(time.monotonic() + 0.05):
        hits = _engine(5).query_scenes("q", token_budget=10**6)
    assert hits.rerank_fallback is True
    assert [h.meta["vector_rank"] for h in hits] == [0, 1, 2, 3, 4]
    assert client_double.created == 0, "预算不够不建连、不发请求"


# ── ⑤ 评测走真实路由 ─────────────────────────────────────────────────


FRAGMENTS = ["甲乙丙丁戊己庚辛壬癸", "子丑寅卯辰巳午未申酉"]
SOURCE = "。".join(f"开头{f}结尾" for f in FRAGMENTS)
HISTORY = [{"role": "user", "content": "祈灼满月宴那天去了吗？"},
           {"role": "assistant", "content": "他说会去。"}]
CASES = [
    {"id": "q0", "phase": None, "query": USER, "should": [FRAGMENTS[0]], "history": HISTORY},
    {"id": "q1", "phase": None, "query": "今天天气不错", "should": [FRAGMENTS[1]],
     "history": HISTORY},
]


class _Rag:
    def __init__(self):
        self.queries: list[str] = []

    def chunk_spans(self, _n):
        return {f"c{i}": (SOURCE.index(f), SOURCE.index(f) + len(f))
                for i, f in enumerate(FRAGMENTS)}

    def query_scenes(self, query, **kw):
        self.queries.append(query)
        return EvidenceHits([SimpleNamespace(text=FRAGMENTS[0], meta={"chunk_id": "c0"})])


class _Call:
    def __init__(self, name, args):
        self.id = "call_1"
        self.function = SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False))

    def model_dump(self):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class _RouterLLM:
    """路由替身：第 1 题调 search_scenes（改写后的 query），第 2 题不调任何工具。"""

    def __init__(self):
        self.seen: list[tuple[str, list[dict]]] = []
        self.last_usage = None

    def chat_with_tools(self, system_prompt, messages, schemas):
        self.seen.append((system_prompt, list(messages)))
        last = messages[-1]
        if last["role"] == "user" and last["content"] == USER:
            return SimpleNamespace(content="", tool_calls=[_Call("search_scenes",
                                                                 {"query": ROUTER_Q})])
        return SimpleNamespace(content="", tool_calls=None)


@GOAL
def test_eval_via_router():
    import eval_recall
    from core import recall_eval

    rag, llm = _Rag(), _RouterLLM()
    rows = eval_recall.run_eval_no_phase(SOURCE, "祈灼", CASES, rag, router_llm=llm)

    # 路由看到的是 history + 当前句
    assert llm.seen[0][1] == HISTORY + [{"role": "user", "content": USER}]
    assert "祈灼" in llm.seen[0][0]
    # 检索用路由的 query（经 ① 与原句拼接）
    assert rag.queries == [f"{USER} | {ROUTER_Q}"]
    assert rows[0]["router_called"] is True and rows[0]["router_query"] == ROUTER_Q
    assert rows[0]["hit"] is True
    # 不调路由 → 未命中
    assert rows[1]["router_called"] is False and rows[1]["router_query"] is None
    assert rows[1]["hit"] is False
    assert recall_eval.aggregate_suite({"书/祈灼": rows})["router_call_rate"] == 0.5


def test_eval_without_router_has_no_router_fields():
    import eval_recall

    rows = eval_recall.run_eval_no_phase(SOURCE, "祈灼", CASES, _Rag())
    assert all("router_called" not in r and "router_query" not in r for r in rows)
