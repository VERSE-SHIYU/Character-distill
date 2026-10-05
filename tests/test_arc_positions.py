# -*- coding: utf-8 -*-
"""U5 / U6 / U7 / U15 / U16b：位置坐标与检索截断的契约。

**本文件不碰真 chroma** —— Windows 宿主对非空集合的任何操作都段错误（同
`tests/test_scene_index_dispatch.py` 的处置）。用脚本化假集合驱动真 `RAGEngine` /
`SceneIndexer` / `SessionRag`。

覆盖：
  * U5  `normalized_starts` 与「规范化前缀长度」逐点相等（C6 的坐标依据）。
  * U16b 切分函数返回的**原文起点**与片段一致（含重复段落，回找会找错）。
  * U6   检索上界：`where={"npos": {"$lt": before}}`；上界 None 全返回。
  * U7   适用性：`pos_schema` 缺 / 指纹不符 → 空 + warning；相符 → 按上界过滤。
  * U15  存卡调度：带起点的卡 → 场景索引带 `need_positions`、`text_` 无位置时补建；
         不带起点 → 都不调度。场景作业：指纹相同 + 无 `pos_schema` + `need_positions`
         → 重建；指纹相同 + 无标志 → 跳过。
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from chromadb.errors import NotFoundError

import core.indexing_service as IS
from core.quotes import normalize, normalized_starts
from core.rag import RAGEngine
from core.scene_indexer import SceneIndexer
from core.schema import CharacterCard
from core.text_manager import TextManager

NAME = "scenes_c1"
TEXT = (
    "第一幕，讲的是魏无羡在云深不知处的一段旧事。那天他坐在廊下，说了很多话，"
    "也做了很多事，直到天色彻底暗下来，才慢慢起身回房。\n\n"
    "第二幕，讲的是江澄后来在莲花坞的日子。他把紫电擦了一遍又一遍，谁也不见，"
    "只让门外的弟子把当日的账册送进来，一直看到深夜才肯停下。"
)


# ══ U5 坐标 ═══════════════════════════════════════════════════════════════

def test_u5_normalized_starts_equal_prefix_lengths():
    text = ("从前有座山，山里有座庙。庙里有个老和尚在讲故事。\n\n"
            "他讲的是：从前有座山——山里有座庙……") * 20
    raw = [0, 3, 9, 20, 37, len(text)]
    assert normalized_starts(text, raw) == [len(normalize(text[:r])) for r in raw]


def test_u5_prefix_consistency_under_segmentation():
    """C6 的不变式：逐段规范化累加 == 规范化整段前缀长。索引坐标靠的就是这条。"""
    text = "甲。乙、丙！\n\n丁：戊；己，庚。辛「壬」癸" * 30
    bounds = [0, 5, 11, 25, 40, len(text)]
    starts = normalized_starts(text, bounds)
    assert starts[0] == 0
    acc = 0
    for i in range(1, len(bounds)):
        acc += len(normalize(text[bounds[i - 1]:bounds[i]]))
        assert starts[i] == acc
    assert acc == len(normalize(text))


# ══ U16b 切分给出精确起点 ═══════════════════════════════════════════════════

_DUP = "这一段会重复出现两遍，用 text.find 回找会取到第一处，起点就错了。" * 2


def test_u16b_split_scenes_start_matches_segment():
    text = _DUP + "\n\n" + _DUP + "\n\n" + "第三段另起，也够长，用来凑数把切分撑开。" * 3
    parts = SceneIndexer()._split_scenes(text)
    assert parts, "没切出场景 —— 下面的断言会变成对空集的恒真"
    for start, seg in parts:
        assert text[start:start + len(seg)] == seg, (
            f"起点与片段对不上：start={start} seg[:12]={seg[:12]!r}"
        )
    # 内容相等还不足以证起点对：重复段落里 `text.find` 会取到**第一处**，而截出来的
    # 内容照样等于该片段。加一条对**位置**敏感的判据 —— 片段互不重叠、起点严格递增。
    starts = [s for s, _ in parts]
    assert all(a < b for a, b in zip(starts, starts[1:])), (
        f"起点没有严格递增（回找会取到第一处）：{starts}"
    )


def test_u16b_split_chat_scenes_start_matches_segment():
    day = "[2024-01-0{}] 今天发生了一件很长的事，长到足以被当成一个场景。\n"
    text = (day.format(1) + "补充一句，让第一天的内容超过五十个字符的长度限制。\n"
            + day.format(2) + "第二天也补充一句，同样要超过五十个字符的长度限制。")
    parts = SceneIndexer()._split_chat_scenes(text)
    assert parts, "没切出场景 —— 下面的断言会变成对空集的恒真"
    for start, seg in parts:
        assert text[start:start + len(seg)] == seg


def test_u16b_chunk_text_start_matches_segment():
    text = ("张三在公司是出了名的冷面总监。他常说做事要讲效率。\n\n"
            "但私下他会偷偷给流浪猫买罐头。李四是他唯一的朋友，两人大学就认识。\n\n"
            "张三的前妻王芳两年前离开了他。这段重复：张三在公司是出了名的冷面总监。" * 4)
    eng = object.__new__(RAGEngine)
    eng._chunk_size, eng._chunk_overlap = 60, 10
    parts = eng._chunk_text(text)
    assert parts, "没切出片段 —— 下面的断言会变成对空集的恒真"
    for start, seg in parts:
        assert text[start:start + len(seg)] == seg


def test_u16b_empty_text_yields_nothing():
    eng = object.__new__(RAGEngine)
    eng._chunk_size, eng._chunk_overlap = 60, 10
    assert eng._chunk_text("") == []
    assert SceneIndexer()._split_scenes("") == []


# ══ 脚本化假集合：复刻 chroma 的 where 语义（$lt / $lte 对 int） ═════════════

def _cmp(v, op, val) -> bool:
    if v is None:
        return False  # C8：不带 npos 键的条目被排除
    if op == "$lt":
        return v < val
    if op == "$lte":
        return v <= val
    raise AssertionError(f"未预期的算子 {op!r}")


class _PosCollection:
    def __init__(self, rows, metadata=None, id=None) -> None:
        self.rows = list(rows)                 # list[(doc, meta)]
        self.metadata = dict(metadata or {})
        self.id = id or "col"
        self.calls: list[dict] = []

    def query(self, query_texts=None, n_results=None, include=None, where=None):
        self.calls.append({"n_results": n_results, "where": where})
        rows = list(self.rows)
        for key, cond in (where or {}).items():
            op, val = next(iter(cond.items()))
            rows = [r for r in rows if _cmp(r[1].get(key), op, val)]
        rows = rows[: (n_results if n_results is not None else len(rows))]
        return {
            "ids": [[f"id{i}" for i in range(len(rows))]],
            "documents": [[d for d, _ in rows]],
            "distances": [[0.1 * i for i in range(len(rows))]],
            "metadatas": [[m for _, m in rows]],
        }

    def count(self) -> int:
        return len(self.rows)

    def add(self, documents=None, ids=None, metadatas=None):
        for i, d in enumerate(documents or []):
            self.rows.append((d, (metadatas or [])[i]))

    def modify(self, metadata=None):
        self.metadata = dict(metadata or {})

    def peek(self, limit: int = 1):
        return {"embeddings": [[0.0] * 8] if self.rows else []}


def _engine(col) -> RAGEngine:
    eng = object.__new__(RAGEngine)
    eng._client = None
    eng._embedding_function = None
    eng.collection = col
    eng.collection_name = "fake"
    eng._chunk_size, eng._chunk_overlap, eng._top_k = 200, 20, 3
    return eng


# ══ U6 检索上界 ═══════════════════════════════════════════════════════════

_ROWS = [
    ("d100", {"npos": 100}),
    ("d599", {"npos": 599}),
    ("d600", {"npos": 600}),
    ("d700", {"npos": 700}),
]


def test_u6_bound_excludes_entries_at_or_after_it():
    col = _PosCollection(_ROWS)
    hits = _engine(col).query("q", before=600, top_k=10)
    assert [str(h) for h in hits] == ["d100", "d599"], f"实得 {list(hits)}"
    assert col.calls[0]["where"] == {"npos": {"$lt": 600}}, f"where 不对：{col.calls[0]['where']}"


def test_u6_no_bound_returns_everything():
    col = _PosCollection(_ROWS)
    hits = _engine(col).query("q", top_k=10)
    assert [str(h) for h in hits] == ["d100", "d599", "d600", "d700"]
    assert col.calls[0]["where"] is None


def test_u6_entries_without_npos_are_excluded_when_bound_given():
    """C8：不带 npos 键的条目在加 where 时被排除（只能对已带位置的集合加过滤）。"""
    col = _PosCollection([("legacy", {"emotion": "平静"}), ("d10", {"npos": 10})])
    hits = _engine(col).query("q", before=600, top_k=10)
    assert [str(h) for h in hits] == ["d10"], f"实得 {list(hits)}"


# ══ U7 适用性判断只在 SessionRag ═══════════════════════════════════════════

class _Embedder:
    _dimensions = 8


class _Client:
    def __init__(self) -> None:
        self.cols: dict[str, _PosCollection] = {}

    def seed(self, name: str, rows, metadata=None) -> None:
        self.cols[name] = _PosCollection(rows, metadata, id=name)

    def get_collection(self, name=None, embedding_function=None):
        if name not in self.cols:
            raise NotFoundError(f"Collection {name} does not exist")
        return self.cols[name]

    def create_collection(self, name=None, embedding_function=None, metadata=None):
        self.cols[name] = _PosCollection([], metadata, id=name)
        return self.cols[name]

    def delete_collection(self, name=None):
        if name not in self.cols:
            raise NotFoundError(f"Collection {name} does not exist")
        del self.cols[name]


def _session_view(monkeypatch, client: _Client):
    def _factory(_config, *_a, **_kw):
        eng = object.__new__(RAGEngine)
        eng._client = client
        eng._embedding_function = _Embedder()
        eng._chunk_size, eng._chunk_overlap, eng._top_k = 200, 20, 3
        eng._collection_name = "rag_test"
        eng.collection = None
        eng.collection_name = None
        return eng

    monkeypatch.setattr(IS, "RAGEngine", _factory)
    svc = IS.IndexingService({"chunk_size": 200, "chunk_overlap": 20, "top_k": 3})
    return svc.get_rag_for_session(
        "t1", card_id="c1", embedding_key="k", embedding_region="cn")


_POS_ROWS = [("d100", {"npos": 100}), ("d599", {"npos": 599}), ("d700", {"npos": 700})]


def test_u7_matching_fingerprint_filters_by_bound(monkeypatch):
    client = _Client()
    client.seed("text_t1", _POS_ROWS,
                {"pos_schema": 1, "content_fingerprint": "FP"})
    view = _session_view(monkeypatch, client)
    hits = view.query_with_emotion_ex("q", before=600, source_fingerprint="FP", top_k=5)
    assert [h.text for h in hits] == ["d100", "d599"], f"实得 {[h.text for h in hits]}"


def test_u7_missing_pos_schema_returns_empty_and_warns(monkeypatch, caplog):
    client = _Client()
    client.seed("text_t1", _POS_ROWS, {"content_fingerprint": "FP"})
    view = _session_view(monkeypatch, client)
    with caplog.at_level(logging.WARNING):
        hits = view.query_with_emotion_ex("q", before=600, source_fingerprint="FP", top_k=5)
    assert list(hits) == [], f"无位置的集合不该被照常检索：{list(hits)}"
    assert client.cols["text_t1"].calls == [], "不适用的集合仍然被查了（会泄露之后的情节）"
    assert any(r.levelno == logging.WARNING for r in caplog.records), caplog.records


def test_u7_fingerprint_mismatch_returns_empty(monkeypatch):
    client = _Client()
    client.seed("text_t1", _POS_ROWS, {"pos_schema": 1, "content_fingerprint": "OTHER"})
    view = _session_view(monkeypatch, client)
    hits = view.query_with_emotion_ex("q", before=600, source_fingerprint="FP", top_k=5)
    assert list(hits) == []
    assert client.cols["text_t1"].calls == []


def test_u7_no_bound_bypasses_the_check(monkeypatch):
    """上界 None（最后阶段 / 旧卡）→ 不看 pos_schema，照常检索（今天的行为）。"""
    client = _Client()
    client.seed("text_t1", _POS_ROWS, {})  # 没有 pos_schema
    view = _session_view(monkeypatch, client)
    hits = view.query_with_emotion_ex("q", source_fingerprint="", top_k=5)
    assert [h.text for h in hits] == ["d100", "d599", "d700"]


# ══ U15 存卡调度 ═══════════════════════════════════════════════════════════

def _card_with_positions() -> CharacterCard:
    phases = [
        {"label": "L1", "state": "S1", "start": 0},
        {"label": "L2", "state": "S2", "start": 40},
    ]
    return CharacterCard.model_validate({
        "name": "魏无羡",
        "character_arc": {"axis": "从冷到热", "phases": phases,
                          "source_fingerprint": "FP"},
    })


class _Idx:
    def __init__(self, text_needs: bool = False) -> None:
        self.scene_calls: list[dict] = []
        self.reindexed: list[str] = []
        self._text_needs = text_needs

    def get_rag_for_session(self, *_a, **_kw):
        return None

    def schedule_scene_index(self, *a, **kw):
        self.scene_calls.append(kw)

    def schedule_text_reindex(self, text_id, content, **kw):
        self.reindexed.append(text_id)

    def text_needs_reindex(self, text_id, **_kw) -> bool:
        return self._text_needs


class _LLM:
    model = "stub"
    last_usage: dict = {}

    def chat(self, *_a, **_kw):
        raise RuntimeError("本用例不调模型")


class _Store:
    def __init__(self, uid: str = "u1") -> None:
        self.uid = uid
        self.text = {"id": "t1", "content": TEXT, "user_id": uid}

    async def save_card(self, card_id, text_id, name, card_json, user_id):
        return {"id": "c1"}

    async def get_text_owned(self, text_id, user_id):
        return self.text

    async def list_cards(self, text_id, user_id):
        return []

    async def get_user_api_config(self, user_id):
        return {"embedding_key": "k", "embedding_region": "cn"}

    async def save_session(self, *_a, **_kw):
        return None


def _tm(idx: _Idx, store: _Store) -> TextManager:
    tm = TextManager(lambda: store, None, _LLM(), {},
                     indexing_service=idx, memory_manager=None)

    async def _chars(*_a, **_kw):
        return [{"name": "魏无羡", "aliases": []}]

    async def _guard(_card):
        from core.moderation.card_guard import GuardVerdict
        return GuardVerdict()

    tm._build_all_characters = _chars
    tm._guard_card = _guard
    tm.memory_for = lambda *_a, **_kw: None
    return tm


def _save(tm: TextManager, card: CharacterCard) -> None:
    asyncio.run(tm.save_distilled_card(
        "t1", card, "u1", embedding_key="k", embedding_region="cn"))


def test_u15_card_with_positions_schedules_need_positions_and_reindex():
    idx = _Idx(text_needs=True)
    _save(_tm(idx, _Store()), _card_with_positions())
    assert idx.scene_calls, "根本没调度场景索引"
    assert idx.scene_calls[-1].get("need_positions") is True, (
        f"带起点的卡没带 need_positions：{idx.scene_calls[-1]}")
    assert idx.reindexed == ["t1"], f"text_ 无位置时应补建，实得 {idx.reindexed}"


def test_u15_card_with_positions_and_text_already_positioned_no_reindex():
    idx = _Idx(text_needs=False)
    _save(_tm(idx, _Store()), _card_with_positions())
    assert idx.scene_calls[-1].get("need_positions") is True
    assert idx.reindexed == [], f"text_ 已有位置不该重建，实得 {idx.reindexed}"


def test_u15_card_without_positions_schedules_neither():
    idx = _Idx(text_needs=True)
    card = CharacterCard.model_validate({
        "name": "魏无羡",
        "character_arc": {"axis": "从冷到热", "phases": [{"label": "L1", "state": "S1"}]},
    })
    _save(_tm(idx, _Store()), card)
    assert idx.scene_calls[-1].get("need_positions") is False, (
        f"不带起点的卡不该带 need_positions：{idx.scene_calls[-1]}")
    assert idx.reindexed == []


def test_u15_card_with_starts_but_no_fingerprint_schedules_neither():
    """起点齐全但**指纹缺失**不是「有位置」：不能只查起点是否齐全（M22）。"""
    idx = _Idx(text_needs=True)
    card = CharacterCard.model_validate({
        "name": "魏无羡",
        "character_arc": {"axis": "从冷到热", "phases": [
            {"label": "L1", "state": "S1", "start": 0},
            {"label": "L2", "state": "S2", "start": 40},
        ]},
    })
    _save(_tm(idx, _Store()), card)
    assert idx.scene_calls[-1].get("need_positions") is False, (
        f"指纹缺失不该被当成有位置：{idx.scene_calls[-1]}")
    assert idx.reindexed == []


# ── 场景作业：need_positions 的幂等规则 ───────────────────────────────────

class _FakeCollection:
    def __init__(self, metadata, dims=8) -> None:
        self.metadata = metadata
        self.dims = dims
        self.docs: list[str] = []
        self.adds = 0

    def add(self, documents=None, ids=None, metadatas=None):
        self.docs.extend(documents or [])
        self.adds += 1

    def modify(self, metadata=None):
        self.metadata = dict(metadata or {})

    def count(self) -> int:
        return len(self.docs)

    def peek(self, limit: int = 1):
        return {"embeddings": [[0.0] * self.dims] if self.docs else []}


class _SceneClient:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}
        self.deletes: list[str] = []
        self.creates: list[str] = []

    def get_collection(self, name=None, embedding_function=None):
        if name not in self.collections:
            raise NotFoundError(f"Collection {name} does not exist")
        return self.collections[name]

    def create_collection(self, name=None, embedding_function=None, metadata=None):
        col = _FakeCollection(dict(metadata or {}))
        self.collections[name] = col
        self.creates.append(name)
        return col

    def delete_collection(self, name=None):
        self.deletes.append(name)
        if name not in self.collections:
            raise NotFoundError(f"Collection {name} does not exist")
        del self.collections[name]


def _scene_rag(client: _SceneClient) -> RAGEngine:
    rag = object.__new__(RAGEngine)
    rag._client = client
    rag._embedding_function = None
    rag.collection = None
    rag.collection_name = None
    return rag


def _build_legacy(client: _SceneClient):
    """按新代码建一次，再抹掉 `pos_schema` —— 模拟「这条 new feature 之前建好的集合」。"""
    rag = _scene_rag(client)
    SceneIndexer().index_scenes(TEXT, rag, "魏无羡", collection_name=NAME)
    client.collections[NAME].metadata.pop("pos_schema", None)
    return len(client.deletes), len(client.creates)


def test_u15_scene_job_rebuilds_when_need_positions_and_no_pos_schema():
    client = _SceneClient()
    d0, c0 = _build_legacy(client)
    SceneIndexer().index_scenes(TEXT, _scene_rag(client), "魏无羡",
                                collection_name=NAME, need_positions=True)
    assert (len(client.deletes), len(client.creates)) == (d0 + 1, c0 + 1), (
        "指纹相同但无 pos_schema、且带 need_positions，必须重建")
    assert client.collections[NAME].metadata.get("pos_schema") == 1


def test_u15_scene_job_skips_when_no_flag_even_without_pos_schema():
    """不带 need_positions 的既有调度幂等规则不变：旧卡不会被重新嵌入（M11b）。"""
    client = _SceneClient()
    d0, c0 = _build_legacy(client)
    SceneIndexer().index_scenes(TEXT, _scene_rag(client), "魏无羡", collection_name=NAME)
    assert (len(client.deletes), len(client.creates)) == (d0, c0), (
        f"不带标志的调度不该重建，实得 deletes+{len(client.deletes) - d0} "
        f"creates+{len(client.creates) - c0}")


def test_u15_scene_job_writes_npos_and_pos_schema_on_first_build():
    client = _SceneClient()
    SceneIndexer().index_scenes(TEXT, _scene_rag(client), "魏无羡", collection_name=NAME)
    assert client.collections[NAME].metadata.get("pos_schema") == 1
    assert client.collections[NAME].metadata.get("content_fingerprint")
