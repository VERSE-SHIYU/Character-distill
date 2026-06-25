"""Indexing service — isolated RAG / scene-index logic, fire-and-forget.

All embedding calls are wrapped in timeouts; failures degrade silently.
This module is the ONLY place that imports SceneIndexer.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.rag import RAGEngine
from core.scene_indexer import SceneIndexer

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
        cache_key = f"{text_id}:{embedding_key}"
        cached = self._text_rag_cache.get(cache_key)
        if cached is not None:
            return cached
        col_name = f"text_{text_id}"
        rag_config = dict(self._rag_config)
        if embedding_key:
            rag_config["embedding_key"] = embedding_key
            rag_config["embedding_region"] = embedding_region
        rag = RAGEngine(rag_config)
        if rag.load_existing(col_name):
            self._text_rag_cache[cache_key] = rag
            return rag
        rag.index(text, collection_name=col_name, all_characters=all_characters)
        self._text_rag_cache[cache_key] = rag
        return rag

    def get_rag_for_session(
        self,
        text_id: str,
        content: str,
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
        except Exception as exc:
            print(f"[IndexingService] RAG build failed (degraded): {exc}")
            return None

    def schedule_scene_index(
        self,
        text_id: str,
        card_id: str,
        content: str,
        char_name: str,
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
            except Exception as exc:
                print(f"[IndexingService] Scene index failed (non-fatal): {exc}")
            finally:
                _scene_index_in_flight.discard(dedup_key)

        asyncio.create_task(_bg())
