"""Card avatar persistence — save and load card avatar images."""

from __future__ import annotations

import logging
from urllib.parse import quote

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from deps import get_storage
from limiter import limiter
from storage.base import StorageBase
from routers.auth import get_current_user

router = APIRouter(prefix="/api/cards", tags=["cards"])


class AvatarSaveRequest(BaseModel):
    data: str  # base64 data URL or raw base64


@router.get("/{card_id}/avatar")
@limiter.limit("60/minute")
async def get_card_avatar(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get saved avatar for a card. Returns {data: base64_string} or 404."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此角色卡")
    data = await storage.get_card_avatar(card_id)
    if data is None:
        raise HTTPException(404, "Avatar not found")
    return {"data": data}


@router.put("/{card_id}/avatar")
@limiter.limit("30/minute")
async def save_card_avatar(
    card_id: str,
    body: AvatarSaveRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Save avatar image (base64) for a card."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此角色卡")
    if not body.data or len(body.data) < 10:
        raise HTTPException(400, "Avatar data too short")
    try:
        await storage.save_card_avatar(card_id, body.data)
    except Exception as exc:
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    return {"ok": True}


@router.get("/{card_id}/export")
@limiter.limit("60/minute")
async def export_card(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> Response:
    """Export a character card's full JSON as a downloadable file."""
    record = await storage.get_card(card_id)
    if not record:
        raise HTTPException(404, "Card not found")
    if record.get("user_id") != user["id"]:
        raise HTTPException(403, "无权导出此角色卡")

    # Parse card_json to extract the character name
    card_json = record.get("card_json", "{}")
    try:
        import json
        parsed = json.loads(card_json) if isinstance(card_json, str) else card_json
    except Exception:
        parsed = {}
    char_name = parsed.get("name", record.get("name", card_id))

    safe_name = quote(f"{char_name}.json")
    return Response(
        content=card_json if isinstance(card_json, str) else json.dumps(card_json, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{safe_name}"
            ),
        },
    )


@router.get("/trash")
@limiter.limit("60/minute")
async def list_trash(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> list[dict]:
    """List soft-deleted cards for the current user."""
    return await storage.list_deleted_cards(user["id"])


@router.get("/{card_id}/detail")
@limiter.limit("60/minute")
async def get_card_detail(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get card detail with author info — works for both market and non-market cards."""
    card = await storage.get_card_detail(card_id, user["id"])
    if not card:
        raise HTTPException(404, "角色不存在")
    return card


@router.get("/{card_id}")
@limiter.limit("60/minute")
async def get_card(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get a character card by ID."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此角色卡")
    return card


@router.post("/{card_id}/restore")
@limiter.limit("30/minute")
async def restore_card_route(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Restore a soft-deleted card."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此角色卡")
    ok = await storage.restore_card(card_id)
    if not ok:
        raise HTTPException(500, "恢复失败")
    return {"ok": True}


@router.delete("/{card_id}/purge")
@limiter.limit("30/minute")
async def purge_card_route(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Permanently delete a card (irreversible)."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此角色卡")
    ok = await storage.purge_card(card_id)
    if not ok:
        raise HTTPException(500, "彻底删除失败")
    return {"ok": True}


@router.delete("/{card_id}")
@limiter.limit("30/minute")
async def delete_card_route(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Soft-delete a character card. Owner or admin can delete."""
    logger.debug("DELETE /api/cards/%s by user %s", card_id, user.get("id"))
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if not user.get("is_admin") and card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权删除此角色卡")
    ok = await storage.delete_card(card_id)
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True}
