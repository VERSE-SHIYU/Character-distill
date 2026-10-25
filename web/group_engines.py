"""群聊成员引擎：依赖准备、构造、建群时选的阶段 —— 建群与恢复群共用的**唯一**一处。

路由（`routers/group.py`）只管 HTTP：鉴权、成员规则、落库、响应。造引擎要什么、
阶段怎么校验、引擎怎么构造，都在这里；以后加一个构造参数只改这一个文件。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from core.arc_view import valid_phase

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroupEngineDeps:
    """同一个用户在群里所有引擎共用的依赖：模型、记忆视图、检索服务、嵌入配置、存储。"""
    llm: Any
    memory_manager: Any
    indexing_service: Any
    emb: Any
    storage: Any


async def group_engine_deps(user_id: str, per_user_llm: Any, storage: Any, *, where: str) -> GroupEngineDeps:
    """群引擎的依赖**只在这里**准备一次。`where` 只进日志（建群 / 恢复哪个群）。"""
    from deps import get_indexing_service, get_user_memory
    from web.user_keys import require_embedding

    emb = await require_embedding(user_id, storage)
    memory_manager = await asyncio.to_thread(
        get_user_memory, user_id, per_user_llm, emb.key, emb.region,
    )
    return GroupEngineDeps(per_user_llm, memory_manager, get_indexing_service(), emb, storage)


def ai_member_ids(card_ids: list[str], persona_type: str, persona_card_id: str) -> list[str]:
    """「谁是 AI 成员」的后端**唯一**一处：用户扮演的那张卡由用户说话，不算 AI 成员；其余原序。

    与前端 `utils/groupMembers.aiMemberIds` 同一条规则（两侧各一处）。导演 / 路人身份
    全员都是 AI 成员（`persona_card_id` 不生效）。
    """
    return [
        cid for cid in card_ids
        if not (persona_type == "character" and cid == persona_card_id)
    ]


def resolve_member_phases(ai_cards: dict[str, Any], requested: dict[str, int]) -> dict[str, int]:
    """建群时的阶段规则（唯一一处）：只收 AI 成员的、`valid_phase` 认可的阶段号。

    `ai_cards` 是卡 id → 卡（扮演的卡调用方已剔除）。无效 / 越界 / 卡不可选 → 不收
    （= 最后阶段）。请求里多出来的卡 id 自然不进。与一对一建会话同一条规则（`valid_phase`）。
    """
    out: dict[str, int] = {}
    for card_id, card in ai_cards.items():
        k = valid_phase(card, requested.get(card_id))
        if k is not None:
            out[card_id] = k
    return out


def build_group_engine(
    gdeps: GroupEngineDeps, card: Any, *,
    card_id: str, text_id: str, group_id: str, arc_phase: int | None,
) -> Any:
    """造一个群成员引擎 —— 群里**唯一**的构造点。

    `arc_phase`：建群时来自 `resolve_member_phases`，恢复群时是库里那一列的值。投影只在
    `ChatEngine` 构造里做一次（`project_card`），None / 越界 → 最后阶段（与一对一恢复同口径）。
    """
    from core.chat_engine import ChatEngine

    rag = gdeps.indexing_service.get_rag_for_session(
        text_id, card_id=card_id,
        embedding_key=gdeps.emb.key, embedding_region=gdeps.emb.region,
    ) if gdeps.indexing_service else None
    return ChatEngine(
        gdeps.llm, rag, card,
        arc_phase=arc_phase,
        memory_manager=gdeps.memory_manager,
        card_id=card_id,
        storage=gdeps.storage,
        session_id="",          # 群引擎没有一对一会话
        group_id=group_id,
        is_new_session=True,    # 群好感随后由 load_affinity(prev) 覆盖
    )
