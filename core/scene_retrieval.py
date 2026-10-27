"""聊天取原文场景的**唯一**调用入口。

``ContextEngine._scene_items`` 与召回评测脚本都走这里，测评量的才是聊天实际在用的
那次调用（按 ``SCENE_TOKEN_BUDGET`` 填块 + 重排）。以后改检索做法（候选池大小、
重排模型、原句双路…）只改这一处。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.rag import EvidenceHits
    from core.schema import RetrievalWindow

# 场景原文注入的总 token 预算（fill-to-budget）。出处：2601.14123 §3 F4 / 表 1
# （事实型问答约 2.5k token 最好；§2：固定注入总量才能在不同块大小间公平比较）。
# 文献值，未在中文角色扮演上验证。全仓唯一定义。
SCENE_TOKEN_BUDGET = 2500


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
        token_budget=SCENE_TOKEN_BUDGET,
        window=window,
    )
