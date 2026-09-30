"""Indexing service — RAG collection lifecycle + per-session retrieval.

Two sides, kept apart on purpose:

* **Building** a collection (embedding the text) happens only in background tasks
  (`schedule_scene_index` / `schedule_text_reindex`); a book's text collection is
  built by one thread at a time (`_collection_lock`).
* **Sessions only load.** Each session gets its own `SessionRag`, which loads
  `scenes_{card_id}` first, then `text_{text_id}`, and re-reads before every
  retrieval until it is bound to its own card's scenes — so a collection finished
  in the background is picked up on the next turn without anyone pushing it.

This module is the ONLY place that imports SceneIndexer.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from core.rag import CollectionUnusableError, EvidenceHits, RAGEngine
from core.scene_indexer import SceneIndexer

logger = logging.getLogger(__name__)

# Dedup: prevent multiple concurrent background jobs for the same key
_scene_index_in_flight: set[str] = set()

# 原文集合同一时刻只许一个线程在建。`index()` 是「先删后建」，两个线程并发建同一集合，
# 一个的写入会落进被另一个删掉的集合里。去重表管不住这件事：它按卡去重，而同一本书的
# 两张卡、以及 `/reindex`，共用一个 `text_` 集合。
_collection_locks: dict[str, threading.Lock] = {}
_collection_locks_guard = threading.Lock()


def _collection_lock(name: str) -> threading.Lock:
    with _collection_locks_guard:
        lock = _collection_locks.get(name)
        if lock is None:
            lock = _collection_locks[name] = threading.Lock()
        return lock


class SessionRag:
    """一个会话自己的检索：只装载、不建；没绑上本卡场景集合之前，每次检索前重读。

    重读顺序 `scenes_{card_id}` → `text_{text_id}`。绑上本卡场景集合后不再重读。
    查询失败（集合被删了再建，旧句柄失效）时丢掉手里的引擎，下一次检索重读 ——
    失败本身照常抛给调用方，本轮检索记为 failed。

    对外只提供 `ContextEngine` 用到的 `query_with_emotion_ex`；
    `collection_name` 只读，供观测。
    """

    def __init__(
        self, service: "IndexingService", text_id: str, card_id: str,
        embedding_key: str, embedding_region: str,
    ) -> None:
        self._service = service
        self._text_id = text_id
        self._card_id = card_id
        self._key = embedding_key
        self._region = embedding_region
        self._engine: RAGEngine | None = None

    @property
    def collection_name(self) -> str | None:
        return self._engine.collection_name if self._engine is not None else None

    def _bound_to_own_scenes(self) -> bool:
        return bool(self._card_id) and self.collection_name == f"scenes_{self._card_id}"

    def _refresh(self) -> None:
        if self._bound_to_own_scenes():
            return
        self._engine = self._service._load_session_rag(
            self._text_id, self._card_id, self._key, self._region)

    def query_with_emotion_ex(self, query_text: str, **kwargs: Any) -> EvidenceHits:
        self._refresh()
        if self._engine is None:
            return EvidenceHits([])
        try:
            return self._engine.query_with_emotion_ex(query_text, **kwargs)
        except CollectionUnusableError:
            self._engine = None
            raise


class IndexingService:
    """Owns RAG collection lifecycle; hands each session its own `SessionRag`."""

    def __init__(
        self,
        storage: Any,
        rag_config: dict[str, Any],
    ) -> None:
        self._storage = storage
        self._rag_config = rag_config

    def _new_rag(self, embedding_key: str, embedding_region: str) -> RAGEngine:
        """A fresh engine for this user's key. (sync)

        每次都新造，**不缓存引擎对象**：引擎持有「当前指向哪个集合」这个可变状态，
        共享它就等于让所有会话共用一个指针（场景预索引会把它改指到某张卡的
        `scenes_*`，同一本书的其他卡跟着串过去）。可共享的部分在更低一层已经共享：
        chroma 客户端按持久化路径共用一个 System，嵌入函数按「地域 + key 指纹」缓存
        （`core/embeddings.py::create_safe_embedding_fn`）。
        """
        rag_config = dict(self._rag_config)
        rag_config["embedding_key"] = embedding_key
        rag_config["embedding_region"] = embedding_region
        return RAGEngine(rag_config)

    def _load_session_rag(
        self, text_id: str, card_id: str, embedding_key: str, embedding_region: str,
    ) -> RAGEngine | None:
        """只装载、不建：`scenes_{card_id}` 优先，其次 `text_{text_id}`，都没有 → None。(sync)

        本地 chroma 读，不走网络、不嵌入。维度不符（`CollectionUnusableError`）照常上抛。
        """
        names = [f"scenes_{card_id}"] if card_id else []
        names.append(f"text_{text_id}")
        rag = self._new_rag(embedding_key, embedding_region)
        for name in names:
            if rag.load_existing(name):
                return rag
        return None

    def get_rag_for_session(
        self,
        text_id: str,
        *,
        card_id: str,
        embedding_key: str,
        embedding_region: str,
    ) -> SessionRag | None:
        """给一个会话它自己的检索。不做 IO：第一次检索时才装载。

        没有 embedding key（用户没配自己的百炼 key）时直接返回 None：检索只用用户自己的
        key，不回落全局，也不拿空 key 去试一次再靠失败降级。
        """
        if not embedding_key:
            logger.info("RAG skipped: user has no embedding key (text_id=%s)", text_id)
            return None
        return SessionRag(self, text_id, card_id, embedding_key, embedding_region)

    def _build_text_collection(
        self,
        text_id: str,
        text: str,
        all_characters: list[dict[str, Any]] | None,
        *,
        embedding_key: str,
        embedding_region: str,
        rebuild: bool = False,
    ) -> RAGEngine:
        """后台专用：私有引擎，装载 `text_{id}`；没有（或要求重建）就整本嵌入。(sync)

        持 `text_{id}` 的集合锁：两张卡同时调度、或重建与调度撞上，都只会一个一个来；
        后到的拿到锁时集合已建好，直接复用。
        """
        col_name = f"text_{text_id}"
        rag = self._new_rag(embedding_key, embedding_region)
        with _collection_lock(col_name):
            if not rebuild and rag.load_existing(col_name):
                return rag
            _t = time.time()
            rag.index(text, collection_name=col_name, all_characters=all_characters)
            logger.info("RAG text collection built: text_id=%s in %.1fs", text_id, time.time() - _t)
        return rag

    def _scene_index_job(
        self, text_id: str, card_id: str, content: str, char_name: str,
        all_characters: list[dict[str, Any]] | None,
        embedding_key: str, embedding_region: str,
    ) -> None:
        """后台场景预索引的整个作业（建原文集合 → 建本卡场景集合）。(sync)"""
        rag = self._build_text_collection(
            text_id, content, all_characters,
            embedding_key=embedding_key, embedding_region=embedding_region,
        )
        if rag.collection is None:
            return
        # rag 是本作业私有的，`index_scenes` 把它改指到场景集合不影响任何会话。
        # 场景集合不另加锁：它按卡命名，同一张卡的作业已由去重键（`scenes_{card_id}`，
        # 线程结束才释放）挡成一个一个来。
        SceneIndexer().index_scenes(content, rag, char_name, collection_name=f"scenes_{card_id}")

    def _run_in_background(self, dedup_key: str, label: str, job, *args) -> None:
        """把 `job` 放进工作线程跑完；去重键在**线程真的结束之后**才释放。

        不套 `wait_for` 超时：它取消不了工作线程，只会让去重键提前释放，
        放进第二个作业去和仍在写的第一个撞（嵌入调用本身各有超时）。
        """
        if dedup_key in _scene_index_in_flight:
            return
        _scene_index_in_flight.add(dedup_key)

        async def _bg():
            _t = time.time()
            try:
                await asyncio.to_thread(job, *args)
                logger.info("%s done in %.1fs", label, time.time() - _t)
            except CollectionUnusableError as exc:
                # 维度不符集合：确定性不可用，跳过（非致命），不自动重建。
                logger.warning("%s skipped: collection unusable (not rebuilt): %s", label, exc)
            except Exception as exc:
                logger.warning("%s failed (non-fatal): %s", label, exc, exc_info=True)
            finally:
                _scene_index_in_flight.discard(dedup_key)

        asyncio.create_task(_bg())

    def schedule_scene_index(
        self,
        text_id: str,
        card_id: str,
        content: str,
        char_name: str,
        *,
        all_characters: list[dict[str, Any]] | None = None,
        embedding_key: str = "",
        embedding_region: str = "",
    ) -> None:
        """Fire-and-forget scene index. Dedup: skips if same card already indexing.

        没有 embedding key 时不调度（理由同 `get_rag_for_session`）。
        """
        if not embedding_key:
            logger.info("Scene index skipped: user has no embedding key (card_id=%s)", card_id)
            return
        self._run_in_background(
            f"scenes_{card_id}", f"Scene index card_id={card_id}", self._scene_index_job,
            text_id, card_id, content, char_name, all_characters,
            embedding_key, embedding_region,
        )

    def schedule_text_reindex(
        self,
        text_id: str,
        content: str,
        *,
        all_characters: list[dict[str, Any]] | None,
        embedding_key: str,
        embedding_region: str,
    ) -> None:
        """Fire-and-forget：按新名单重建 `text_{id}`（角色标记）。

        不碰任何会话：绑在原文集合上的会话下一次检索会自己重读到新集合。
        """
        self._run_in_background(
            f"text_{text_id}", f"Text reindex text_id={text_id}",
            lambda: self._build_text_collection(
                text_id, content, all_characters,
                embedding_key=embedding_key, embedding_region=embedding_region,
                rebuild=True,
            ),
        )
