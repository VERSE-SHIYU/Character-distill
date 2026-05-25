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


class CommentRequest(BaseModel):
    content: str


class PostRequest(BaseModel):
    content: str
    visibility: str = "public"


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


@router.get("/card/{card_id}")
async def get_card_detail(
    card_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get a single public card detail with author info."""
    card = await storage.get_market_card_detail(card_id, user["id"])
    if not card:
        raise HTTPException(404, "角色不存在或未公开")
    return card


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


@router.get("/{card_id}/comments")
async def list_comments(
    card_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get all comments for a card."""
    comments = await storage.get_comments(card_id)
    return {"comments": comments}


@router.post("/{card_id}/comments")
async def add_comment(
    card_id: str,
    body: CommentRequest,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Add a comment to a card."""
    if not body.content.strip():
        raise HTTPException(400, "评论内容不能为空")
    comment = await storage.add_comment(card_id, user["id"], user["username"], body.content.strip())
    return comment


@router.get("/author/{user_id}")
async def get_author(
    user_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get author profile and their public data."""
    author = await storage.get_user_by_id(user_id)
    if not author:
        raise HTTPException(404, "用户不存在")
    cards = await storage.get_author_cards(user_id)
    following_ids = await storage.get_following(user["id"])
    followers_count = await storage.get_followers_count(user_id)
    following_count = await storage.get_following_count(user_id)
    texts = await storage.get_author_texts(user_id)
    return {
        "author": {k: v for k, v in author.items() if k != "password_hash"},
        "cards": cards,
        "texts": texts,
        "is_following": user_id in following_ids,
        "followers_count": followers_count,
        "following_count": following_count,
    }


@router.post("/author/{user_id}/follow")
async def toggle_follow_author(
    user_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Toggle follow/unfollow an author."""
    if user_id == user["id"]:
        raise HTTPException(400, "不能关注自己")
    author = await storage.get_user_by_id(user_id)
    if not author:
        raise HTTPException(404, "用户不存在")
    return await storage.toggle_follow(user["id"], user_id)


@router.get("/my/following")
async def my_following(
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get authors the current user is following."""
    users = await storage.get_following_details(user["id"])
    return {"users": users}


@router.get("/author/{user_id}/posts")
async def get_author_posts(
    user_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get posts for an author. Own profile sees all, others see only public."""
    posts = await storage.get_user_posts(user_id, user["id"])
    return {"posts": posts}


@router.post("/author/posts")
async def create_post(
    body: PostRequest,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Create a new post."""
    if not body.content.strip():
        raise HTTPException(400, "内容不能为空")
    if body.visibility not in ("public", "private"):
        raise HTTPException(400, "visibility 必须是 'public' 或 'private'")
    post = await storage.add_post(user["id"], body.content.strip(), body.visibility)
    return {"post": post}


@router.delete("/posts/{post_id}")
async def delete_post(
    post_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Delete a post — owner or admin."""
    if user.get("is_admin"):
        ok = await storage.admin_delete_post(post_id)
    else:
        ok = await storage.delete_post(post_id, user["id"])
    if not ok:
        raise HTTPException(404, "动态不存在或无权删除")
    return {"ok": True}
