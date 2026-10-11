"""Unified trash/recycle-bin service for card, session, text, and group entities.

Centralizes owner auth, deleted_at validation, and storage dispatch so that
router handlers don't duplicate the same boilerplate across four modules.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from core.authz import fetch_for_actor
from core.memory_scope import MemoryScope

logger = logging.getLogger(__name__)

# 每种实体在 storage 上的五个操作，逐一显式列出。
#
# 取数走 owned/unscoped 两支：属主读 `*_owned`（SQL 里 WHERE user_id=?），管理员跨属主才落
# `*_unscoped`，由 core.authz.fetch_for_actor 统一裁决。这里不再有 Python 层的属主比较 ——
# 比较的前提是「已经拿到了那条记录」，而拿到记录本身就该带身份。
#
# **槽位是「取 storage 上那个方法」的可调用，不是方法名的字符串**。名字一旦是字符串、再由
# 反射按名解析，它就成了数据：改 storage 侧方法名时这里不会报错，
# 静态检查与 AST 锁都看不见，测试不跑就没人发现 —— 2026-09-11 的 `f7bd92a` 改名漏改这四处，
# 回收站四个实体全灭 9 天。写成属性引用后，同一处改名不会再静默悬空 —— 调用该槽即
# `AttributeError`。
#
# **但 AttributeError 只在调用那一刻才发生**：lambda 体要到被调用时才解析属性，`storage`
# 又是 duck-typed（形参标 `Any`，静态检查看不见），所以导入、定义、乃至把槽取出来都不报错。
# 真正让改名响的是 `tests/test_trash_service.py` —— 20 个槽它全求值过一遍（`owned` /
# `unscoped` 是 `core.authz.fetch_for_actor` 的两个实参，每次取数都求值；`soft` / `restore` /
# `hard` 在各自成功路径上被调用），任一槽改名都会打红对应用例。
# `tests/test_storage_scope_lock.py` 的字符串形态仍在扫（兜底，防这类写法再出现）。
ENTITY_MAP: dict[str, dict[str, Callable[[Any], Any]]] = {
    "card": dict(
        owned=lambda s: s.get_card_owned,
        unscoped=lambda s: s.get_card_unscoped,
        soft=lambda s: s.delete_card,
        restore=lambda s: s.restore_card,
        hard=lambda s: s.purge_card,
    ),
    "session": dict(
        owned=lambda s: s.get_session_owned,
        unscoped=lambda s: s.get_session_unscoped,
        soft=lambda s: s.delete_session,
        restore=lambda s: s.restore_session,
        hard=lambda s: s.hard_delete_session,
    ),
    "text": dict(
        owned=lambda s: s.get_text_owned,
        unscoped=lambda s: s.get_text_unscoped,
        soft=lambda s: s.delete_text,
        restore=lambda s: s.restore_text,
        hard=lambda s: s.hard_delete_text,
    ),
    "group": dict(
        owned=lambda s: s.get_group_session_owned,
        unscoped=lambda s: s.get_group_session_unscoped,
        soft=lambda s: s.delete_group_session,
        restore=lambda s: s.restore_group_session,
        hard=lambda s: s.hard_delete_group_session,
    ),
}


def _get_entity_config(entity_type: str) -> dict[str, Callable[[Any], Any]]:
    config = ENTITY_MAP.get(entity_type)
    if not config:
        raise HTTPException(500, f"Unknown entity type: {entity_type}")
    return config


# ── 永久删除要跟着清掉的长期记忆：每类东西一个解析函数 ────────────────────────────
#
# 「删什么清什么」的对应关系只写这一处（spec `docs/specs/memory-delete-cascade.md` §4.1）：
# 输入是**已经取到的记录**（`hard_delete` 鉴权时拿到的）加这次的删除参数（如 text 的
# `keep_cards`），输出要清的记忆范围列表。存储只在书那条路径上用得到（要枚举它名下的卡）。
#
# 对照 `ENTITY_MAP`：那张表是「怎么删」，这张是「删了之后记忆该清哪几处」。新增一类能永久
# 删除的东西 → 两张表各加一行；新增一个永久删除的入口 → 调 `hard_delete` 就自动带上。
_MemoryScopes = Callable[[Any, dict, dict], Awaitable[list[MemoryScope]]]


async def _session_scopes(storage: Any, record: dict, op_kwargs: dict) -> list[MemoryScope]:
    """一对一存档：只清这个存档的 —— 同卡别的存档、群里的都不动。"""
    return [MemoryScope.archive(record["card_id"], record["id"])]


async def _group_scopes(storage: Any, record: dict, op_kwargs: dict) -> list[MemoryScope]:
    """群聊：清每个成员（卡）在这个群里的 —— 成员的一对一存档、别的群不动。"""
    return [MemoryScope.group(card_id, record["id"]) for card_id in record["card_ids"]]


async def _card_scopes(storage: Any, record: dict, op_kwargs: dict) -> list[MemoryScope]:
    """一张卡：整卡范围（所有存档、所有群、没有归属的旧记忆）。"""
    return [MemoryScope.whole_card(record["id"])]


async def _text_scopes(storage: Any, record: dict, op_kwargs: dict) -> list[MemoryScope]:
    """一本书：留卡时不清；否则清它名下每张卡的整卡范围。

    用 `get_text_card_ids`（含回收站里的卡）—— 要清的必须**正好**是 `hard_delete_text`
    那条 `DELETE FROM cards WHERE text_id = ?` 会删掉的那一批，不多不少。
    """
    if op_kwargs.get("keep_cards"):
        return []
    return [MemoryScope.whole_card(cid) for cid in await storage.get_text_card_ids(record["id"])]


MEMORY_SCOPES: dict[str, _MemoryScopes] = {
    "session": _session_scopes,
    "group": _group_scopes,
    "card": _card_scopes,
    "text": _text_scopes,
}


async def _purge_memory(entity_type: str, storage: Any, record: dict,
                        memory: Any, op_kwargs: dict) -> None:
    """删行**之前**清掉这一实体对应的长期记忆。

    清不掉就抛 500 —— 行留在回收站，用户可以重试。反过来（先删行）的话行没了而记忆还在
    库里，就再也没有线索找到它们（spec §1）。`memory` 为 None 或没启用时跳过：记忆系统
    没开不该挡删除。

    走 `MemoryManager.delete_all`（底层视图，不依赖用户自己的 key，故没配 key 的用户也
    清得掉）；它是同步的，挪进线程别堵住事件循环。
    """
    if memory is None or not memory.enabled:
        return
    for scope in await MEMORY_SCOPES[entity_type](storage, record, op_kwargs):
        if not await asyncio.to_thread(memory.delete_all, scope):
            logger.error(
                "Memory cleanup failed for %s %s (scope=%s); leaving the row in the trash",
                entity_type, record["id"], scope,
            )
            raise HTTPException(500, "记忆清理失败，请稍后重试")


async def _fetch(entity_type: str, entity_id: str, user: dict, storage: Any, *,
                 allow_admin: bool) -> dict | None:
    """取记录。None 既可能是「不存在」，也可能是「不属于我且我不是管理员」——由调用方判 404。

    两种情况的 404 由 core.authz 保证同码（防 ID 枚举）；这里不区分、不抛。
    """
    config = _get_entity_config(entity_type)
    return await fetch_for_actor(
        config["owned"](storage),
        config["unscoped"](storage),
        entity_id,
        user,
        allow_admin=allow_admin,
    )


def _in_trash(record: dict) -> bool:
    """记录是否已在回收站（`deleted_at` 非空）。

    一个谓词、两处**相反**的用法：`soft_delete` 要求**不在**回收站，`hard_delete` 要求**在**。
    判定必须落在 `before_mutation` **之前** —— 副作用（取消在途任务、断开卡片）不可逆，
    穿过了前置条件就会作用在「本来不该发生这次操作」的对象上（缺陷 77）。
    """
    return bool(record.get("deleted_at"))


async def soft_delete(entity_type: str, entity_id: str, user: dict, storage: Any, *,
                      before_mutation: Callable[[dict], Awaitable[None]] | None = None,
                      **op_kwargs: Any) -> bool:
    """Soft-delete an entity (move to trash). 属主或 admin。

    before_mutation runs **after** the caller is authorized but **before** the first
    storage write —— 给调用方一个挂「鉴权前绝不能跑」的副作用的钩子（缺陷 75：取消在途
    任务必须在鉴权通过后、存储写之前，晚到删除之后则任务会去写已删的文本）。
    op_kwargs are forwarded to the storage method unchanged.

    Returns True on success, raises HTTPException on failure.
    """
    record = await _fetch(entity_type, entity_id, user, storage, allow_admin=True)
    # 已在回收站与不存在同判 404 —— 对「软删」这个动作，已删记录就是「没有了」，且与
    # text / session 经 rowcount 得到 404 的既有行为一致（旧读数 404/200/404/200，
    # card / group 的空转 200 统一到这里）。
    if not record or _in_trash(record):
        raise HTTPException(404, f"{entity_type} not found")

    if before_mutation is not None:
        await before_mutation(record)

    config = _get_entity_config(entity_type)
    result = await config["soft"](storage)(entity_id, **op_kwargs)
    # Group storage methods return None (success); others return bool.
    return True if result is None else bool(result)


async def restore(entity_type: str, entity_id: str, user: dict, storage: Any) -> bool:
    """Restore a soft-deleted entity from trash. **仅属主**（admin 不例外）。

    Returns True on success, raises HTTPException on failure.
    """
    record = await _fetch(entity_type, entity_id, user, storage, allow_admin=False)
    if not record:
        raise HTTPException(404, f"{entity_type} not found")

    config = _get_entity_config(entity_type)
    result = await config["restore"](storage)(entity_id)
    return True if result is None else bool(result)


async def hard_delete(entity_type: str, entity_id: str, user: dict, storage: Any, *,
                      memory: Any,
                      before_mutation: Callable[[dict], Awaitable[None]] | None = None,
                      **op_kwargs: Any) -> bool:
    """Permanently delete an entity (irreversible). 属主或 admin。

    Validates that the entity exists, the caller may see it, and it has already
    been soft-deleted (deleted_at is set).

    `memory` is the MemoryManager (or None when memory is switched off) whose memories
    for this entity must be cleared before the row goes away; the mapping from
    entity type to memory scopes lives in `MEMORY_SCOPES`. It is a **required**
    keyword-only argument with no default, on purpose: a new permanent-delete call
    site that forgets it fails loudly with `TypeError` instead of silently leaving
    orphaned memories behind.

    before_mutation runs after both checks pass and before the first storage write;
    see `soft_delete`. op_kwargs (e.g. keep_cards, meaningful for "text") are
    forwarded to the storage method unchanged —— 这里不再特判实体类型。

    Returns True on success, raises HTTPException on failure.
    """
    record = await _fetch(entity_type, entity_id, user, storage, allow_admin=True)
    if not record:
        raise HTTPException(404, f"{entity_type} not found")

    # Require deleted_at to be set (entity must be in trash first)
    if not _in_trash(record):
        raise HTTPException(400, "请先移入回收站再永久删除")

    if before_mutation is not None:
        await before_mutation(record)

    # 清记忆在删行之前：清失败抛 500，行还在回收站，可重试。
    await _purge_memory(entity_type, storage, record, memory, op_kwargs)

    config = _get_entity_config(entity_type)
    result = await config["hard"](storage)(entity_id, **op_kwargs)
    return True if result is None else bool(result)
