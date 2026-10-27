"""#211 补：写入向量库按 chroma 单批上限分批，复用只认「已建完」。

目标：
1. 块数超过 chroma 单批上限（`client.get_max_batch_size()`）时，原文集合与场景集合都能写满；
   每批不超过上限（替身照生产对超限批次抛错）。
2. 构建完成标记（`BUILD_MARK_KEY`）只在最后一批写完之后打一次。
3. 写到第 k 批失败 → 集合无构建标记 → 下次 `index_scenes` 不复用，删掉重建并写满。
"""
from __future__ import annotations

import pytest
from chromadb.errors import NotFoundError

from core.rag import BUILD_MARK_KEY, RAGEngine

LIMIT = 3


class _Col:
    """替身集合：单批超过 LIMIT 照 chroma 抛错；可设第 k 次 add 失败。"""

    def __init__(self, metadata, fail_on_add: int | None = None):
        self.metadata = dict(metadata or {})
        self.ids, self.docs, self.metas = [], [], []
        self.events: list[str] = []
        self._fail_on_add = fail_on_add
        self._adds = 0

    def add(self, documents=None, ids=None, metadatas=None, **_):
        self._adds += 1
        if len(ids) > LIMIT:
            raise ValueError(
                f"Batch size of {len(ids)} is greater than max batch size of {LIMIT}")
        if self._fail_on_add == self._adds:
            raise RuntimeError(f"embedding failed on batch {self._adds}")
        self.ids += list(ids)
        self.docs += list(documents)
        self.metas += list(metadatas)
        self.events.append("add")

    def modify(self, metadata=None):
        self.metadata = dict(metadata or {})
        self.events.append("modify")

    def count(self):
        return len(self.docs)

    def peek(self, limit=1):
        return {"embeddings": [[0.0]] if self.docs else []}


class _Client:
    def __init__(self, fail_on_add: int | None = None):
        self.collections: dict[str, _Col] = {}
        self.creates = 0
        self.fail_on_add = fail_on_add

    def get_max_batch_size(self):
        return LIMIT

    def get_collection(self, name=None, embedding_function=None):
        if name not in self.collections:
            raise NotFoundError(f"Collection {name} does not exist")
        return self.collections[name]

    def create_collection(self, name=None, embedding_function=None, metadata=None):
        self.collections[name] = _Col(metadata, self.fail_on_add)
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
    rag._chunk_size = 10
    rag._chunk_overlap = 0
    rag._collection_name = "text_t1"
    rag.collection = None
    rag.collection_name = None
    return rag


SCENES = "\n\n".join(f"第{i}段，祈灼在渡口等了一夜。" for i in range(8)) + "\n"


def _index_scenes(client):
    from core.scene_indexer import SceneIndexer, ChunkScheme
    return SceneIndexer().index_scenes(
        SCENES, _rag(client), "祈灼", collection_name="scenes_c1",
        scheme=ChunkScheme.from_key("para:20:0"))


def test_1_add_in_batches_respects_client_limit_and_writes_all():
    from core.rag import add_in_batches
    client = _Client()
    col = client.create_collection(name="c")
    n = 2 * LIMIT + 1
    add_in_batches(client, col, [f"d{i}" for i in range(n)], [f"i{i}" for i in range(n)],
                   [{"k": i} for i in range(n)])
    assert col.ids == [f"i{i}" for i in range(n)]
    assert col.events == ["add"] * 3


def test_1_text_collection_written_in_batches_marked_once_at_end():
    client = _Client()
    _rag(client).index("一二三四五六七八九十" * 8, collection_name="text_t1")
    col = client.collections["text_t1"]
    assert col.count() > LIMIT
    assert col.events[-1] == "modify" and col.events.count("modify") == 1
    assert BUILD_MARK_KEY in col.metadata


def test_2_scene_collection_written_in_batches_marked_once_at_end():
    client = _Client()
    n = _index_scenes(client)
    col = client.collections["scenes_c1"]
    assert n > LIMIT and col.count() == n
    assert col.events[-1] == "modify" and col.events.count("modify") == 1
    assert BUILD_MARK_KEY in col.metadata


def test_3_partial_write_is_not_reused_and_gets_rebuilt():
    client = _Client(fail_on_add=2)
    with pytest.raises(RuntimeError):
        _index_scenes(client)
    half = client.collections["scenes_c1"]
    assert 0 < half.count() and BUILD_MARK_KEY not in half.metadata

    client.fail_on_add = None
    n = _index_scenes(client)
    col = client.collections["scenes_c1"]
    assert client.creates == 2, "半截集合不得被复用"
    assert col.count() == n and BUILD_MARK_KEY in col.metadata


def test_3_finished_collection_is_still_reused():
    client = _Client()
    _index_scenes(client)
    _index_scenes(client)
    assert client.creates == 1


# ── 4：is_built —— 旧集合（单次写入）可用，新集合须有构建标记 ─────────────────

def _col_with(client, name, metadata):
    col = client.create_collection(name=name, metadata=metadata)
    col.add(documents=["正文"], ids=["chunk_0"], metadatas=[{"npos": 0}])
    return col


def test_4_old_single_add_collection_without_both_keys_is_usable():
    client = _Client()
    _col_with(client, "text_t1", {})
    assert _rag(client).load_existing("text_t1") is True


def test_4_half_written_new_collection_is_not_loaded():
    from core.rag import WRITE_MODE_KEY
    client = _Client()
    _col_with(client, "text_t1", {WRITE_MODE_KEY: "batched"})
    assert _rag(client).load_existing("text_t1") is False


def test_4_session_skips_half_written_scenes_and_falls_back_to_text():
    from core.indexing_service import IndexingService
    from core.rag import WRITE_MODE_KEY
    client = _Client()
    _col_with(client, "scenes_c1", {WRITE_MODE_KEY: "batched"})
    _rag(client).index("一二三四五六七八九十", collection_name="text_t1")
    rag, why = IndexingService._load_session_rag(_rag(client), "t1", "c1")
    assert why is None and rag.collection_name == "text_t1"


def test_4_new_builds_record_write_mode_and_count_as_built():
    from core.rag import WRITE_MODE_KEY, is_built
    client = _Client()
    _rag(client).index("一二三四五六七八九十" * 2, collection_name="text_t1")
    _index_scenes(client)
    for name in ("text_t1", "scenes_c1"):
        meta = client.collections[name].metadata
        assert meta[WRITE_MODE_KEY] == "batched" and is_built(meta)
    assert _rag(client).load_existing("text_t1") is True
