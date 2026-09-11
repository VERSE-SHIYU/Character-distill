"""Hermetic regression: characters 过滤在 Python 侧做，且候选不足必须可见。

根因：core/rag.py 把 characters 写成逗号分隔字符串，过滤却交给 chroma 的
``{"characters": {"$contains": name}}``。chroma 1.5.9 的 ``$contains`` 只对**数组**
元数据生效，对字符串元数据恒不命中（连 ``$contains`` 完整原串都是 0 命中）——
于是场景检索恒空：ContextEngine._retrieve_scenes 恒传 card.name，web 单卡 chat 与
MCP 三工具一起静默失效。

修法：不做数据迁移（不重建集合），改为取回候选后在 Python 侧子串过滤；过滤后取不满
top_k 时扩大候选重取一次，仍不足则如实返回并置 ``SceneHits.candidates_exhausted``。

本文件不碰真 chroma（Windows 宿主对非空集合的任何操作都段错误），用脚本化的假集合
（``_ScriptedCollection`` 复刻"传了 where 就返回空"这一实测行为）驱动真实 RAGEngine。

Run: python tests/test_rag_characters_filter.py   (also pytest-collectable)
"""

from __future__ import annotations

import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from core.rag import (  # noqa: E402
    CHARACTERS_NONE_TAG,
    CHARACTER_FILTER_MULTIPLIER,
    RAGEngine,
    SceneHits,
    characters_tag,
)
from core.scene_indexer import SceneIndexer  # noqa: E402

# 三条候选：第一条是**多角色** chunk，这正是 $eq 方案会漏掉的那条。
ROWS = [
    ("魏无羡与江澄并肩站在屋顶。", {"characters": "魏无羡,江澄", "emotion": "平静"}),
    ("江澄握紧了紫电，指节发白。", {"characters": "江澄", "emotion": "愤怒"}),
    ("金光瑶微微一笑，掩去眼底冷意。", {"characters": "金光瑶", "emotion": "温柔"}),
]


class _ScriptedCollection:
    """chroma Collection 替身：复刻"where 一传就返回空"的实测行为。

    真实 chroma 1.5.9 对字符串元数据 ``$contains`` 恒不命中。把它钉进假件里，
    "过滤改回交给 chroma" 这个变异才有可观测的红。
    """

    def __init__(self, rows=ROWS):
        self._rows = list(rows)
        self.calls: list[dict] = []

    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        self.calls.append({"n_results": n_results, "where": where})
        if where is not None:
            return {"documents": [[]], "distances": [[]], "metadatas": [[]]}
        rows = self._rows[:n_results]
        return {
            "documents": [[d for d, _ in rows]],
            "distances": [[0.1 * i for i in range(len(rows))]],
            "metadatas": [[m for _, m in rows]],
        }


def _make_engine(collection) -> RAGEngine:
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


# ── 正向：多角色 chunk 能被其中任一名字命中（$eq 方案不可行的那条） ──

def test_multi_character_chunk_matched_by_either_name():
    eng = _make_engine(_ScriptedCollection())
    by_a = eng.query("屋顶上的旧事", character_name="魏无羡", top_k=3)
    assert list(by_a) == [ROWS[0][0]], f"多角色 chunk 应被其中一个名字命中，实得 {list(by_a)}"
    by_b = eng.query("屋顶上的旧事", character_name="江澄", top_k=3)
    assert list(by_b) == [ROWS[0][0], ROWS[1][0]], f"江澄应命中两条，实得 {list(by_b)}"
    print("  [PASS] 多角色 chunk 被任一名字命中（魏无羡：1 条；江澄：2 条）")


def test_positive_hit_does_not_ask_chroma_to_filter():
    """变异锚点：把过滤改回 chroma 的 $contains where → 本条必红（假件会返回空）。"""
    col = _ScriptedCollection()
    eng = _make_engine(col)
    hits = eng.query("屋顶上的旧事", character_name="魏无羡", top_k=3)
    assert list(hits) == [ROWS[0][0]], "正向命中缺失 —— 过滤很可能又被交给了 chroma"
    assert all(c["where"] is None for c in col.calls), \
        f"不应再把 characters 当 where 交给 chroma，实得 {col.calls}"
    print("  [PASS] 正向命中不经 chroma 的 where 过滤（候选取回后 Python 侧比对）")


# ── 负例：不含目标角色的 chunk 不得被返回 ──

def test_chunk_without_target_not_returned():
    """变异锚点：删掉 Python 侧过滤 → 本条必红（会把三条原样返回）。"""
    eng = _make_engine(_ScriptedCollection())
    hits = eng.query("屋顶上的旧事", character_name="蓝忘机", top_k=3)
    assert list(hits) == [], f"不含目标角色的 chunk 被返回了：{list(hits)}"
    assert hits.candidates_exhausted is True
    print("  [PASS] 不含目标角色的 chunk 不被返回（且如实标记候选耗尽）")


# ── 候选耗尽：少于 top_k 且标记为真 ──

def test_candidates_exhausted_flagged_and_fewer_than_top_k():
    """变异锚点：删掉候选耗尽标记 → 本条必红。"""
    rows = [(f"路人片段{i}", {"characters": "路人甲", "emotion": "平静"}) for i in range(6)]
    rows.append(("唯一命中片段", {"characters": "魏无羡", "emotion": "平静"}))
    eng = _make_engine(_ScriptedCollection(rows))
    hits = eng.query("旧事", character_name="魏无羡", top_k=3)
    assert list(hits) == ["唯一命中片段"], f"实得 {list(hits)}"
    assert len(hits) < 3 and hits.candidates_exhausted is True, \
        f"少于 top_k 必须标记候选耗尽，实得 len={len(hits)} flag={hits.candidates_exhausted}"
    print("  [PASS] 候选耗尽：返回 1 条（< top_k=3）且 candidates_exhausted=True")


def test_refetch_widens_candidates_once_when_not_exhausted():
    """候选刚好取满 n 但过滤后不足 → 必须扩大重取，而不是就此返回少量。"""
    rows = [(f"路人片段{i}", {"characters": "路人甲", "emotion": "平静"}) for i in range(12)]
    rows.append(("命中在候选之外", {"characters": "魏无羡", "emotion": "平静"}))
    col = _ScriptedCollection(rows)
    eng = _make_engine(col)
    hits = eng.query("旧事", character_name="魏无羡", top_k=3)
    assert list(hits) == ["命中在候选之外"], f"实得 {list(hits)}"
    assert len(col.calls) == 2, f"应扩大候选重取一次，实得 {len(col.calls)} 次查询"
    assert col.calls[1]["n_results"] == 3 * CHARACTER_FILTER_MULTIPLIER * CHARACTER_FILTER_MULTIPLIER
    print("  [PASS] 候选未取尽时扩大重取一次（1 次 → 2 次查询，候选倍数递增）")


def test_query_with_emotion_uses_same_filter_and_flag():
    eng = _make_engine(_ScriptedCollection())
    hits = eng.query_with_emotion("旧事", current_emotion="平静", character_name="江澄", top_k=3)
    assert list(hits) == [ROWS[0][0], ROWS[1][0]], f"实得 {list(hits)}"
    assert isinstance(hits, SceneHits)
    none_hits = eng.query_with_emotion("旧事", current_emotion="平静", top_k=3)
    assert len(none_hits) == 3, "不传 character_name 时应不过滤"
    print("  [PASS] query_with_emotion 同一过滤与标记；不传角色名时不过滤")


def test_no_collection_returns_empty_hits():
    eng = _make_engine(None)
    assert list(eng.query("q")) == [] and list(eng.query_with_emotion("q")) == []
    print("  [PASS] 无集合 → 空 SceneHits（行为不变）")


# ── 写入格式单点：rag 与 scene_indexer 不得各写一份 ──

class _CapturingCollection:
    def __init__(self):
        self.metas = None

    def add(self, documents=None, ids=None, metadatas=None):
        self.metas = metadatas


class _CapturingClient:
    def __init__(self):
        self.col = _CapturingCollection()

    def delete_collection(self, name=None):
        raise RuntimeError("no such collection")

    def create_collection(self, name=None, embedding_function=None):
        return self.col


def test_characters_write_format_single_source():
    assert characters_tag(["a", "b"]) == "a,b"
    assert characters_tag([]) == CHARACTERS_NONE_TAG

    eng = _make_engine(None)
    chars = [{"name": "魏无羡"}, {"name": "江澄"}]
    assert eng._tag_characters("魏无羡与江澄同行", chars) == characters_tag(["江澄", "魏无羡"]), \
        "rag 写入格式必须就是 characters_tag()"

    # scene_indexer 写的是同一格式（此前它自己写单个字符串）
    rag = object.__new__(RAGEngine)
    rag._client = _CapturingClient()
    rag._embedding_function = None
    rag.collection = None
    rag.collection_name = None
    text = (
        "第一段落，讲的是魏无羡在云深不知处的一段旧事。那天他坐在廊下，说了很多话，"
        "也做了很多事，直到天色彻底暗下来，才慢慢起身回房。\n\n"
        "第二段落，讲的是江澄后来在莲花坞的日子。他把紫电擦了一遍又一遍，谁也不见，"
        "只让门外的弟子把当日的账册送进来，一直看到深夜才肯停下。"
    )
    n = SceneIndexer().index_scenes(text, rag, "魏无羡")
    assert n > 0 and rag._client.col.metas, "scene_indexer 应写入元数据"
    assert all(m["characters"] == characters_tag(["魏无羡"]) for m in rag._client.col.metas), \
        f"scene_indexer 的 characters 格式与 rag 不一致：{rag._client.col.metas}"
    print("  [PASS] characters 写入格式单点：rag 与 scene_indexer 同出 characters_tag()")


def main() -> int:
    tests = [
        test_multi_character_chunk_matched_by_either_name,
        test_positive_hit_does_not_ask_chroma_to_filter,
        test_chunk_without_target_not_returned,
        test_candidates_exhausted_flagged_and_fewer_than_top_k,
        test_refetch_widens_candidates_once_when_not_exhausted,
        test_query_with_emotion_uses_same_filter_and_flag,
        test_no_collection_returns_empty_hits,
        test_characters_write_format_single_source,
    ]
    print("=== RAG CHARACTERS-FILTER REGRESSION ===\n")
    for t in tests:
        t()
    print("\n=== ALL %d TESTS PASSED ===" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
