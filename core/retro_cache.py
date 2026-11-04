# -*- coding: utf-8 -*-
"""回忆区的唯一入口：按书缓存（`core.book_cache`）读缓存 → 没有就标一次 → 能缓存的写回。

回忆区是**作品的属性**：同一本书换角色蒸、用存下的草稿重新转卡，都用同一份。缓存键是
（text_id、属主、`RETRO_VERSION`），版本不符即当无缓存。

**失败不挡蒸馏**：回忆区是位置核对的辅助信息，标不了时下游按「未标」处理、行为同今天。
读缓存、调模型、写回任何一步出错都只打 warning 并返回「未标」（偶发，不缓存）。

**前置条件**：调用方已用 `get_text_owned(text_id, user_id)` 做过属主校验（同名单入口）。
"""
from __future__ import annotations

from typing import Any

from core.book_cache import resolve_cached
from core.chapters import Chapters
from core.retro_regions import RETRO_VERSION, RetroRegions


async def resolve_retro_regions(storage: Any, distiller: Any, text_id: str, user_id: str,
                                content: str,
                                chapters: Chapters | None = None) -> RetroRegions:
    return await resolve_cached(
        storage=storage, text_id=text_id, user_id=user_id, version=RETRO_VERSION,
        getter="get_retro_regions_owned", setter="save_retro_regions",
        compute=lambda: distiller.detect_retro_regions(content, chapters=chapters),
        parse=RetroRegions.from_json,
        serialize=lambda regions: regions.to_json(),
        cacheable=lambda regions: regions.final,
        fallback=lambda exc: RetroRegions.unavailable(f"获取失败：{exc}", final=False),
        label="回忆区",
        summary=lambda regions: regions.summary(),
    )
