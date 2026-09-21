# -*- coding: utf-8 -*-
"""角色名单的唯一入口：读缓存 → 识别 → 落库，收敛到一处。

**前置条件（三个函数都适用）**：调用方**已经**用 `get_text_owned(text_id, user_id)`
做过属主校验。本模块不重复校验 —— 它也无法校验：`get_characters_owned` 对「无缓存」
与「非属主」都返回 None，本模块区分不出，而顺序反过来就等于没有校验（distill 路由
踩过，见缺陷 19）。

分层：本模块只做名单的生命周期（读 / 算 / 写），不管 LLM 细节（`Distiller` 的活）、
不管 HTTP（路由的活）、不管卡片与蒸馏。名单是**作品的属性**，不是某个会话或某条
请求的属性 —— 所以它不参与会话状态。

为什么要有一个入口：这段「读缓存 → 没命中就跑识别 → 写回」原先只在 `/identify`
一处，而 `/start`、`/run_stream`、`/reindex`、`TextManager.distill_all` 各自**只跑
识别、不读缓存也不写回** —— 同一份名单被反复重算。缓存键是（text_id、属主、**识别
算法版本**），版本不符即当无缓存（见 `Distiller.IDENTIFY_VERSION`）：口径一改，旧
名单必须自己失效，否则残缺的旧名单会被一直当全书名单用。
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.distiller import Distiller


async def cached_characters(
    storage: Any, text_id: str, user_id: str,
) -> list[dict[str, Any]] | None:
    """读名单缓存：无缓存 / 非属主 / 版本不符，三者都是 None。"""
    return await storage.get_characters_owned(
        text_id, user_id, version=Distiller.IDENTIFY_VERSION)


async def resolve_characters(
    storage: Any,
    distiller: Distiller,
    text_id: str,
    user_id: str,
    content: str,
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """取这部作品的名单：命中缓存即返回（不发 LLM），未命中才识别一次并写回。

    ``refresh=True`` 跳过缓存强制重算并覆盖（用户显式点「重新识别」时用）。
    识别是同步阻塞的 LLM 调用，这里统一丢进线程，调用方不必各自 `to_thread`。
    """
    if not refresh:
        cached = await cached_characters(storage, text_id, user_id)
        if cached:
            return cached
    chars = await asyncio.to_thread(distiller.identify_characters, content)
    await storage.save_characters(
        text_id, chars, version=Distiller.IDENTIFY_VERSION)
    return chars


def aliases_for(chars: list[dict[str, Any]], name: str) -> list[str]:
    """某人的别名；名单里没有此人（或此人无别名）时返回空列表。

    下游按子串用别名（蒸馏选片、RAG 打标签），所以别名只该来自识别结果里
    **唯一指向此人**的称呼（规则见 `core.distiller.ALIAS_UNIQUENESS_RULE`）。
    """
    for c in chars:
        if c.get("name") == name:
            return c.get("aliases") or []
    return []
