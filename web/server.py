"""FastAPI entry point: create app, mount routers, serve static files."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEB_DIR = Path(__file__).resolve().parent

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routers.text import router as text_router
from routers.distill import legacy_router as distill_legacy
from routers.distill import router as distill_router
from routers.chat import legacy_router as chat_legacy
from routers.chat import router as chat_router
from routers.history import router as history_router
from routers.tts import router as tts_router
from deps import get_config

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

app = FastAPI(title="Character Simulator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Mount routers ----
app.include_router(text_router)
app.include_router(distill_router)
app.include_router(chat_router)
app.include_router(history_router)
app.include_router(tts_router)


@app.get("/api/settings/config")
def read_settings_config() -> dict[str, str]:
    """Read-only LLM config for settings UI (edit config.yaml to change)."""
    try:
        llm = get_config().get("llm", {})
        return {
            "base_url": str(llm.get("base_url", "")),
            "model": str(llm.get("model", "")),
        }
    except Exception as exc:
        print(f"[server] Read settings config failed: {exc}")
        raise HTTPException(500, "Read config failed") from exc


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


_assets_dir = _STATIC_DIR / "assets"
if _assets_dir.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_assets_dir)),
        name="frontend_assets",
    )

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    print("Server starting: http://localhost:7860")
    uvicorn.run(app, host="0.0.0.0", port=7860)
