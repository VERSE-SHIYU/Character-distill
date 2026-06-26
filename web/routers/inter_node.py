"""Inter-node endpoints: DM receive (HMAC-authenticated, no JWT)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from deps import get_storage
from inter_node_auth import verify_auth_header
from storage.base import StorageBase


router = APIRouter(prefix="/api/inter-node", tags=["inter-node"])


@router.post("/dm/receive")
async def receive_dm(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive a cross-border DM forwarded from a peer node.

    Authenticated via HMAC-SHA256 (inter_node_auth), NOT JWT.
    Idempotent: re-delivery of the same message_id is silently ignored.
    """
    body = await request.json()
    msg = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, msg)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    msg_id = msg.get("id", "")
    sender_id = msg.get("sender_id", "")
    receiver_id = msg.get("receiver_id", "")
    content = msg.get("content", "")

    if not all([msg_id, sender_id, receiver_id, content]):
        raise HTTPException(400, "Missing required message fields")

    # Idempotent insert: ON CONFLICT DO NOTHING via store check
    existing = await storage.get_dm_message(msg_id)
    if existing:
        return {"ok": True, "duplicate": True}

    try:
        result = await storage.send_message(sender_id, receiver_id, content, cross_border_synced=1)
    except Exception as exc:
        raise HTTPException(500, f"Failed to store received message: {exc}")

    return {"ok": True, "message": result}
