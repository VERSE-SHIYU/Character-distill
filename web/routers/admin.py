"""Admin: user management, invite codes. Requires is_admin=1."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from routers.auth import get_current_user
from deps import get_storage
from storage.sqlite_store import SQLiteStore

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
    if len(req.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 个字符")
    from routers.auth import password_hasher
    password_hash = password_hasher.hash(req.new_password)
    ok = await storage.reset_user_password(user_id, password_hash)
    if not ok:
        raise HTTPException(404, "用户不存在")
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
