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


@router.post("/card/receive")
async def receive_card(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive a public card replica from a peer node.

    Authenticated via HMAC-SHA256, NOT JWT.
    Upserts into remote_cards (INSERT if new, UPDATE if existing) as a
    read-only copy with no FK dependencies on local texts/users.
    """
    body = await request.json()
    card = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, card)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    required = ("id", "user_id", "name", "card_json", "visibility", "origin_region")
    missing = [f for f in required if not card.get(f)]
    if missing:
        raise HTTPException(400, f"Missing required fields: {', '.join(missing)}")

    try:
        await storage.upsert_remote_card(
            card_id=card["id"],
            origin_region=card["origin_region"],
            user_id=card["user_id"],
            name=card.get("name", ""),
            card_json=card.get("card_json", "{}"),
            avatar_data=card.get("avatar_data", ""),
            market_description=card.get("market_description", ""),
            market_tags=card.get("market_tags", ""),
            origin_created_at=card.get("created_at", ""),
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to upsert received card: {exc}")

    return {"ok": True, "card_id": card["id"]}


@router.post("/card/delete")
async def receive_card_delete(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive a card-delete propagation from a peer node.

    Authenticated via HMAC-SHA256, NOT JWT.
    Idempotent: deleting an already-deleted (or never-synced) card returns 200.
    """
    body = await request.json()
    payload = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, payload)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    card_id = str(payload.get("target_id", ""))
    if not card_id:
        raise HTTPException(400, "Missing target_id")

    try:
        await storage.delete_remote_card(card_id)
    except Exception as exc:
        raise HTTPException(500, f"Failed to delete remote card: {exc}")

    return {"ok": True, "target_id": card_id}


@router.post("/dm/retract")
async def receive_dm_retract(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive a DM retract propagation from a peer node.

    Authenticated via HMAC-SHA256, NOT JWT.
    Idempotent: retracting an already-retracted (or missing) message returns 200.
    """
    body = await request.json()
    payload = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, payload)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    message_id = str(payload.get("target_id", ""))
    if not message_id:
        raise HTTPException(400, "Missing target_id")

    try:
        await storage.retract_dm_message(message_id)
    except Exception as exc:
        raise HTTPException(500, f"Failed to retract DM: {exc}")

    return {"ok": True, "target_id": message_id}


@router.post("/invite-code/receive")
async def receive_invite_code(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive an invite code from a peer node.

    Authenticated via HMAC-SHA256, NOT JWT.
    Idempotent: re-delivery of an existing code is silently ignored.
    """
    body = await request.json()
    payload = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, payload)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    code = str(payload.get("code", ""))
    created_by = str(payload.get("created_by", "peer"))
    if not code:
        raise HTTPException(400, "Missing required field: code")

    existing = await storage.get_invite_code(code)
    if existing:
        return {"ok": True, "duplicate": True}

    try:
        await storage.create_invite_code(code, created_by)
    except Exception as exc:
        raise HTTPException(500, f"Failed to store invite code: {exc}")

    return {"ok": True}


@router.post("/invite-code/delete")
async def receive_invite_code_delete(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive an invite-code delete propagation from a peer node.

    Authenticated via HMAC-SHA256, NOT JWT.
    Idempotent: deleting an already-deleted (or never-synced) code returns 200.
    """
    body = await request.json()
    payload = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, payload)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    code = str(payload.get("code", ""))
    if not code:
        raise HTTPException(400, "Missing required field: code")

    try:
        await storage.delete_invite_code(code)
    except Exception as exc:
        raise HTTPException(500, f"Failed to delete invite code: {exc}")

    return {"ok": True}


@router.post("/user/purge")
async def receive_user_purge(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive a user-purge propagation from a peer node.

    Authenticated via HMAC-SHA256, NOT JWT.
    Idempotent: purging an already-purged (or never-synced) user returns 200.
    """
    body = await request.json()
    payload = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, payload)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    user_id = str(payload.get("target_id", ""))
    if not user_id:
        raise HTTPException(400, "Missing target_id")

    try:
        counts = await storage.purge_remote_user_data(user_id)
    except Exception as exc:
        raise HTTPException(500, f"Failed to purge user data: {exc}")

    return {"ok": True, "target_id": user_id, "deleted": counts}


@router.post("/admin/users")
async def receive_admin_users(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> list[dict]:
    """Return admin-safe user fields for cross-border admin view.

    Authenticated via HMAC-SHA256 (inter_node_auth), NOT JWT.
    Only returns whitelisted admin fields — no password_hash, api_key, or
    any secrets from user_secrets table.  This is the read-only view used
    by the peer node's admin panel.
    """
    body = await request.json()
    payload = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, payload)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    return await storage.get_all_users_admin_fields()


@router.post("/user/sync")
async def receive_user_sync(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive a user profile from a peer node (lightweight stub).

    Authenticated via HMAC-SHA256, NOT JWT.
    Idempotent: upserts into remote_user_profiles table.
    """
    body = await request.json()
    payload = body if isinstance(body, dict) else {}

    auth_header = request.headers.get("Authorization", "")
    valid, reason = verify_auth_header(auth_header, payload)
    if not valid:
        raise HTTPException(401, f"Unauthorized: {reason}")

    user_id = str(payload.get("id", ""))
    username = str(payload.get("username", ""))
    home_region = str(payload.get("home_region", ""))
    avatar_data = str(payload.get("avatar_data", ""))

    if not user_id or not username:
        raise HTTPException(400, "Missing required fields: id, username")

    try:
        await storage.upsert_remote_user_profile(user_id, username, home_region, avatar_data)
    except Exception as exc:
        raise HTTPException(500, f"Failed to store remote user profile: {exc}")

    return {"ok": True, "user_id": user_id}
