"""召回升级第 1 轮目标检查：场景切块存全文（docs/specs/recall-r1-chunking.md §1）。

目标：场景集合每块存**完整文本**和起止位置，块按段落边界攒到预算大小。
夹具带真实书的格式特征：省略号（…… / ...）、中文引号、单换行段落、重复句。

1. 块内文本 = 原文对应区间原样；所有块首尾相接覆盖全文（除空白）。
2. 段落不被切断；单段超预算只在句末切；单句超预算整句成块。
3. 块长 ≤ 预算（单句超长除外）。
4. metadata 带 npos 与 nend；chunk_spans 有 nend 用 nend，没有退回下一块起点。
5. 0c 理想检索对照在新切法下 R@3 = 1.0，越界判得出。
6. 集合 metadata 带 chunk_scheme；存卡、开卡两条路径都按它判复用；POS_SCHEMA 不变。
"""
from __future__ import annotations

import re

import pytest
from chromadb.errors import NotFoundError

from core.quotes import normalize
from core.rag import POS_SCHEMA, RAGEngine


# ── 夹具 ────────────────────────────────────────────────────────────────

_P = [
    "第一章 渡口",
    "甲说：“你来了……我等了很久。”",
    "乙沉默不语，转身离去。",
    "雨下了一夜...天亮时，渡口只剩一盏灯。",
    "甲说：“你来了……我等了很久。”",            # 重复句
    "丙站在桥头，说：“我知道你会来！”又问：“你还走吗？”",
    "祈灼在渡口等到天亮。",
]
_LONG_PARA = "".join(
    f"第{i}次，他回头看了一眼，说：“再等等……”她没有答话。" for i in range(1, 13)
)  # 单段远超预算，只能在句末切
_LONG_SENTENCE = "他" + "一步一步" * 60 + "走到了尽头。"   # 单句 > 预算
_TAIL = "尾声\n魏无羡在另一处杀了人，这件事祈灼不知道。"

TEXT = "\n".join(_P) + "\n" + _LONG_PARA + "\n" + _LONG_SENTENCE + "\n\n" + _TAIL + "\n"

_SENT_END = re.compile(r"[。！？…][”’」』）)]*$")


def _schemes():
    from core.scene_indexer import ChunkScheme
    return {
        "A": ChunkScheme(mode="para", chars=60, overlap=0),
        "B": ChunkScheme(mode="para", chars=120, overlap=0),
        # 第三档预算：只在复用判断用例里充当「切法变了」的另一侧。
        "C": ChunkScheme(mode="para", chars=200, overlap=0),
    }


def _split(text: str, scheme):
    from core.scene_indexer import SceneIndexer
    return SceneIndexer()._split_scenes(text, scheme)


# ── 1–3：切法 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant", ["A", "B", "C"])
def test_1_chunks_are_verbatim_and_cover_text(variant):
    chunks = _split(TEXT, _schemes()[variant])
    assert chunks
    cursor = 0
    for start, end, body in chunks:
        assert body == TEXT[start:end], "块内文本必须是原文区间原样"
        assert TEXT[cursor:start].strip() == "", (
            f"块之间只能隔空白，实得 {TEXT[cursor:start]!r}")
        cursor = max(cursor, end)
    assert TEXT[cursor:].strip() == "", "全文末尾有内容没进任何块"


@pytest.mark.parametrize("variant", ["A", "B"])
def test_2_paragraphs_kept_whole_long_ones_cut_at_sentence_end(variant):
    scheme = _schemes()[variant]
    chunks = _split(TEXT, scheme)
    para_starts = {m.start() for m in re.finditer(r"(?:^|(?<=\n))(?=\S)", TEXT)}
    for start, end, body in chunks:
        if start in para_starts and (end == len(TEXT) or TEXT[end] == "\n"):
            continue                                    # 整段（或多段）成块
        assert _SENT_END.search(body), f"段内切口不在句末：{body[-12:]!r}"
    long_start = TEXT.index(_LONG_PARA)
    inner = [c for c in chunks if long_start < c[0] < long_start + len(_LONG_PARA)]
    assert inner, "超预算的单段必须被切开"


@pytest.mark.parametrize("variant", ["A", "B"])
def test_3_para_chunks_within_budget_except_single_sentence(variant):
    scheme = _schemes()[variant]
    for _, _, body in _split(TEXT, scheme):
        if len(body) > scheme.chars:
            assert body == _LONG_SENTENCE, f"超预算的只能是单句：{body[:20]!r}…"


# ── 4–6：入库、区间、复用 ────────────────────────────────────────────────

class _Col:
    def __init__(self, metadata):
        self.metadata = dict(metadata or {})
        self.ids, self.docs, self.metas = [], [], []

    def add(self, documents=None, ids=None, metadatas=None, **_):
        self.ids += list(ids)
        self.docs += list(documents)
        self.metas += list(metadatas)

    def get(self, include=None, **_):
        return {"ids": list(self.ids), "metadatas": list(self.metas),
                "documents": list(self.docs)}

    def modify(self, metadata=None):
        self.metadata = dict(metadata or {})

    def count(self):
        return len(self.docs)

    def peek(self, limit=1):
        return {"embeddings": [[0.0]] if self.docs else []}


class _Client:
    def __init__(self):
        self.collections: dict[str, _Col] = {}
        self.creates = 0

    def get_collection(self, name=None, embedding_function=None):
        if name not in self.collections:
            raise NotFoundError(f"Collection {name} does not exist")
        return self.collections[name]

    def get_max_batch_size(self):  # 真 ClientAPI 接口；写入按它分批

        return 5461


    def create_collection(self, name=None, embedding_function=None, metadata=None):
        self.collections[name] = _Col(metadata)
        self.creates += 1
        return self.collections[name]

    def delete_collection(self, name=None):
        if name not in self.collections:
            raise NotFoundError(f"Collection {name} does not exist")
        del self.collections[name]


def _rag(client):
    rag = object.__new__(RAGEngine)
    rag._client = client
    rag._embedding_function = None
    rag.collection = None
    rag.collection_name = None
    return rag


NAME = "scenes_c1"


def _build(client, scheme, need_positions=False):
    from core.scene_indexer import SceneIndexer
    SceneIndexer().index_scenes(TEXT, _rag(client), "祈灼", collection_name=NAME,
                                need_positions=need_positions, scheme=scheme)
    return client.collections[NAME]


@pytest.mark.parametrize("variant", ["A", "C"])
def test_4_full_text_stored_with_npos_and_nend(variant):
    scheme = _schemes()[variant]
    col = _build(_Client(), scheme)
    chunks = _split(TEXT, scheme)
    assert col.docs == [body for _, _, body in chunks], "存的必须是整块原文，不截断"
    for (start, end, _), meta in zip(chunks, col.metas):
        assert meta["npos"] == len(normalize(TEXT[:start]))
        assert meta["nend"] == len(normalize(TEXT[:end]))


def test_4_chunk_spans_prefers_nend_and_falls_back_for_old_collections():
    from core.rag import chunk_spans
    col = _Col({})
    col.add(ids=["a", "b", "c"], documents=["", "", ""],
            metadatas=[{"npos": 0, "nend": 8}, {"npos": 5, "nend": 20}, {"npos": 30}])
    assert chunk_spans(col, 40) == {"a": (0, 8), "b": (5, 20), "c": (30, 40)}
    old = _Col({})
    old.add(ids=["x", "y"], documents=["", ""], metadatas=[{"npos": 0}, {"npos": 9}])
    assert chunk_spans(old, 15) == {"x": (0, 9), "y": (9, 15)}


@pytest.mark.parametrize("variant", ["A", "B", "C"])
def test_5_ideal_retrieval_still_one_under_new_chunking(variant):
    from core import recall_eval
    from core.rag import chunk_spans
    from core.schema import scene_evidence

    col = _build(_Client(), _schemes()[variant])
    source_norm = normalize(TEXT)
    spans = chunk_spans(col, len(source_norm))

    def ideal(fragment):
        frag = normalize(fragment)
        cid, doc = next((i, d) for i, d in zip(col.ids, col.docs) if frag in normalize(d))
        return [scene_evidence(text=doc, vector_rank=0,
                               chunk_id=cid, chapter=None)]

    fragments = ["乙沉默不语，转身离去", "丙站在桥头", "祈灼在渡口等到天亮",
                 "第12次，他回头看了一眼"]
    rows = [recall_eval.score_case(source_norm,
                                   {"id": f"q{i}", "should": [f], "should_not": []},
                                   ideal(f), spans)
            for i, f in enumerate(fragments)]
    assert recall_eval.aggregate(rows)["r_at_3"] == 1.0

    leak = recall_eval.score_case(
        source_norm, {"id": "leak", "should": [], "should_not": ["魏无羡在另一处杀了人"]},
        ideal("魏无羡在另一处杀了人"), spans)
    assert leak["leak"] is True


def test_6_scheme_recorded_and_rebuilds_on_both_paths():
    schemes = _schemes()
    client = _Client()
    col = _build(client, schemes["A"])
    assert col.metadata["chunk_scheme"] == schemes["A"].key()
    assert col.metadata["pos_schema"] == POS_SCHEMA == 1

    n = client.creates
    _build(client, schemes["A"])                      # 开卡：同 scheme 复用
    _build(client, schemes["A"], need_positions=True)  # 存卡：同 scheme 复用
    assert client.creates == n

    _build(client, schemes["B"])                      # 开卡：scheme 变了 → 重建
    assert client.creates == n + 1
    _build(client, schemes["C"], need_positions=True)  # 存卡：scheme 变了 → 重建
    assert client.creates == n + 2

    client.collections[NAME].metadata.pop("chunk_scheme")   # 旧集合：没有该键
    _build(client, schemes["C"])
    assert client.creates == n + 3


def test_6_default_scheme_is_used_when_none_given():
    from core.scene_indexer import CHUNK_SCHEME, SceneIndexer
    assert CHUNK_SCHEME.key() == "para:238:0"   # 定稿值；SG 评测库复用它已建的集合
    client = _Client()
    SceneIndexer().index_scenes(TEXT, _rag(client), "祈灼", collection_name=NAME)
    assert client.collections[NAME].metadata["chunk_scheme"] == CHUNK_SCHEME.key()


# ── S3：切法字符串只在一处解析 ───────────────────────────────────────────

@pytest.mark.parametrize("variant", ["A", "B", "C"])
def test_s3_from_key_inverts_key(variant):
    from core.scene_indexer import ChunkScheme
    scheme = _schemes()[variant]
    assert ChunkScheme.from_key(scheme.key()) == scheme


@pytest.mark.parametrize("bad", ["", "para", "para:300", "para:x:0", "word:300:0",
                                 "fixed:100:100", "para:0:0", "para:300:0:1", "para:-5:0"])
def test_s3_from_key_rejects_bad_values(bad):
    from core.scene_indexer import ChunkScheme
    with pytest.raises(ValueError):
        ChunkScheme.from_key(bad)


@pytest.mark.parametrize("key", ["fixed:100:100", "fixed:100:0"])
def test_s3_from_key_rejects_deleted_fixed_mode(key):
    """固定切法已删除：from_key 明确拒绝，不静默解析成别的切法。"""
    from core.scene_indexer import ChunkScheme
    with pytest.raises(ValueError, match="固定切法已删除"):
        ChunkScheme.from_key(key)
