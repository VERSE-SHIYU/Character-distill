"""Cross-border DM + card forwarding: shared functions + background resync loop.

每类载荷各有一个 `forward_*_to_peer`，只负责「载荷长什么样、失败怎么记」；
地址、签名、https 校验与超时全在 `peer_client.post_to_peer` 一处（原先这里 6 份手抄）。
"""

from __future__ import annotations

import asyncio
import logging

import peer_client
from core.nonfatal import nonfatal
from storage.base import StorageBase

logger = logging.getLogger(__name__)


async def _forward(path: str, payload: dict, *, what: str, level: int) -> bool:
    """发一条，返回对端是否确认（HTTP 200）。失败按 `level` 记一条，带状态码或异常。

    `what` 是调用方给的定位信息（id 之类），**不许放凭据**（邀请码本身就是凭据）。
    未配置对端（单节点部署）静默返回 False —— 那不是失败。
    """
    try:
        resp = await peer_client.post_to_peer(path, payload)
    except peer_client.PeerNotConfigured:
        return False
    except Exception as exc:
        logger.log(level, "Peer forward failed: %s path=%s error=%r", what, path, exc,
                   exc_info=True)
        return False
    if resp.status_code != 200:
        logger.log(level, "Peer forward rejected: %s path=%s status=%s", what, path,
                   resp.status_code)
        return False
    return True


async def forward_dm_to_peer(msg: dict, storage: StorageBase) -> bool:
    """Forward one DM to the peer. Caller marks it synced on True; resync retries on False."""
    if not peer_client.peer_url():  # 单节点部署：什么都不做（先判，不白组请求体）
        return False
    payload = {
        "id": msg["id"],
        "sender_id": msg["sender_id"],
        "receiver_id": msg["receiver_id"],
        "content": msg["content"],
        "created_at": str(msg["created_at"]),
    }
    return await _forward("/api/inter-node/dm/receive", payload,
                          what=f"dm msg_id={msg.get('id')}", level=logging.WARNING)


async def forward_card_to_peer(card: dict, storage: StorageBase) -> bool:
    """Forward one public card to the peer. Resync retries on False."""
    if not peer_client.peer_url():  # 单节点部署：什么都不做（先判，不白组请求体）
        return False
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
    return await _forward("/api/inter-node/card/receive", payload,
                          what=f"card card_id={card.get('id')}", level=logging.WARNING)


_ENDPOINT_MAP: dict[str, str] = {
    "card_delete": "/api/inter-node/card/delete",
    "dm_retract": "/api/inter-node/dm/retract",
    "user_purge": "/api/inter-node/user/purge",
}


async def forward_delete_to_peer(op_type: str, target_id: str, payload: str, storage: StorageBase) -> bool:
    """Forward a delete/retract/purge intent to the peer node.

    The op_type determines the endpoint (see `_ENDPOINT_MAP`). Returns True if the
    peer acknowledged (HTTP 200); the resync loop removes the outbox row on True.
    Every failure path logs op_type / target_id plus the status code or the exception.
    """
    if not peer_client.peer_url():  # 单节点部署：什么都不做（先判，不白组请求体）
        return False
    endpoint = _ENDPOINT_MAP.get(op_type)
    if not endpoint:
        logger.error("Unknown delete op_type: %s", op_type)
        return False
    body = {"op_type": op_type, "target_id": target_id, "payload": payload}
    return await _forward(endpoint, body, what=f"op_type={op_type} target_id={target_id}",
                          level=logging.ERROR)


async def forward_invite_code_to_peer(record: dict) -> bool:
    """Forward a newly created invite code. Best-effort, **no retry**."""
    if not peer_client.peer_url():  # 单节点部署：什么都不做（先判，不白组请求体）
        return False
    payload = {
        "code": str(record.get("code", "")),
        "created_by": str(record.get("created_by", "")),
    }
    # 不记 code 本身（邀请码是凭据），用 created_by 定位是哪一次
    return await _forward("/api/inter-node/invite-code/receive", payload,
                          what=f"invite-code (no retry) created_by={record.get('created_by')}",
                          level=logging.ERROR)


async def forward_invite_code_delete_to_peer(code: str) -> bool:
    """Forward an invite-code delete. Best-effort, **no retry**."""
    if not peer_client.peer_url():  # 单节点部署：什么都不做（先判，不白组请求体）
        return False
    # 不记 code 本身（邀请码是凭据）：这条也无重试，对端会一直留着这个码
    return await _forward("/api/inter-node/invite-code/delete", {"code": code},
                          what="invite-code delete (no retry)", level=logging.ERROR)


async def forward_user_profile_to_peer(user_id: str, username: str, home_region: str, avatar_data: str = "") -> bool:
    """Forward a user profile to the peer node (lightweight stub sync). Best-effort, no retry.

    A previous version of this docstring said the profile would be synced when
    the first DM exchange happens.  That is not true: the peer's
    ``/api/inter-node/dm/receive`` only inserts the message and never writes the
    user row.  So once this forward fails, the peer stays without the profile
    until some other explicit forward succeeds.
    """
    if not peer_client.peer_url():  # 单节点部署：什么都不做（先判，不白组请求体）
        return False
    payload = {
        "id": user_id,
        "username": username,
        "home_region": home_region,
        "avatar_data": avatar_data,
    }
    return await _forward("/api/inter-node/user/sync", payload,
                          what=f"user-profile (no retry) user_id={user_id}", level=logging.ERROR)


async def _resync_once(storage: StorageBase) -> None:
    """One resync round: DMs, cards, then the delete outbox.

    The three sections are independent — each guards its own query, so one
    failing does not cancel the others.  DM/card rows get a `synced` flag;
    outbox rows are **removed** once the peer acknowledges them.

    Silent no-op when PEER_NODE_URL is unset (single-node deployment).
    """
    if not peer_client.peer_url():
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
