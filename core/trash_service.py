"""Unified trash/recycle-bin service for card, session, text, and group entities.

Centralizes owner auth, deleted_at validation, and storage dispatch so that
router handlers don't duplicate the same boilerplate across four modules.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from core.authz import fetch_for_actor

logger = logging.getLogger(__name__)

# 每种实体在 storage 上的五个操作，逐一显式列出（**不再只写一个 `get` 再让 getattr 兜底**）。
#
# 取数走 owned/unscoped 两支：属主读 `*_owned`（SQL 里 WHERE user_id=?），管理员跨属主才落
# `*_unscoped`，由 core.authz.fetch_for_actor 统一裁决。这里不再有 Python 层的属主比较 ——
# 比较的前提是「已经拿到了那条记录」，而拿到记录本身就该带身份。
#
# **五个名字是数据**（存在 dict 里、按名 getattr 取数），所以改 storage 侧方法名时这里不会
# 报错、测试不跑就没人发现 —— 2026-09-11 的 `f7bd92a` 改名漏改这四处，回收站四个实体全灭
# 9 天。这正是 `tests/test_storage_scope_lock.py` 按「字符串常量」形态扫 `*_unscoped` 的原因。
ENTITY_MAP: dict[str, dict[str, str]] = {
    "card": dict(
        owned="get_card_owned",
        unscoped="get_card_unscoped",
        soft="delete_card",
        restore="restore_card",
        hard="purge_card",
    ),
    "session": dict(
        owned="get_session_owned",
        unscoped="get_session_unscoped",
        soft="delete_session",
        restore="restore_session",
        hard="hard_delete_session",
    ),
    "text": dict(
        owned="get_text_owned",
        unscoped="get_text_unscoped",
        soft="delete_text",
        restore="restore_text",
        hard="hard_delete_text",
    ),
    "group": dict(
        owned="get_group_session_owned",
        unscoped="get_group_session_unscoped",
        soft="delete_group_session",
        restore="restore_group_session",
        hard="hard_delete_group_session",
    ),
}


def _get_entity_config(entity_type: str) -> dict[str, str]:
    config = ENTITY_MAP.get(entity_type)
    if not config:
        raise HTTPException(500, f"Unknown entity type: {entity_type}")
    return config


def _method(storage: Any, name: str):
    """按名取 storage 方法；取不到即 500（名字悬空是编程错误，不静默降级成「没这条记录」）。"""
    method = getattr(storage, name, None)
    if method is None:
        raise HTTPException(500, f"Storage missing method: {name}")
    return method


async def _fetch(entity_type: str, entity_id: str, user: dict, storage: Any, *,
                 allow_admin: bool) -> dict | None:
    """取记录。None 既可能是「不存在」，也可能是「不属于我且我不是管理员」——由调用方判 404。

    两种情况的 404 由 core.authz 保证同码（防 ID 枚举）；这里不区分、不抛。
    """
    config = _get_entity_config(entity_type)
    return await fetch_for_actor(
        _method(storage, config["owned"]),
        _method(storage, config["unscoped"]),
        entity_id,
        user,
        allow_admin=allow_admin,
    )


async def soft_delete(entity_type: str, entity_id: str, user: dict, storage: Any) -> bool:
    """Soft-delete an entity (move to trash). 属主或 admin。

    Returns True on success, raises HTTPException on failure.
    """
    record = await _fetch(entity_type, entity_id, user, storage, allow_admin=True)
    if not record:
        raise HTTPException(404, f"{entity_type} not found")

    config = _get_entity_config(entity_type)
    result = await _method(storage, config["soft"])(entity_id)
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
    result = await _method(storage, config["restore"])(entity_id)
    return True if result is None else bool(result)


async def hard_delete(entity_type: str, entity_id: str, user: dict, storage: Any, keep_cards: bool = False) -> bool:
    """Permanently delete an entity (irreversible). 属主或 admin。

    Validates that the entity exists, the caller may see it, and it has already
    been soft-deleted (deleted_at is set).

    keep_cards is only meaningful for "text" type: when True, cards are
    detached (text_id→NULL) instead of cascade-deleted, so they and their
    chat sessions survive the text deletion.

    Returns True on success, raises HTTPException on failure.
    """
    record = await _fetch(entity_type, entity_id, user, storage, allow_admin=True)
    if not record:
        raise HTTPException(404, f"{entity_type} not found")

    # Require deleted_at to be set (entity must be in trash first)
    if not record.get("deleted_at"):
        raise HTTPException(400, "请先移入回收站再永久删除")

    config = _get_entity_config(entity_type)
    method = _method(storage, config["hard"])
    if entity_type == "text":
        result = await method(entity_id, keep_cards=keep_cards)
    else:
        result = await method(entity_id)
    return True if result is None else bool(result)
