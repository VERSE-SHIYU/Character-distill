"""把持久化的引擎状态（好感 + 本场记录）装回引擎：一对一存档一个入口，群里的人物一个入口。

- `restore_engine_state`：一对一的两个恢复路径（`web/routers/chat.py` 自动续聊、
  `web/routers/history.py` 打开存档）共用 —— 以前两处各抄一份好感恢复，本场记录不再抄第三份。
- `restore_group_engine_state`：群会话发消息前（单发、广播）共用，同一个理由。
属主校验都在调用方完成。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.affinity_service import read_persisted_affinity
from core.history_digest import Digests
from core.session_record import SessionRecord

logger = logging.getLogger(__name__)


async def restore_engine_state(engine: Any, session_id: str, storage: Any) -> None:
    """把存档的好感和本场记录装回引擎。各自失败只打 warning，互不拖累。"""
    try:
        data, source = await read_persisted_affinity(session_id, storage)
        # 走 to_thread：升级分支里 `load_affinity` 会调 `_save_affinity_state`，那是
        # `submit_to_main_loop(wait=True)` —— 在本 loop 上直接调就是主 loop 等自己。
        if source == 'state':
            await asyncio.to_thread(engine.load_affinity, data, initialized=True)
        else:
            # legacy（已评估旧格式）或全新默认行 → load_affinity 自行判定升级/初值计算
            await asyncio.to_thread(engine.load_affinity, data)
    except Exception as exc:
        logger.warning("Restore affinity failed (non-fatal): %s", exc, exc_info=True)
    try:
        raw = await storage.load_session_record_unscoped(session_id)
        engine.load_session_record(SessionRecord.from_persist(raw))
    except Exception as exc:
        logger.warning("Restore session record failed (non-fatal): %s", exc, exc_info=True)
    try:
        raw = await storage.load_history_digest_unscoped(session_id)
        engine.load_history_digest(Digests.from_persist(raw))
    except Exception as exc:
        logger.warning("Restore history digest failed (non-fatal): %s", exc, exc_info=True)


async def restore_group_engine_state(engine: Any, group_id: str, card_id: str, storage: Any) -> None:
    """把群里一个人物的好感和本场记录装回引擎。各自失败只打 warning，互不拖累。

    好感没评估过（没有那一行）就不装，引擎保持初值 —— 与原先 `GroupSession` 里的写法一致。"""
    try:
        prev = await storage.get_group_affinity(group_id, card_id)
        if prev:
            engine.load_affinity(prev)
    except Exception as exc:
        logger.warning("Load group affinity failed (card=%s): %s", card_id, exc, exc_info=True)
    try:
        raw = await storage.load_group_session_record_unscoped(group_id, card_id)
        engine.load_session_record(SessionRecord.from_persist(raw))
    except Exception as exc:
        logger.warning("Restore group session record failed (card=%s): %s", card_id, exc, exc_info=True)
