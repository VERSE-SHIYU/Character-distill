"""角色长期记忆管理 API。

查看 / 删除不需要 key（mem0 这几条路径不调模型），走工厂的底层视图；手动添加 / 编辑要
向量化，只用**用户自己的** LLM key 与百炼 key —— 缺一把就 409 说清去哪里配
（`docs/specs/user-own-keys.md`）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from deps import get_memory_manager, get_storage
from core.memory_manager import MemoryManager, MemoryView
from limiter import limiter
from routers.auth import get_current_user
from storage.base import StorageBase
from web.llm_resolution import resolve_embedding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])

#: 没配齐自己的两把 key 时，写入类接口的上屏文案（列表接口改给 `configured: false`）。
MEMORY_UNCONFIGURED = "请在设置页配置 LLM Key 与百炼 Key 以启用长期记忆"


class AddMemoryRequest(BaseModel):
    text: str


class UpdateMemoryRequest(BaseModel):
    text: str


async def _user_api_config(user_id: str, storage: StorageBase) -> dict:
    try:
        return await storage.get_user_api_config(user_id) or {}
    except Exception as exc:
        # 读不到配置 → 按「没配」处理（不回落全局）。失败要留痕。
        logger.warning(
            "Memory: per-user api config unreadable, treating as unconfigured (user_id=%s): %r",
            user_id, exc, exc_info=True,
        )
        return {}


async def _user_view(manager: MemoryManager, user_id: str, storage: StorageBase) -> MemoryView:
    """这个用户的记忆视图；没配齐自己的两把 key → 409。"""
    from deps import get_user_llm

    llm = await get_user_llm(user_id, storage)
    emb = resolve_embedding(await _user_api_config(user_id, storage))
    view = await asyncio.to_thread(
        manager.for_user,
        user_id=user_id, llm=llm, embedding_key=emb.key, embedding_region=emb.region,
    )
    if view is None:
        raise HTTPException(409, MEMORY_UNCONFIGURED)
    return view


async def _check_owner(storage: StorageBase, card_id: str, user_id: str, detail: str) -> None:
    owner_id = await storage.get_card_author_id(card_id)
    # 卡不存在（author 为 None）与非属主同判 404：403 会让人靠状态码枚举出 card_id 存在。
    if not owner_id or owner_id != user_id:
        raise HTTPException(404, detail)


@router.get("/list/{card_id}")
@limiter.limit("60/minute")
async def list_memories(
    card_id: str,
    request: Request,
    user=Depends(get_current_user),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
    storage: StorageBase = Depends(get_storage),
):
    """获取指定角色的全部长期记忆。

    `configured` = 这个用户配齐了自己的 LLM key 与百炼 key（没配齐时仍能查看、删除已有
    记忆，只是不会再记新的）。
    """
    if not memory_manager or not memory_manager.enabled:
        return {"memories": [], "enabled": False, "configured": False}
    await _check_owner(storage, card_id, user["id"], "无权访问此角色的记忆")
    cfg = await _user_api_config(user["id"], storage)
    configured = bool(cfg.get("api_key")) and bool(resolve_embedding(cfg).key)
    memories = await asyncio.to_thread(memory_manager.get_all, card_id)
    return {"memories": memories, "enabled": True, "configured": configured}


@router.post("/add/{card_id}")
@limiter.limit("30/minute")
async def add_memory(
    card_id: str,
    body: AddMemoryRequest,
    request: Request,
    user=Depends(get_current_user),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
    storage: StorageBase = Depends(get_storage),
):
    """手动添加一条记忆。"""
    if not memory_manager or not memory_manager.enabled:
        raise HTTPException(400, "记忆系统未启用")
    if not body.text.strip():
        raise HTTPException(422, "记忆内容不能为空")
    await _check_owner(storage, card_id, user["id"], "无权操作此角色的记忆")
    view = await _user_view(memory_manager, user["id"], storage)
    ok = await asyncio.to_thread(view.add_manual, body.text.strip(), card_id)
    if not ok:
        raise HTTPException(500, "添加失败")
    return {"ok": True}


@router.put("/update/{memory_id}")
@limiter.limit("30/minute")
async def update_memory(
    memory_id: str,
    body: UpdateMemoryRequest,
    request: Request,
    user=Depends(get_current_user),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
    storage: StorageBase = Depends(get_storage),
    card_id: str = Query(...),
):
    """更新一条记忆的内容（新内容要重新向量化）。"""
    if not memory_manager or not memory_manager.enabled:
        raise HTTPException(400, "记忆系统未启用")
    if not body.text.strip():
        raise HTTPException(422, "记忆内容不能为空")
    await _check_owner(storage, card_id, user["id"], "无权操作此角色的记忆")
    view = await _user_view(memory_manager, user["id"], storage)
    ok = await asyncio.to_thread(view.update, memory_id, body.text.strip())
    if not ok:
        raise HTTPException(500, "更新失败")
    return {"ok": True}


@router.delete("/delete/{memory_id}")
@limiter.limit("30/minute")
async def delete_memory(
    memory_id: str,
    request: Request,
    user=Depends(get_current_user),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
    storage: StorageBase = Depends(get_storage),
    card_id: str = Query(...),
):
    """删除单条记忆。需要 card_id 校验所有权。不需要 key。"""
    if not memory_manager or not memory_manager.enabled:
        raise HTTPException(400, "记忆系统未启用")
    await _check_owner(storage, card_id, user["id"], "无权删除此记忆")
    ok = await asyncio.to_thread(memory_manager.delete, memory_id)
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True}


@router.delete("/clear/{card_id}")
@limiter.limit("30/minute")
async def clear_memories(
    card_id: str,
    request: Request,
    user=Depends(get_current_user),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
    storage: StorageBase = Depends(get_storage),
):
    """清空指定角色的全部记忆。不需要 key。"""
    if not memory_manager or not memory_manager.enabled:
        raise HTTPException(400, "记忆系统未启用")
    await _check_owner(storage, card_id, user["id"], "无权访问此角色的记忆")
    ok = await asyncio.to_thread(memory_manager.delete_all, card_id)
    if not ok:
        raise HTTPException(500, "清空失败")
    return {"ok": True}
