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
from limiter import limiter
from storage.sqlite_store import SQLiteStore
from routers.auth import get_current_user

router = APIRouter(prefix="/api/group", tags=["group"])

# In-memory group sessions: group_id → GroupSession
_group_sessions: dict[str, Any] = {}


class CreateGroupRequest(BaseModel):
    name: str = ""
    card_ids: list[str]


class RenameGroupRequest(BaseModel):
    name: str


class SendMessageRequest(BaseModel):
    target_card_id: str
    message: str
    speaker: str = ""
    reply_to_id: int | None = None

class BroadcastRequest(BaseModel):
    target_card_ids: list[str]
    message: str
    speaker: str = ""
    auto_mode: bool = False
    reply_to_id: int | None = None

class ReactRequest(BaseModel):
    emoji: str


async def _rebuild_group_session(
    group_id: str,
    user_id: str,
    storage: SQLiteStore,
) -> Any | None:
    """Rebuild an in-memory GroupSession from the persisted DB record."""
    from core.schema import CharacterCard
    from core.chat_engine import ChatEngine
    from core.rag import RAGEngine
    from core.group_session import GroupSession
    from deps import get_rag_config, get_memory_manager

    session = await storage.get_group_session(group_id)
    if not session:
        return None
    if session.get("user_id") != user_id:
        return None

    per_user_llm = await get_user_llm(user_id, storage)
    if per_user_llm is None:
        return None

    rag_config = get_rag_config()
    memory_manager = get_memory_manager()

    engines: dict[str, ChatEngine] = {}
    text_rag_cache: dict[str, RAGEngine] = {}

    for card_id in session["card_ids"]:
        card_rec = await storage.get_card(card_id)
        if not card_rec:
            continue
        if card_rec.get("user_id") != user_id:
            continue
        try:
            card = CharacterCard.model_validate_json(card_rec["card_json"])
        except Exception:
            continue

        text_id = card_rec["text_id"]
        if text_id not in text_rag_cache:
            text_rec = await storage.get_text(text_id)
            if not text_rec:
                continue
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

    if len(engines) < 2:
        return None

    group = GroupSession(group_id, engines)
    _group_sessions[group_id] = group

    # Restore message history from DB
    try:
        history = await storage.get_group_messages(group_id)
        group.group_history = [
            {
                "speaker": m["speaker"],
                "role": m["role"],
                "content": m["content"],
                "speaker_card_id": m.get("speaker_card_id", ""),
            }
            for m in history
        ]
    except Exception as exc:
        print(f"[Group] Session rebuild failed: {exc}")

    return group


def _get_group_sessions() -> dict[str, Any]:
    return _group_sessions


@router.post("/create")
@limiter.limit("30/minute")
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
@limiter.limit("60/minute")
async def list_groups(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """列出用户的群聊会话。"""
    groups = await storage.list_group_sessions(user["id"])
    return {"groups": groups}


@router.post("/{group_id}/send")
@limiter.limit("30/minute")
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
        group = await _rebuild_group_session(group_id, user_id, storage)
    if group is None:
        raise HTTPException(404, "群聊会话已过期，请重新创建")

    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    if req.target_card_id not in group.engines:
        raise HTTPException(400, "目标角色不在群聊中")

    session_rec = await storage.get_group_session(group_id)
    if session_rec and session_rec.get("deleted_at"):
        raise HTTPException(410, "群聊已被删除")

    async with group.lock:
        try:
            resp = await group.send(req.target_card_id, req.message)
        except Exception as exc:
            raise HTTPException(500, f"群聊消息发送失败: {exc}") from exc

    # Persist to DB
    reply_preview = ""
    if req.reply_to_id:
        try:
            history = await storage.get_group_messages(group_id)
            replied = next((m for m in history if m["id"] == req.reply_to_id), None)
            if replied:
                reply_preview = (replied.get("speaker", "") + ": " + replied["content"])[:80]
        except Exception:
            pass
    try:
        await storage.save_group_message(
            group_id, req.speaker or "导演", "user", req.message, "",
            reply_to_id=req.reply_to_id, reply_to_preview=reply_preview,
        )
        await storage.save_group_message(
            group_id, group.engines[req.target_card_id].card.name,
            "assistant", resp, req.target_card_id,
        )
    except Exception as exc:
        print(f"[group] Save messages failed (non-fatal): {exc}")

    return {"reply": resp, "speaker": group.engines[req.target_card_id].card.name}


@router.post("/{group_id}/broadcast")
@limiter.limit("30/minute")
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
        group = await _rebuild_group_session(group_id, user_id, storage)
    if group is None:
        raise HTTPException(404, "群聊会话已过期，请重新创建")

    if not req.message.strip() and not req.auto_mode:
        raise HTTPException(400, "消息不能为空")
    invalid = [cid for cid in req.target_card_ids if cid not in group.engines]
    if invalid:
        raise HTTPException(400, f"目标角色不在群聊中: {invalid}")

    session_rec = await storage.get_group_session(group_id)
    if session_rec and session_rec.get("deleted_at"):
        raise HTTPException(410, "群聊已被删除")

    async with group.lock:
        try:
            results = await group.broadcast(req.message, req.target_card_ids, auto_mode=req.auto_mode)
        except Exception as exc:
            raise HTTPException(500, f"群聊广播失败: {exc}") from exc

    # 持久化
    reply_preview = ""
    if req.reply_to_id:
        try:
            history = await storage.get_group_messages(group_id)
            replied = next((m for m in history if m["id"] == req.reply_to_id), None)
            if replied:
                reply_preview = (replied.get("speaker", "") + ": " + replied["content"])[:80]
        except Exception:
            pass
    try:
        if not req.auto_mode:
            await storage.save_group_message(
                group_id, req.speaker or "导演", "user", req.message, "",
                reply_to_id=req.reply_to_id, reply_to_preview=reply_preview,
            )
        for r in results:
            if r["reply"]:
                await storage.save_group_message(
                    group_id, r["speaker"], "assistant", r["reply"], r["card_id"],
                )
    except Exception as exc:
        print(f"[group] Save broadcast messages failed (non-fatal): {exc}")

    return {"replies": results}


@router.post("/{group_id}/message/{message_id}/react")
@limiter.limit("60/minute")
async def toggle_reaction(
    group_id: str,
    message_id: int,
    req: ReactRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Toggle a reaction emoji on a message."""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")

    if not req.emoji.strip():
        raise HTTPException(400, "emoji 不能为空")

    added = await storage.toggle_reaction(message_id, user["id"], req.emoji.strip())
    return {"ok": True, "added": added}

@router.get("/{group_id}/history")
@limiter.limit("60/minute")
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

    if session.get("deleted_at"):
        raise HTTPException(410, "群聊已被删除")

    messages = await storage.get_group_messages(group_id)
    return {"messages": messages}


@router.patch("/{group_id}/rename")
@limiter.limit("30/minute")
async def rename_group(
    group_id: str,
    req: RenameGroupRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """重命名群聊。"""
    if not req.name.strip():
        raise HTTPException(400, "名称不能为空")
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此群聊")
    if session.get("deleted_at"):
        raise HTTPException(410, "群聊已被删除")
    await storage.update_group_session(group_id, req.name.strip())
    return {"ok": True}


@router.delete("/{group_id}")
@limiter.limit("30/minute")
async def delete_group(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """软删除群聊会话（移入回收站）。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")
    await storage.delete_group_session(group_id)
    _group_sessions.pop(group_id, None)
    return {"ok": True}


@router.get("/trash")
@limiter.limit("30/minute")
async def list_trash_groups(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """列出已删除的群聊。"""
    groups = await storage.get_deleted_group_sessions(user["id"])
    return {"groups": groups}


@router.post("/{group_id}/restore")
@limiter.limit("30/minute")
async def restore_group(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """恢复已删除的群聊。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")
    if not session.get("deleted_at"):
        raise HTTPException(400, "群聊未被删除")
    await storage.restore_group_session(group_id)
    return {"ok": True}


@router.delete("/{group_id}/permanent")
@limiter.limit("30/minute")
async def permanent_delete_group(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """永久删除群聊及其所有消息。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")
    await storage.hard_delete_group_session(group_id)
    _group_sessions.pop(group_id, None)
    return {"ok": True}
