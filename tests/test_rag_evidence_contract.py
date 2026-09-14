"""Evidence 契约层：rag 结构化出口与字符串出口的逐字节等价 + meta 键集契约。

两条锁，缺一不可（本仓 §四「断言空转假绿」）：

- **(1) 离线基线**：``query_with_emotion`` 在固定假集合上的产出，冻结的是**改动前**
  抓的字节（抓取脚本 ``e2e/scratch/capture_rag_golden.py``，gitignored 的一次性脚本）。
  值本身已冻在下面，复现不依赖那个脚本 —— 回退到任一旧 commit、用同一假集合即可。
  这条抓的是「两个出口共用的那份实现被改动」：同源改动两边一起变，单比两出口相等
  是恒真的，抓不到。
- **(2) 两出口相等**：``[e.text for e in _ex(...)]`` 与原方法返回逐元素同序。
  这条抓的是「``_ex`` 自己把文本改了一个字符」——即本轮的验收变异。

为什么用假集合：本机（Windows）chroma 对**非空**集合 query 会段错误，真库跑不了。
假集合只喂 ``documents/distances/metadatas``，与被测代码的取用路径一致。
"""

from __future__ import annotations

import pytest

from core.rag import RAGEngine
from core.schema import EVIDENCE_META, EvidenceItem, MemoryMeta, SceneMeta, WebMeta

ROWS = [
    ("屋顶上的旧事，风很凉。", {"characters": "魏无羡,江澄", "emotion": "平静"}),
    ("紫电出鞘那一瞬。", {"characters": "江澄", "emotion": "愤怒"}),
    ("金光瑶微微一笑。", {"characters": "金光瑶", "emotion": "温柔"}),
    ("雨里的告别。", {"characters": "魏无羡", "emotion": "悲伤"}),
]


class _ScriptedCollection:
    """脚本化假集合：按 n_results 截断返回，距离为 0.1 的等差序列。"""

    def __init__(self, rows=ROWS):
        self._rows = list(rows)

    def query(self, query_texts=None, n_results=None, include=None, where=None, **kw):
        rows = self._rows[:n_results]
        return {
            "documents": [[d for d, _ in rows]],
            "distances": [[0.1 * i for i in range(len(rows))]],
            "metadatas": [[m for _, m in rows]],
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
        ex = _run_ex(tag)
        assert [e.text for e in ex] == GOLDEN[tag]["docs"], f"{tag}: _ex 的文本偏离基线"

    @pytest.mark.parametrize("tag", ["A", "B", "C", "D"])
    def test_ex_variant_prompt_side_equals_legacy(self, tag):
        """**只**比两出口 —— 这是用户要求的形态。

        它抓「``_ex`` 自己改文本」，抓不到「共用实现被改」（两边一起变，恒真）。
        后者靠上面两条基线锁。故本类三条一起才是完整守护，不是重复。
        """
        ex, legacy = _run_ex(tag), _run_legacy(tag)
        assert [e.text for e in ex] == list(legacy)
        assert ex.candidates_exhausted is legacy.candidates_exhausted

    def test_ex_variant_is_structured_not_strings(self):
        ex = _run_ex("A")
        assert all(isinstance(e, EvidenceItem) for e in ex)
        assert [e.kind for e in ex] == ["scene"] * 3


class TestSceneMetaExplainsFinal:
    """0.7 语义与 0.3 情感必须拆开存：合成一个 final 就没了可解释性。"""

    def test_components_are_separate_and_recombine(self):
        for e in _run_ex("A"):
            m = e.meta
            assert m["final"] == pytest.approx(0.7 * m["semantic"] + 0.3 * m["emotion_affinity"])
            assert e.score == m["final"]

    def test_opposite_emotion_moves_affinity_only(self):
        """同一次检索、只换 current_emotion：semantic 逐条不动，emotion_affinity 变。

        必须同集合同过滤地比 —— ``semantic`` 是**按本次结果集的 max_dist 归一化**的
        （``1 - dist/max_dist``），跨查询不可比。这不是本用例的假设，是被测代码的性质：
        换一组候选，同一条原文的 semantic 就变了。下游若要展示「相关度」，得知道
        这个数只在单次检索内有意义。
        """
        kw = dict(query_text="旧事", character_name="江澄", top_k=2)
        calm = {e.text: e.meta for e in _ex(**kw, current_emotion="平静")}
        angry = {e.text: e.meta for e in _ex(**kw, current_emotion="愤怒")}
        assert set(calm) == set(angry) and calm, "两次检索命中集不同，本用例失去判别力"
        for text in calm:
            assert calm[text]["semantic"] == pytest.approx(angry[text]["semantic"])
            assert calm[text]["emotion_affinity"] != angry[text]["emotion_affinity"]


class TestMetaKeyContract:
    """③ 防 dict 变垃圾桶：键集由 TypedDict 声明，运行期强制，无第二份手维护清单。"""

    def test_every_kind_has_a_meta_contract(self):
        assert set(EVIDENCE_META) == {"scene", "memory", "web"}

    @pytest.mark.parametrize("kind,cls", list(EVIDENCE_META.items()))
    def test_declared_keys_match_the_spec(self, kind, cls):
        expected = {
            "scene": {"chapter", "chunk_id", "semantic", "emotion_affinity", "final"},
            "memory": {"relevance", "importance", "age_seconds", "mood"},
            "web": {"url", "source", "fetched_at"},
        }[kind]
        assert set(cls.__annotations__) == expected

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
