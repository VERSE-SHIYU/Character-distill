"""按调用方身份取数的唯一原语：先按属主取，管理员才落到无身份原语。

「先 `*_owned`，拿不到且调用方是管理员时才 `*_unscoped`」这套逻辑此前在路由层手写了
三处（market.delete_market_card / market.delete_comment / text.get_text_deletion_impact），
形态各不相同，且每处漏写一个分支都是静默的越权或静默的 404。收成一个原语后，
「跨属主读」只可能发生在显式传 `allow_admin=True` 的调用点上。
"""

from __future__ import annotations

from typing import Awaitable, Callable

__all__ = ["fetch_for_actor"]

_Owned = Callable[[str, str], Awaitable["dict | None"]]
_Unscoped = Callable[[str], Awaitable["dict | None"]]


async def fetch_for_actor(
    owned: _Owned,
    unscoped: _Unscoped,
    entity_id: str,
    user: dict,
    *,
    allow_admin: bool,
) -> dict | None:
    """取 entity_id 这条记录，身份口径如下：

      1. `owned(entity_id, user["id"])` 有结果 → 返回它。「存在且属于我」与
         「存在但不属于我」由此分开（后者为 None，调用方据此判 404，与非属主同码）。
      2. 拿到 None ∧ `allow_admin` ∧ `user.is_admin` → 才落 `unscoped(entity_id)`。
      3. 其余一律返回 None，`unscoped` **不会被调用** —— 这是本原语存在的意义：
         不分身份就取数的路径只有一条，且看得见。

    返回 None 不等于「不存在」，也可能只是「不属于我且我不是管理员」；两种情况调用方
    都该判 404（防 ID 枚举），所以这里不做区分、也不抛异常。
    """
    record = await owned(entity_id, user["id"])
    if record is None and allow_admin and user.get("is_admin"):
        return await unscoped(entity_id)
    return record
