# -*- coding: utf-8 -*-
"""回忆区的唯一入口：读缓存 → 没有就标一次 → 能缓存的写回。与 `core/character_roster.py`
的名单入口同一形状。

回忆区是**作品的属性**：同一本书换角色蒸、用存下的草稿重新转卡，都用同一份。缓存键是
（text_id、属主、`RETRO_VERSION`），版本不符即当无缓存。

**失败不挡蒸馏**：回忆区是位置核对的辅助信息，标不了时下游按「未标」处理、行为同今天。
读缓存、调模型、写回任何一步出错都只打 warning 并返回「未标」（偶发，不缓存）。

**前置条件**：调用方已用 `get_text_owned(text_id, user_id)` 做过属主校验（同名单入口）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.retro_regions import RETRO_VERSION, RetroRegions

logger = logging.getLogger(__name__)


async def resolve_retro_regions(storage: Any, distiller: Any, text_id: str, user_id: str,
                                content: str) -> RetroRegions:
    try:
        cached = await storage.get_retro_regions_owned(text_id, user_id, version=RETRO_VERSION)
        if cached:
            return RetroRegions.from_json(cached)
        regions = await asyncio.to_thread(distiller.detect_retro_regions, content)
        if regions.final:
            await storage.save_retro_regions(text_id, regions.to_json(), version=RETRO_VERSION)
    except Exception as exc:
        logger.warning("[retro] text=%s 回忆区获取失败，按未标处理：%s", text_id, exc, exc_info=True)
        return RetroRegions.unavailable(f"获取失败：{exc}", final=False)
    logger.warning("[retro] text=%s 回忆区：%s", text_id, regions.summary())
    return regions
