"""Card avatar persistence — save and load card avatar images."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deps import get_storage
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/cards", tags=["cards"])


class AvatarSaveRequest(BaseModel):
    data: str  # base64 data URL or raw base64


@router.get("/{card_id}/avatar")
async def get_card_avatar(
    card_id: str,
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get saved avatar for a card. Returns {data: base64_string} or 404."""
    data = await storage.get_card_avatar(card_id)
    if data is None:
        raise HTTPException(404, "Avatar not found")
    return {"data": data}


@router.put("/{card_id}/avatar")
async def save_card_avatar(
    card_id: str,
    body: AvatarSaveRequest,
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Save avatar image (base64) for a card."""
    if not body.data or len(body.data) < 10:
        raise HTTPException(400, "Avatar data too short")
    try:
        await storage.save_card_avatar(card_id, body.data)
    except Exception as exc:
        raise HTTPException(500, f"Save failed: {exc}") from exc
    return {"ok": True}
