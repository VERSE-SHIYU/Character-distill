"""Unified trash/recycle-bin service for card, session, text, and group entities.

Centralizes owner auth, deleted_at validation, and storage dispatch so that
router handlers don't duplicate the same boilerplate across four modules.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

ENTITY_MAP: dict[str, dict[str, str]] = {
    "card": dict(
        get="get_card",
        soft="delete_card",
        restore="restore_card",
        hard="purge_card",
        owner_key="user_id",
    ),
    "session": dict(
        get="get_session",
        soft="delete_session",
        restore="restore_session",
        hard="hard_delete_session",
        owner_key="user_id",
    ),
    "text": dict(
        get="get_text",
        soft="delete_text",
        restore="restore_text",
        hard="hard_delete_text",
        owner_key="user_id",
    ),
    "group": dict(
        get="get_group_session",
        soft="delete_group_session",
        restore="restore_group_session",
        hard="hard_delete_group_session",
        owner_key="user_id",
    ),
}


def _get_entity_config(entity_type: str) -> dict[str, str]:
    config = ENTITY_MAP.get(entity_type)
    if not config:
        raise HTTPException(500, f"Unknown entity type: {entity_type}")
    return config


async def _fetch(entity_type: str, entity_id: str, storage: Any) -> dict | None:
    """Fetch the entity record by ID."""
    config = _get_entity_config(entity_type)
    method = getattr(storage, config["get"], None)
    if method is None:
        raise HTTPException(500, f"Storage missing method: {config['get']}")
    return await method(entity_id)


async def soft_delete(entity_type: str, entity_id: str, user: dict, storage: Any) -> bool:
    """Soft-delete an entity (move to trash).

    Returns True on success, raises HTTPException on failure.
    """
    record = await _fetch(entity_type, entity_id, storage)
    if not record:
        raise HTTPException(404, f"{entity_type} not found")

    owner_key = ENTITY_MAP[entity_type]["owner_key"]
    if record.get(owner_key) != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, f"无权删除此{entity_type}")

    config = _get_entity_config(entity_type)
    method = getattr(storage, config["soft"], None)
    if method is None:
        raise HTTPException(500, f"Storage missing method: {config['soft']}")

    result = await method(entity_id)
    # Group storage methods return None (success); others return bool.
    return True if result is None else bool(result)


async def restore(entity_type: str, entity_id: str, user: dict, storage: Any) -> bool:
    """Restore a soft-deleted entity from trash.

    Returns True on success, raises HTTPException on failure.
    """
    record = await _fetch(entity_type, entity_id, storage)
    if not record:
        raise HTTPException(404, f"{entity_type} not found")

    owner_key = ENTITY_MAP[entity_type]["owner_key"]
    if record.get(owner_key) != user["id"]:
        raise HTTPException(403, f"无权恢复此{entity_type}")

    config = _get_entity_config(entity_type)
    method = getattr(storage, config["restore"], None)
    if method is None:
        raise HTTPException(500, f"Storage missing method: {config['restore']}")

    result = await method(entity_id)
    return True if result is None else bool(result)


async def hard_delete(entity_type: str, entity_id: str, user: dict, storage: Any) -> bool:
    """Permanently delete an entity (irreversible).

    Validates that the entity exists, the user owns it, and it has already
    been soft-deleted (deleted_at is set).

    Returns True on success, raises HTTPException on failure.
    """
    record = await _fetch(entity_type, entity_id, storage)
    if not record:
        raise HTTPException(404, f"{entity_type} not found")

    owner_key = ENTITY_MAP[entity_type]["owner_key"]
    if record.get(owner_key) != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, f"无权操作此{entity_type}")

    # Require deleted_at to be set (entity must be in trash first)
    if not record.get("deleted_at"):
        raise HTTPException(400, "请先移入回收站再永久删除")

    config = _get_entity_config(entity_type)
    method = getattr(storage, config["hard"], None)
    if method is None:
        raise HTTPException(500, f"Storage missing method: {config['hard']}")

    result = await method(entity_id)
    return True if result is None else bool(result)
