"""恢复一个一对一存档的引擎状态：好感 + 本场记录。

两个恢复路径（`web/routers/chat.py` 自动续聊、`web/routers/history.py` 打开存档）共用这一个
入口 —— 以前两处各抄一份好感恢复，本场记录不再抄第三份。属主校验在调用方完成。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.affinity_service import read_persisted_affinity
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
