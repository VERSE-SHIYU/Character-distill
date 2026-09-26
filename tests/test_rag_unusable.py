"""Hermetic regression: dimension-mismatched / failing collections are DISTINGUISHED from no-match.

Regression for the root-cause fix: load_existing was dimension-blind (only count()>0 →
loaded a pre-embedder-migration 384-dim collection as "OK"), and query / query_with_emotion
swallowed every exception into [] — so a stale collection "loaded successfully" then
silently returned empty forever (no log, callers saw "no content").

Assertions (per directive):
  1. load_existing on a dimension-mismatched collection raises explicitly (CollectionUnusableError),
     carrying stored_dim / expected_dim — it does NOT return True, and does NOT leave the
     collection half-loaded.
  2. query() on a failing collection raises a distinguishable error — it does NOT return [].
     Genuine no-match (empty results) still returns [].

No real chroma write: Windows host reading/writing chroma segfaults, and the data dir is
Linux-written. Fake chroma client/collection objects exercise the REAL RAGEngine logic only
(via object.__new__, so __init__ never touches chromadb / OpenAI).

Run: python tests/test_rag_unusable.py   (also pytest-collectable)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))
_web = _repo / "web"
if str(_web) not in sys.path:
    sys.path.insert(0, str(_web))  # web.routers.group 顶层 `from deps import ...`

from chromadb.errors import NotFoundError

from core.rag import CollectionUnusableError, RAGEngine


class _FakeCollection:
    """Minimal chroma Collection stand-in: count / peek / query / add with scripted behavior."""

    def __init__(self, count=0, dim=None, query_error=None, query_result=None, peek_error=None):
        self.name = "fake"
        self._count = count
        self._dim = dim
        self._query_error = query_error
        self._query_result = query_result
        self._peek_error = peek_error
        self.docs: list[str] = []

    def count(self) -> int:
        return self._count

    def peek(self, limit=1):
        if self._peek_error is not None:
            raise self._peek_error
        embs = [[0.0] * self._dim] if self._dim is not None else []
        return {"embeddings": embs}

    def query(self, **kwargs):
        if self._query_error is not None:
            raise self._query_error
        return self._query_result

    def add(self, documents=None, ids=None, metadatas=None):
        self.docs.extend(documents or [])


class _FakeClient:
    """Minimal chroma PersistentClient stand-in.

    Two shapes of construction, kept apart on purpose:
      - ``_FakeClient(col)`` / ``_FakeClient(exc)`` — scripted ``get_collection``, for the
        load_existing / query tests (the only method they touch).
      - ``_FakeClient()`` — dict-backed, mirroring the real client's contract for the
        *write* path: ``get_collection`` and ``delete_collection`` both raise
        ``NotFoundError`` when the name is absent. That is precisely what
        ``core/rag.py::index`` relies on when it deletes a not-yet-existing collection.
    """

    def __init__(self, target=None):
        self._target = target
        self._collections: dict[str, _FakeCollection] = {}
        self.deletes: list[str] = []
        self.delete_error: Exception | None = None

    def get_collection(self, name=None, embedding_function=None):
        if isinstance(self._target, Exception):
            raise self._target
        if self._target is not None:
            return self._target
        if name not in self._collections:
            raise NotFoundError(f"Collection {name} does not exist")
        return self._collections[name]

    def create_collection(self, name=None, embedding_function=None, metadata=None):
        col = _FakeCollection()
        self._collections[name] = col
        return col

    def delete_collection(self, name=None):
        self.deletes.append(name)
        if self.delete_error is not None:
            raise self.delete_error
        if name not in self._collections:
            raise NotFoundError(f"Collection {name} does not exist")
        del self._collections[name]


def _make_engine(client=None, embed_dim=1024, collection=None) -> RAGEngine:
    """Build a RAGEngine without running __init__ (avoids touching real chromadb / OpenAI)."""
    eng = object.__new__(RAGEngine)
    eng._client = client

    class _EF:
        pass

    ef = _EF()
    if embed_dim is not None:
        ef._dimensions = embed_dim
    eng._embedding_function = ef
    eng._collection_name = "rag_fake_default"
    eng.collection = collection
    eng.collection_name = collection.name if collection is not None else None
    eng._chunk_size = 500
    eng._chunk_overlap = 50
    eng._top_k = 3
    return eng


def test_dim_mismatch_load_existing_raises():
    """384-dim collection + 1024-dim embedder → load_existing raises, not True."""
    col = _FakeCollection(count=5, dim=384)
    eng = _make_engine(client=_FakeClient(col), embed_dim=1024)
    try:
        eng.load_existing("text_stale")
    except CollectionUnusableError as exc:
        assert exc.stored_dim == 384, exc.stored_dim
        assert exc.expected_dim == 1024, exc.expected_dim
        assert exc.collection_name == "text_stale"
        assert "384" in str(exc) and "1024" in str(exc)
    else:
        raise AssertionError("dim-mismatch load_existing must raise, not return True")
    # not left half-loaded
    assert eng.collection is None and eng.collection_name is None
    print("  [PASS] load_existing dim-mismatch raises CollectionUnusableError(384 vs 1024), no half-load")


def test_dim_match_load_existing_ok():
    """1024-dim collection + 1024-dim embedder → loads normally."""
    col = _FakeCollection(count=5, dim=1024)
    eng = _make_engine(client=_FakeClient(col), embed_dim=1024)
    assert eng.load_existing("text_ok") is True
    assert eng.collection is col and eng.collection_name == "text_ok"
    print("  [PASS] load_existing matching dim returns True and loads")


def test_missing_collection_load_existing_false():
    """NotFoundError from get_collection → False (designed no-collection signal)."""
    eng = _make_engine(client=_FakeClient(NotFoundError("nope")), embed_dim=1024)
    assert eng.load_existing("text_absent") is False
    print("  [PASS] load_existing missing collection returns False (no log noise)")


def test_empty_collection_load_existing_false():
    """count()==0 → False (nothing to load)."""
    col = _FakeCollection(count=0, dim=None)
    eng = _make_engine(client=_FakeClient(col), embed_dim=1024)
    assert eng.load_existing("text_empty") is False
    print("  [PASS] load_existing empty collection returns False")


def test_get_collection_error_load_existing_logs_returns_false():
    """Unexpected get_collection error → logged, returns False (not silent pass, not raise)."""
    eng = _make_engine(client=_FakeClient(RuntimeError("corrupt catalog")), embed_dim=1024)
    assert eng.load_existing("text_broken") is False
    print("  [PASS] load_existing get_collection error returns False (logged)")


def test_embedder_dim_unknown_skips_validation():
    """Embedder without _dimensions → cannot validate → loads (query layer still guards)."""
    col = _FakeCollection(count=5, dim=384)
    eng = _make_engine(client=_FakeClient(col), embed_dim=None)
    assert eng.load_existing("text_legacy") is True
    print("  [PASS] embedder dim unknown → load proceeds (query failure still surfaced)")


def test_query_dim_error_raises_not_empty():
    """A failing collection.query → CollectionUnusableError, NOT []."""
    col = _FakeCollection(
        count=5, dim=384,
        query_error=ValueError("expecting dimension of 384, got 1024"),
    )
    eng = _make_engine(collection=col, embed_dim=1024)
    try:
        eng.query("问一句")
    except CollectionUnusableError as exc:
        assert "384" in str(exc) and "1024" in str(exc)
    else:
        raise AssertionError("query on failing collection must raise, not return []")
    print("  [PASS] query() dim error raises CollectionUnusableError (not silent empty)")


def test_query_with_emotion_dim_error_raises_not_empty():
    """query_with_emotion also surfaces, not swallows-to-[] then fallback."""
    col = _FakeCollection(
        count=5,
        query_error=ValueError("expecting dimension of 384, got 1024"),
    )
    eng = _make_engine(collection=col, embed_dim=1024)
    try:
        eng.query_with_emotion("问一句", current_emotion="平静", character_name="角色", top_k=3)
    except CollectionUnusableError:
        pass
    else:
        raise AssertionError("query_with_emotion on failing collection must raise, not []")
    print("  [PASS] query_with_emotion() raises CollectionUnusableError (no swallow→fallback→[])")


def test_query_no_match_still_returns_empty():
    """Genuine no-match (empty results) must STILL return [] — distinguishable from failure."""
    col = _FakeCollection(count=5, query_result={"documents": [[]]})
    eng = _make_engine(collection=col, embed_dim=1024)
    assert eng.query("没有匹配") == []
    col2 = _FakeCollection(count=5, query_result={"documents": [[], [], []]})
    eng2 = _make_engine(collection=col2, embed_dim=1024)
    assert eng2.query_with_emotion("没有匹配", current_emotion="平静") == []
    print("  [PASS] genuine no-match still returns [] (recoverable ≠ collection failure)")


def test_query_no_collection_returns_empty():
    """collection is None → [] (not indexed yet), unchanged."""
    eng = _make_engine(collection=None, embed_dim=1024)
    assert eng.query("q") == []
    assert eng.query_with_emotion("q") == []
    print("  [PASS] no collection → query returns [] (unchanged)")


# ── #27 窄化守卫：index() 首次索引删的是「不存在的集合」──

def test_real_chromadb_delete_absent_collection_raises_notfound():
    """契约守卫：真 chromadb 删一个不存在的集合，抛的就是 ``chromadb.errors.NotFoundError``。

    这是 ``except NotFoundError`` 成立的**前提**，不是对 RAGEngine 的断言。升级 chromadb
    若把这个类型换掉，本条先红 —— 而不是等生产上「首次索引一律 500」。

    只删一个不存在的集合：内存客户端、无写入（Windows 宿主对非空集合写盘段错误，此处不落盘）。
    """
    import chromadb

    client = chromadb.EphemeralClient()
    try:
        client.delete_collection(name="definitely_absent_rag_collection")
    except NotFoundError:
        pass
    else:
        raise AssertionError(
            "删不存在的集合没抛 chromadb.errors.NotFoundError —— "
            "core/rag.py::index 的 `except NotFoundError` 前提已变，升级后首次索引会 500"
        )
    print("  [PASS] 真 chromadb：删不存在的集合抛 NotFoundError（窄化前提成立）")


def test_index_succeeds_when_collection_absent():
    """首次索引（集合不存在）：delete 抛 NotFoundError 被吞掉，index 照常建好、写入。

    变异：把 `except NotFoundError` 换成别的类型 → 异常逃逸，本条红。
    """
    client = _FakeClient()  # dict-backed：delete 一个不存在的集合抛 NotFoundError
    eng = _make_engine(client=client, embed_dim=1024)
    eng.index("魏无羡坐在廊下说了很多话。" * 20, collection_name="text_new")

    assert client.deletes == ["text_new"], "首次索引确实走了一次 delete（删不存在的集合）"
    assert eng.collection_name == "text_new"
    assert eng.collection is not None and eng.collection.docs, "集合建好了但没写入切片"
    print("  [PASS] index() 集合不存在：delete 的 NotFoundError 被吞、照常建好并写入")


def test_index_delete_failure_propagates():
    """负控：delete 的真故障（非 NotFoundError）必须上抛 —— 窄化不等于「什么都吞」。

    变异：把 `except NotFoundError` 放宽回 `except Exception` → 本条红。
    """
    client = _FakeClient()
    client.delete_error = RuntimeError("permission denied")
    eng = _make_engine(client=client, embed_dim=1024)
    try:
        eng.index("正文" * 200, collection_name="text_locked")
    except RuntimeError as exc:
        assert "permission denied" in str(exc), exc
    else:
        raise AssertionError("delete_collection 的真故障被咽掉了（窄化过头）")
    print("  [PASS] index() delete 真故障上抛（窄化未把真故障也吞掉）")


# ── 调用路径回归：维度不符集合 → 三条 load_existing 调用路径都降级、都绝不 index() ──

def test_caller_indexing_service_degrades_no_index(caplog):
    """indexing_service（单卡 chat 主路径）：CollectionUnusableError → 特定 WARN + None 降级 + index 不触发。

    守卫必须在宽 except 之前：维度不符是确定性不可用，要和瞬时 build 故障分开记日志、
    也不落进任何可能触发 index() 的兜底（此处 _get_or_build_rag 顺序执行 index()，
    异常上抛即天然跳过；具体捕获只为区分日志与语义）。

    断言走 `caplog` 而不是 stdout（spec-119）：这条要求的意义正是「面板上看得见」，
    而容器 stdout 到不了面板。
    """
    from core.indexing_service import IndexingService

    svc = IndexingService(storage=MagicMock(), rag_config={"embedding_key": "k"})
    caplog.set_level(logging.WARNING)
    with patch("core.indexing_service.RAGEngine") as cls:
        inst = MagicMock()
        inst.load_existing.side_effect = CollectionUnusableError(
            "dim 384 != 1024", stored_dim=384, expected_dim=1024)
        cls.return_value = inst
        got = svc.get_rag_for_session("t_unusable", "正文内容")
        assert got is None, "维度不符集合必须降级返回 None（不静默空、不重建）"
        inst.index.assert_not_called()
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "维度不符不可用" in log and "不自动重建" in log, \
        f"维度不符必须有专属可见 WARN（区分于瞬时故障）：{log!r}"
    assert "RAG build failed" not in log, f"维度不符不应误报成 build 失败：{log!r}"
    print("  [PASS] indexing_service 路径：None 降级、index 未调用、维度不符专属 WARN")


def test_caller_indexing_service_generic_error_still_degrades(caplog):
    """indexing_service：非维度普通故障仍走宽 except 降级 None（专属守卫不误伤，也不 500）。"""
    from core.indexing_service import IndexingService

    svc = IndexingService(storage=MagicMock(), rag_config={"embedding_key": "k"})
    caplog.set_level(logging.WARNING)
    with patch("core.indexing_service.RAGEngine") as cls:
        inst = MagicMock()
        inst.load_existing.side_effect = RuntimeError("embed API 瞬断")
        cls.return_value = inst
        got = svc.get_rag_for_session("t_transient", "正文内容")
        assert got is None, "瞬时 build 故障也必须降级 None，不能 500"
        inst.index.assert_not_called()
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "RAG build failed (degraded)" in log, f"瞬时故障走原宽 except 日志：{log!r}"
    print("  [PASS] indexing_service 路径：非维度异常仍走 generic 降级日志（守卫未误伤）")


def test_caller_mcp_degrades_no_index():
    """mcp_server：CollectionUnusableError → 空检索降级 + index 不触发；
    非维度普通异常仍走旧 fallback（index）不被误伤。"""
    import mcp_server.server as mserver

    orig = dict(mserver._rag_by_text_id)
    mserver._rag_by_text_id.clear()
    try:
        with patch("core.rag.RAGEngine") as cls:
            inst = MagicMock()
            inst.load_existing.side_effect = CollectionUnusableError(
                "dim 384 != 1024", stored_dim=384, expected_dim=1024)
            cls.return_value = inst
            rag = mserver._rag_for_text_id("mcp_unusable", "有正文")
            assert rag is inst and inst.index.call_count == 0, "维度不符必须降级不 index"
        print("  [PASS] mcp_server 路径：CollectionUnusableError 降级、index 未调用")

        with patch("core.rag.RAGEngine") as cls:
            inst2 = MagicMock()
            inst2.load_existing.side_effect = RuntimeError("普通故障")
            cls.return_value = inst2
            rag2 = mserver._rag_for_text_id("mcp_genuine_err", "有正文")
            assert rag2 is inst2 and inst2.index.call_count == 1, "非维度异常仍应走旧 fallback"
        print("  [PASS] mcp_server 路径：非维度普通异常仍走 index fallback（守卫未误伤）")
    finally:
        mserver._rag_by_text_id.clear()
        mserver._rag_by_text_id.update(orig)


def _stub(storage, name: str, **kw) -> None:
    """给 storage 挂桩，并当场核对**真实 store** 有同名方法。

    桩名从「实际要桩什么」推出（赋值即声明），不另存一份硬编码清单 —— 清单自己会漂：
    缺陷19 把 group.py 的 get_group_session 改成 get_group_session_owned 后桩名没跟着改，
    测试只报「MagicMock 不是 awaitable」，指不到根因。名字若不在真实 store 上，本断言先炸。
    """
    from storage.sqlite_store import SQLiteStore

    assert hasattr(SQLiteStore, name), (
        f"桩 {name!r} 在真实 store 上不存在 —— 桩漂移了（方法改名/删除后测试没跟着改）")
    setattr(storage, name, AsyncMock(**kw))


def test_caller_group_rebuild_degrades_no_index(caplog):
    """web group.py _rebuild_group_session：CollectionUnusableError → 该卡跳过场景检索 + index 不触发。

    断言走 `caplog` 而不是 stdout（spec-119）：这条要求的意义正是「面板上看得见」，
    而容器 stdout 到不了面板。
    """
    from core.schema import CharacterCard, SpeakingStyle

    async def _run() -> None:
        import web.routers.group as grp

        card_json = CharacterCard(
            name="测角", personality_traits=["温柔"],
            speaking_style=SpeakingStyle(tone="轻", sentence_pattern="短句"),
            background="背景",
        ).model_dump_json()

        storage = MagicMock()
        _stub(storage, "get_group_session_owned", return_value={
            "user_id": "u1", "user_persona_type": "director",
            "user_persona_card_id": "", "user_persona_name": "", "user_persona_desc": "",
            "card_ids": ["c1", "c2"],
        })
        _stub(storage, "get_card_owned", side_effect=[
            {"id": "c1", "user_id": "u1", "text_id": "t_unusable_a", "card_json": card_json},
            {"id": "c2", "user_id": "u1", "text_id": "t_unusable_b", "card_json": card_json},
        ])
        _stub(storage, "get_text_owned", side_effect=[{"content": "正文A"}, {"content": "正文B"}])
        _stub(storage, "get_group_messages", return_value=[])

        caplog.set_level(logging.WARNING)

        async def _fake_llm(_user_id, _storage):
            return MagicMock()

        with (\
            patch("web.routers.group.get_user_llm", new=_fake_llm), \
            patch("deps.get_rag_config",
                  return_value={"chunk_size": 500, "chunk_overlap": 50, "top_k": 3}), \
            patch("deps.get_memory_manager", return_value=MagicMock()), \
            patch("core.rag.RAGEngine") as rag_cls, \
            patch("core.chat_engine.ChatEngine"), \
            patch("core.group_session.GroupSession")):
            inst = MagicMock()
            inst.load_existing.side_effect = CollectionUnusableError(
                "dim 384 != 1024", stored_dim=384, expected_dim=1024)
            rag_cls.return_value = inst
            group = await grp._rebuild_group_session("grp1", "u1", storage)
        assert group is not None
        assert inst.index.call_count == 0, "维度不符集合在 group 路径也绝不 index"
        log = "\n".join(r.getMessage() for r in caplog.records)
        assert "集合不可用" in log and "不自动重建" in log, \
            f"group 降级必须留下可见日志：{log!r}"

    asyncio.run(_run())
    print("  [PASS] group.py 路径：维度不符降级跳过场景检索、index 未调用、有可见日志")


def main() -> int:
    tests = [
        test_dim_mismatch_load_existing_raises,
        test_dim_match_load_existing_ok,
        test_missing_collection_load_existing_false,
        test_empty_collection_load_existing_false,
        test_get_collection_error_load_existing_logs_returns_false,
        test_embedder_dim_unknown_skips_validation,
        test_query_dim_error_raises_not_empty,
        test_query_with_emotion_dim_error_raises_not_empty,
        test_query_no_match_still_returns_empty,
        test_query_no_collection_returns_empty,
        test_real_chromadb_delete_absent_collection_raises_notfound,
        test_index_succeeds_when_collection_absent,
        test_index_delete_failure_propagates,
        test_caller_mcp_degrades_no_index,
    ]
    print("=== RAG UNUSABLE-COLLECTION REGRESSION ===\n")
    for t in tests:
        t()
        # 另三条（test_caller_indexing_service_* ×2、test_caller_group_rebuild_*）断言的是
        # **logging** 记录，靠 pytest 的 `caplog` fixture 拿（spec-119：落点从 print 改成模块
        # logger）—— 手跑这条入口没有 fixture，故只列在此处、不静默少跑：
    print("  [SKIP] test_caller_indexing_service_degrades_no_index / "
          "..._generic_error_still_degrades / ..._group_rebuild_degrades_no_index："
          "只在 pytest 下跑（用 caplog 断言日志）")
    print("\n=== ALL %d TESTS PASSED ===" % len(tests))
    print("load_existing: dimension-blindness fixed (mismatch raises, never silent-True)")
    print("query/query_with_emotion: failures raise, genuine no-match still returns []")
    return 0


if __name__ == "__main__":
    sys.exit(main())
