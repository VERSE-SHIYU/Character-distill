"""Cross-border DM + card forwarding: shared functions + background resync loop.

每类载荷各有一个 `forward_*_to_peer`，只负责「载荷长什么样、失败怎么记」；
地址、签名、https 校验与超时全在 `peer_client.post_to_peer` 一处（原先这里 6 份手抄）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum

import httpx

import peer_client
from core.nonfatal import nonfatal
from core.node import node_region
from storage.base import USER_PROFILE_OP, StorageBase

logger = logging.getLogger(__name__)


class ForwardResult(Enum):
    """一次补发发送的结果。"""

    DELIVERED = "delivered"   # 已送达
    FAILED = "failed"         # 没送达，保留该行继续下一行
    PEER_DOWN = "peer_down"   # 对端整体不可用，整轮中止


_PEER_DOWN_STATUSES = frozenset({401, 403, 429, 502, 503, 504})


def _classify_forward(exc: Exception | None, status: int | None) -> ForwardResult:
    """发送结果的唯一判定：TransportError / PeerConfigError / 401/403/429/502/503/504 → PEER_DOWN；200 → DELIVERED；其余 → FAILED。"""
    if exc is not None:
        if isinstance(exc, (httpx.TransportError, peer_client.PeerConfigError)):
            return ForwardResult.PEER_DOWN
        return ForwardResult.FAILED
    if status == 200:
        return ForwardResult.DELIVERED
    if status in _PEER_DOWN_STATUSES:
        return ForwardResult.PEER_DOWN
    return ForwardResult.FAILED


def _abort_resync(section: str, path: str, reason: str) -> None:
    """对端整体不可用：记一条固定 ERROR，调用方随即中止整轮。"""
    logger.error("Peer unavailable, resync round aborted: section=%s path=%s reason=%s",
                 section, path, reason)


async def _forward(path: str, payload: dict, *, what: str, level: int) -> tuple[ForwardResult, str, str]:
    """发一条，返回 (结果, 原因, path)。只对 FAILED 按 `level` 记一条日志；PEER_DOWN 交给调用方。

    `what` 是调用方给的定位信息（id 之类），**不许放凭据**（邀请码本身就是凭据）。
    """
    exc: Exception | None = None
    status: int | None = None
    try:
        resp = await peer_client.post_to_peer(path, payload)
        status = resp.status_code
    except Exception as e:  # noqa: BLE001 — 分类在 _classify_forward 里做
        exc = e
    result = _classify_forward(exc, status)
    reason = repr(exc) if exc is not None else f"status={status}"
    if result is ForwardResult.FAILED:
        logger.log(level, "Peer forward failed: %s path=%s reason=%s", what, path, reason,
                   exc_info=(exc is not None))
    return result, reason, path


async def forward_dm_to_peer(msg: dict, storage: StorageBase) -> tuple[ForwardResult, str, str]:
    """Forward one DM to the peer. Caller marks it synced on DELIVERED."""
    if not peer_client.peer_url():  # 单节点部署：什么都不做（先判，不白组请求体）
        return ForwardResult.FAILED, "未配置对端（单节点）", ""
    payload = {
        "id": msg["id"],
        "sender_id": msg["sender_id"],
        "receiver_id": msg["receiver_id"],
        "content": msg["content"],
        "created_at": str(msg["created_at"]),
    }
    return await _forward("/api/inter-node/dm/receive", payload,
                          what=f"dm msg_id={msg.get('id')}", level=logging.WARNING)


async def forward_card_to_peer(card: dict, storage: StorageBase) -> tuple[ForwardResult, str, str]:
    """Forward one public card to the peer."""
    if not peer_client.peer_url():  # 单节点部署：什么都不做（先判，不白组请求体）
        return ForwardResult.FAILED, "未配置对端（单节点）", ""
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


#: 发件箱的操作类型 → 对端接口。前三类是删除同步（请求体是 {op_type, target_id, payload}，
#: payload 为空串）；三类邀请码的请求体就是入队时写好的 payload（JSON）。用户资料不在表里：
#: 它的 payload 是版本戳，由 `forward_user_profile_to_peer` 发送时现读资料。
_OUTBOX_ENDPOINTS: dict[str, str] = {
    "card_delete": "/api/inter-node/card/delete",
    "dm_retract": "/api/inter-node/dm/retract",
    "user_purge": "/api/inter-node/user/purge",
    "invite_create": "/api/inter-node/invite-code/receive",
    "invite_delete": "/api/inter-node/invite-code/delete",
    "invite_used": "/api/inter-node/invite-code/used",
}
_LEGACY_DELETE_OPS = frozenset({"card_delete", "dm_retract", "user_purge"})


def _ordering_key(op_type: str, target_id: str) -> tuple[str, str]:
    """同一个键的记录必须按写入顺序送达。邀请码的新增 / 已使用 / 删除共用一个键
    （同一个码），其余各自独立。"""
    if op_type.startswith("invite_"):
        return ("invite", target_id)
    return (op_type, target_id)


async def forward_outbox_to_peer(op_type: str, target_id: str, payload: str) -> tuple[ForwardResult, str, str]:
    """发件箱的一行 → 对端。不记 payload 本身（邀请码是凭据）。"""
    if not peer_client.peer_url():
        return ForwardResult.FAILED, "未配置对端（单节点）", ""
    endpoint = _OUTBOX_ENDPOINTS.get(op_type)
    if not endpoint:
        logger.error("Unknown outbox op_type: %s", op_type)
        return ForwardResult.FAILED, f"未知 op_type={op_type}", ""
    if op_type in _LEGACY_DELETE_OPS:
        body = {"op_type": op_type, "target_id": target_id, "payload": payload}
        what = f"op_type={op_type} target_id={target_id}"
    else:
        try:
            body = json.loads(payload)
        except ValueError:
            logger.error("Outbox payload is not JSON: op_type=%s", op_type)
            return ForwardResult.FAILED, "payload 非 JSON", ""
        what = f"op_type={op_type}"  # 不带 target_id：邀请码的 target_id 就是码本身（凭据）
    return await _forward(endpoint, body, what=what, level=logging.ERROR)


async def forward_user_profile_to_peer(user_id: str, storage: StorageBase) -> tuple[ForwardResult, str, str]:
    """Send one `user_profile` outbox row: read the user's current profile, POST it.

    Returns DELIVERED when the row is finished and may be removed: the peer
    acknowledged, or there is nothing this node should announce —
      - the user no longer exists here (a `user_purge` row carries the delete);
      - the user is not homed on this node (e.g. a demo account mirrored from
        the peer): each node announces only its own region's users.
    Returns FAILED to keep the row for the next round.

    The profile is read at send time, not stored in the row, so a round always
    sends the latest version; see `USER_PROFILE_OP` for the version stamp.
    Only 3.2(1) fields go out: id / username / home_region / avatar_data.
    """
    if not peer_client.peer_url():
        return ForwardResult.FAILED, "未配置对端（单节点）", ""
    what = f"op_type={USER_PROFILE_OP} target_id={user_id}"
    try:
        user = await storage.get_user_by_id(user_id)
    except Exception as exc:
        logger.error("Outbox profile read failed: %s error=%r", what, exc, exc_info=True)
        return ForwardResult.FAILED, repr(exc), ""
    if user is None:
        logger.info("Outbox profile dropped, user no longer exists: %s", what)
        return ForwardResult.DELIVERED, "用户不存在", ""
    if user.get("home_region") != node_region():
        logger.info("Outbox profile dropped, user homed on %s not here: %s",
                    user.get("home_region"), what)
        return ForwardResult.DELIVERED, "用户归属对端", ""

    body = {
        "id": user["id"],
        "username": user.get("username", ""),
        "home_region": user["home_region"],
        "avatar_data": user.get("avatar_data") or "",
    }
    return await _forward("/api/inter-node/user/sync", body, what=what, level=logging.ERROR)


async def _forward_outbox_row(row: dict, storage: StorageBase) -> tuple[ForwardResult, str, str]:
    """Route one outbox row to its sender by op_type."""
    if row["op_type"] == USER_PROFILE_OP:
        return await forward_user_profile_to_peer(row["target_id"], storage)
    return await forward_outbox_to_peer(row["op_type"], row["target_id"], row.get("payload") or "")


async def _resync_once(storage: StorageBase) -> None:
    """One resync round: DMs, cards, then the outbox (deletes + user profiles).

    任一段拿到 PEER_DOWN 就记一条固定 ERROR 并立即中止整轮；已送达的行照常标记/删除。
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
            result, reason, path = await forward_dm_to_peer(msg, storage)
            if result is ForwardResult.PEER_DOWN:
                _abort_resync("dm", path, reason)
                return
            if result is ForwardResult.DELIVERED:
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
            result, reason, path = await forward_card_to_peer(card, storage)
            if result is ForwardResult.PEER_DOWN:
                _abort_resync("card", path, reason)
                return
            if result is ForwardResult.DELIVERED:
                async with nonfatal(
                    "cross_border_resync", f"mark card synced for {card['id']}",
                ):
                    await storage.mark_card_synced(card["id"])

    # ── Outbox resync（删除同步 + 邀请码 + 用户资料）──
    try:
        pending = await storage.get_pending_delete_propagations(limit=100)
    except Exception as exc:
        logger.error("Outbox query failed: %s", exc, exc_info=True)
    else:
        blocked: set[tuple[str, str]] = set()
        for row in pending:
            key = _ordering_key(row["op_type"], row["target_id"])
            if key in blocked:
                continue  # 同一个键前面那条这轮没送到：后面的不许越过它先送
            result, reason, path = await _forward_outbox_row(row, storage)
            if result is ForwardResult.PEER_DOWN:
                _abort_resync("outbox", path, reason)
                return
            if result is not ForwardResult.DELIVERED:
                blocked.add(key)
                continue
            async with nonfatal(
                "cross_border_resync", f"remove outbox row {row['id']}",
            ):
                # 按「发出时的 payload」删：资料行发送途中被换了版本戳就留到下一轮
                await storage.remove_delete_propagation(row["id"], row.get("payload"))


_wake: asyncio.Event | None = None


def wake_resync() -> None:
    """请补发循环立刻跑一轮（刚写了发件箱的路由调用）。循环没在跑（单节点 / 测试）时什么都不做。

    发送仍只由循环这一处做：路由不自己发，避免两处同时发同一行。
    """
    if _wake is not None:
        _wake.set()


async def _cross_border_resync_loop() -> None:
    """每 60 秒补发一轮；有路由调了 `wake_resync()` 就提前跑。"""
    global _wake
    _wake = asyncio.Event()
    while True:
        try:
            await asyncio.wait_for(_wake.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
        _wake.clear()

        from deps import get_storage

        await _resync_once(get_storage())
