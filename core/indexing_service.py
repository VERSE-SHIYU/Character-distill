"""Indexing service — isolated RAG / scene-index logic, fire-and-forget.

All embedding calls are wrapped in timeouts; failures degrade silently.
This module is the ONLY place that imports SceneIndexer.
"""

from __future__ import annotations

import logging

import asyncio
from typing import Any

from core.embeddings import key_fingerprint
from core.rag import CollectionUnusableError, RAGEngine
from core.scene_indexer import SceneIndexer

logger = logging.getLogger(__name__)

# Dedup: prevent multiple concurrent scene-index tasks for the same card
_scene_index_in_flight: set[str] = set()


class IndexingService:
    """Owns text-level RAG cache; provides fire-and-forget scene indexing."""

    def __init__(
        self,
        storage: Any,
        rag_config: dict[str, Any],
    ) -> None:
        self._storage = storage
        self._rag_config = rag_config
        self._text_rag_cache: dict[str, RAGEngine] = {}

    def _get_or_build_rag(
        self,
        text_id: str,
        text: str,
        all_characters: list[dict[str, Any]] | None = None,
        embedding_key: str = "",
        embedding_region: str = "",
    ) -> RAGEngine:
        """Return cached text-level RAG, or build + cache. (sync)"""
        import time
        # 身份必须用整个 key（前 8 位里有 5 位是随机字符，两个 key 会撞成同一个
        # embedder，后者的请求记在前者账上），但身份不能进日志 —— 故只留指纹。
        cache_key = f"{text_id}:{key_fingerprint(embedding_key)}"
        cached = self._text_rag_cache.get(cache_key)
        if cached is not None:
            print(f"[RAG] cache HIT {text_id}")
            return cached
        print(f"[RAG] cache MISS {text_id}, building...")
        _t = time.time()
        col_name = f"text_{text_id}"
        rag_config = dict(self._rag_config)
        if embedding_key:
            rag_config["embedding_key"] = embedding_key
            rag_config["embedding_region"] = embedding_region
        rag = RAGEngine(rag_config)
        if rag.load_existing(col_name):
            self._text_rag_cache[cache_key] = rag
            print(f"[RAG] loaded existing in {time.time()-_t:.1f}s")
            return rag
        rag.index(text, collection_name=col_name, all_characters=all_characters)
        self._text_rag_cache[cache_key] = rag
        print(f"[RAG] built in {time.time()-_t:.1f}s")
        return rag

    def get_rag_for_session(
        self,
        text_id: str,
        content: str,
        *,
        all_characters: list[dict[str, Any]] | None = None,
        embedding_key: str = "",
        embedding_region: str = "",
    ) -> RAGEngine | None:
        """Lazy-load RAG; 60s timeout, returns None on failure."""
        try:
            return self._get_or_build_rag(
                text_id, content, all_characters,
                embedding_key=embedding_key, embedding_region=embedding_region,
            )
        except CollectionUnusableError as exc:
            # 集合维度与当前 embedder 不符（如迁移前 384 旧集合）：确定性不可用，
            # 只降级记日志、不 index() 重建 —— 与 group.py / mcp_server 同语义。
            # 不吞进上面宽 except：否则会和瞬时 build 故障混成一个误导性日志。
            logger.warning(
                "text_%s 集合维度不符不可用，降级返回 None（不自动重建）：%s",
                text_id, exc,
            )
            return None
        except Exception as exc:
            logger.warning("RAG build failed (degraded): %s", exc, exc_info=True)
            return None

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
        """Fire-and-forget scene index. Dedup: skips if same card already indexing."""
        dedup_key = f"scenes_{card_id}"
        if dedup_key in _scene_index_in_flight:
            return
        _scene_index_in_flight.add(dedup_key)

        async def _bg():
            try:
                rag = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._get_or_build_rag,
                        text_id, content, all_characters,
                        embedding_key=embedding_key, embedding_region=embedding_region,
                    ),
                    timeout=120,
                )
                if rag.collection:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            SceneIndexer().index_scenes,
                            content, rag, char_name,
                            collection_name=f"scenes_{card_id}",
                        ),
                        timeout=180,
                    )
            except CollectionUnusableError as exc:
                # 维度不符集合：确定性不可用，跳过场景预索引（非致命），同样不 index() 重建。
                logger.warning(
                    "text_%s 集合维度不符不可用，跳过场景预索引（非致命，不重建）：%s",
                    text_id, exc,
                )
            except Exception as exc:
                logger.warning("Scene index failed (non-fatal): %s", exc, exc_info=True)
            finally:
                _scene_index_in_flight.discard(dedup_key)

        asyncio.create_task(_bg())
