"""Cross-border DM + card forwarding: shared functions + background resync loop.

All HMAC-signed peer forwarding goes through forward_dm_to_peer() or
forward_card_to_peer() so the payload-construction and signing logic has a
single source of truth per domain.
"""

from __future__ import annotations

import asyncio
import logging
import os

from core.nonfatal import nonfatal
from storage.base import StorageBase

logger = logging.getLogger(__name__)


async def forward_dm_to_peer(msg: dict, storage: StorageBase) -> bool:
    """Forward one DM to the peer node via HMAC-signed HTTP POST.

    Builds an explicit string-typed payload (no datetime/dict surprises),
    signs it with inter-node HMAC, POSTs to the peer's receive endpoint.

    Returns True if the peer acknowledged (HTTP 200), False otherwise.
    The caller is responsible for updating cross_border_synced on success.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {
        "id": msg["id"],
        "sender_id": msg["sender_id"],
        "receiver_id": msg["receiver_id"],
        "content": msg["content"],
        "created_at": str(msg["created_at"]),
    }
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/dm/receive",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception as exc:
        logger.warning(
            "DM forward failed: msg_id=%s error=%r", msg.get("id"), exc, exc_info=True,
        )
        return False


async def forward_card_to_peer(card: dict, storage: StorageBase) -> bool:
    """Forward one public card to the peer node via HMAC-signed HTTP POST.

    Builds an explicit string-typed payload and POSTs to the peer's
    card receive endpoint.  Separate from forward_dm_to_peer because
    the payload fields and endpoint path are different.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    # origin_region = sender's home_region
    origin_region = ""
    try:
        owner = await storage.get_user_by_id(card.get("user_id", ""))
        if owner:
            origin_region = owner.get("home_region", "")
    except Exception as exc:
        # 降级而非失败：origin_region 留空，卡片本体照发
        logger.warning(
            "Card forward: owner region unreadable (card_id=%s error=%r)",
            card.get("id"), exc, exc_info=True,
        )

    payload = {
        "id": card["id"],
        "user_id": card.get("user_id", ""),
        "origin_region": origin_region,
        "name": card.get("name", ""),
        "card_json": card.get("card_json", "{}"),
        "avatar_data": card.get("avatar_data", ""),
        "visibility": card.get("visibility", "public"),
        "market_description": card.get("market_description", ""),
        "market_tags": card.get("market_tags", ""),
        "created_at": str(card.get("created_at", "")),
    }
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/card/receive",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception as exc:
        logger.warning(
            "Card forward failed: card_id=%s error=%r", card.get("id"), exc, exc_info=True,
        )
        return False


_ENDPOINT_MAP: dict[str, str] = {
    "card_delete": "/api/inter-node/card/delete",
    "dm_retract": "/api/inter-node/dm/retract",
    "user_purge": "/api/inter-node/user/purge",
}


async def forward_delete_to_peer(op_type: str, target_id: str, payload: str, storage: StorageBase) -> bool:
    """Forward a delete/retract/purge intent to the peer node.

    The op_type determines the endpoint:
      card_delete → /api/inter-node/card/delete
      dm_retract  → /api/inter-node/dm/retract
      user_purge  → /api/inter-node/user/purge

    Returns True if the peer acknowledged (HTTP 200), False otherwise.
    The caller (resync loop) is responsible for removing the outbox row.

    Every failure path prints op_type / target_id plus the status code or the
    exception — a bare False is indistinguishable from "there was nothing to
    send", so the row would sit in the outbox with nothing to debug from.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    endpoint = _ENDPOINT_MAP.get(op_type)
    if not endpoint:
        logger.error("Unknown delete op_type: %s", op_type)
        return False

    from inter_node_auth import create_auth_header

    body = {
        "op_type": op_type,
        "target_id": target_id,
        "payload": payload,
    }
    headers = create_auth_header(body)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}{endpoint}",
                json=body,
                headers=headers,
            )
        if resp.status_code != 200:
            logger.error(
                "Delete forward rejected: op_type=%s target_id=%s status=%s",
                op_type, target_id, resp.status_code,
            )
            return False
        return True
    except Exception as exc:
        logger.error(
            "Delete forward failed: op_type=%s target_id=%s error=%r",
            op_type, target_id, exc, exc_info=True,
        )
        return False


async def forward_invite_code_to_peer(record: dict) -> bool:
    """Forward a newly created invite code to the peer node.

    Builds a string-typed payload, signs with HMAC, POSTs to the peer's
    invite-code receive endpoint.  Best-effort: returns False on failure,
    caller is not expected to retry.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {
        "code": str(record.get("code", "")),
        "created_by": str(record.get("created_by", "")),
    }
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/invite-code/receive",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception as exc:
        # 不记 code 本身（邀请码是凭据），用 created_by 定位是哪一次
        logger.error(
            "Invite-code forward failed (no retry): created_by=%s error=%r",
            record.get("created_by"), exc, exc_info=True,
        )
        return False


async def forward_invite_code_delete_to_peer(code: str) -> bool:
    """Forward an invite-code delete to the peer node.

    Best-effort: returns False on failure, caller is not expected to retry.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {"code": code}
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/invite-code/delete",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception as exc:
        # 不记 code 本身（邀请码是凭据）：这条也无重试，对端会一直留着这个码
        logger.error(
            "Invite-code delete forward failed (no retry): error=%r", exc, exc_info=True,
        )
        return False


async def forward_user_profile_to_peer(user_id: str, username: str, home_region: str, avatar_data: str = "") -> bool:
    """Forward a user profile to the peer node (lightweight stub sync).

    Best-effort: returns False on failure.  Caller is not expected to retry.

    A previous version of this docstring said the profile would be synced when
    the first DM exchange happens.  That is not true: the peer's
    ``/api/inter-node/dm/receive`` only inserts the message and never writes the
    user row.  So once this forward fails, the peer stays without the profile
    until some other explicit forward succeeds.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {
        "id": user_id,
        "username": username,
        "home_region": home_region,
        "avatar_data": avatar_data,
    }
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/user/sync",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception as exc:
        logger.error(
            "User-profile forward failed (no retry): user_id=%s error=%r",
            user_id, exc, exc_info=True,
        )
        return False


async def _resync_once(storage: StorageBase) -> None:
    """One resync round: DMs, cards, then the delete outbox.

    The three sections are independent — each guards its own query, so one
    failing does not cancel the others.  DM/card rows get a `synced` flag;
    outbox rows are **removed** once the peer acknowledges them.

    Silent no-op when PEER_NODE_URL is unset (single-node deployment).
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return

    # ── DM resync ──
    try:
        msgs = await storage.get_unsynced_cross_border_messages_unscoped(limit=100)
    except Exception as exc:
        logger.error("DM query failed: %s", exc, exc_info=True)
    else:
        for msg in msgs:
            ok = await forward_dm_to_peer(msg, storage)
            if ok:
                async with nonfatal(
                    "cross_border_resync", f"mark DM synced for {msg['id']}",
                ):
                    await storage.mark_message_synced(msg["id"])

    # ── Card resync ──
    try:
        cards = await storage.get_unsynced_cross_border_cards_unscoped(limit=100)
    except Exception as exc:
        logger.error("Card query failed: %s", exc, exc_info=True)
    else:
        for card in cards:
            ok = await forward_card_to_peer(card, storage)
            if ok:
                async with nonfatal(
                    "cross_border_resync", f"mark card synced for {card['id']}",
                ):
                    await storage.mark_card_synced(card["id"])

    # ── Delete propagation resync ──
    try:
        pending = await storage.get_pending_delete_propagations(limit=100)
    except Exception as exc:
        logger.error("Delete outbox query failed: %s", exc, exc_info=True)
    else:
        for row in pending:
            ok = await forward_delete_to_peer(
                row["op_type"], row["target_id"], row.get("payload", ""), storage,
            )
            if ok:
                async with nonfatal(
                    "cross_border_resync", f"remove delete propagation {row['id']}",
                ):
                    await storage.remove_delete_propagation(row["id"])


async def _cross_border_resync_loop() -> None:
    """Retry cross-border sync every 60 seconds — one `_resync_once` per tick."""
    while True:
        await asyncio.sleep(60)

        from deps import get_storage

        await _resync_once(get_storage())
