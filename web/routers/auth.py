"""Authentication: register, login, JWT verification."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pydantic import BaseModel

from deps import get_storage
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_SECRET = os.getenv("JWT_SECRET", "character-distill-dev-secret-key-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7

password_hasher = PasswordHash.recommended()
security_scheme = HTTPBearer(auto_error=False)


class AuthRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
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
        raise HTTPException(401, "Token 已过期，请重新登录")
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

@router.post("/register", response_model=TokenResponse)
async def register(req: AuthRequest, storage: SQLiteStore = Depends(get_storage)) -> dict[str, Any]:
    """Register a new user and return JWT token."""
    username = req.username.strip()
    if not username or len(username) < 2:
        raise HTTPException(400, "用户名至少 2 个字符")
    if not req.password or len(req.password) < 4:
        raise HTTPException(400, "密码至少 4 个字符")

    existing = await storage.get_user_by_username(username)
    if existing:
        raise HTTPException(409, "用户名已存在")

    user_id = uuid.uuid4().hex[:16]
    password_hash = password_hasher.hash(req.password)
    user = await storage.create_user(user_id, username, password_hash)

    token = _create_token(user["id"], user["username"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.post("/login", response_model=TokenResponse)
async def login(req: AuthRequest, storage: SQLiteStore = Depends(get_storage)) -> dict[str, Any]:
    """Login with username + password, return JWT token."""
    user = await storage.get_user_by_username(req.username.strip())
    if user is None:
        raise HTTPException(401, "用户名或密码错误")
    if not password_hasher.verify(req.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")

    token = _create_token(user["id"], user["username"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.get("/me", response_model=UserResponse)
async def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Return current authenticated user."""
    return _user_response(user)


# ---- Helpers ----

def _create_token(user_id: str, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _user_response(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": user.get("created_at", ""),
    }
