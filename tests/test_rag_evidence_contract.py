"""Evidence 契约层：scene 结构化出口的 meta 键集与**取值来源**契约。

召回第 2 轮（rerank）**有意重写**本文件：``SceneMeta`` 从
``semantic / emotion_affinity / final`` 改为 ``rerank_score / vector_rank``，旧的
「两出口逐字节等价 + 0.7/0.3 合成」不再是契约。保留下来的是两条与排序算法无关的锁：

- **(1) meta 取值来源**：键集对不代表取值对 —— ``chunk_id`` 必须取 chroma 的真 id，
  不取 metadata 里的影子副本；``chapter`` 今天无生产方，恒 None。
- **(2) meta 键集契约**：由 TypedDict 声明、运行期强制，无第二份手维护清单。

排序本身（重排分数决定前 3、失败退回向量顺序）由 ``tests/test_scene_rerank_goal.py``
锁，不在这里重复。

为什么用假集合：本机（Windows）chroma 对**非空**集合 query 会段错误，真库跑不了。
假集合按 chroma ``QueryResult`` 的形状喂 ids/documents/distances/metadatas。
"""

from __future__ import annotations

import pytest

from core.rag import RAGEngine
from core.schema import EVIDENCE_META, EvidenceItem, MemoryMeta, SceneMeta, WebMeta

# 行 = (chroma id, 原文, metadata)。id 与 metadata 键集**逐键对齐
# core/scene_indexer.py 实际写入的形态（id=f"scene_{i}"、metadata 为
# characters / scene_index / npos）。fixture 少了 scene_index，chunk_id 在全部
# 用例里恒 None，取哪个键都绿 —— 测的不是生产形态。
ROWS = [
    ("scene_0", "屋顶上的旧事，风很凉。",
     {"characters": "魏无羡,江澄", "scene_index": "0"}),
    ("scene_1", "紫电出鞘那一瞬。",
     {"characters": "江澄", "scene_index": "1"}),
    ("scene_2", "金光瑶微微一笑。",
     {"characters": "金光瑶", "scene_index": "2"}),
    ("scene_3", "雨里的告别。",
     {"characters": "魏无羡", "scene_index": "3"}),
]


class _ScriptedCollection:
    """脚本化假集合：按 n_results 截断，距离默认 0.1 等差。"""

    def __init__(self, rows=ROWS):
        self._rows = list(rows)

    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        rows = self._rows[:n_results]
        return {
            "ids": [[rid for rid, _, _ in rows]],
            "documents": [[d for _, d, _ in rows]],
            "distances": [[0.1 * i for i in range(len(rows))]],
            "metadatas": [[m for _, _, m in rows]],
        }


def _make_engine(collection) -> RAGEngine:
    """不跑 __init__；没有 ``_embedding_key`` → 重排视为不可用，走向量顺序（不碰网络）。"""
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


def _ex(**kw):
    return _make_engine(_ScriptedCollection()).query_scenes(**kw)


class TestSceneMetaValueSources:
    """键集对 ≠ 取值对：meta 每个值取自哪里，必须有专属红源。"""

    def test_chunk_id_is_the_chroma_id_not_a_metadata_copy(self):
        ex = _ex(query_text="旧事", top_k=3)
        assert [e.meta["chunk_id"] for e in ex] == ["scene_0", "scene_1", "scene_2"]

    def test_chunk_id_follows_the_character_filter(self):
        """过滤后 chunk_id 跟着走 —— 与 docs 同一套 keep 下标，不许错位。

        必须用 **keep 非前缀** 的过滤：金光瑶只命中下标 2。前缀 keep（如江澄 → [0,1]）
        过滤与不过滤取值相同，判别力为零 —— 变异「``_take(ids)`` 退回 ``ids``（ids 与
        docs 错位）」在原用例上**全绿**（实测），锁的就是这个失效形态：原文对、来源 id 错。
        """
        ex = _ex(query_text="旧事", character_name="金光瑶", top_k=3)
        assert [e.text for e in ex] == ["金光瑶微微一笑。"]
        assert [e.meta["chunk_id"] for e in ex] == ["scene_2"]

    def test_metadata_scene_index_lie_does_not_leak(self):
        """判别力证明：让 metadata 的 scene_index 撒谎，chunk_id 不受影响。

        改回读 metadata 影子副本时这条必红（取值会变成 999）。
        """
        lying = [(rid, doc, {**meta, "scene_index": "999"}) for rid, doc, meta in ROWS]
        ex = _make_engine(_ScriptedCollection(lying)).query_scenes(
            query_text="旧事", top_k=3)
        assert [e.meta["chunk_id"] for e in ex] == ["scene_0", "scene_1", "scene_2"]

    def test_chapter_is_none_today(self):
        """今天无生产方就必须是 None —— 将来谁塞假章节号必须红。"""
        ex = _ex(query_text="旧事", top_k=3)
        assert ex, "本用例需要非空命中"
        assert all(e.meta["chapter"] is None for e in ex)

    def test_vector_rank_is_the_candidate_position(self):
        """退回向量顺序时，vector_rank 就是候选位次（0 起），与返回序一致。"""
        ex = _ex(query_text="旧事", top_k=3)
        assert [e.meta["vector_rank"] for e in ex] == [0, 1, 2]

    def test_fallback_has_no_rerank_score(self):
        """重排不可用（本引擎无 key）→ rerank_score / score 都是 None，不许编一个数。"""
        ex = _ex(query_text="旧事", top_k=3)
        assert ex and ex.rerank_fallback is True
        assert all(e.meta["rerank_score"] is None for e in ex)
        assert all(e.score is None for e in ex)


class TestMetaKeyContract:
    """④ 防 dict 变垃圾桶：键集由 TypedDict 声明，运行期强制，无第二份手维护清单。"""

    def test_every_kind_has_a_meta_contract(self):
        assert set(EVIDENCE_META) == {"scene", "memory", "web"}

    @pytest.mark.parametrize("kind,cls", list(EVIDENCE_META.items()))
    def test_declared_keys_match_the_spec(self, kind, cls):
        expected = {
            "scene": {"chapter", "chunk_id", "rerank_score", "vector_rank"},
            "memory": {"relevance", "importance", "age_seconds", "mood",
                       "emo_affinity", "final"},
            "web": {"url", "source", "fetched_at"},
        }[kind]
        assert set(cls.__annotations__) == expected

    def test_memory_keeps_components_and_composite_apart(self):
        """与 scene 同一条规矩：memory 也要分量 + 合成分并列。"""
        assert {"emo_affinity", "final"} <= set(MemoryMeta.__annotations__)

    def test_produced_scene_meta_satisfies_the_contract(self):
        ex = _ex(query_text="旧事", top_k=3)
        assert ex, "本用例需要非空命中"
        for e in ex:
            assert set(e.meta) == set(SceneMeta.__annotations__)

    def test_validator_rejects_extra_key(self):
        with pytest.raises(ValueError, match="键集与契约不符"):
            EvidenceItem(kind="web", text="t", meta=WebMeta(url="u", source="s",
                                                           fetched_at="now", bonus="x"))

    def test_validator_rejects_missing_key(self):
        with pytest.raises(ValueError, match="键集与契约不符"):
            EvidenceItem(kind="memory", text="t",
                         meta=MemoryMeta(relevance=1.0, importance=5, age_seconds=1.0))
