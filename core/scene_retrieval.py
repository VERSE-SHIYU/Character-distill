"""聊天取原文场景的**唯一**调用入口。

``ContextEngine._scene_items`` 与召回评测脚本都走这里，测评量的才是聊天实际在用的
那次调用（``top_k=3`` + 重排）。以后改检索做法（候选池大小、重排模型、原句双路…）
只改这一处。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.rag import EvidenceHits
    from core.schema import RetrievalWindow


def scene_hits(
    rag: Any,
    query: str,
    *,
    character_name: str | None,
    window: "RetrievalWindow | None",
) -> "EvidenceHits":
    return rag.query_scenes(
        query,
        character_name=character_name,
        top_k=3,
        window=window,
    )
