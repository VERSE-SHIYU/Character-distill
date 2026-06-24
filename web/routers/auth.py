"""Authentication: register, login, JWT, refresh tokens, logout."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pydantic import BaseModel

from core.email_service import send_verification_code
from deps import clear_user_llm_cache, get_config, get_storage
from storage.base import StorageBase
from limiter import limiter
from web.geo_guard import check_api_allowed
from web.legal_versions import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION
from web.limiter import get_client_ip

logger = logging.getLogger("charsim.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET or JWT_SECRET == "character-distill-dev-secret-key-change-in-prod":
    raise RuntimeError(
        "JWT_SECRET 未设置或使用了默认值！"
        "请在 .env 中设置: JWT_SECRET=$(openssl rand -hex 32)"
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 30
REFRESH_TOKEN_DAYS = 30

password_hasher = PasswordHash.recommended()
security_scheme = HTTPBearer(auto_error=False)


class AuthRequest(BaseModel):
    username: str
    password: str
    invite_code: str = ""
    email: str = ""
    code: str = ""
    agreed_terms_version: str = ""
    agreed_privacy_version: str = ""


class SendCodeRequest(BaseModel):
    email: str
    purpose: str = "register"  # register | reset_password | bind_email


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


class BindEmailRequest(BaseModel):
    email: str
    code: str


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
    embedding_key: str = ""
    embedding_region: str = "cn"


class EmbeddingTestRequest(BaseModel):
    embedding_key: str = ""
    embedding_region: str = "cn"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


# ---- Dependency ----


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    storage: StorageBase = Depends(get_storage),
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
    if user.get("is_disabled"):
        raise HTTPException(403, "账号已被禁用")

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Like get_current_user but returns empty dict for unauthenticated requests."""
    if credentials is None:
        return {}
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return {}
    user_id = payload.get("sub")
    if not user_id:
        return {}
    user = await storage.get_user_by_id(user_id)
    if user is None or user.get("is_disabled"):
        return {}
    return user


# ---- Routes ----

@router.post("/send-code")
@limiter.limit("3/minute")
async def send_code(
    request: Request,
    req: SendCodeRequest,
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Send a verification code to an email address."""
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "邮箱地址无效")

    if req.purpose == "register":
        existing = await storage.get_user_by_email(email)
        if existing:
            raise HTTPException(400, "该邮箱已注册")
    elif req.purpose == "reset_password":
        existing = await storage.get_user_by_email(email)
        if not existing:
            raise HTTPException(400, "该邮箱未注册")

    code = f"{secrets.randbelow(1000000):06d}"
    await storage.save_verification_code(email, code, req.purpose)

    purpose_label = {"register": "注册", "reset_password": "重置密码", "bind_email": "绑定邮箱"}.get(req.purpose, "验证")
    try:
        send_verification_code(email, code, purpose_label)
    except RuntimeError as exc:
        raise HTTPException(500, "邮件发送失败，请稍后重试")
    return {"ok": True}


@router.post("/register")
@limiter.limit("3/hour")
async def register(request: Request, req: AuthRequest, storage: StorageBase = Depends(get_storage)) -> dict[str, Any]:
    """Register a new user and return JWT + refresh token."""
    username = req.username.strip()
    if not username or len(username) < 2:
        raise HTTPException(400, "用户名至少 2 个字符")
    if not req.password or len(req.password) < 8:
        raise HTTPException(400, "密码至少 8 位，需包含字母和数字")
    if not _is_strong_password(req.password):
        raise HTTPException(400, "密码至少 8 位，需包含字母和数字")

    # Check registration mode
    reg_cfg = get_config().get("registration", {})
    invite_required = reg_cfg.get("mode", "invite_only") != "open"

    inv = req.invite_code.strip()
    if invite_required and not inv:
        raise HTTPException(400, "需要邀请码才能注册")

    if inv:
        # Seed invite: if no codes exist and ADMIN_INVITE_CODE is set, auto-create
        admin_seed = os.getenv("ADMIN_INVITE_CODE", "")
        if admin_seed and inv == admin_seed:
            existing_codes = await storage.list_invite_codes()
            if not existing_codes:
                await storage.create_invite_code(admin_seed, "system")

        invite = await storage.get_invite_code(inv)
        if not invite:
            raise HTTPException(400, "邀请码无效")
        if invite.get("used_by"):
            raise HTTPException(400, "邀请码已被使用")

    existing = await storage.get_user_by_username(username)
    if existing:
        raise HTTPException(409, "用户名已存在")

    # Server-side version check (frontend can't be trusted)
    if req.agreed_terms_version.strip() != CURRENT_TERMS_VERSION or req.agreed_privacy_version.strip() != CURRENT_PRIVACY_VERSION:
        raise HTTPException(400, "请先同意最新版用户协议与隐私政策")

    # Email verification (optional during migration period)
    email = req.email.strip().lower()
    if email:
        if req.code:
            valid = await storage.verify_code(email, req.code, "register")
            if not valid:
                raise HTTPException(400, "邮箱验证码无效或已过期")
        else:
            raise HTTPException(400, "请填写邮箱验证码")

    user_id = uuid.uuid4().hex[:16]
    password_hash = password_hasher.hash(req.password)
    user = await storage.create_user(user_id, username, password_hash, email)
    if inv:
        await storage.use_invite_code(inv, user["id"])

    # Record consent
    try:
        client_ip = get_client_ip(request)
        await storage.record_user_consent(user["id"], CURRENT_TERMS_VERSION, CURRENT_PRIVACY_VERSION, client_ip)
    except Exception as exc:
        print(f"[auth] Failed to record consent for {user_id}: {exc}")

    # First user with seed code becomes admin
    admin_seed = os.getenv("ADMIN_INVITE_CODE", "")
    if admin_seed and inv == admin_seed:
        await storage.set_user_admin(user["id"], True)
        user["is_admin"] = True

    access_token = _create_access_token(user["id"], user["username"])
    refresh_token = await _create_refresh_token(user["id"], storage)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    req: ResetPasswordRequest,
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Reset password via email verification code."""
    valid = await storage.verify_code(req.email.strip().lower(), req.code, "reset_password")
    if not valid:
        raise HTTPException(400, "验证码无效或已过期")
    user = await storage.get_user_by_email(req.email.strip().lower())
    if not user:
        raise HTTPException(400, "用户不存在")
    if not req.new_password or len(req.new_password) < 8:
        raise HTTPException(400, "新密码至少 8 位")
    if not _is_strong_password(req.new_password):
        raise HTTPException(400, "新密码需包含字母和数字")
    new_hash = password_hasher.hash(req.new_password)
    await storage.update_user_password(user["id"], new_hash)
    await storage.delete_user_refresh_tokens(user["id"])
    return {"ok": True}


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, req: AuthRequest, storage: StorageBase = Depends(get_storage)) -> dict[str, Any]:
    """Login with username + password, return JWT + refresh token."""
    user = await storage.get_user_by_username(req.username.strip())
    if user is None:
        logger.warning("Login failed: unknown username '%s' from %s", req.username.strip(), request.client.host if request.client else "unknown")
        raise HTTPException(401, "用户名或密码错误")
    if not password_hasher.verify(req.password, user["password_hash"]):
        logger.warning("Login failed: wrong password for '%s' from %s", req.username.strip(), request.client.host if request.client else "unknown")
        raise HTTPException(401, "用户名或密码错误")
    if user.get("is_disabled"):
        raise HTTPException(403, "账号已被禁用")

    access_token = _create_access_token(user["id"], user["username"])
    refresh_token = await _create_refresh_token(user["id"], storage)
    await storage.update_last_login(user["id"])
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.post("/refresh")
async def refresh(req: RefreshRequest, storage: StorageBase = Depends(get_storage)) -> dict[str, Any]:
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
    await storage.update_last_login(user["id"])
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.post("/logout")
async def logout(
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, bool]:
    """Delete all refresh tokens for the current user."""
    await storage.delete_user_refresh_tokens(user["id"])
    return {"ok": True}


@router.get("/me")
async def me(
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Return current authenticated user with API config status."""
    resp = _user_response(user)
    config = await storage.get_user_api_config(user["id"])
    resp["has_api_key"] = bool(config.get("api_key"))
    resp["base_url"] = config.get("base_url", "https://api.deepseek.com")
    resp["model"] = config.get("model", "deepseek-v4-pro")
    resp["has_embedding_key"] = bool(config.get("embedding_key"))
    resp["embedding_region"] = config.get("embedding_region", "cn")
    resp["avatar_data"] = await storage.get_user_avatar(user["id"])
    resp["email"] = await storage.get_user_email(user["id"])
    resp["email_verified"] = bool(user.get("email_verified", False))
    resp["profile_stats_visible"] = bool(user.get("profile_stats_visible", True))
    resp["cards_visible"] = bool(user.get("cards_visible", True))
    resp["books_visible"] = bool(user.get("books_visible", True))
    resp["following_visible"] = bool(user.get("following_visible", True))
    return resp


@router.get("/announcement")
async def get_announcement(
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Return the current active announcement (if any)."""
    ann = await storage.get_active_announcement()
    return {"announcement": ann}


@router.patch("/api-config")
@limiter.limit("10/minute")
async def update_api_config(
    request: Request,
    req: ApiConfigRequest,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Update the current user's API key, base URL, and model."""
    # Geo guard: block domestic IPs from using non-whitelisted LLM APIs
    client_ip = get_client_ip(request)
    allowed, reason = check_api_allowed(client_ip, req.base_url)
    if not allowed:
        await storage.record_geo_block(user["id"], client_ip, req.base_url, reason)
        raise HTTPException(403, detail=reason)

    try:
        await storage.update_user_api_config(
            user["id"], req.api_key, req.base_url, req.model,
            req.embedding_key, req.embedding_region,
        )
        clear_user_llm_cache(user["id"])
        return {"ok": True}
    except Exception as exc:
        print(f"[auth] Update API config failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.post("/test-embedding")
@limiter.limit("5/minute")
async def test_embedding(
    request: Request,
    req: EmbeddingTestRequest,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Test DashScope embedding connectivity with the user's key and region."""
    key = req.embedding_key.strip()
    region = req.embedding_region.strip() or "cn"

    if not key:
        try:
            config = await storage.get_user_api_config(user["id"])
            key = config.get("embedding_key", "")
        except Exception:
            pass

    if not key:
        return {"ok": False, "error": "未提供 API Key，请先填写并保存"}

    try:
        from core.embeddings import DashScopeEmbedding
        emb = DashScopeEmbedding(api_key=key, region=region)
        emb(["测试"])
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/usage")
async def my_usage(
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Return the current user's usage stats."""
    return await storage.get_usage_stats(user["id"])


@router.put("/avatar")
@limiter.limit("10/minute")
async def update_avatar(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    body = await request.json()
    avatar = body.get("avatar_data", "")
    if len(avatar) > 150_000:
        raise HTTPException(400, "头像过大，请压缩后上传")
    await storage.update_user_avatar(user["id"], avatar)
    return {"ok": True}


@router.get("/avatar")
async def get_avatar(
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    data = await storage.get_user_avatar(user["id"])
    return {"avatar_data": data}


@router.put("/banner")
@limiter.limit("10/minute")
async def update_banner(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    body = await request.json()
    banner = body.get("banner_data", "")
    if len(banner) > 300_000:
        raise HTTPException(400, "封面图过大，请压缩后上传")
    await storage.update_user_banner(user["id"], banner)
    return {"ok": True}


@router.get("/banner")
async def get_banner(
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    banner = await storage.get_user_banner(user["id"])
    return {"banner_data": banner}


@router.put("/password")
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    req: ChangePasswordRequest,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    if not password_hasher.verify(req.old_password, user["password_hash"]):
        raise HTTPException(400, "当前密码错误")
    if not req.new_password or len(req.new_password) < 8:
        raise HTTPException(400, "新密码至少 8 位")
    if not _is_strong_password(req.new_password):
        raise HTTPException(400, "新密码需包含字母和数字")
    new_hash = password_hasher.hash(req.new_password)
    await storage.update_user_password(user["id"], new_hash)
    await storage.delete_user_refresh_tokens(user["id"])
    return {"ok": True}


@router.put("/email")
@limiter.limit("3/minute")
async def bind_email(
    request: Request,
    req: BindEmailRequest,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Bind or change email for the current user via verification code."""
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "邮箱地址无效")
    current_email = user.get("email", "")
    if email == current_email:
        raise HTTPException(400, "新邮箱不能与当前邮箱相同")
    valid = await storage.verify_code(email, req.code, "bind_email")
    if not valid:
        raise HTTPException(400, "验证码无效或已过期")
    existing = await storage.get_user_by_email(email)
    if existing and existing["id"] != user["id"]:
        raise HTTPException(400, "该邮箱已被其他账号绑定")
    await storage.update_user_email(user["id"], email)
    return {"ok": True, "email": email}


@router.put("/bio")
@limiter.limit("10/minute")
async def update_bio(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    body = await request.json()
    bio = (body.get("bio", "") or "").strip()[:200]
    await storage.update_user_bio(user["id"], bio)
    return {"ok": True, "bio": bio}


# ---- Presence visibility ----

@router.get("/presence-visibility")
@limiter.limit("30/minute")
async def get_presence_visibility(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, str]:
    """Get current user's presence visibility setting."""
    return {"presence_visibility": user.get("presence_visibility", "mutual")}


@router.put("/presence-visibility")
@limiter.limit("10/minute")
async def update_presence_visibility(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Update presence visibility setting: 'all', 'fans', 'mutual', or 'none'."""
    body = await request.json()
    visibility = body.get("presence_visibility", "").strip()
    if visibility not in ("all", "fans", "mutual", "none"):
        raise HTTPException(400, "presence_visibility 必须是 all/fans/mutual/none")
    ok = await storage.set_user_presence_visibility(user["id"], visibility)
    if not ok:
        raise HTTPException(500, "保存失败")
    return {"ok": True, "presence_visibility": visibility}


@router.get("/user/{user_id}/online")
@limiter.limit("60/minute")
async def get_user_online_status(
    request: Request,
    user_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Get another user's online status with privacy enforcement."""
    target = await storage.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "用户不存在")

    can_see = await storage.can_see_online_status(
        user["id"], user_id, is_admin=user.get("is_admin", False)
    )
    if not can_see:
        return {"online": None, "last_active_at": None, "hidden": True}

    ts = target.get("last_active_at") or target.get("last_login_at")
    online = False
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            online = (time.time() - dt.timestamp()) < 300
        except Exception:
            pass
    return {
        "online": online,
        "last_active_at": target.get("last_active_at"),
        "hidden": False,
    }


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


async def _create_refresh_token(user_id: str, storage: StorageBase) -> str:
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
        "bio": user.get("bio", ""),
    }
