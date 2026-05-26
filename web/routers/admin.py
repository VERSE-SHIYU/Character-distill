"""Admin: user management, invite codes. Requires is_admin=1."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from routers.auth import get_current_user
from deps import get_storage, get_memory_manager
from storage.sqlite_store import SQLiteStore
from core.memory_manager import MemoryManager

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(403, "需要管理员权限")
    return user


# ---- Users ----

@router.get("/users")
async def list_users(
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    return await storage.get_all_users()


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    await storage.set_user_disabled(user_id, True)
    return {"ok": True}


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: str,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    await storage.set_user_disabled(user_id, False)
    return {"ok": True}


class ResetPasswordRequest(BaseModel):
    new_password: str


@router.patch("/users/{user_id}/reset-password")
async def reset_user_password(
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
async def delete_user(
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
        raise HTTPException(500, f"Delete user failed: {exc}") from exc


class SetEmailRequest(BaseModel):
    email: str


@router.patch("/users/{user_id}/email")
async def set_user_email(
    user_id: str,
    req: SetEmailRequest,
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Set a user's email (admin, no verification needed). Empty string clears it."""
    await storage.update_user_email(user_id, req.email)
    return {"ok": True}


@router.delete("/users/{user_id}/email")
async def clear_user_email(
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
async def generate_invites(
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
async def list_invites(
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    return await storage.list_invite_codes()


@router.delete("/invite/{code}")
async def delete_invite(
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
        raise HTTPException(500, f"Delete invite failed: {exc}") from exc


@router.delete("/invites/used")
async def delete_used_invites(
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Delete all used invite codes."""
    try:
        count = await storage.delete_used_invites()
        return {"ok": True, "deleted": count}
    except Exception as exc:
        print(f"[admin] Delete used invites failed: {exc}")
        raise HTTPException(500, f"Delete used invites failed: {exc}") from exc


@router.get("/usage")
async def admin_usage(
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    """Return usage summary for all users (admin only)."""
    return await storage.get_all_usage_summary()


# ---- Comment Reports ----


@router.get("/reports")
async def list_reports(
    _admin: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List pending comment reports grouped by comment."""
    return await storage.get_comment_reports_grouped()


@router.post("/reports/{comment_id}/resolve")
async def resolve_reports(
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
async def delete_reported_comment(
    comment_id: str,
    admin_user: dict = Depends(require_admin),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Delete a reported comment and resolve all its pending reports."""
    ok = await storage.delete_comment_and_resolve_reports(comment_id, admin_user["id"])
    if not ok:
        raise HTTPException(500, "操作失败")
    return {"ok": True}
