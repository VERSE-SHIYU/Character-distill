"""Indexing service — RAG collection lifecycle + per-session retrieval.

Two sides, kept apart on purpose:

* **Building** a collection (embedding the text) happens only in background jobs
  (`schedule_scene_index` / `schedule_text_reindex`). Every build goes through
  `_builds.building(name)`: one thread per collection name at a time, and when it
  ends the build generation moves on.
* **Sessions only load.** Each session gets its own `SessionRag`, which loads the
  first usable of `scenes_{card_id}` → `text_{text_id}`, and loads again only when
  a build has finished since (or its last query failed) — so a collection finished
  in the background is picked up on the next turn without anyone pushing it.

This module is the ONLY place that imports SceneIndexer.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from core.rag import CollectionUnusableError, EvidenceHits, RAGEngine
from core.scene_indexer import SceneIndexer

logger = logging.getLogger(__name__)


class CollectionBuilding(RuntimeError):
    """检索要用的集合此刻正在后台建（先删后建，建完之前查不到东西）。

    与「真没有集合」分开：没有 → 本轮检索是空；正在建 → 本轮检索记为失败。
    与 `CollectionUnusableError` 分开：那是确定性不可用，这是建完就好。
    """


class _CollectionBuilds:
    """进程内的集合构建登记：谁在建哪个集合、到现在一共建完过几次。

    * `building(name)`：建集合时持有。同名集合同一时刻只许一个线程在建 ——
      `index()` / `index_scenes` 都是「先删后建」，两个线程并发建同一集合，一个的写入
      会落进被另一个删掉的集合里。
    * `is_building(name)`：会话装载时区分「此刻正在建」与「不存在」。
    * `generation`：每结束一次构建（成功或失败）加一。会话据此判断「上次装载之后
      有没有集合变过」，没变就不重读。

    只管本进程。`text_*` / `scenes_*` 只有 web 进程会建（MCP 只读，见
    `mcp_server/server.py::_rag_for_text_id`）。
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._generation = 0

    def _lock(self, name: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(name)
            if lock is None:
                lock = self._locks[name] = threading.Lock()
            return lock

    @contextlib.contextmanager
    def building(self, name: str) -> Iterator[None]:
        with self._lock(name):
            try:
                yield
            finally:
                with self._guard:
                    self._generation += 1

    def is_building(self, name: str) -> bool:
        return self._lock(name).locked()

    @property
    def generation(self) -> int:
        with self._guard:
            return self._generation


_builds = _CollectionBuilds()

# 后台作业去重：同一个键同一时刻只跑一个作业；跑着的时候再来一个，记为「待跑」，
# 前一个结束后拿最新的参数再跑一次（后到的请求带着更新的输入，不能丢）。
# 两张表只在事件循环线程上读写。
_scene_index_in_flight: set[str] = set()
_pending_jobs: dict[str, tuple[str, Callable[..., Any], tuple[Any, ...]]] = {}


def _session_candidates(text_id: str, card_id: str) -> list[str]:
    """会话检索按顺序尝试的集合：本卡场景集合优先，其次原文集合。"""
    return ([f"scenes_{card_id}"] if card_id else []) + [f"text_{text_id}"]


class SessionRag:
    """一个会话自己的检索：只装载、不建。

    装载结果（可用的引擎 / 真没有 / 不可用的原因）一直用到下面两件事之一发生：
    某个集合建完了（`_builds.generation` 变了），或本会话的一次查询失败了
    （集合被删了再建，旧句柄失效）。两者都在下一次检索前重读。

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
        self._unavailable: Exception | None = None
        self._loaded_at: int | None = None  # 上次装载时的构建代数；None = 下次检索前必须装载

    @property
    def collection_name(self) -> str | None:
        return self._engine.collection_name if self._engine is not None else None

    def _refresh(self) -> None:
        generation = _builds.generation  # 先读代数：装载期间有构建结束，下一轮会再读
        if self._loaded_at == generation:
            return
        self._engine, self._unavailable, self._loaded_at = None, None, None
        self._engine, self._unavailable = self._service._load_session_rag(
            self._text_id, self._card_id, self._key, self._region)
        self._loaded_at = generation

    def query_with_emotion_ex(self, query_text: str, **kwargs: Any) -> EvidenceHits:
        self._refresh()
        if self._unavailable is not None:
            raise self._unavailable.with_traceback(None)
        if self._engine is None:
            return EvidenceHits([])
        try:
            return self._engine.query_with_emotion_ex(query_text, **kwargs)
        except CollectionUnusableError:
            self._loaded_at = None
            raise


class IndexingService:
    """Owns RAG collection lifecycle; hands each session its own `SessionRag`."""

    def __init__(self, rag_config: dict[str, Any]) -> None:
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
    ) -> tuple[RAGEngine | None, Exception | None]:
        """只装载、不建：取 `_session_candidates` 里第一个可用的集合。(sync)

        一个候选不可用（正在建 / 维度不符）就看下一个 —— 本卡场景集合坏了，原文集合
        照样能用。返回 `(引擎, None)`；一个集合都没有 → `(None, None)`，本轮检索为空；
        有集合但都不可用 → `(None, 第一个原因)`，本轮检索记为失败。

        本地 chroma 读，不走网络、不嵌入。
        """
        rag = self._new_rag(embedding_key, embedding_region)
        unavailable: Exception | None = None
        for name in _session_candidates(text_id, card_id):
            if _builds.is_building(name):
                unavailable = unavailable or CollectionBuilding(f"集合 {name} 正在建")
                continue
            try:
                if rag.load_existing(name):
                    return rag, None
            except CollectionUnusableError as exc:
                logger.warning("RAG collection %s unusable, skipped: %s", name, exc)
                unavailable = unavailable or exc
        return None, unavailable

    def get_rag_for_session(
        self,
        text_id: str,
        *,
        card_id: str,
        embedding_key: str,
        embedding_region: str,
    ) -> SessionRag | None:
        """给一个会话它自己的检索。不做 IO：第一次检索时才装载。

        没有原文（独立卡片）或没有 embedding key（用户没配自己的百炼 key）时返回 None：
        检索只用用户自己的 key，不回落全局，也不拿空 key 去试一次再靠失败降级。
        """
        if not text_id:
            return None
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

        已建好的集合在登记之外直接装载，不把会话挡成「正在建」；要建时进登记，
        拿到锁后再看一次 —— 两张卡同时调度时，后到的那个直接复用。
        """
        col_name = f"text_{text_id}"
        rag = self._new_rag(embedding_key, embedding_region)
        if not rebuild and rag.load_existing(col_name):
            return rag
        with _builds.building(col_name):
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
        name = f"scenes_{card_id}"
        with _builds.building(name):
            SceneIndexer().index_scenes(content, rag, char_name, collection_name=name)

    def _run_in_background(
        self, dedup_key: str, label: str, job: Callable[..., Any], *args: Any,
    ) -> None:
        """把 `job` 放进工作线程跑完；去重键在**线程真的结束之后**才释放。

        同一个键已有作业在跑时不丢弃这次调度：记为待跑（只留最新一次的参数），
        前一个结束后接着跑。不套 `wait_for` 超时：它取消不了工作线程，只会让去重键
        提前释放（嵌入调用本身各有超时）。
        """
        if dedup_key in _scene_index_in_flight:
            _pending_jobs[dedup_key] = (label, job, args)
            return
        _scene_index_in_flight.add(dedup_key)

        async def _bg() -> None:
            nxt: tuple[str, Callable[..., Any], tuple[Any, ...]] | None = (label, job, args)
            try:
                while nxt is not None:
                    await _run_job(*nxt)
                    nxt = _pending_jobs.pop(dedup_key, None)
            finally:
                _pending_jobs.pop(dedup_key, None)
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
        """Fire-and-forget scene index（同一张卡的作业一个接一个跑）。

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

        不碰任何会话：绑在原文集合上的会话，重建期间本轮检索记为失败，建完后下一轮
        自己重读到新集合。
        """
        job = functools.partial(
            self._build_text_collection,
            embedding_key=embedding_key, embedding_region=embedding_region, rebuild=True,
        )
        self._run_in_background(
            f"text_{text_id}", f"Text reindex text_id={text_id}", job,
            text_id, content, all_characters,
        )


async def _run_job(label: str, job: Callable[..., Any], args: tuple[Any, ...]) -> None:
    """在工作线程里跑一个后台作业；失败只记日志（后台作业不影响任何请求）。"""
    _t = time.time()
    try:
        await asyncio.to_thread(job, *args)
        logger.info("%s done in %.1fs", label, time.time() - _t)
    except CollectionUnusableError as exc:
        # 维度不符集合：确定性不可用，跳过（非致命），不自动重建。
        logger.warning("%s skipped: collection unusable (not rebuilt): %s", label, exc)
    except Exception as exc:
        logger.warning("%s failed (non-fatal): %s", label, exc, exc_info=True)
