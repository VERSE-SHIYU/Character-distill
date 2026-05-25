"""FastAPI entry point: create app, mount routers, serve static files."""

from __future__ import annotations

# 在所有会触发模型加载的 import 之前，先执行全局 meta-tensor 防御。
# 此模块设置环境变量、torch 默认设备，并修补 nn.Module.to。
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import core.fix_meta_tensor  # noqa: E402  必须最先执行

_WEB_DIR = Path(__file__).resolve().parent
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

import yaml
from pathlib import Path as _Path
from pydantic import BaseModel

from fastapi import Depends, FastAPI, HTTPException, Request
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
from routers.auth import router as auth_router
from routers.auth import JWT_SECRET, JWT_ALGORITHM
from routers.admin import require_admin, router as admin_router
from deps import get_config, get_storage, reset_llm_and_dependents

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

app = FastAPI(title="Character Simulator API", docs_url=None, redoc_url=None, openapi_url=None)

app.state.limiter = limiter


@app.on_event("startup")
async def _preload_embedding():
    """Preload SentenceTransformer model so first chat is fast (1-2s instead of 5s)."""
    from core.embeddings import create_safe_embedding_fn
    create_safe_embedding_fn()
    print("[startup] Embedding model preloaded")


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

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://yourdomain.cn").split(",")

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

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

# ---- Auth middleware ----
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

PUBLIC_PATHS = {"/api/auth/register", "/api/auth/login", "/api/auth/refresh", "/api/auth/send-code", "/api/auth/reset-password", "/api/health"}
PUBLIC_PREFIXES = ("/assets/", "/static/", "/favicon", "/manifest", "/login")


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
            payload = _jwt_lib.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
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
        return await call_next(request)


app.add_middleware(AuthMiddleware)


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
def update_settings_config(
    req: UpdateConfigRequest,
    _admin: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Update LLM + voice config at runtime and persist to config.yaml."""
    try:
        cfg = get_config()
        llm = cfg.setdefault("llm", {})
        if req.base_url is not None and req.base_url.strip():
            llm["base_url"] = req.base_url.strip()
        if req.model is not None and req.model.strip():
            llm["model"] = req.model.strip()
        if req.api_key is not None and req.api_key.strip():
            llm["api_key"] = req.api_key.strip()
        if req.summary_threshold is not None:
            llm["summary_threshold"] = req.summary_threshold

        voice = cfg.setdefault("voice", {})
        if req.gptsovits_url is not None and req.gptsovits_url.strip():
            voice["gptsovits_url"] = req.gptsovits_url.strip()
        if req.funasr_url is not None and req.funasr_url.strip():
            voice["funasr_url"] = req.funasr_url.strip()

        cfg_path = _REPO_ROOT / "config.yaml"
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)

        # 先持久化到 config.yaml，再调用 reset_llm_and_dependents()
        # 重建 LLM/Distiller/TextManager 单例使新配置即时生效
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


@app.get("/{path:path}")
def serve_spa(path: str):
    """SPA fallback: serve index.html for all non-API routes."""
    if path.startswith("api/"):
        raise HTTPException(status_code=404)
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(index_path)


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


if __name__ == "__main__":
    import uvicorn
    print("Server starting: http://localhost:7860")
    uvicorn.run(app, host="0.0.0.0", port=7860)
