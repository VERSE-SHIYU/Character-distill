"""Group chat: create group, send message, list history."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from deps import get_storage, get_user_llm, get_sessions
from storage.sqlite_store import SQLiteStore
from routers.auth import get_current_user

router = APIRouter(prefix="/api/group", tags=["group"])

# In-memory group sessions: group_id → GroupSession
_group_sessions: dict[str, Any] = {}


class CreateGroupRequest(BaseModel):
    name: str = ""
    card_ids: list[str]


class SendMessageRequest(BaseModel):
    target_card_id: str
    message: str
    speaker: str = ""

class BroadcastRequest(BaseModel):
    target_card_ids: list[str]
    message: str
    speaker: str = ""
    auto_mode: bool = False


def _get_group_sessions() -> dict[str, Any]:
    return _group_sessions


@router.post("/create")
async def create_group(
    req: CreateGroupRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict:
    """创建群聊，包含多个角色的 ChatEngine。"""
    user_id = user["id"]
    per_user_llm = await get_user_llm(user_id, storage)
    if per_user_llm is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    if not req.card_ids:
        raise HTTPException(400, "请至少选择两个角色")
    if len(req.card_ids) < 2:
        raise HTTPException(400, "群聊至少需要两个角色")

    from core.schema import CharacterCard
    from core.chat_engine import ChatEngine
    from core.rag import RAGEngine
    from deps import get_rag_config, get_memory_manager

    rag_config = get_rag_config()
    memory_manager = get_memory_manager()

    engines: dict[str, ChatEngine] = {}
    card_infos: list[dict] = []
    text_rag_cache: dict[str, RAGEngine] = {}

    for card_id in req.card_ids:
        card_rec = await storage.get_card(card_id)
        if not card_rec:
            raise HTTPException(404, f"角色卡 {card_id} 不存在")
        if card_rec.get("user_id") != user_id:
            raise HTTPException(403, f"无权使用角色卡 {card_id}")

        try:
            card = CharacterCard.model_validate_json(card_rec["card_json"])
        except Exception as exc:
            raise HTTPException(500, f"角色卡 {card_id} 数据损坏: {exc}") from exc

        text_id = card_rec["text_id"]
        if text_id not in text_rag_cache:
            text_rec = await storage.get_text(text_id)
            if not text_rec:
                raise HTTPException(404, f"原文 {text_id} 不存在")
            rag = RAGEngine(rag_config)
            try:
                rag.load_existing(f"text_{text_id}")
            except Exception:
                rag.index(text_rec["content"])
            text_rag_cache[text_id] = rag

        engine = ChatEngine(
            per_user_llm, text_rag_cache[text_id], card,
            memory_manager=memory_manager,
            card_id=card_id,
        )
        engines[card_id] = engine
        card_infos.append({"card_id": card_id, "name": card.name})

    from core.group_session import GroupSession
    group_id = uuid.uuid4().hex[:12]
    group = GroupSession(group_id, engines)
    _group_sessions[group_id] = group

    # Persist to DB
    await storage.create_group_session(
        group_id, req.name, req.card_ids, user_id,
    )

    return {
        "group_id": group_id,
        "name": req.name or "群聊",
        "characters": card_infos,
    }


@router.get("/list")
async def list_groups(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """列出用户的群聊会话。"""
    groups = await storage.list_group_sessions(user["id"])
    return {"groups": groups}


@router.post("/{group_id}/send")
async def send_message(
    group_id: str,
    req: SendMessageRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """导演模式下向指定角色发消息。"""
    user_id = user["id"]
    group = _group_sessions.get(group_id)
    if group is None:
        # Try to rebuild from DB (future: lazy reload)
        raise HTTPException(404, "群聊会话已过期，请重新创建")

    # Verify ownership via DB
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此群聊")

    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    if req.target_card_id not in group.engines:
        raise HTTPException(400, "目标角色不在群聊中")

    async with group.lock:
        try:
            resp = await group.send(req.target_card_id, req.message)
        except Exception as exc:
            raise HTTPException(500, f"群聊消息发送失败: {exc}") from exc

    # Persist to DB
    try:
        await storage.save_group_message(
            group_id, req.speaker or "导演", "user", req.message, "",
        )
        await storage.save_group_message(
            group_id, group.engines[req.target_card_id].card.name,
            "assistant", resp, req.target_card_id,
        )
    except Exception as exc:
        print(f"[group] Save messages failed (non-fatal): {exc}")

    return {"reply": resp, "speaker": group.engines[req.target_card_id].card.name}


@router.post("/{group_id}/broadcast")
async def broadcast_message(
    group_id: str,
    req: BroadcastRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """导演发一条消息，多个角色并行回复（仅记录一条导演消息）。"""
    user_id = user["id"]
    group = _group_sessions.get(group_id)
    if group is None:
        raise HTTPException(404, "群聊会话已过期，请重新创建")

    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此群聊")

    if not req.message.strip() and not req.auto_mode:
        raise HTTPException(400, "消息不能为空")
    invalid = [cid for cid in req.target_card_ids if cid not in group.engines]
    if invalid:
        raise HTTPException(400, f"目标角色不在群聊中: {invalid}")

    async with group.lock:
        try:
            results = await group.broadcast(req.message, req.target_card_ids, auto_mode=req.auto_mode)
        except Exception as exc:
            raise HTTPException(500, f"群聊广播失败: {exc}") from exc

    # 持久化
    try:
        if not req.auto_mode:
            await storage.save_group_message(
                group_id, req.speaker or "导演", "user", req.message, "",
            )
        for r in results:
            if r["reply"]:
                await storage.save_group_message(
                    group_id, r["speaker"], "assistant", r["reply"], r["card_id"],
                )
    except Exception as exc:
        print(f"[group] Save broadcast messages failed (non-fatal): {exc}")

    return {"replies": results}


@router.get("/{group_id}/history")
async def get_history(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """获取群聊历史消息。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此群聊")

    messages = await storage.get_group_messages(group_id)
    return {"messages": messages}


@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """删除群聊会话及所有消息。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")
    await storage.delete_group_session(group_id)
    _group_sessions.pop(group_id, None)
    return {"ok": True}
