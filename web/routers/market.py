"""Market: browse, search, fork, and like public character cards."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel

from deps import get_storage
from limiter import get_client_ip, limiter
from storage.sqlite_store import SQLiteStore
from routers.auth import get_current_user

router = APIRouter(prefix="/api/market", tags=["market"])


async def _get_ip_location(client_ip: str) -> str:
    """Look up IP location via ip-api.com. Returns city/region or empty."""
    import httpx
    # Local/private IPs → show "本地" so the badge is visible during dev
    if not client_ip or client_ip in ("127.0.0.1", "::1", "localhost") or client_ip.startswith(("192.168.", "10.", "172.16.")):
        return "本地"
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            resp = await c.get(f"http://ip-api.com/json/{client_ip}?fields=status,country,regionName,city")
            data = resp.json()
            if data.get("status") == "success":
                parts = [data.get("regionName") or data.get("city") or ""]
                return "".join(parts)
    except Exception:
        pass
    return ""


class ForkRequest(BaseModel):
    text_id: str = ""


class VisibilityUpdate(BaseModel):
    visibility: str


class CommentRequest(BaseModel):
    content: str


class PostRequest(BaseModel):
    content: str
    visibility: str = "public"
    images: str = ""
    card_id: str = ""


class PublishRequest(BaseModel):
    market_description: str = ""
    market_tags: str = ""
    publish_message: str = ""


class UpdatePublishRequest(BaseModel):
    card_json: str = ""
    market_description: str = ""
    market_tags: str = ""
    publish_message: str = ""


# ── Concrete routes first (no wildcard card_id) ──


@router.get("/list")
@limiter.limit("60/minute")
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
@limiter.limit("60/minute")
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


@router.get("/my/following")
@limiter.limit("60/minute")
async def my_following(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get authors the current user is following."""
    users = await storage.get_following_details(user["id"])
    return {"users": users}


@router.get("/feed")
@limiter.limit("60/minute")
async def feed(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get feed posts from followed users."""
    posts = await storage.get_feed_posts(user["id"], page, page_size)
    for p in posts:
        p["liked_by_me"] = bool(p.get("liked_by_me"))
    return {"posts": posts}


@router.get("/author/{user_id}")
@limiter.limit("60/minute")
async def get_author(
    request: Request,
    user_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get author profile and their public data."""
    author = await storage.get_user_by_id(user_id)
    if not author:
        raise HTTPException(404, "用户不存在")
    is_self = user_id == user["id"]
    cards = await storage.get_author_cards(user_id, include_private=is_self)
    following_ids = await storage.get_following(user["id"])
    followers_count = await storage.get_followers_count(user_id)
    following_count = await storage.get_following_count(user_id)
    texts = await storage.get_author_texts(user_id)
    stats_visible = bool(author.get("profile_stats_visible", 1) or is_self)
    cards_visible = bool(author.get("cards_visible", 1) or is_self)
    books_visible = bool(author.get("books_visible", 1) or is_self)
    if not cards_visible:
        cards = []
    if not books_visible:
        texts = []
    return {
        "author": {k: v for k, v in author.items() if k != "password_hash"},
        "cards": cards,
        "texts": texts,
        "is_following": user_id in following_ids,
        "followers_count": followers_count,
        "following_count": following_count,
        "stats_visible": bool(stats_visible),
        "cards_visible": bool(cards_visible),
        "books_visible": bool(books_visible),
    }


@router.get("/author/{user_id}/followers")
@limiter.limit("60/minute")
async def get_author_followers(
    request: Request,
    user_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get followers with details for an author."""
    followers = await storage.get_followers_details(user_id)
    return {"followers": followers}


@router.patch("/author/visibility")
@limiter.limit("30/minute")
async def update_privacy(
    request: Request,
    body: dict,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Update privacy settings (stats_visible, cards_visible, books_visible)."""
    kwargs = {}
    if "stats_visible" in body:
        kwargs["profile_stats_visible"] = body["stats_visible"]
    if "cards_visible" in body:
        kwargs["cards_visible"] = body["cards_visible"]
    if "books_visible" in body:
        kwargs["books_visible"] = body["books_visible"]
    if not kwargs:
        return {"ok": True}
    ok = await storage.set_user_privacy(user["id"], **kwargs)
    if not ok:
        raise HTTPException(500, "设置失败")
    return {
        "ok": True,
        "stats_visible": kwargs.get("profile_stats_visible", None),
        "cards_visible": kwargs.get("cards_visible", None),
        "books_visible": kwargs.get("books_visible", None),
    }


@router.get("/author/{user_id}/posts")
@limiter.limit("60/minute")
async def get_author_posts(
    request: Request,
    user_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get posts for an author. Own profile sees all, others see only public."""
    posts = await storage.get_user_posts(user_id, user["id"])
    liked_ids = await storage.get_liked_post_ids(user["id"])
    for p in posts:
        p["liked_by_me"] = p["id"] in liked_ids
    return {"posts": posts}


@router.post("/author/{user_id}/follow")
@limiter.limit("30/minute")
async def toggle_follow_author(
    request: Request,
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


@router.post("/author/posts")
@limiter.limit("30/minute")
async def create_post(
    request: Request,
    body: PostRequest,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Create a new post."""
    if not body.content.strip():
        raise HTTPException(400, "内容不能为空")
    if body.visibility not in ("public", "private"):
        raise HTTPException(400, "visibility 必须是 'public' 或 'private'")
    post = await storage.add_post(user["id"], body.content.strip(), body.visibility, body.images, body.card_id)
    return {"post": post}


@router.get("/card/{card_id}")
@limiter.limit("60/minute")
async def get_card_detail(
    request: Request,
    card_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get a single public card detail with author info."""
    card = await storage.get_market_card_detail(card_id, user["id"])
    if not card:
        raise HTTPException(404, "角色不存在或未公开")
    return card


# ── Publish / Update / Versions / Forks / Delete ──


@router.post("/{card_id}/publish")
@limiter.limit("30/minute")
async def publish_card(
    request: Request,
    card_id: str,
    body: PublishRequest,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Publish a card to the market (first-time publish)."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此角色卡")

    # AI auto-review (fails open)
    try:
        from adapters.llm_adapter import LLMAdapter
        from core.moderation.auto_review import auto_review_card
        llm = LLMAdapter()
        card_json = card.get("card_json", {})
        if isinstance(card_json, str):
            try:
                card_json = json.loads(card_json)
            except Exception:
                card_json = {}
        review = await auto_review_card(card_json, llm)
        import uuid as _uuid
        await storage.save_review_log(
            _uuid.uuid4().hex[:12], card_id, user["id"],
            "pass" if review["pass"] else "reject",
            review.get("reason", ""),
        )
        if not review["pass"]:
            raise HTTPException(400, f"发布失败：内容审核未通过 — {review['reason']}")
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[market] Auto-review failed (fails open): {exc}")

    card_json_str = card.get("card_json", "{}")
    if isinstance(card_json_str, dict):
        card_json_str = json.dumps(card_json_str, ensure_ascii=False)
    ok = await storage.publish_card(
        card_id, user["id"],
        body.market_description, body.market_tags, body.publish_message,
        card_json_str,
    )
    if not ok:
        raise HTTPException(500, "发布失败")
    return {"ok": True, "card_id": ok}


@router.put("/{card_id}/publish")
@limiter.limit("30/minute")
async def update_published_card(
    request: Request,
    card_id: str,
    body: UpdatePublishRequest,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Update an already-published card (with field-level diff)."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此角色卡")
    old_json = card.get("card_json", "{}")
    if isinstance(old_json, dict):
        old_json = json.dumps(old_json, ensure_ascii=False)
    ver = await storage.update_published_card(
        card_id, user["id"],
        body.card_json, body.market_description, body.market_tags,
        body.publish_message, old_json,
    )
    if not ver:
        raise HTTPException(500, "更新失败")
    return {"version": ver}


@router.get("/{card_id}/versions")
@limiter.limit("60/minute")
async def get_card_versions(
    request: Request,
    card_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """List all published versions for a card."""
    versions = await storage.get_card_versions(card_id)
    return {"versions": versions}


@router.delete("/{card_id}/versions/{version_id}")
@limiter.limit("30/minute")
async def delete_card_version(
    request: Request,
    card_id: str,
    version_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Delete a specific version — admin only. Versions are permanent records."""
    if not user.get("is_admin"):
        raise HTTPException(403, "仅管理员可删除版本历史")
    ok = await storage.delete_card_version(card_id, version_id)
    if not ok:
        raise HTTPException(404, "版本不存在")
    return {"ok": True}


@router.put("/{card_id}/versions/{version_id}")
@limiter.limit("30/minute")
async def update_card_version(
    request: Request,
    card_id: str,
    version_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Update version publish_message — card author only."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")
    message = (body.get("publish_message") or "").strip()
    if not message:
        raise HTTPException(400, "发布说明不能为空")
    ok = await storage.update_card_version(card_id, version_id, message)
    if not ok:
        raise HTTPException(404, "版本不存在")
    return {"ok": True}


@router.get("/{card_id}/forks")
@limiter.limit("60/minute")
async def get_card_forks(
    request: Request,
    card_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """List public forks of a card."""
    forks = await storage.get_card_forks(card_id)
    return {"forks": forks}


@router.delete("/{card_id}")
@limiter.limit("30/minute")
async def delete_market_card(
    request: Request,
    card_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Delete a card from market: soft-delete + set visibility private."""
    card = await storage.get_card(card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    if not user.get("is_admin") and card.get("user_id") != user["id"]:
        raise HTTPException(403, "无权删除此角色卡")
    ok = await storage.delete_card(card_id)
    if not ok:
        raise HTTPException(500, "删除失败")
    # Also hide from market immediately
    await storage.update_card_visibility(card_id, "private")
    return {"ok": True}


# ── Post routes ──


@router.get("/post/{post_id}/comments")
@limiter.limit("60/minute")
async def list_post_comments(
    request: Request,
    post_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get all comments for a post."""
    comments = await storage.get_post_comments(post_id)
    return {"comments": comments}


@router.post("/post/{post_id}/comments")
@limiter.limit("30/minute")
async def add_post_comment(
    post_id: str,
    body: CommentRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Add a comment to a post."""
    if not body.content.strip():
        raise HTTPException(400, "评论内容不能为空")

    # Get client IP and look up location (like Douyin/Xiaohongshu style)
    client_ip = get_client_ip(request)
    ip_location = await _get_ip_location(client_ip)

    comment = await storage.add_post_comment(post_id, user["id"], user["username"], body.content.strip(), ip_location)
    return comment


@router.post("/post/{post_id}/like")
@limiter.limit("30/minute")
async def like_post(
    request: Request,
    post_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Toggle like on a post."""
    return await storage.toggle_post_like(post_id, user["id"])


@router.delete("/posts/{post_id}")
@limiter.limit("30/minute")
async def delete_post(
    request: Request,
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


# ── Wildcard card routes (must be last — {card_id} catches anything) ──


@router.get("/{card_id}/comments")
@limiter.limit("60/minute")
async def list_comments(
    request: Request,
    card_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get all comments for a card."""
    comments = await storage.get_comments(card_id)
    return {"comments": comments}


@router.post("/{card_id}/comments")
@limiter.limit("30/minute")
async def add_comment(
    request: Request,
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


# ── Comment delete & report (must be before wildcard {card_id} routes) ──


@router.post("/{card_id}/comments/batch-delete")
@limiter.limit("30/minute")
async def batch_delete_comments(
    request: Request,
    card_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Batch delete comments — card author or admin only."""
    card_author_id = await storage.get_card_author_id(card_id)
    if card_author_id != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, "无权操作")
    comment_ids = body.get("comment_ids", [])
    if not comment_ids:
        return {"ok": True}
    ok = await storage.batch_delete_comments(comment_ids)
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True}


@router.delete("/{card_id}/comments/{comment_id}")
@limiter.limit("30/minute")
async def delete_comment(
    request: Request,
    card_id: str,
    comment_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Delete a comment — card author/comment author/admin can delete, others get 403."""
    card_author_id = await storage.get_card_author_id(card_id)
    # Get comment to check ownership
    comment = await storage.get_comment(comment_id)
    if not comment:
        raise HTTPException(404, "评论不存在")
    is_comment_author = comment["user_id"] == user["id"]
    is_card_author = card_author_id == user["id"]
    if not is_comment_author and not is_card_author and not user.get("is_admin"):
        raise HTTPException(403, "无权删除此评论")
    ok = await storage.delete_comment(comment_id)
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True}


@router.post("/{card_id}/comments/{comment_id}/report")
@limiter.limit("30/minute")
async def report_comment(
    request: Request,
    card_id: str,
    comment_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Report a comment."""
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "请填写举报原因")
    ok = await storage.add_comment_report(comment_id, card_id, user["id"], reason)
    if not ok:
        raise HTTPException(500, "举报提交失败")
    return {"ok": True}


@router.post("/{card_id}/fork")
@limiter.limit("30/minute")
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
@limiter.limit("30/minute")
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
@limiter.limit("30/minute")
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
