"""Market: browse, search, fork, and like public character cards."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel

from deps import get_storage
from storage.sqlite_store import SQLiteStore
from routers.auth import get_current_user

router = APIRouter(prefix="/api/market", tags=["market"])


class ForkRequest(BaseModel):
    text_id: str = ""


class VisibilityUpdate(BaseModel):
    visibility: str


@router.get("/list")
async def list_cards(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("new", regex="^(new|hot)$"),
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """List public cards with pagination and sorting."""
    cards = await storage.list_public_cards(page, page_size, sort)
    total = await storage.list_public_cards_total()
    liked_ids = await storage.get_liked_card_ids(user["id"])

    for c in cards:
        c["liked_by_me"] = c["id"] in liked_ids

    return {"cards": cards, "total": total, "page": page, "page_size": page_size}


@router.get("/search")
async def search_cards(
    request: Request,
    q: str = Query("", min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Search public cards by name."""
    cards = await storage.search_public_cards(q, page, page_size)
    total = await storage.search_public_cards_total(q)
    liked_ids = await storage.get_liked_card_ids(user["id"])

    for c in cards:
        c["liked_by_me"] = c["id"] in liked_ids

    return {"cards": cards, "total": total, "page": page, "page_size": page_size}


@router.post("/{card_id}/fork")
async def fork_card(
    card_id: str,
    body: ForkRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Deep copy a public card into the user's own collection."""
    new_id = uuid.uuid4().hex[:12]
    new_card = await storage.fork_card(card_id, new_id, user["id"], body.text_id)
    if new_card is None:
        raise HTTPException(404, "Card not found or not public")
    return {"card_id": new_id, "card": new_card}


@router.post("/{card_id}/like")
async def like_card(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Toggle like on a public card."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    return await storage.toggle_like(card_id, user["id"])


@router.patch("/{card_id}/visibility")
async def set_visibility(
    card_id: str,
    body: VisibilityUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Set card visibility (public/private). Only the card owner can change it."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此角色卡")
    ok = await storage.update_card_visibility(card_id, body.visibility)
    if not ok:
        raise HTTPException(400, "visibility 必须是 'public' 或 'private'")
    return {"ok": True, "visibility": body.visibility}
