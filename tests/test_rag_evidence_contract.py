"""Evidence 契约层：rag 结构化出口的逐字节等价 + meta 键集/**取值来源**契约。

三条锁，红源互不重叠（本仓 §四「断言空转假绿」）：

- **(1) 离线基线**：``query_with_emotion`` 在固定假集合上的产出，冻结的是**改动前**
  抓的字节（抓取脚本 ``e2e/scratch/capture_rag_golden.py``，gitignored 的一次性脚本；
  值已冻在下面，复现不依赖它）。**这是抓「两个出口共用的那份实现被改」的唯一红源** ——
  同源改动两边一起变，单比两出口相等是恒真的。只有**一份** GOLDEN：``_ex`` 在改动前
  不存在，抓不到它自己的基线，``test_ex_matches_frozen_baseline`` 复用的是同一份。
- **(2) 两出口相等**：``[e.text for e in _ex(...)]`` 与原方法返回逐元素同序。
  它的**专属红源是 exhausted 维，不是文本维** —— 文本维上两边都等于 GOLDEN，
  彼此相等是蕴含的。实测：``_ex`` 的 text 改一字符 → 基线与相等**同时**红；
  ``_ex`` 的 candidates_exhausted 恒 False → **只有**相等红。
- **(3) meta 取值来源**：键集对不代表取值对 —— 上一轮 ``chunk_id`` 恒 None，
  取哪个键都绿。见 ``TestSceneMetaValueSources``。

为什么用假集合：本机（Windows）chroma 对**非空**集合 query 会段错误，真库跑不了。
假集合按 chroma ``QueryResult`` 的形状喂 ids/documents/distances/metadatas。
"""

from __future__ import annotations

import pytest

from core.rag import RAGEngine
from core.schema import EVIDENCE_META, EvidenceItem, MemoryMeta, SceneMeta, WebMeta

# 行 = (chroma id, 原文, metadata)。id 与 metadata 键集**逐键对齐
# core/scene_indexer.py:69-79 实际写入的形态**（id=f"scene_{i}"、metadata 恰为
# emotion / characters / scene_index 三键）。上一轮的教训：fixture 少了 scene_index，
# chunk_id 在全部用例里恒 None，取哪个键都绿 —— 测的不是生产形态。
ROWS = [
    ("scene_0", "屋顶上的旧事，风很凉。",
     {"emotion": "平静", "characters": "魏无羡,江澄", "scene_index": "0"}),
    ("scene_1", "紫电出鞘那一瞬。",
     {"emotion": "愤怒", "characters": "江澄", "scene_index": "1"}),
    ("scene_2", "金光瑶微微一笑。",
     {"emotion": "温柔", "characters": "金光瑶", "scene_index": "2"}),
    ("scene_3", "雨里的告别。",
     {"emotion": "悲伤", "characters": "魏无羡", "scene_index": "3"}),
]


class _ScriptedCollection:
    """脚本化假集合：按 n_results 截断，距离默认 0.1 等差（可覆盖）。"""

    def __init__(self, rows=ROWS, distances=None):
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


def _make_engine(collection) -> RAGEngine:
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


CASES = {
    "A": dict(query_text="旧事", current_emotion="平静", top_k=3),
    "B": dict(query_text="旧事", current_emotion="愤怒", character_name="江澄", top_k=2),
    "C": dict(query_text="旧事", current_emotion="悲伤", character_name="蓝忘机", top_k=3),
}

# 改动前抓的基线（capture_rag_golden.py，2026-09-14，跑在改 core/rag.py 之前）。
# 手算复核（A：1.0 / 0.41 / 0.15 降序；B：0.76 / 0.3 降序）与冻结值一致。
GOLDEN = {
    "A": {
        "docs": ["屋顶上的旧事，风很凉。", "紫电出鞘那一瞬。", "金光瑶微微一笑。"],
        "exhausted": False,
    },
    "B": {"docs": ["屋顶上的旧事，风很凉。", "紫电出鞘那一瞬。"], "exhausted": False},
    "C": {"docs": [], "exhausted": True},
    "D": {"docs": [], "exhausted": False},
}


def _run_legacy(tag: str):
    """跑字符串出口。D 用 None 集合（早返回路径），其余用假集合。"""
    eng = _make_engine(None if tag == "D" else _ScriptedCollection())
    return eng.query_with_emotion(**CASES.get(tag, {"query_text": "q"}))


def _run_ex(tag: str):
    eng = _make_engine(None if tag == "D" else _ScriptedCollection())
    return eng.query_with_emotion_ex(**CASES.get(tag, {"query_text": "q"}))


def _ex(**kw):
    """跑结构化出口，参数直给（用于同集合、只换单一变量的对照）。"""
    return _make_engine(_ScriptedCollection()).query_with_emotion_ex(**kw)


class TestPromptSideByteEquality:
    """⑤ 自动化守护：结构性改动不得挪动 prompt 侧的任何一字节。"""

    @pytest.mark.parametrize("tag", ["A", "B", "C", "D"])
    def test_legacy_matches_frozen_baseline(self, tag):
        hits = _run_legacy(tag)
        assert list(hits) == GOLDEN[tag]["docs"], f"{tag}: prompt 侧产出偏离改动前基线"
        assert hits.candidates_exhausted is GOLDEN[tag]["exhausted"]

    @pytest.mark.parametrize("tag", ["A", "B", "C", "D"])
    def test_ex_matches_frozen_baseline(self, tag):
        """复用同一份 GOLDEN —— ``_ex`` 改动前不存在，抓不到它自己的基线。"""
        ex = _run_ex(tag)
        assert [e.text for e in ex] == GOLDEN[tag]["docs"], f"{tag}: _ex 的文本偏离基线"

    @pytest.mark.parametrize("tag", ["A", "B", "C", "D"])
    def test_ex_variant_prompt_side_equals_legacy(self, tag):
        """**只**比两出口。专属红源是 exhausted 维（文本维两边都等于 GOLDEN，恒真）。"""
        ex, legacy = _run_ex(tag), _run_legacy(tag)
        assert [e.text for e in ex] == list(legacy)
        assert ex.candidates_exhausted is legacy.candidates_exhausted

    def test_ex_variant_is_structured_not_strings(self):
        ex = _run_ex("A")
        assert all(isinstance(e, EvidenceItem) for e in ex)
        assert [e.kind for e in ex] == ["scene"] * 3


class TestSceneMetaValueSources:
    """② 键集对 ≠ 取值对：meta 每个值取自哪里，必须有专属红源。"""

    def test_chunk_id_is_the_chroma_id_not_a_metadata_copy(self):
        ex = _run_ex("A")
        assert [e.meta["chunk_id"] for e in ex] == ["scene_0", "scene_1", "scene_2"]

    def test_chunk_id_follows_the_character_filter(self):
        """过滤后 chunk_id 跟着走 —— 与 docs 同一套 keep 下标，不许错位。"""
        ex = _run_ex("B")  # 江澄 → 行 0、1
        assert [e.text for e in ex] == GOLDEN["B"]["docs"]
        assert [e.meta["chunk_id"] for e in ex] == ["scene_0", "scene_1"]

    def test_metadata_scene_index_lie_does_not_leak(self):
        """判别力证明：让 metadata 的 scene_index 撒谎，chunk_id 不受影响。

        改回读 metadata 影子副本时这条必红（取值会变成 999）。
        """
        lying = [(rid, doc, {**meta, "scene_index": "999"}) for rid, doc, meta in ROWS]
        ex = _make_engine(_ScriptedCollection(lying)).query_with_emotion_ex(
            query_text="旧事", current_emotion="平静", top_k=3)
        assert [e.meta["chunk_id"] for e in ex] == ["scene_0", "scene_1", "scene_2"]

    def test_chapter_is_none_today(self):
        """今天无生产方就必须是 None —— 将来谁塞假章节号必须红。"""
        ex = _run_ex("A")
        assert ex, "本用例需要非空命中"
        assert all(e.meta["chapter"] is None for e in ex)


class TestSceneMetaExplainsFinal:
    """0.7 语义与 0.3 情感必须拆开存：合成一个 final 就没了可解释性。"""

    def test_components_are_separate_and_recombine(self):
        for e in _run_ex("A"):
            m = e.meta
            assert m["final"] == pytest.approx(0.7 * m["semantic"] + 0.3 * m["emotion_affinity"])
            assert e.score == m["final"]

    def test_opposite_emotion_moves_affinity_only(self):
        """同一次检索、只换 current_emotion：semantic 逐条不动，emotion_affinity 变。

        ``semantic`` 按**本次结果集**的 max_dist 归一化，跨查询不可比，故必须同集合比。
        """
        kw = dict(query_text="旧事", character_name="江澄", top_k=2)
        calm = {e.text: e.meta for e in _ex(**kw, current_emotion="平静")}
        angry = {e.text: e.meta for e in _ex(**kw, current_emotion="愤怒")}
        assert set(calm) == set(angry) and calm, "两次检索命中集不同，本用例失去判别力"
        for text in calm:
            assert calm[text]["semantic"] == pytest.approx(angry[text]["semantic"])
            assert calm[text]["emotion_affinity"] != angry[text]["emotion_affinity"]

    def test_single_hit_semantic_is_zero(self):
        """只命中一条时 max_dist 就是它自己的距离 → semantic 恒 0（契约里写了就要锁）。"""
        ex = _ex(query_text="旧事", current_emotion="平静", character_name="金光瑶", top_k=3)
        assert len(ex) == 1
        assert ex[0].meta["semantic"] == 0.0

    def test_last_returned_is_not_necessarily_zero(self):
        """**「最后一条恒为 0」不成立** —— 反例钉在这里。

        距离最大的那条 semantic 恒 0，但返回序按 final 排，它可以排在**前面**。
        故契约里只承诺「距离最大者恒 0」，不承诺「最后一条恒 0」。
        """
        rows = [
            ("scene_0", "近但情绪不匹配。",
             {"emotion": "愤怒", "characters": "甲", "scene_index": "0"}),
            ("scene_1", "远但情绪匹配。",
             {"emotion": "悲伤", "characters": "甲", "scene_index": "1"}),
        ]
        eng = _make_engine(_ScriptedCollection(rows, distances=[0.9, 1.0]))
        ex = eng.query_with_emotion_ex(query_text="q", current_emotion="悲伤", top_k=2)
        assert [e.text for e in ex] == ["远但情绪匹配。", "近但情绪不匹配。"]
        assert ex[0].meta["semantic"] == 0.0
        assert ex[-1].meta["semantic"] != 0.0, "本反例的前提：最后一条不是距离最大那条"


class TestMetaKeyContract:
    """④ 防 dict 变垃圾桶：键集由 TypedDict 声明，运行期强制，无第二份手维护清单。"""

    def test_every_kind_has_a_meta_contract(self):
        assert set(EVIDENCE_META) == {"scene", "memory", "web"}

    @pytest.mark.parametrize("kind,cls", list(EVIDENCE_META.items()))
    def test_declared_keys_match_the_spec(self, kind, cls):
        expected = {
            "scene": {"chapter", "chunk_id", "semantic", "emotion_affinity", "final"},
            "memory": {"relevance", "importance", "age_seconds", "mood",
                       "emo_affinity", "final"},
            "web": {"url", "source", "fetched_at"},
        }[kind]
        assert set(cls.__annotations__) == expected

    def test_memory_keeps_components_and_composite_apart(self):
        """与 scene 同一条规矩：memory 也要分量 + 合成分并列。"""
        assert {"emo_affinity", "final"} <= set(MemoryMeta.__annotations__)

    def test_produced_scene_meta_satisfies_the_contract(self):
        ex = _run_ex("A")
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
