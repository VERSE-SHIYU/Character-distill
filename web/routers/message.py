"""Direct messaging: send, list conversations, view messages, mark read."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from deps import get_storage
from storage.sqlite_store import SQLiteStore
from routers.auth import get_current_user
from pydantic import BaseModel


class SendMessageRequest(BaseModel):
    receiver_id: str
    content: str


router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("/conversations")
async def get_conversations(
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get conversation list for the current user."""
    conversations = await storage.get_conversations(user["id"])
    return {"conversations": conversations}


@router.get("/with/{other_id}")
async def get_conversation_messages(
    other_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get messages between the current user and another user."""
    messages = await storage.get_conversation_messages(user["id"], other_id, page, page_size)
    return {"messages": messages}


@router.post("/send")
async def send_message(
    body: SendMessageRequest,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Send a direct message."""
    if not body.content.strip():
        raise HTTPException(400, "消息不能为空")
    if body.receiver_id == user["id"]:
        raise HTTPException(400, "不能给自己发消息")
    msg = await storage.send_message(user["id"], body.receiver_id, body.content.strip())
    return {"message": msg}


@router.post("/read/{other_id}")
async def mark_read(
    other_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Mark all messages from other_id as read."""
    count = await storage.mark_read(user["id"], other_id)
    return {"ok": True, "count": count}


@router.get("/unread-count")
async def unread_count(
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get total unread message count."""
    count = await storage.get_unread_count(user["id"])
    return {"count": count}
