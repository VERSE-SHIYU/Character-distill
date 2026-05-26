"""角色长期记忆管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from deps import get_memory_manager, get_storage
from core.memory_manager import MemoryManager
from routers.auth import get_current_user
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/list/{card_id}")
async def list_memories(
    card_id: str,
    user=Depends(get_current_user),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
    storage: SQLiteStore = Depends(get_storage),
):
    """获取指定角色的全部长期记忆。"""
    if not memory_manager or not memory_manager.enabled:
        return {"memories": [], "enabled": False}
    owner_id = await storage.get_card_author_id(card_id)
    if not owner_id or owner_id != user["id"]:
        raise HTTPException(403, "无权访问此角色的记忆")
    memories = memory_manager.get_all(card_id)
    return {"memories": memories, "enabled": True}


@router.delete("/delete/{memory_id}")
async def delete_memory(
    memory_id: str,
    user=Depends(get_current_user),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
    storage: SQLiteStore = Depends(get_storage),
    card_id: str = Query(...),
):
    """删除单条记忆。需要 card_id 校验所有权。"""
    if not memory_manager or not memory_manager.enabled:
        raise HTTPException(400, "记忆系统未启用")
    owner_id = await storage.get_card_author_id(card_id)
    if not owner_id or owner_id != user["id"]:
        raise HTTPException(403, "无权删除此记忆")
    ok = memory_manager.delete(memory_id)
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True}


@router.delete("/clear/{card_id}")
async def clear_memories(
    card_id: str,
    user=Depends(get_current_user),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
    storage: SQLiteStore = Depends(get_storage),
):
    """清空指定角色的全部记忆。"""
    if not memory_manager or not memory_manager.enabled:
        raise HTTPException(400, "记忆系统未启用")
    owner_id = await storage.get_card_author_id(card_id)
    if not owner_id or owner_id != user["id"]:
        raise HTTPException(403, "无权访问此角色的记忆")
    ok = memory_manager.delete_all(card_id)
    if not ok:
        raise HTTPException(500, "清空失败")
    return {"ok": True}
