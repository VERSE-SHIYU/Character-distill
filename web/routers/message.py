"""Direct messaging: send, list conversations, view messages, mark read."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from deps import get_storage
from limiter import limiter
from storage.base import StorageBase
from routers.auth import get_current_user
from pydantic import BaseModel


class SendMessageRequest(BaseModel):
    receiver_id: str
    content: str


class ConsentRequest(BaseModel):
    target_region: str


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
    """Send a direct message.

    Cross-border flow: if sender and receiver are in different regions,
    checks for explicit consent (409 if missing), saves locally with
    cross_border_synced=0, then forwards to the peer node via HMAC-signed
    request. If the peer is unreachable the message stays locally with
    synced=0 (not lost).
    """
    if not body.content.strip():
        raise HTTPException(400, "消息不能为空")
    if body.receiver_id == user["id"]:
        raise HTTPException(400, "不能给自己发消息")
    receiver = await storage.get_user_by_id(body.receiver_id)
    if not receiver:
        raise HTTPException(404, "用户不存在")

    sender_region = user.get("home_region", "")
    receiver_region = receiver.get("home_region", "")

    # Cross-border DM
    if sender_region and receiver_region and sender_region != receiver_region:
        has_consent = await storage.has_cross_border_consent(
            user["id"], receiver_region, "direct_message",
        )
        if not has_consent:
            raise HTTPException(
                409,
                detail={
                    "need_consent": True,
                    "target_region": receiver_region,
                    "receiver_username": receiver.get("username", ""),
                },
            )

        # Save locally first (pending sync)
        msg = await storage.send_message(
            user["id"], body.receiver_id, body.content.strip(), cross_border_synced=0,
        )

        # Forward to peer node
        peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
        if peer_url:
            try:
                from inter_node_auth import create_auth_header

                # Build an explicit string-typed payload for signing + wire transfer.
                # Using the raw msg dict is unsafe: datetime fields get serialized to
                # strings by httpx.json() but are seen as datetime objects by json.dumps
                # inside _sign(), producing mismatched HMAC signatures.
                payload = {
                    "id": msg["id"],
                    "sender_id": msg["sender_id"],
                    "receiver_id": msg["receiver_id"],
                    "content": msg["content"],
                    "created_at": str(msg["created_at"]),
                }
                headers = create_auth_header(payload)
                import httpx

                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{peer_url}/api/inter-node/dm/receive",
                        json=payload,
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        await storage.mark_message_synced(msg["id"])
            except Exception as exc:
                # Already saved locally with synced=0 — don't lose the message
                print(f"[message] Cross-border forward failed for {msg['id']}: {exc}")

        return {"message": msg}

    # Same region: normal flow
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


@router.post("/consent")
@limiter.limit("30/minute")
async def grant_consent(
    request: Request,
    body: ConsentRequest,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Grant cross-border consent for DMs to target_region."""
    if not body.target_region.strip():
        raise HTTPException(400, "target_region 不能为空")
    await storage.grant_cross_border_consent(user["id"], body.target_region.strip(), "direct_message")
    return {"ok": True, "target_region": body.target_region.strip(), "scope": "direct_message"}


@router.delete("/consent")
@limiter.limit("30/minute")
async def revoke_consent(
    request: Request,
    body: ConsentRequest,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Revoke cross-border consent for DMs to target_region."""
    if not body.target_region.strip():
        raise HTTPException(400, "target_region 不能为空")
    await storage.revoke_cross_border_consent(user["id"], body.target_region.strip(), "direct_message")
    return {"ok": True, "target_region": body.target_region.strip(), "scope": "direct_message"}
