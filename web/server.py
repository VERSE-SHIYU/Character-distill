"""FastAPI entry point: create app, mount routers, serve static files."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

# 在所有会触发模型加载的 import 之前，先执行全局 meta-tensor 防御。
# 此模块设置环境变量、torch 默认设备，并修补 nn.Module.to。
import os
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_WEB_DIR = Path(__file__).resolve().parent
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

import yaml
from pydantic import BaseModel

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from limiter import limiter

from security import SecurityHeadersMiddleware

import jwt as _jwt_lib

from routers.text import router as text_router
from routers.distill import legacy_router as distill_legacy
from routers.distill import router as distill_router
from routers.chat import legacy_router as chat_legacy
from routers.chat import router as chat_router
from routers.history import router as history_router
from routers.voice import router as voice_router
from routers.wechat import router as wechat_router
from routers.card import router as card_router
from routers.market import router as market_router
from routers.group import router as group_router
from routers.message import router as message_router
from routers.inter_node import router as inter_node_router
from routers.memory import router as memory_router
from routers.auth import get_current_user, router as auth_router
from routers.auth import (
    JWT_ALGORITHM,
    get_jwt_secret,
    validate_jwt_secret,
)
from inter_node_auth import validate_inter_node_secret
from routers.admin import require_admin, router as admin_router
from cross_border_sync import _cross_border_resync_loop
from deps import get_config, get_storage, reset_llm_and_dependents, _session_cleanup_loop
from storage.base import StorageBase
from core.log_collector import install_log_collector

_FRONTEND_DIST_DIR = _WEB_DIR / "frontend" / "dist"
_LEGACY_STATIC_DIR = _WEB_DIR / "static"
if _FRONTEND_DIST_DIR.exists():
    _STATIC_DIR = _FRONTEND_DIST_DIR
else:
    _STATIC_DIR = _LEGACY_STATIC_DIR
    print(
        "[server] frontend dist not found at web/frontend/dist. "
        "Run `cd web/frontend && npm run build` for production files, "
        "or `npm run dev` for Vite dev server."
    )

@asynccontextmanager
async def _lifespan(app: FastAPI):
    validate_jwt_secret()
    validate_inter_node_secret()
    install_log_collector()
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=200, thread_name_prefix="chat_pool"))
    from deps import set_main_loop
    set_main_loop(loop)
    await _preload_embedding()
    cleanup_task = asyncio.create_task(_session_cleanup_loop())
    resync_task = asyncio.create_task(_cross_border_resync_loop())
    yield
    cleanup_task.cancel()
    resync_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await resync_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Character Simulator API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)

app.state.limiter = limiter


async def _preload_embedding():
    """Embedding is now API-based (DashScope), no local model to preload."""
    print("[startup] Embedding: DashScope API mode (on-demand, no preload needed)")


async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse({"detail": "请求过于频繁，请稍后再试"}, status_code=429)


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )

_origins_env = os.getenv("ALLOWED_ORIGINS", "")
if not _origins_env:
    print(
        "\n[WARN] ALLOWED_ORIGINS 未设置！CORS 将拒绝所有跨域请求。"
        "\n   请在 .env 中设置: ALLOWED_ORIGINS=https://你的域名\n"
    )
ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()] or ["http://localhost:5173"]

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ---- Auth middleware ----
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import time

PUBLIC_PATHS = {"/api/auth/register", "/api/auth/login", "/api/auth/refresh", "/api/auth/send-code", "/api/auth/reset-password", "/api/health", "/api/announcement/active"}
PUBLIC_PREFIXES = ("/assets/", "/static/", "/favicon", "/manifest", "/login", "/api/market/", "/api/inter-node/")

# Throttle last_active updates to once per 60s per user
_last_active_ticks: dict[str, float] = {}


def _maybe_update_last_active(user_id: str) -> None:
    now = time.time()
    last = _last_active_ticks.get(user_id, 0)
    if now - last < 60:
        return
    _last_active_ticks[user_id] = now
    import asyncio
    asyncio.ensure_future(get_storage().update_last_active(user_id))


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = {}  # default, prevents AttributeError on non-API paths
        path = request.url.path
        # Allow public paths through
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES) or not path.startswith("/api/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse({"detail": "请先登录"}, status_code=401)

        try:
            payload = _jwt_lib.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        except _jwt_lib.ExpiredSignatureError:
            return JSONResponse({"detail": "Token 已过期，请重新登录"}, status_code=401)
        except _jwt_lib.InvalidTokenError:
            return JSONResponse({"detail": "Token 无效"}, status_code=401)

        user_id = payload.get("sub")
        if not user_id:
            return JSONResponse({"detail": "Token 无效"}, status_code=401)

        user = await get_storage().get_user_by_id(user_id)
        if user is None:
            return JSONResponse({"detail": "用户不存在"}, status_code=401)
        if user.get("is_disabled"):
            return JSONResponse({"detail": "账号已被禁用"}, status_code=403)
        request.state.user = user
        _maybe_update_last_active(user_id)
        return await call_next(request)


# ---- Auth middleware (last added = outermost, before include_router for exception_handler) ----
app.add_middleware(AuthMiddleware)

# ---- Mount routers ----
app.include_router(auth_router)      # 不需要认证
app.include_router(admin_router)
app.include_router(text_router)
app.include_router(distill_router)
app.include_router(chat_router)
app.include_router(history_router)
app.include_router(voice_router)
app.include_router(wechat_router)
app.include_router(card_router)
app.include_router(market_router)
app.include_router(group_router)
app.include_router(message_router)
app.include_router(inter_node_router)
app.include_router(memory_router)


# ---- Public: announce router ----
_announce_router = APIRouter(prefix="/api/announcement", tags=["announce"])


@_announce_router.get("/active")
async def public_active_announcement(
    storage: StorageBase = Depends(get_storage),
):
    """Get the currently active announcement (no auth required)."""
    return await storage.get_active_announcement() or {}


app.include_router(_announce_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/settings/config")
def read_settings_config(
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Read LLM + voice config for settings UI (authenticated users)."""
    try:
        llm = get_config().get("llm", {})
        voice = get_config().get("voice", {})
        has_key = bool(llm.get("api_key") or os.getenv("DEEPSEEK_API_KEY"))
        return {
            "base_url": str(llm.get("base_url", "")),
            "model": str(llm.get("model", "")),
            "api_key": "***" if has_key else "",
            "summary_threshold": int(llm.get("summary_threshold", 50)),
            "gptsovits_url": str(voice.get("gptsovits_url", "http://127.0.0.1:9880")),
            "funasr_url": str(voice.get("funasr_url", "ws://127.0.0.1:10095")),
        }
    except Exception as exc:
        print(f"[server] Read settings config failed: {exc}")
        raise HTTPException(500, "Read config failed") from exc


class UpdateConfigRequest(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    summary_threshold: int | None = None
    gptsovits_url: str | None = None
    funasr_url: str | None = None


@app.post("/api/settings/config")
@limiter.limit("30/minute")
async def update_settings_config(
    request: Request,
    req: UpdateConfigRequest,
    admin_user: dict = Depends(require_admin),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Update LLM + voice config at runtime and persist to config.yaml."""
    try:
        cfg = get_config()
        llm = cfg.setdefault("llm", {})
        changes = []

        def _changed(field: str, old: Any, new: Any) -> bool:
            return new is not None and str(new).strip() and str(new).strip() != str(old).strip()

        for field, val in [("base_url", req.base_url), ("model", req.model), ("api_key", req.api_key), ("summary_threshold", req.summary_threshold)]:
            old = llm.get(field, "")
            if val is not None and (not isinstance(val, str) or val.strip()):
                new_val = val.strip() if isinstance(val, str) else val
                if str(new_val) != str(old):
                    changes.append((field, str(old), str(new_val)))
                    llm[field] = new_val

        voice = cfg.setdefault("voice", {})
        for field, val in [("gptsovits_url", req.gptsovits_url), ("funasr_url", req.funasr_url)]:
            old = voice.get(field, "")
            if val is not None and val.strip() and val.strip() != str(old).strip():
                changes.append((field, str(old), val.strip()))
                voice[field] = val.strip()

        cfg_path = _REPO_ROOT / "config.yaml"
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)

        # Log config changes
        if changes:
            import uuid
            for field, old_val, new_val in changes:
                await storage.save_config_change(
                    uuid.uuid4().hex[:12], admin_user["id"], admin_user.get("username", ""),
                    field, old_val, new_val,
                )

        # 先持久化到 config.yaml，再调用 reset_llm_and_dependents()
        reset_llm_and_dependents()

        return {
            "base_url": str(llm.get("base_url", "")),
            "model": str(llm.get("model", "")),
            "api_key": "***" if llm.get("api_key") else "",
            "summary_threshold": int(llm.get("summary_threshold", 50)),
            "gptsovits_url": str(voice.get("gptsovits_url", "http://127.0.0.1:9880")),
            "funasr_url": str(voice.get("funasr_url", "ws://127.0.0.1:10095")),
        }
    except Exception as exc:
        print(f"[server] Update config failed: {exc}")
        raise HTTPException(500, f"Update config failed: {exc}") from exc


@app.post("/api/settings/test-gptsovits")
async def test_gptsovits_connection(req: Request, _admin: dict = Depends(require_admin)) -> dict[str, Any]:
    """Test GPT-SoVITS connectivity."""
    try:
        body = await req.json()
        url = body.get("url", "").strip()
        if not url:
            url = get_config().get("voice", {}).get("gptsovits_url", "http://127.0.0.1:9880")
        from speech.voice_clone import VoiceCloneClient
        vc = VoiceCloneClient(base_url=url)
        ok = await vc.health_check()
        return {"ok": ok, "url": url}
    except Exception as exc:
        print(f"[server] Test GPT-SoVITS failed: {exc}")
        return {"ok": False, "url": "", "error": str(exc)}


@app.post("/api/settings/test-funasr")
async def test_funasr_connection(req: Request, _admin: dict = Depends(require_admin)) -> dict[str, Any]:
    """Test FunASR connectivity."""
    try:
        body = await req.json()
        url = body.get("url", "").strip()
        if not url:
            url = get_config().get("voice", {}).get("funasr_url", "ws://127.0.0.1:10095")
        from speech.funasr_client import FunASRClient
        client = FunASRClient(url=url)
        ok = await client.is_available()
        return {"ok": ok, "url": url}
    except Exception as exc:
        print(f"[server] Test FunASR failed: {exc}")
        return {"ok": False, "url": "", "error": str(exc)}


# Legacy compat: keep old /api/identify, /api/distill, /api/chat, /api/reset
app.include_router(distill_legacy)
app.include_router(chat_legacy)


# ---- Static files ----
# Vite build references /assets/* and /favicon.svg at site root (not under /static).


@app.get("/")
def serve_index():
    """Serve the frontend index page."""
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "index.html not found. Build frontend with "
                "`cd web/frontend && npm run build` or run `npm run dev`."
            ),
        )
    return FileResponse(index_path)


@app.get("/favicon.svg")
def serve_favicon() -> FileResponse:
    """Vite public favicon."""
    path = _STATIC_DIR / "favicon.svg"
    if not path.exists():
        raise HTTPException(404, "favicon.svg not found")
    return FileResponse(path)


@app.get("/icons.svg")
def serve_icons() -> FileResponse:
    """Sprite sheet from Vite public/."""
    path = _STATIC_DIR / "icons.svg"
    if not path.exists():
        raise HTTPException(404, "icons.svg not found")
    return FileResponse(path)


# ---- Static file mounts (must be registered BEFORE the catch-all) ----
_assets_dir = _STATIC_DIR / "assets"
if _assets_dir.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_assets_dir)),
        name="frontend_assets",
    )

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:
    print(f"[server] WARNING: Static dir {_STATIC_DIR} not found — serving API only.")

# Voice cache audio files
_voice_cache_dir = _REPO_ROOT / "data" / "voice_cache"
_voice_cache_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(_voice_cache_dir)), name="voice_audio")


@app.get("/{path:path}")
def serve_spa(path: str):
    """SPA fallback: serve index.html for all non-API routes (must be last-registered route)."""
    if path.startswith("api/"):
        raise HTTPException(status_code=404)
    # Serve real static files at the root level (e.g. Chinese-named files)
    # before falling back to index.html.
    candidate = _STATIC_DIR / path
    try:
        real = os.path.realpath(candidate)
    except (OSError, ValueError):
        real = None
    if real is not None and os.path.isfile(real):
        try:
            Path(real).resolve().relative_to(_STATIC_DIR.resolve())
            return FileResponse(real)
        except ValueError:
            pass  # path traversal attempt — fall through to index.html
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(index_path)


if __name__ == "__main__":
    import uvicorn
    print("Server starting: http://localhost:7860")
    uvicorn.run(app, host="0.0.0.0", port=7860)
