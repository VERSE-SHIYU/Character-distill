"""Evidence 线 2b：三条检索源的**共用**假件 —— 一套，不是三份复制。

三源（scene / memory / web）的差异只在「怎么产出 items」与「块标题/行格式」，
故假件也共用同一套装配器 ``build_ctx`` —— 跑哪条路径由传进去的假件决定。

本文件不碰真 chroma（Windows 宿主对非空集合 query 段错误），``FakeCollection`` 按
chroma ``QueryResult`` 形状喂 ids / documents / distances / metadatas，与
``test_rag_evidence_contract.py`` 的 ``_ScriptedCollection`` 同形。两处各自持有一份是
**故意**的：那份服务 rag 层契约、这份服务 context_engine 层，跨测试模块互相 import
会让两个文件的生命周期绑死（改一个动两个）。

chroma 的 ``query()`` 恒返回 ids（且 ``"ids"`` 不是合法 include 值），故假件恒给 ——
rag 侧按下标取 chunk_id，假件不给会被 IndexError 当场拦下。
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from core.context_engine import ContextEngine  # noqa: E402
from core.rag import RAGEngine  # noqa: E402
from core.schema import CharacterCard  # noqa: E402

# 行 = (chroma id, 原文, metadata)。metadata 键集逐键对齐 scene_indexer 实际写入的
# 形态（emotion / characters / scene_index 三键），上轮教训：fixture 少键会让取值锁空转。
SCENE_ROWS = [
    ("scene_0", "屋顶上的旧事，风很凉。",
     {"emotion": "平静", "characters": "魏无羡,江澄", "scene_index": "0"}),
    ("scene_1", "紫电出鞘那一瞬。",
     {"emotion": "愤怒", "characters": "江澄", "scene_index": "1"}),
    ("scene_2", "金光瑶微微一笑。",
     {"emotion": "温柔", "characters": "金光瑶", "scene_index": "2"}),
]

# 键集照抄 memory_manager.search 的产出（memory_manager.py:271-301 + 287-301 的合成量）。
# 数值是**冻结的字面量**：假件扮演 memory_manager，交出来的就是它算好的数，不许在假件里
# 现算（现算等于把生产公式抄进测试，公式改了测试跟着改，锁不住）。
MEMORY_ROWS = [
    {
        "text": "他说过要在莲花坞种满莲花。",
        "relevance": 0.91, "importance": 8, "age_seconds": 3600.0,
        "memory_mood": "怀念", "emo_affinity": 0.80, "final": 0.660, "base": 0.550,
    },
    {
        "text": "她答应过下次带酒来。",
        "relevance": 0.72, "importance": 5, "age_seconds": 86400.0,
        "memory_mood": "平静", "emo_affinity": 0.30, "final": 0.312, "base": 0.240,
    },
]

# DDG Instant Answer API 的两种回应形态：Abstract 优先；无 Abstract 才看 RelatedTopics。
DDG_ABSTRACT = {
    "AbstractText": "莲花坞是云梦江氏的家园，以莲塘与荷风闻名。",
    "AbstractURL": "https://example.org/lianhua",
    "AbstractSource": "示例百科",
    "RelatedTopics": [],
}

DDG_TOPICS = {
    "AbstractText": "",
    "Abstract": "",
    "RelatedTopics": [
        {"Text": "云梦江氏以紫电为家传法器。", "FirstURL": "https://example.org/zidian"},
        {"Text": "莲花坞四季荷花不谢。", "FirstURL": ""},  # 取不到 url → None，不许填 ""
        {"Text": "江澄曾在莲花坞重整门楣。", "FirstURL": "https://example.org/jiangcheng"},
    ],
}

# RelatedTopics 里嵌 Topics 的形态。旧实现只认**顶层**带 Text 的条目，不下钻 ——
# 下钻会改变喂给改写阶段的拼接文本（prompt 侧字节），本轮硬约束是 prompt 侧不变。
DDG_NESTED = {
    "AbstractText": "",
    "Abstract": "",
    "RelatedTopics": [
        {"Text": "顶层片段。", "FirstURL": "https://example.org/top"},
        {"Name": "分组", "Topics": [
            {"Text": "嵌套片段，旧实现不会取。", "FirstURL": "https://example.org/nested"},
        ]},
    ],
}


class FakeCollection:
    """chroma Collection 替身：按 n_results 截断，恒返回 ids。"""

    def __init__(self, rows=SCENE_ROWS, distances=None):
        self._rows = list(rows)
        self._distances = distances

    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        rows = self._rows[:n_results]
        if self._distances is not None:
            dists = self._distances[:n_results]
        else:
            dists = [0.1 * i for i in range(len(rows))]
        return {
            "ids": [[rid for rid, _, _ in rows]],
            "documents": [[d for _, d, _ in rows]],
            "distances": [dists],
            "metadatas": [[m for _, _, m in rows]],
        }


class RaisingCollection:
    """确定性不可用（模拟 chroma 真炸）：用来验「失败」态与「真无匹配」态可辨。"""

    def __init__(self, exc):
        self._exc = exc

    def query(self, *a, **kw):
        raise self._exc


def make_rag(collection=None) -> RAGEngine:
    """不跑 __init__（避免碰真 chromadb / OpenAI），只装 RAGEngine 需要的字段。"""
    eng = object.__new__(RAGEngine)
    eng._client = None
    eng._embedding_function = None
    eng._collection_name = "rag_fake_default"
    eng.collection = collection
    eng.collection_name = "fake"
    eng._chunk_size = 500
    eng._chunk_overlap = 50
    eng._top_k = 3
    return eng


class FakeMemory:
    """memory_manager 替身。``enabled`` / ``card_id`` 的空值态由构造参数控制。"""

    def __init__(self, rows=None, enabled=True):
        self._rows = list(MEMORY_ROWS if rows is None else rows)
        self.enabled = enabled
        self.calls: list[dict] = []

    def search(self, query, card_id, current_mood=None):
        self.calls.append({"query": query, "card_id": card_id, "current_mood": current_mood})
        return [dict(r) for r in self._rows]


class FakeLLM:
    """LLM 替身：记录 prompt，返回脚本化文本（空串 = 角色过滤器判定「全不适合」）。

    ``raise_on_chat`` 非空时改为抛该异常 —— 限流 / 超时 / 网络失败是改写阶段最常发生
    的失败形态，与「返回空串」是**两条独立分支**，只锁后者会漏掉常态那条。
    """

    def __init__(self, reply="我听说过莲花坞这个地方。", raise_on_chat: Exception | None = None):
        self._reply = reply
        self._raise = raise_on_chat
        self.calls: list[str] = []

    def chat(self, prompt, messages=None, **kw):
        self.calls.append(prompt)
        if self._raise is not None:
            raise self._raise
        return self._reply


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@contextlib.contextmanager
def fake_ddg(payload):
    """把 ``httpx.get`` 换成返回脚本化 payload 的假件（``_search_web`` 函数内 import httpx）。"""
    import httpx

    real, calls = httpx.get, []

    def fake_get(url, params=None, timeout=None, **kw):
        calls.append({"url": url, "params": params})
        return _FakeResponse(payload)

    httpx.get = fake_get
    try:
        yield calls
    finally:
        httpx.get = real


def make_card() -> CharacterCard:
    return CharacterCard(
        name="魏无羡",
        identity="云梦江氏故人，夷陵老祖",
        background="曾在莲花坞长大，后历经乱世。",
    )


def build_ctx(*, rag=None, memory=None, llm=None, card=None) -> ContextEngine:
    """装配 ContextEngine —— 三源共用这一个入口。"""
    return ContextEngine(
        card=card or make_card(),
        rag=rag,
        memory_manager=memory,
        card_id="card_test",
        llm=llm,
        model="deepseek-v4-pro",
        usage_ctx=None,
    )
