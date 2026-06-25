"""Direct messaging: send, list conversations, view messages, mark read."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from deps import get_storage
from limiter import limiter
from storage.base import StorageBase
from routers.auth import get_current_user
from pydantic import BaseModel


class SendMessageRequest(BaseModel):
    receiver_id: str
    content: str


class ReactRequest(BaseModel):
    emoji: str


router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("/conversations")
@limiter.limit("60/minute")
async def get_conversations(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get conversation list for the current user."""
    conversations = await storage.get_conversations(user["id"])
    return {"conversations": conversations}


@router.get("/with/{other_id}")
@limiter.limit("60/minute")
async def get_conversation_messages(
    request: Request,
    other_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get messages between the current user and another user."""
    messages = await storage.get_conversation_messages(user["id"], other_id, page, page_size)
    return {"messages": messages}


@router.post("/send")
@limiter.limit("30/minute")
async def send_message(
    request: Request,
    body: SendMessageRequest,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Send a direct message."""
    if not body.content.strip():
        raise HTTPException(400, "消息不能为空")
    if body.receiver_id == user["id"]:
        raise HTTPException(400, "不能给自己发消息")
    receiver = await storage.get_user_by_id(body.receiver_id)
    if not receiver:
        raise HTTPException(404, "用户不存在")
    msg = await storage.send_message(user["id"], body.receiver_id, body.content.strip())
    return {"message": msg}


@router.post("/read/{other_id}")
@limiter.limit("30/minute")
async def mark_read(
    request: Request,
    other_id: str,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Mark all messages from other_id as read."""
    count = await storage.mark_read(user["id"], other_id)
    return {"ok": True, "count": count}


@router.get("/unread-count")
@limiter.limit("60/minute")
async def unread_count(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get total unread message count."""
    count = await storage.get_unread_count(user["id"])
    return {"count": count}


@router.post("/{message_id}/react")
@limiter.limit("60/minute")
async def react_to_dm(
    message_id: str,
    req: ReactRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Toggle a reaction on a direct message."""
    if not req.emoji.strip():
        raise HTTPException(400, "Emoji cannot be empty")

    msg = await storage.get_dm_message(message_id)
    if not msg:
        raise HTTPException(404, "消息不存在")
    if msg["sender_id"] != user["id"] and msg["receiver_id"] != user["id"]:
        raise HTTPException(403, "无权操作此消息")

    added = await storage.toggle_dm_reaction(message_id, user["id"], req.emoji)
    return {"ok": True, "added": added}


@router.get("/with/{other_id}/reactions")
@limiter.limit("60/minute")
async def get_dm_reactions(
    other_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Return all reactions for messages in the conversation."""
    reactions = await storage.get_dm_reactions(user["id"], other_id)
    return {"reactions": reactions}
