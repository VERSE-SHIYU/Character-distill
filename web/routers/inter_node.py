"""Inter-node endpoints: DM receive (HMAC-authenticated, no JWT).

下方 8 处原先 `except Exception → HTTPException(500, f"...: {exc}")`，把异常原文拼进
`detail` 上屏。现已删掉包装、交给 web/server.py 的统一出口：`detail` 只剩通用文案，
原文随**带堆栈的 ERROR** 进日志（告警邮件与面板都看得到），排障线索不再靠响应体。
对端也不读 `detail`（`web/cross_border_sync.py` 只看状态码），收掉不影响节点协作。
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request

import admin_user_ops
from deps import get_storage
from inter_node_auth import verify_inter_node_request
from limiter import limiter
from storage.base import StorageBase

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/inter-node", tags=["inter-node"])


async def _verified_payload(request: Request, storage: StorageBase) -> dict:
    """本路由所有接口的唯一验签入口：读原始请求体 → 验签（v2 或 v1）→ 返回 JSON 对象。

    v2 的摘要算在**原始字节**上，所以必须先 `request.body()` 再解析，不能反过来。
    请求体不是 JSON 对象时按空对象处理（与原先各接口的写法一致），缺字段由各接口判 400。
    """
    raw = await request.body()
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {}
    payload = parsed if isinstance(parsed, dict) else {}
    valid, reason, version = await verify_inter_node_request(request, raw, payload, storage)
    if not valid:
        logger.warning("inter-node rejected: path=%s version=%s reason=%s",
                       request.url.path, version, reason)
        raise HTTPException(401, f"Unauthorized: {reason}")
    logger.info("inter-node accepted: path=%s version=%s", request.url.path, version)
    return payload


@router.post("/dm/receive")
async def receive_dm(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive a cross-border DM forwarded from a peer node.

    Authenticated via HMAC-SHA256 (inter_node_auth), NOT JWT.
    Idempotent: re-delivery of the same message_id is silently ignored.
    """
    msg = await _verified_payload(request, storage)

    msg_id = msg.get("id", "")
    sender_id = msg.get("sender_id", "")
    receiver_id = msg.get("receiver_id", "")
    content = msg.get("content", "")

    if not all([msg_id, sender_id, receiver_id, content]):
        raise HTTPException(400, "Missing required message fields")

    # Idempotent insert: ON CONFLICT DO NOTHING via store check
    existing = await storage.get_dm_message_unscoped(msg_id)
    if existing:
        return {"ok": True, "duplicate": True}

    result = await storage.send_message(sender_id, receiver_id, content, cross_border_synced=1)

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
    card = await _verified_payload(request, storage)

    required = ("id", "user_id", "name", "card_json", "visibility", "origin_region")
    missing = [f for f in required if not card.get(f)]
    if missing:
        raise HTTPException(400, f"Missing required fields: {', '.join(missing)}")

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
    payload = await _verified_payload(request, storage)

    card_id = str(payload.get("target_id", ""))
    if not card_id:
        raise HTTPException(400, "Missing target_id")

    await storage.delete_remote_card(card_id)

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
    payload = await _verified_payload(request, storage)

    message_id = str(payload.get("target_id", ""))
    if not message_id:
        raise HTTPException(400, "Missing target_id")

    await storage.retract_dm_message(message_id)

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
    payload = await _verified_payload(request, storage)

    code = str(payload.get("code", ""))
    created_by = str(payload.get("created_by", "peer"))
    if not code:
        raise HTTPException(400, "Missing required field: code")

    existing = await storage.get_invite_code(code)
    if existing:
        return {"ok": True, "duplicate": True}

    await storage.create_invite_code(code, created_by, propagate=False)  # 从对端来的，不回传

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
    payload = await _verified_payload(request, storage)

    code = str(payload.get("code", ""))
    if not code:
        raise HTTPException(400, "Missing required field: code")

    await storage.delete_invite_code(code, propagate=False)  # 从对端来的，不回传

    return {"ok": True}


@router.post("/invite-code/used")
async def receive_invite_code_used(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """对端通知某个邀请码已被使用（隐私政策 3.2(4)：只同步「已使用」，不含使用者身份）。

    Idempotent：码已被使用（或本机没有这个码）照样回 200 —— 对端发件箱据此删掉这一行。
    """
    payload = await _verified_payload(request, storage)
    code = str(payload.get("code", ""))
    if not code:
        raise HTTPException(400, "Missing required field: code")
    marked = await storage.mark_invite_used_from_peer(code)
    return {"ok": True, "marked": marked}


@router.post("/user/purge")
async def receive_user_purge(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Receive a user-purge propagation from a peer node.

    Authenticated via HMAC-SHA256, NOT JWT.
    Idempotent: purging an already-purged (or never-synced) user returns 200.
    """
    payload = await _verified_payload(request, storage)

    user_id = str(payload.get("target_id", ""))
    if not user_id:
        raise HTTPException(400, "Missing target_id")

    counts = await storage.purge_remote_user_data(user_id)

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
    payload = await _verified_payload(request, storage)

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
    payload = await _verified_payload(request, storage)

    user_id = str(payload.get("id", ""))
    username = str(payload.get("username", ""))
    home_region = str(payload.get("home_region", ""))
    avatar_data = str(payload.get("avatar_data", ""))
    # 缺省 False：滚动发布期间旧版发送方不带这个字段，按「正常」处理。
    is_disabled = payload.get("is_disabled", False) is True

    if not user_id or not username:
        raise HTTPException(400, "Missing required fields: id, username")

    await storage.upsert_remote_account(user_id, username, home_region, avatar_data,
                                        is_disabled=is_disabled)

    return {"ok": True, "user_id": user_id}


#: 与发起端 `routers/admin.py::PEER_SET_DISABLED_OP` 同值。请求体里必须带这个操作名：
#: 现行签名只覆盖请求体、不覆盖路径，别的接口签出来的请求体若字段凑巧兼容，就能搬到
#: 这里来 —— 校验操作名把这份请求体绑定到本接口（字段名也刻意不与其它接收端重合）。
SET_DISABLED_OP = "admin_set_user_disabled"


@router.post("/admin/user-disabled")
@limiter.limit("30/minute")
async def receive_admin_set_user_disabled(
    request: Request,
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """对端管理员禁用 / 启用本节点的用户（谁的用户谁执行）。

    Authenticated via HMAC-SHA256, NOT JWT. 执行走 `admin_user_ops.set_user_disabled`，
    与本地路由同一份前置条件（404 / 不能禁用自己）。
    """
    payload = await _verified_payload(request, storage)

    if payload.get("op") != SET_DISABLED_OP:
        raise HTTPException(400, "op 与接口不符")
    subject = str(payload.get("subject_user_id") or "")
    operator = str(payload.get("operator_id") or "")
    disabled = payload.get("disabled")
    request_id = str(payload.get("request_id") or "")
    if not subject or not operator or not isinstance(disabled, bool):
        raise HTTPException(400, "Missing required fields: subject_user_id, operator_id, disabled")

    try:
        await admin_user_ops.set_user_disabled(
            storage, target_id=subject, operator_id=operator, disabled=disabled,
        )
    except HTTPException as exc:
        logger.warning(
            "peer admin set_disabled refused: request_id=%s operator=%s subject=%s "
            "disabled=%s status=%s", request_id, operator, subject, disabled, exc.status_code)
        raise
    logger.info(
        "peer admin set_disabled applied: request_id=%s operator=%s subject=%s disabled=%s",
        request_id, operator, subject, disabled)
    return {"ok": True}
