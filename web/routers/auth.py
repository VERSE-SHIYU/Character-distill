"""Authentication: register, login, JWT, refresh tokens, logout."""

from __future__ import annotations

import enum
import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pydantic import BaseModel, field_validator

from adapters.llm_adapter import LLMCallRefused
from core import roles
from core.email_service import send_verification_code
from core.nonfatal import nonfatal
from deps import get_config, get_storage, refresh_user_llm
from storage.base import StorageBase
from limiter import limiter
from web.llm_gate import emit_geo_block_audit, geo_refusal
from web.legal_versions import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION
from web.limiter import get_client_ip

USERNAME_RE = re.compile(r'^[a-zA-Z0-9_]{2,20}$')

logger = logging.getLogger("charsim.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_DEFAULT_INSECURE_JWT_SECRET = "character-distill-dev-secret-key-change-in-prod"


def get_jwt_secret() -> str:
    """JWT 签名/验签的 secret —— **唯一取值点，同时也是 FastAPI 的注入点**。

    「是注入点」这件事是承重的（缺陷 46）：需要**值**的地方用 `Depends(get_jwt_secret)`
    拿它（签发侧：`login` / `register` / `refresh`），测试才能用
    `app.dependency_overrides[get_jwt_secret]` **显式**给值。不这样收敛，用例的通过
    条件就变成「这台机器恰好有没有 .env」—— 换台干净检出结果就变，而且签名里看不出
    「这里需要一个 secret」，下一个写用例的人照样隐式读 .env。
    校验语义不变：未设置 / 等于已知默认值 / 短于 32 字符，一律拒绝。

    只需要**怎么取**的地方（验签侧的适配器）改用 `jwt_secret_source`：`Depends`
    会在进端点前求值，匿名请求也会因此读一次 secret，未配置时直接 500（缺陷 95）。
    """
    secret = os.getenv("JWT_SECRET")
    if not secret or secret == _DEFAULT_INSECURE_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET 未设置或使用了默认值！"
            "请在 .env 中设置: JWT_SECRET=$(openssl rand -hex 32)"
        )
    if len(secret) < 32:
        raise RuntimeError(
            f"JWT_SECRET 长度不足 32 字符（当前 {len(secret)}）。"
            "请用 openssl rand -hex 32 生成"
        )
    return secret


def jwt_secret_source() -> Callable[[], str]:
    """依赖：把 secret 的**取值函数**交给调用方，而不是在这里取值。

    `Depends(get_jwt_secret)` 在进端点函数体之前就求值，与请求带不带凭据无关 —— 一条
    匿名公开读也会因此把 secret 读一遍，`JWT_SECRET` 未配置时直接 500（缺陷 95）。
    返回函数本身，取值时机留给 `resolve_identity`（它判完 MISSING 才调），
    「不需要 secret 的路径根本不碰它」这条口径才成立。

    要**值**的地方（签发侧）仍用 `Depends(get_jwt_secret)`：那里本来就需要它。
    """
    return get_jwt_secret


def validate_jwt_secret() -> None:
    get_jwt_secret()


def validate_fernet_key() -> None:
    """Validate FERNET_KEY format at startup if set.

    Fernet requires a 32-byte url-safe base64 key. If FERNET_KEY is not set,
    the system falls back to JWT_SECRET (validated separately), so we only
    check when it IS explicitly configured.
    """
    key = os.getenv("FERNET_KEY")
    if not key:
        return
    try:
        from cryptography.fernet import Fernet
        Fernet(key.encode())
    except Exception as exc:
        raise RuntimeError(
            "FERNET_KEY 格式错误：必须是 base64-urlsafe 编码的 32 字节 key。\n"
            "当前 FERNET_KEY 无法初始化 Fernet 加密器。\n"
            "请用以下命令生成正确格式的 key：\n"
            "  python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            "或：\n"
            "  openssl rand -base64 32"
        ) from exc


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


class NicknameRequest(BaseModel):
    nickname: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    username: str
    created_at: str
    role: str = roles.DEFAULT_ROLE
    is_disabled: bool = False
    has_api_key: bool = False
    base_url: str = ""
    model: str = ""


class ApiConfigRequest(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    embedding_key: str = ""
    embedding_region: str = ""

    @field_validator("embedding_region")
    @classmethod
    def _known_region_or_blank(cls, v: str) -> str:
        """地域只能是空串或 `DASHSCOPE_BASE_URLS` 里的键（台账 120）。

        空串是「这次不改这个字段」—— 仓储层对空字段一律不写（`update_user_api_config`），
        所以放行空串与「不写」是同一件事，不是漏检。

        查的是**同一张表**：另写一份 `{"cn", "intl"}` 会与构造函数分叉（试连端点已因此
        改成查表，见 test_E9b）。导入放在函数内 —— `core.embeddings` 顶层 import chromadb，
        路由模块不该为一次字段校验背上它。
        """
        if not v:
            return v
        from core.embeddings import DASHSCOPE_BASE_URLS

        if v not in DASHSCOPE_BASE_URLS:
            raise ValueError(f"地域只能是 {' / '.join(DASHSCOPE_BASE_URLS)}，或留空")
        return v


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


class Verdict(enum.Enum):
    """凭据 → 身份的判定结果。**事实**，不含任何 HTTP 形态。"""

    OK = "ok"
    MISSING = "missing"      # 没带凭据 / scheme 不是 Bearer
    EXPIRED = "expired"
    INVALID = "invalid"      # 签名错、格式错、其它任何解不开
    NO_SUB = "no_sub"        # 验签通过但 payload 里没有 sub
    NO_USER = "no_user"      # sub 指向的账号不存在
    DISABLED = "disabled"


#: 拒绝事态 → (状态码, 文案)。**只写这一处**：三个出口的 401/403 都从这里取，
#: 于是「中间件说 Token 已过期、依赖又是另一句」这种漂移在结构上不可能发生。
#:
#: 状态码与收敛前逐一相同（不变的鉴权结果）；文案统一了两处历史分歧：
#: 过期取「Token 已过期，请重新登录」（中间件那份更完整），sub 缺失取「Token 无效」
#: （与签名不对同类，不另立一句）。前端按状态码分支，不解析文案。
IDENTITY_REJECTIONS: dict[Verdict, tuple[int, str]] = {
    Verdict.MISSING: (401, "请先登录"),
    Verdict.EXPIRED: (401, "Token 已过期，请重新登录"),
    Verdict.INVALID: (401, "Token 无效"),
    Verdict.NO_SUB: (401, "Token 无效"),
    Verdict.NO_USER: (401, "用户不存在"),
    Verdict.DISABLED: (403, "账号已被禁用"),
}


async def resolve_identity(
    token: str | None,
    secret_source: Callable[[], str],
    storage: StorageBase,
) -> tuple[Verdict, dict[str, Any] | None]:
    """凭据 → (事态, 身份)。**全仓唯一的验签与取号点**（`jwt.decode` 只在这里调）。

    `token=None` 包住两种「没凭据」：真的没带，以及 scheme 不是 Bearer
    （`security_scheme` 是 HTTPBearer 且 `auto_error=False`，非 Bearer 时它返回 None）。
    调用方拿到事后自己决定出口形态：抛异常、返回空字典、还是造 JSONResponse。

    不返回 HTTP 形态是刻意的：`web/server.py` 的中间件与两个依赖依赖各自的错误通道，
    在这里统一成一种就把另外两种形态的语义丢掉了。

    **secret 以「取值函数」传入，且在判完 MISSING 之后才调。** `get_jwt_secret()` 在
    `JWT_SECRET` 未配置/过短时是抛 `RuntimeError` 的，即「取 secret」本身会失败。若在
    入口先取值，一条**没带凭据**的请求（本该 401）就会因为一个与它无关的配置变成 500 ——
    鉴权结果被配置改了。传函数、按需调，是让「不需要 secret 的路径」根本不碰它。

    为什么不是「让调用方自己先判一下再传值」：那会把「没凭据」的判定开出第二个出处，
    而收敛成一处正是本函数存在的理由。时机与判定都留在这一处，调用方只给**怎么取**。
    """
    if not token:
        return Verdict.MISSING, None
    secret = secret_source()
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return Verdict.EXPIRED, None
    except jwt.InvalidTokenError:
        return Verdict.INVALID, None
    user_id = payload.get("sub")
    if not user_id:
        return Verdict.NO_SUB, None
    user = await storage.get_user_by_id(user_id)
    if user is None:
        return Verdict.NO_USER, None
    if user.get("is_disabled"):
        return Verdict.DISABLED, None
    return Verdict.OK, user


async def resolve_request_identity(
    request: Request,
    token: str | None,
    secret_source: Callable[[], str],
    storage: StorageBase,
) -> tuple[Verdict, dict[str, Any] | None]:
    """一次请求内身份**只判一次**：先看这次请求有没有算过同一个 token。

    中间件在路由匹配**之前**就跑，带有效凭据的 `/api/` 请求因此先算一次；端点侧的
    `get_current_user` / `get_optional_user` 拿到的是**同一个请求、同一个 token**，
    再算一次就是重复（多一次 `get_user_by_id`，公开的 `/api/market/*` 写路由上实测
    两次）。两层鉴权都留着 —— 纵深防御、纯路由 app 与 `get_jwt_secret` 注入点都要
    适配器能独立工作；这里消掉的只是**同一次请求里的重复执行**。

    **判定结果连带 token 一起存**，匹配的是 token 字符串而不是「有没有存过」：同一个
    Request 上换了 token 就必须重算（`tests/test_identity_resolution.py` 有一条钉着
    这点）。空 token 也照存 —— 没凭据时 `resolve_identity` 立刻返回 MISSING，不碰
    secret 也不碰 storage，存下来省掉的正是这次空跑。

    上游没存过（纯路由 app、探针 app、直接调用）就自己算 —— 与
    `getattr(request.state, ..., None)` 取不到即视为「没有」同一条口径。
    """
    cached = getattr(request.state, "identity_verdict", None)
    if cached is not None and cached[0] == token:
        return cached[1], cached[2]
    verdict, user = await resolve_identity(token, secret_source, storage)
    request.state.identity_verdict = (token, verdict, user)
    return verdict, user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    storage: StorageBase = Depends(get_storage),
    secret_source: Callable[[], str] = Depends(jwt_secret_source),
) -> dict[str, Any]:
    """Extract and verify JWT from Authorization header. Raises 401 if missing/invalid.

    `request` 由框架注入（`Depends` 一个不动），只为复用中间件算过的同一次判定。
    secret 收的是**取值函数**：本依赖在公开路径上也挂着（`get_optional_user` 同理），
    在这里取值会让匿名请求连带读一次 secret（缺陷 95）。
    """
    verdict, user = await resolve_request_identity(
        request, credentials.credentials if credentials else None, secret_source, storage,
    )
    if verdict is Verdict.OK and user is not None:
        return user
    status, detail = IDENTITY_REJECTIONS[verdict]
    raise HTTPException(status, detail)


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    storage: StorageBase = Depends(get_storage),
    secret_source: Callable[[], str] = Depends(jwt_secret_source),
) -> dict[str, Any]:
    """Like get_current_user but returns empty dict for unauthenticated requests."""
    verdict, user = await resolve_request_identity(
        request, credentials.credentials if credentials else None, secret_source, storage,
    )
    return user if verdict is Verdict.OK and user is not None else {}


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
    except Exception as exc:
        # 绑定却不用 = 诊断整条丢弃：用户只看到「邮件发送失败」，运维无从知道是 SMTP 还是
        # Resend 的哪一步炸了（缺陷 39 顺带，与「线索不能丢」同族）。
        print(f"[auth] Send verification code failed: {exc!r}")
        raise HTTPException(500, "邮件发送失败，请稍后重试")
    return {"ok": True}


@router.post("/register")
@limiter.limit("3/hour")
async def register(
    request: Request,
    req: AuthRequest,
    storage: StorageBase = Depends(get_storage),
    secret_source: Callable[[], str] = Depends(jwt_secret_source),
) -> dict[str, Any]:
    """Register a new user and return JWT + refresh token."""
    username = req.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "用户名只能包含英文字母、数字、下划线，长度 2–20 位")
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
    node_region = os.getenv("NODE_REGION", "cn-shenzhen")
    user = await storage.create_user(
        user_id, username, password_hash, email=email, home_region=node_region,
    )
    if inv:
        await storage.use_invite_code(inv, user["id"])

    # Best-effort sync profile to peer node
    try:
        from cross_border_sync import forward_user_profile_to_peer
        await forward_user_profile_to_peer(user["id"], user.get("username", ""), node_region, user.get("avatar_data", ""))
    except Exception as exc:
        logger.error("Forward user profile to peer failed: %s", exc, exc_info=True)

    # Record consent
    try:
        client_ip = get_client_ip(request)
        await storage.record_user_consent(user["id"], CURRENT_TERMS_VERSION, CURRENT_PRIVACY_VERSION, client_ip)
    except Exception as exc:
        logger.error("Failed to record consent for %s: %s", user_id, exc, exc_info=True)

    # First user with seed code becomes admin
    admin_seed = os.getenv("ADMIN_INVITE_CODE", "")
    if admin_seed and inv == admin_seed:
        await storage.set_user_role(user["id"], roles.ADMIN)
        user["role"] = roles.ADMIN

    access_token = _create_access_token(user["id"], user["username"], secret_source())
    refresh_token, _ = await _create_refresh_token(user["id"], storage)
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
@limiter.limit("10/minute")
async def login(
    request: Request,
    req: AuthRequest,
    storage: StorageBase = Depends(get_storage),
    secret: str = Depends(get_jwt_secret),
) -> dict[str, Any]:
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

    access_token = _create_access_token(user["id"], user["username"], secret)
    refresh_token, _ = await _create_refresh_token(user["id"], storage)
    await _touch_last_login(storage, user["id"])
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": _user_response(user),
    }


@router.post("/refresh")
async def refresh(
    req: RefreshRequest,
    storage: StorageBase = Depends(get_storage),
    secret: str = Depends(get_jwt_secret),
) -> dict[str, Any]:
    """Exchange a refresh token for a new access_token + new refresh_token (rotation)."""
    token_hash = _hash_token(req.refresh_token)
    record = await storage.get_refresh_token(token_hash)
    if not record:
        raise HTTPException(401, "Refresh token 无效")
    now = datetime.now(timezone.utc)
    if record.get("used"):
        used_at = record.get("used_at")
        replaced_by = record.get("replaced_by", "")
        if used_at and replaced_by:
            try:
                used_dt = datetime.fromisoformat(used_at)
                if (now - used_dt).total_seconds() < 30:
                    # Grace window: issue a new pair, chain replaced_by forward
                    user = await storage.get_user_by_id(record["user_id"])
                    if user and not user.get("is_disabled"):
                        access_token = _create_access_token(user["id"], user["username"], secret)
                        new_refresh_token, new_token_hash = await _create_refresh_token(user["id"], storage)
                        # Chain forward: update already-used row's replaced_by
                        await storage.mark_refresh_token_used(token_hash, replaced_by=new_token_hash)
                        await _touch_last_login(storage, user["id"])
                        return {
                            "access_token": access_token,
                            "refresh_token": new_refresh_token,
                            "token_type": "bearer",
                            "user": _user_response(user),
                        }
            except (ValueError, TypeError):
                pass
        # Outside grace window — revoke all tokens (breach protection)
        await storage.delete_user_refresh_tokens(record["user_id"])
        raise HTTPException(401, "Refresh token 已被使用")
    if record.get("expires_at", "") < now.isoformat():
        raise HTTPException(401, "Refresh token 已过期")

    user = await storage.get_user_by_id(record["user_id"])
    if not user:
        raise HTTPException(401, "用户不存在")
    if user.get("is_disabled"):
        raise HTTPException(403, "账号已被禁用")

    access_token = _create_access_token(user["id"], user["username"], secret)
    new_refresh_token, new_token_hash = await _create_refresh_token(user["id"], storage)
    # Chain: mark old token as used, pointing to the new token
    await storage.mark_refresh_token_used(token_hash, replaced_by=new_token_hash)
    await _touch_last_login(storage, user["id"])
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
    resp["nickname"] = user.get("nickname", "")
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
    # Geo guard: block domestic IPs from using non-whitelisted LLM APIs.
    # 判定与审计都走门的两个单点（geo_refusal / emit_geo_block_audit）—— 这里不再
    # 自带一份策略、也不再设例外，否则保存路径与出站路径会各有一套口径。
    client_ip = get_client_ip(request)
    # `embedding_region` 也要判（缺陷 73）：它决定嵌入出站的端点，国内 IP 存成 `intl`
    # 就是一条出境的嵌入路。空串是「这次不改这个字段」（`_known_region_or_blank`），
    # 与「没填」同义，故不判。判的是**同一个** `geo_refusal`，不另写一套。
    targets = [req.base_url]
    if req.embedding_region:
        from core.embeddings import DASHSCOPE_BASE_URLS

        targets.append(DASHSCOPE_BASE_URLS[req.embedding_region])
    for base_url in targets:
        reason = geo_refusal(client_ip, base_url)
        if reason:
            # 审计写入失败不得改写判定：emit_geo_block_audit 自己吞（非致命只记日志），
            # 故下面必须仍然回 403。
            emit_geo_block_audit(user["id"], client_ip, base_url, reason)
            raise HTTPException(403, detail=reason)

    try:
        await storage.update_user_api_config(
            user["id"], req.api_key, req.base_url, req.model,
            req.embedding_key, req.embedding_region,
        )
        # 换活会话手里的连接（含清缓存）。换失败**不改写**这次保存的结果：配置已经落库，
        # 下个请求自己会取到新实例（缓存已经清掉了），活会话只是晚一轮生效。
        async with nonfatal("auth", "refresh live sessions"):
            await refresh_user_llm(user["id"], storage)
        return {"ok": True}
    except ValueError as exc:
        # 仓储层「语句执行了却没改到行」= 用户不存在（`update_user_api_config` 的契约）。
        # 这跟写库失败不是一回事：500 会让人去查数据库，404 才说清是身份对不上。
        # 必须排在下面那条通用 except 之前，否则被吞成 500。
        raise HTTPException(404, "用户不存在") from exc
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
    """Test DashScope embedding connectivity with the user's key and region.

    这是**排障**端点，不是用户的日常路径：失败时除中文提示外**有意**把上游原话
    （`detail`）一并返回给用户 —— 用户来这里就是为了看 key 哪里不通。故它不在
    台账 94「一律通用文案」的口径内。
    """
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

    from core.embeddings import (
        DASHSCOPE_BASE_URLS,
        DashScopeEmbedding,
        describe_embedding_failure,
    )

    # 先在构造客户端之前拦下非法地域（改前是 `DASHSCOPE_BASE_URLS[region]` 抛 KeyError 上屏）。
    # 校验与构造函数查**同一张表**，不另写一份 {"cn", "intl"}。
    if region not in DASHSCOPE_BASE_URLS:
        return {"ok": False, "error": "地域只能选 cn 或 intl"}

    try:
        emb = DashScopeEmbedding(api_key=key, region=region)
        emb(["测试"])
        return {"ok": True}
    except LLMCallRefused:
        # 门拒了就不吞：请求根本没发出去，把理由当「嵌入失败」上屏是在说谎。交统一出口
        # （`web/server.py` 的 `_llm_error_handler` → `call_refused` → 403 + 理由）。
        raise
    except Exception as exc:
        # 日志只放 user id 与异常，绝不放 key。
        logger.warning("Embedding test failed for user %s: %r", user["id"], exc)
        return {"ok": False, "error": describe_embedding_failure(exc), "detail": str(exc)}


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


@router.put("/nickname")
@limiter.limit("10/minute")
async def update_nickname(
    request: Request,
    req: NicknameRequest,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Update the current user's display nickname (0–30 chars, empty = use username)."""
    nickname = req.nickname.strip()
    if len(nickname) > 30:
        raise HTTPException(400, "昵称长度不能超过 30 个字符")
    await storage.update_user_nickname(user["id"], nickname)
    return {"ok": True, "nickname": nickname}


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
        user["id"], user_id, as_admin=roles.is_admin(user)
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
        "last_active_at": ts,
        "hidden": False,
    }


# ---- Helpers ----

async def _touch_last_login(storage: StorageBase, user_id: str) -> None:
    """记录登录时间；失败只记日志，**不得**让登录失败。

    store 层现在对库失败一律抛 `StoreError`（不变量：空返回只表示无数据）。
    容忍是**调用方的策略**，必须写在这里而不是藏回 store —— 刷新令牌那一支
    （`/refresh`）在这行之前已经 `mark_refresh_token_used` 提交了新令牌，
    若此处上抛，客户端拿到 500、手里的旧令牌又已作废 = 被登出。
    """
    try:
        await storage.update_last_login(user_id)
    except Exception as exc:
        logger.warning("Update last_login failed (non-fatal) for %s: %s", user_id, exc)


def _create_access_token(user_id: str, username: str, secret: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


async def _create_refresh_token(user_id: str, storage: StorageBase, replaced_by: str = "") -> tuple[str, str]:
    raw = secrets.token_urlsafe(64)
    token_hash = _hash_token(raw)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS)).isoformat()
    await storage.save_refresh_token(token_hash, user_id, expires_at, replaced_by=replaced_by)
    return raw, token_hash


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
        "nickname": user.get("nickname", ""),
        "created_at": user.get("created_at", ""),
        "role": roles.role_of(user),
        "is_disabled": bool(user.get("is_disabled", False)),
        "bio": user.get("bio", ""),
    }
