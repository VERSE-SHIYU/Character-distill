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
import contextlib
import io
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
    """Minimal chroma Collection stand-in: count / peek / query with scripted behavior."""

    def __init__(self, count=0, dim=None, query_error=None, query_result=None, peek_error=None):
        self.name = "fake"
        self._count = count
        self._dim = dim
        self._query_error = query_error
        self._query_result = query_result
        self._peek_error = peek_error

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


class _FakeClient:
    """Minimal chroma PersistentClient stand-in: get_collection returns a col or raises."""

    def __init__(self, target):
        self._target = target

    def get_collection(self, name=None, embedding_function=None):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


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


# ── 调用路径回归：维度不符集合 → 三条 load_existing 调用路径都降级、都绝不 index() ──

def test_caller_indexing_service_degrades_no_index():
    """indexing_service（单卡 chat 主路径）：CollectionUnusableError → None 降级 + index 不触发。"""
    from core.indexing_service import IndexingService

    svc = IndexingService(storage=MagicMock(), rag_config={"embedding_key": "k"})
    buf = io.StringIO()
    with patch("core.indexing_service.RAGEngine") as cls, contextlib.redirect_stdout(buf):
        inst = MagicMock()
        inst.load_existing.side_effect = CollectionUnusableError(
            "dim 384 != 1024", stored_dim=384, expected_dim=1024)
        cls.return_value = inst
        got = svc.get_rag_for_session("t_unusable", "正文内容")
        assert got is None, "维度不符集合必须降级返回 None（不静默空、不重建）"
        inst.index.assert_not_called()
    assert "RAG build failed (degraded)" in buf.getvalue(), "降级必须留下可见日志"
    print("  [PASS] indexing_service 路径：返回 None 降级、index 未调用、有可见日志")


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


def test_caller_group_rebuild_degrades_no_index():
    """web group.py _rebuild_group_session：CollectionUnusableError → 该卡跳过场景检索 + index 不触发。"""
    from core.schema import CharacterCard, SpeakingStyle

    async def _run() -> None:
        import web.routers.group as grp

        card_json = CharacterCard(
            name="测角", personality_traits=["温柔"],
            speaking_style=SpeakingStyle(tone="轻", sentence_pattern="短句"),
            background="背景",
        ).model_dump_json()

        storage = MagicMock()
        storage.get_group_session = AsyncMock(return_value={
            "user_id": "u1", "user_persona_type": "director",
            "user_persona_card_id": "", "user_persona_name": "", "user_persona_desc": "",
            "card_ids": ["c1", "c2"],
        })
        storage.get_user_api_config = AsyncMock(return_value={})
        storage.get_card = AsyncMock(side_effect=[
            {"id": "c1", "user_id": "u1", "text_id": "t_unusable_a", "card_json": card_json},
            {"id": "c2", "user_id": "u1", "text_id": "t_unusable_b", "card_json": card_json},
        ])
        storage.get_text = AsyncMock(side_effect=[{"content": "正文A"}, {"content": "正文B"}])
        storage.get_group_messages = AsyncMock(return_value=[])

        buf = io.StringIO()

        async def _fake_llm(_user_id, _storage):
            return MagicMock()

        with (\
            patch("web.routers.group.get_user_llm", new=_fake_llm), \
            patch("deps.get_rag_config",
                  return_value={"chunk_size": 500, "chunk_overlap": 50, "top_k": 3}), \
            patch("deps.get_memory_manager", return_value=MagicMock()), \
            patch("core.rag.RAGEngine") as rag_cls, \
            patch("core.chat_engine.ChatEngine"), \
            patch("core.group_session.GroupSession"), \
            contextlib.redirect_stdout(buf)):
            inst = MagicMock()
            inst.load_existing.side_effect = CollectionUnusableError(
                "dim 384 != 1024", stored_dim=384, expected_dim=1024)
            rag_cls.return_value = inst
            group = await grp._rebuild_group_session("grp1", "u1", storage)
        assert group is not None
        assert inst.index.call_count == 0, "维度不符集合在 group 路径也绝不 index"
        assert "集合不可用" in buf.getvalue() and "不自动重建" in buf.getvalue(), \
            "group 降级必须留下可见日志"

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
        test_caller_indexing_service_degrades_no_index,
        test_caller_mcp_degrades_no_index,
        test_caller_group_rebuild_degrades_no_index,
    ]
    print("=== RAG UNUSABLE-COLLECTION REGRESSION ===\n")
    for t in tests:
        t()
    print("\n=== ALL %d TESTS PASSED ===" % len(tests))
    print("load_existing: dimension-blindness fixed (mismatch raises, never silent-True)")
    print("query/query_with_emotion: failures raise, genuine no-match still returns []")
    return 0


if __name__ == "__main__":
    sys.exit(main())
