"""Authentication: register, login, JWT, refresh tokens, logout."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pydantic import BaseModel

from deps import clear_user_llm_cache, get_storage
from storage.sqlite_store import SQLiteStore
from limiter import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_SECRET = os.getenv("JWT_SECRET", "character-distill-dev-secret-key-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 30
REFRESH_TOKEN_DAYS = 30

password_hasher = PasswordHash.recommended()
security_scheme = HTTPBearer(auto_error=False)


class AuthRequest(BaseModel):
    username: str
    password: str
    invite_code: str = ""


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    username: str
    created_at: str
    is_admin: bool = False
    is_disabled: bool = False
    has_api_key: bool = False
    base_url: str = ""
    model: str = ""


class ApiConfigRequest(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


# ---- Dependency ----

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Extract and verify JWT from Authorization header. Raises 401 if missing/invalid."""
    if credentials is None:
        raise HTTPException(401, "请先登录")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Token 无效")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Token 缺少用户标识")
    user = await storage.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(401, "用户不存在")
    return user


# ---- Routes ----

@router.post("/register")
@limiter.limit("3/hour")
async def register(request: Request, req: AuthRequest, storage: SQLiteStore = Depends(get_storage)) -> dict[str, Any]:
    """Register a new user and return JWT + refresh token."""
    username = req.username.strip()
    if not username or len(username) < 2:
        raise HTTPException(400, "用户名至少 2 个字符")
    if not req.password or len(req.password) < 8:
        raise HTTPException(400, "密码至少 8 位，需包含字母和数字")
    if not _is_strong_password(req.password):
        raise HTTPException(400, "密码至少 8 位，需包含字母和数字")

    inv = req.invite_code.strip()
    if not inv:
        raise HTTPException(400, "需要邀请码才能注册")
    invite = await storage.get_invite_code(inv)
    if not invite:
        raise HTTPException(400, "邀请码无效")
    if invite.get("used_by"):
        raise HTTPException(400, "邀请码已被使用")

    existing = await storage.get_user_by_username(username)
    if existing:
        raise HTTPException(409, "用户名已存在")

    user_id = uuid.uuid4().hex[:16]
    password_hash = password_hasher.hash(req.password)
    user = await storage.create_user(user_id, username, password_hash)
    await storage.use_invite_code(inv, user["id"])

    access_token = _create_access_token(user["id"], user["username"])
    refresh_token = await _create_refresh_token(user["id"], storage)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, req: AuthRequest, storage: SQLiteStore = Depends(get_storage)) -> dict[str, Any]:
    """Login with username + password, return JWT + refresh token."""
    user = await storage.get_user_by_username(req.username.strip())
    if user is None:
        raise HTTPException(401, "用户名或密码错误")
    if not password_hasher.verify(req.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    if user.get("is_disabled"):
        raise HTTPException(403, "账号已被禁用")

    access_token = _create_access_token(user["id"], user["username"])
    refresh_token = await _create_refresh_token(user["id"], storage)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.post("/refresh")
async def refresh(req: RefreshRequest, storage: SQLiteStore = Depends(get_storage)) -> dict[str, Any]:
    """Exchange a refresh token for a new access_token + new refresh_token (rotation)."""
    token_hash = _hash_token(req.refresh_token)
    record = await storage.get_refresh_token(token_hash)
    if not record:
        raise HTTPException(401, "Refresh token 无效")
    if record.get("used"):
        # Token reuse detected — revoke all tokens for this user (breach protection)
        await storage.delete_user_refresh_tokens(record["user_id"])
        raise HTTPException(401, "Refresh token 已被使用")
    if record.get("expires_at", "") < datetime.now(timezone.utc).isoformat():
        raise HTTPException(401, "Refresh token 已过期")

    # Mark old token as used (rotation)
    await storage.mark_refresh_token_used(token_hash)

    user = await storage.get_user_by_id(record["user_id"])
    if not user:
        raise HTTPException(401, "用户不存在")
    if user.get("is_disabled"):
        raise HTTPException(403, "账号已被禁用")

    access_token = _create_access_token(user["id"], user["username"])
    new_refresh_token = await _create_refresh_token(user["id"], storage)
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.post("/logout")
async def logout(
    user: dict[str, Any] = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, bool]:
    """Delete all refresh tokens for the current user."""
    await storage.delete_user_refresh_tokens(user["id"])
    return {"ok": True}


@router.get("/me")
async def me(
    user: dict[str, Any] = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Return current authenticated user with API config status."""
    resp = _user_response(user)
    config = await storage.get_user_api_config(user["id"])
    resp["has_api_key"] = bool(config.get("api_key"))
    resp["base_url"] = config.get("base_url", "https://api.deepseek.com")
    resp["model"] = config.get("model", "deepseek-v4-pro")
    return resp


@router.patch("/api-config")
async def update_api_config(
    req: ApiConfigRequest,
    user: dict[str, Any] = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Update the current user's API key, base URL, and model."""
    try:
        await storage.update_user_api_config(
            user["id"], req.api_key, req.base_url, req.model
        )
        clear_user_llm_cache(user["id"])
        return {"ok": True}
    except Exception as exc:
        print(f"[auth] Update API config failed: {exc}")
        raise HTTPException(500, f"Update API config failed: {exc}") from exc


@router.get("/usage")
async def my_usage(
    user: dict[str, Any] = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Return the current user's usage stats."""
    return await storage.get_usage_stats(user["id"])


# ---- Helpers ----

def _create_access_token(user_id: str, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def _create_refresh_token(user_id: str, storage: SQLiteStore) -> str:
    raw = secrets.token_urlsafe(64)
    token_hash = _hash_token(raw)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS)).isoformat()
    await storage.save_refresh_token(token_hash, user_id, expires_at)
    return raw


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _is_strong_password(pw: str) -> bool:
    has_letter = any(c.isalpha() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    return has_letter and has_digit


def _user_response(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": user.get("created_at", ""),
        "is_admin": bool(user.get("is_admin", False)),
        "is_disabled": bool(user.get("is_disabled", False)),
    }
