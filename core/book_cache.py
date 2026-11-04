# -*- coding: utf-8 -*-
"""按书缓存的共用流程：读（带版本）→ 同一本书并发只算一次 → 可缓存的写回；
任一步出错 WARNING + 返回调用方给的兜底值。

只含缓存编排，不含任何回忆区 / 章节语义：解析、序列化、可缓存判定、兜底值都由调用方给。
`parse` 返回 None 表示「缓存对不上原文」，按无缓存重算。

**前置条件**：调用方已用 `get_text_owned(text_id, user_id)` 做过属主校验（同名单入口）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: 正在算的书（键 = (标签, text_id)）。同一本书同时开几个任务时只算一次 —— 两个调用点
#: 都在主事件循环上，后来者 await 先行者的 Future 即可。
_INFLIGHT: dict[tuple[str, str], asyncio.Future] = {}


async def resolve_cached(
    *,
    storage: Any,
    text_id: str,
    user_id: str,
    version: int,
    getter: str,
    setter: str,
    compute: Callable[[], Any],
    parse: Callable[[str], Any],
    serialize: Callable[[Any], str],
    cacheable: Callable[[Any], bool],
    fallback: Callable[[BaseException], Any],
    label: str,
    summary: Callable[[Any], str] | None = None,
) -> Any:
    try:
        cached = await getattr(storage, getter)(text_id, user_id, version=version)
        if cached:
            value = parse(cached)
            if value is not None:
                return value
    except Exception as exc:
        logger.warning("[%s] text=%s %s获取失败，按兜底值处理：%s", label, text_id, label, exc,
                       exc_info=True)
        return fallback(exc)

    key = (label, text_id)
    inflight = _INFLIGHT.get(key)
    if inflight is not None:
        return await asyncio.shield(inflight)

    future = asyncio.get_running_loop().create_future()
    _INFLIGHT[key] = future
    value: Any = None
    try:
        try:
            value = await asyncio.to_thread(compute)
            if cacheable(value):
                await getattr(storage, setter)(text_id, serialize(value), version=version)
        except Exception as exc:
            logger.warning("[%s] text=%s %s获取失败，按兜底值处理：%s", label, text_id, label, exc,
                           exc_info=True)
            value = fallback(exc)
            return value
        if summary is not None:
            logger.warning("[%s] text=%s %s：%s", label, text_id, label, summary(value))
        return value
    finally:
        if not future.done():
            future.set_result(
                value if value is not None else fallback(RuntimeError("获取失败：识别被取消")))
        _INFLIGHT.pop(key, None)
