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
from core.node import node_region
from storage.base import USER_PROFILE_OP, StorageBase

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


async def _post_to_peer(endpoint: str, body: dict, what: str) -> bool:
    """Sign `body` and POST it to the peer's `endpoint`; True only on HTTP 200.

    Shared by the outbox senders.  Every failure path logs `what` (op_type /
    target_id) plus the status code or the exception — a bare False is
    indistinguishable from "there was nothing to send", so the row would sit in
    the outbox with nothing to debug from.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    import httpx

    # 签名也在 try 里：签名失败（如请求体序列化不了）只让这一行留待下一轮，不能抛出
    # `_resync_once` 把补发循环的后台任务整个带走。
    try:
        headers = create_auth_header(body)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{peer_url}{endpoint}", json=body, headers=headers)
        if resp.status_code != 200:
            logger.error("Outbox forward rejected: %s status=%s", what, resp.status_code)
            return False
        return True
    except Exception as exc:
        logger.error("Outbox forward failed: %s error=%r", what, exc, exc_info=True)
        return False


async def forward_delete_to_peer(op_type: str, target_id: str, payload: str, storage: StorageBase) -> bool:
    """Forward a delete/retract/purge intent to the peer node.

    The op_type determines the endpoint:
      card_delete → /api/inter-node/card/delete
      dm_retract  → /api/inter-node/dm/retract
      user_purge  → /api/inter-node/user/purge

    Returns True if the peer acknowledged (HTTP 200), False otherwise.
    The caller (resync loop) is responsible for removing the outbox row.
    """
    endpoint = _ENDPOINT_MAP.get(op_type)
    if not endpoint:
        logger.error("Unknown delete op_type: %s", op_type)
        return False

    body = {
        "op_type": op_type,
        "target_id": target_id,
        "payload": payload,
    }
    return await _post_to_peer(endpoint, body, f"op_type={op_type} target_id={target_id}")


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


async def forward_user_profile_to_peer(user_id: str, storage: StorageBase) -> bool:
    """Send one `user_profile` outbox row: read the user's current profile, POST it.

    Returns True when the row is finished and may be removed: the peer
    acknowledged, or there is nothing this node should announce —
      - the user no longer exists here (a `user_purge` row carries the delete);
      - the user is not homed on this node (e.g. a demo account mirrored from
        the peer): each node announces only its own region's users.
    Returns False to keep the row for the next round.

    The profile is read at send time, not stored in the row, so a round always
    sends the latest version; see `USER_PROFILE_OP` for the version stamp.
    """
    what = f"op_type={USER_PROFILE_OP} target_id={user_id}"
    try:
        user = await storage.get_user_by_id(user_id)
    except Exception as exc:
        logger.error("Outbox profile read failed: %s error=%r", what, exc, exc_info=True)
        return False
    if user is None:
        logger.info("Outbox profile dropped, user no longer exists: %s", what)
        return True
    if user.get("home_region") != node_region():
        logger.info("Outbox profile dropped, user homed on %s not here: %s",
                    user.get("home_region"), what)
        return True

    body = {
        "id": user["id"],
        "username": user.get("username", ""),
        "home_region": user["home_region"],
        "avatar_data": user.get("avatar_data") or "",
    }
    return await _post_to_peer("/api/inter-node/user/sync", body, what)


async def _forward_outbox_row(row: dict, storage: StorageBase) -> bool:
    """Route one outbox row to its sender by op_type."""
    if row["op_type"] == USER_PROFILE_OP:
        return await forward_user_profile_to_peer(row["target_id"], storage)
    return await forward_delete_to_peer(
        row["op_type"], row["target_id"], row.get("payload", ""), storage,
    )


async def _resync_once(storage: StorageBase) -> None:
    """One resync round: DMs, cards, then the outbox (deletes + user profiles).

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

    # ── Outbox resync (delete propagations + user profiles) ──
    try:
        pending = await storage.get_pending_delete_propagations(limit=100)
    except Exception as exc:
        logger.error("Delete outbox query failed: %s", exc, exc_info=True)
    else:
        for row in pending:
            ok = await _forward_outbox_row(row, storage)
            if ok:
                async with nonfatal(
                    "cross_border_resync", f"remove outbox row {row['id']}",
                ):
                    await storage.remove_delete_propagation(row["id"], row.get("payload"))


async def _cross_border_resync_loop() -> None:
    """Retry cross-border sync every 60 seconds — one `_resync_once` per tick."""
    while True:
        await asyncio.sleep(60)

        from deps import get_storage

        await _resync_once(get_storage())
