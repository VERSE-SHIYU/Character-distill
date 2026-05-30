"""Admin: user management, invite codes. Requires is_admin=1."""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from routers.auth import get_current_user
from deps import get_config, get_storage, get_memory_manager, patch_config
from storage.sqlite_store import SQLiteStore
from core.memory_manager import MemoryManager
from core.log_collector import get_recent_logs
from limiter import limiter

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(403, "需要管理员权限")
    return user


# ---- Users ----

@router.get("/users")
@limiter.limit("30/minute")
async def list_users(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    users = await storage.get_all_users()
    now = time.time()
    for u in users:
        ts = u.get("last_active_at") or u.get("last_login_at")
        if ts:
            try:
                dt = __import__("datetime").datetime.fromisoformat(ts)
                u["online"] = (now - dt.timestamp()) < 300  # 5 min
            except Exception:
                u["online"] = False
        else:
            u["online"] = False
    return users


@router.get("/dashboard")
@limiter.limit("30/minute")
async def dashboard(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    return await storage.get_dashboard_stats()


@router.post("/users/{user_id}/disable")
@limiter.limit("30/minute")
async def disable_user(
    request: Request,
    user_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    await storage.set_user_disabled(user_id, True)
    return {"ok": True}


@router.post("/users/{user_id}/enable")
@limiter.limit("30/minute")
async def enable_user(
    request: Request,
    user_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    await storage.set_user_disabled(user_id, False)
    return {"ok": True}


class ResetPasswordRequest(BaseModel):
    new_password: str


@router.patch("/users/{user_id}/reset-password")
@limiter.limit("30/minute")
async def reset_user_password(
    request: Request,
    user_id: str,
    req: ResetPasswordRequest,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    pw = req.new_password
    if len(pw) < 8:
        raise HTTPException(400, "新密码至少 8 个字符")
    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        raise HTTPException(400, "新密码需包含字母和数字")
    from routers.auth import password_hasher
    password_hash = password_hasher.hash(req.new_password)
    ok = await storage.reset_user_password(user_id, password_hash)
    if not ok:
        raise HTTPException(404, "用户不存在")
    return {"ok": True}


@router.delete("/users/{user_id}")
@limiter.limit("30/minute")
async def delete_user(
    request: Request,
    user_id: str,
    admin_user: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
) -> dict[str, Any]:
    """Cascade-delete a user: texts, cards, sessions, messages, stats, tokens, Mem0 memories."""
    if user_id == admin_user.get("id"):
        raise HTTPException(400, "不能删除自己的账号")

    # Clean up Mem0 memories for each card owned by the user
    try:
        card_ids = await storage.get_user_card_ids(user_id)
        if memory_manager and memory_manager.enabled:
            for cid in card_ids:
                memory_manager.delete_all(cid)
    except Exception as exc:
        print(f"[admin] Mem0 cleanup for user {user_id} failed (non-fatal): {exc}")

    try:
        counts = await storage.delete_user(user_id)
        return {"ok": True, "deleted": counts}
    except ValueError:
        raise HTTPException(404, "用户不存在")
    except Exception as exc:
        print(f"[admin] Delete user failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


class BatchDeleteRequest(BaseModel):
    user_ids: list[str]


@router.post("/users/batch-delete")
@limiter.limit("10/minute")
async def batch_delete_users(
    request: Request,
    req: BatchDeleteRequest,
    admin_user: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
    memory_manager: MemoryManager | None = Depends(get_memory_manager),
) -> dict[str, Any]:
    """Batch cascade-delete users."""
    if not req.user_ids:
        raise HTTPException(400, "请选择要删除的用户")
    if admin_user.get("id") in req.user_ids:
        raise HTTPException(400, "不能删除自己的账号")

    deleted = 0
    failed = 0
    for user_id in req.user_ids:
        try:
            card_ids = await storage.get_user_card_ids(user_id)
            if memory_manager and memory_manager.enabled:
                for cid in card_ids:
                    memory_manager.delete_all(cid)
            await storage.delete_user(user_id)
            deleted += 1
        except Exception as exc:
            print(f"[admin] Batch delete user {user_id} failed: {exc}")
            failed += 1

    return {"ok": True, "deleted": deleted, "failed": failed}


class SetEmailRequest(BaseModel):
    email: str


@router.patch("/users/{user_id}/email")
@limiter.limit("30/minute")
async def set_user_email(
    request: Request,
    user_id: str,
    req: SetEmailRequest,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Set a user's email (admin, no verification needed). Empty string clears it."""
    await storage.update_user_email(user_id, req.email)
    return {"ok": True}


@router.delete("/users/{user_id}/email")
@limiter.limit("30/minute")
async def clear_user_email(
    request: Request,
    user_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Clear a user's email and email_verified flag."""
    await storage.update_user_email(user_id, "")
    return {"ok": True}


# ---- Invite codes ----

class GenerateInviteRequest(BaseModel):
    count: int = 1


@router.post("/invite/generate")
@limiter.limit("30/minute")
async def generate_invites(
    request: Request,
    req: GenerateInviteRequest,
    admin_user: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    count = max(1, min(req.count, 100))
    codes = []
    for _ in range(count):
        code = secrets.token_urlsafe(12)
        record = await storage.create_invite_code(code, admin_user["id"])
        codes.append(record)
    return codes


@router.get("/invite/list")
@limiter.limit("30/minute")
async def list_invites(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    return await storage.list_invite_codes()


@router.delete("/invite/{code}")
@limiter.limit("30/minute")
async def delete_invite(
    request: Request,
    code: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Delete a single invite code."""
    try:
        ok = await storage.delete_invite_code(code)
        if not ok:
            raise HTTPException(404, "邀请码不存在")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[admin] Delete invite failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.delete("/invites/used")
@limiter.limit("30/minute")
async def delete_used_invites(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Delete all used invite codes."""
    try:
        count = await storage.delete_used_invites()
        return {"ok": True, "deleted": count}
    except Exception as exc:
        print(f"[admin] Delete used invites failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.get("/usage")
@limiter.limit("30/minute")
async def admin_usage(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    """Return usage summary for all users (admin only)."""
    return await storage.get_all_usage_summary()


# ---- Comment Reports ----


@router.get("/reports")
@limiter.limit("30/minute")
async def list_reports(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List pending comment reports grouped by comment."""
    return await storage.get_comment_reports_grouped()


@router.post("/reports/{comment_id}/resolve")
@limiter.limit("30/minute")
async def resolve_reports(
    request: Request,
    comment_id: str,
    admin_user: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Resolve all pending reports for a comment (dismiss, keep comment)."""
    ok = await storage.resolve_all_reports(comment_id, admin_user["id"])
    if not ok:
        raise HTTPException(500, "操作失败")
    return {"ok": True}


@router.post("/reports/{comment_id}/delete-comment")
@limiter.limit("30/minute")
async def delete_reported_comment(
    request: Request,
    comment_id: str,
    admin_user: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Delete a reported comment and resolve all its pending reports."""
    ok = await storage.delete_comment_and_resolve_reports(comment_id, admin_user["id"])
    if not ok:
        raise HTTPException(500, "操作失败")
    return {"ok": True}


# ============================================================
# P1-1: Content Moderation
# ============================================================


@router.get("/cards")
@limiter.limit("30/minute")
async def admin_list_cards(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict]:
    """List all cards with user info for admin review."""
    return await storage.list_all_cards_admin()


@router.post("/cards/{card_id}/takedown")
@limiter.limit("30/minute")
async def admin_takedown_card(
    request: Request,
    card_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, bool]:
    """Set a public card to private (takedown)."""
    ok = await storage.takedown_card(card_id)
    if not ok:
        raise HTTPException(404, "卡片不存在或已是非公开状态")
    return {"ok": True}


@router.get("/posts")
@limiter.limit("30/minute")
async def admin_list_posts(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict]:
    """List all user posts for admin review."""
    return await storage.list_all_posts_admin()


@router.delete("/posts/{post_id}")
@limiter.limit("30/minute")
async def admin_delete_post(
    request: Request,
    post_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, bool]:
    """Delete any user post by id."""
    ok = await storage.admin_delete_post(post_id)
    if not ok:
        raise HTTPException(404, "帖子不存在")
    return {"ok": True}


class BanUserRequest(BaseModel):
    admin_id: str = ""


@router.post("/users/{user_id}/ban")
@limiter.limit("30/minute")
async def admin_ban_user(
    request: Request,
    user_id: str,
    req: BanUserRequest,
    admin_user: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Disable user + delete their posts + resolve reports."""
    if user_id == admin_user.get("id"):
        raise HTTPException(400, "不能封禁自己的账号")
    admin_id = req.admin_id or admin_user["id"]
    counts = await storage.ban_user_and_contents(user_id, admin_id)
    return {"ok": True, **counts}


# ============================================================
# P1-2: System logs & Task status
# ============================================================


@router.get("/logs")
@limiter.limit("30/minute")
async def admin_logs(
    request: Request,
    _admin: dict = Depends(require_admin),
) -> list[dict[str, Any]]:
    """Return recent WARNING+ log entries from the ring buffer."""
    return get_recent_logs(limit=100)


@router.get("/tasks")
@limiter.limit("30/minute")
async def admin_tasks(
    request: Request,
    _admin: dict = Depends(require_admin),
) -> list[dict[str, Any]]:
    """List all distill tasks with owner info."""
    from routers.distill import _tasks, _task_lock
    with _task_lock:
        result = [
            {"task_id": tid, **task}
            for tid, task in list(_tasks.items())
        ]
    return result


# ============================================================
# P2-2: User Detail
# ============================================================


@router.get("/users/{user_id}/detail")
@limiter.limit("30/minute")
async def admin_user_detail(
    request: Request,
    user_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get detailed user info for admin: stats, cards, sessions, usage, login history."""
    try:
        detail = await storage.get_user_detail(user_id)
        ts = detail.get("last_active_at") or detail.get("last_login_at")
        if ts:
            try:
                dt = __import__("datetime").datetime.fromisoformat(ts)
                detail["online"] = (time.time() - dt.timestamp()) < 300
            except Exception:
                detail["online"] = False
        else:
            detail["online"] = False
        return detail
    except ValueError:
        raise HTTPException(404, "用户不存在")


# ============================================================
# P2-1: Announcements
# ============================================================


class AnnouncementCreateRequest(BaseModel):
    content: str
    align: str = 'left'


@router.post("/announcement")
@limiter.limit("30/minute")
async def admin_create_announcement(
    request: Request,
    req: AnnouncementCreateRequest,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Create a new announcement (deactivates previous ones)."""
    if not req.content.strip():
        raise HTTPException(400, "公告内容不能为空")
    align = req.align if req.align in ('left', 'center', 'right') else 'left'
    return await storage.create_announcement(req.content.strip(), align)


@router.delete("/announcement/{announcement_id}")
@limiter.limit("30/minute")
async def admin_delete_announcement(
    request: Request,
    announcement_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, bool]:
    """Delete an announcement by id."""
    ok = await storage.delete_announcement(announcement_id)
    if not ok:
        raise HTTPException(404, "公告不存在")
    return {"ok": True}


@router.get("/announcements")
@limiter.limit("30/minute")
async def admin_list_announcements(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict]:
    """List all announcements."""
    return await storage.list_announcements()


# ============================================================
# P2-3: Data Export
# ============================================================


@router.get("/export/users")
@limiter.limit("30/minute")
async def admin_export_users(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
):
    """Export all users as CSV."""
    from fastapi.responses import PlainTextResponse
    csv_str = await storage.export_users_csv()
    return PlainTextResponse(
        csv_str,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=users.csv"},
    )


@router.get("/export/usage")
@limiter.limit("30/minute")
async def admin_export_usage(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
):
    """Export usage summary as CSV."""
    from fastapi.responses import PlainTextResponse
    csv_str = await storage.export_usage_csv()
    return PlainTextResponse(
        csv_str,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=usage.csv"},
    )


# ============================================================
# P3-1: Config Center
# ============================================================


@router.get("/config/changelog")
@limiter.limit("30/minute")
async def admin_config_changelog(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict]:
    """Return recent config changelog entries."""
    return await storage.get_config_changelog(50)


class RegistrationModeRequest(BaseModel):
    mode: str  # "invite_only" | "open"


@router.post("/config/registration-mode")
@limiter.limit("30/minute")
async def admin_set_registration_mode(
    request: Request,
    req: RegistrationModeRequest,
    _admin: dict = Depends(require_admin),
) -> dict[str, str]:
    """Set registration mode: 'invite_only' or 'open'."""
    mode = req.mode.strip().lower()
    if mode not in ("invite_only", "open"):
        raise HTTPException(400, "mode 必须是 invite_only 或 open")
    cfg = patch_config("registration", {"mode": mode})
    current = cfg.get("registration", {})
    return {"mode": current.get("mode", mode)}


class RateLimitsRequest(BaseModel):
    default: str | None = None
    login: str | None = None


@router.post("/config/rate-limits")
@limiter.limit("30/minute")
async def admin_set_rate_limits(
    request: Request,
    req: RateLimitsRequest,
    _admin: dict = Depends(require_admin),
) -> dict[str, str]:
    """Set rate-limit thresholds in config.yaml."""
    cfg = get_config()
    limits = cfg.get("rate_limits", {})
    if req.default is not None:
        limits["default"] = req.default
    if req.login is not None:
        limits["login"] = req.login
    patch_config("rate_limits", limits)
    return {"default": limits.get("default", ""), "login": limits.get("login", "")}


# ============================================================
# P3-2: Review log
# ============================================================


@router.get("/review-log")
@limiter.limit("30/minute")
async def admin_review_log(
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict]:
    """Return recent AI review logs."""
    return await storage.get_review_logs(50)


# ============================================================
# Admin: Featured Cards
# ============================================================


class AddFeaturedRequest(BaseModel):
    card_id: str


class ReorderFeaturedRequest(BaseModel):
    ids: list[str]


@router.post("/featured")
@limiter.limit("30/minute")
async def admin_add_featured(
    request: Request,
    req: AddFeaturedRequest,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Add a card to the featured list (max 10)."""
    featured = await storage.get_featured_cards()
    if len(featured) >= 10:
        raise HTTPException(400, "置顶角色已达上限（最多 10 个）")
    # Check for duplicates
    if any(fc["card_id"] == req.card_id for fc in featured):
        raise HTTPException(400, "该角色已在置顶列表中")
    fid = await storage.add_featured_card(req.card_id)
    if not fid:
        raise HTTPException(500, "添加置顶失败")
    return {"id": fid}


@router.delete("/featured/{id}")
@limiter.limit("30/minute")
async def admin_remove_featured(
    id: str,
    request: Request,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Remove a card from the featured list."""
    ok = await storage.remove_featured_card(id)
    if not ok:
        raise HTTPException(404, "置顶记录不存在")
    return {"ok": True}


@router.patch("/featured/reorder")
@limiter.limit("30/minute")
async def admin_reorder_featured(
    request: Request,
    req: ReorderFeaturedRequest,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Reorder featured cards by id array index."""
    await storage.reorder_featured_cards(req.ids)
    return {"ok": True}
