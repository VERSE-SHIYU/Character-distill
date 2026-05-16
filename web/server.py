"""FastAPI 后端：为 React 前端提供蒸馏与对话 API，并托管静态文件。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from adapters.llm_adapter import LLMAdapter
from core.chat_engine import ChatEngine
from core.distiller import Distiller
from core.rag import RAGEngine
from core.schema import CharacterCard

_CFG_PATH = _REPO_ROOT / "config.yaml"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

try:
    with open(_CFG_PATH, encoding="utf-8") as _f:
        cfg: dict[str, Any] = yaml.safe_load(_f)
except Exception as exc:
    print(f"读取配置失败：{exc}")
    raise

llm = LLMAdapter()
distiller = Distiller(llm)

# 每个蒸馏会话的状态：{session_id: {"engine": ChatEngine, "card": CharacterCard}}
_sessions: dict[str, dict[str, Any]] = {}

app = FastAPI(title="角色模拟器 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求/响应模型 ──────────────────────────────────────────────

class IdentifyRequest(BaseModel):
    """角色识别请求。"""
    text: str

class DistillRequest(BaseModel):
    """蒸馏请求。"""
    text: str
    character_name: str = ""

class ChatRequest(BaseModel):
    """对话请求。"""
    session_id: str
    message: str

class ResetRequest(BaseModel):
    """重置对话请求。"""
    session_id: str


# ── API 端点 ────────────────────────────────────────────────────

@app.post("/api/identify")
def api_identify(req: IdentifyRequest):
    """识别文本中的角色列表。"""
    if not req.text.strip():
        raise HTTPException(400, "文本不能为空")
    try:
        chars = distiller.identify_characters(req.text)
        return {"characters": chars}
    except Exception as exc:
        print(f"角色识别失败：{exc}")
        raise HTTPException(500, f"角色识别失败：{exc}") from exc


@app.post("/api/distill")
def api_distill(req: DistillRequest):
    """蒸馏指定角色并返回角色卡 + session_id。"""
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "文本不能为空")

    char_name = req.character_name.strip()
    try:
        if not char_name:
            chars = distiller.identify_characters(text)
            if not chars:
                raise HTTPException(400, "未识别到角色，请手动输入角色名")
            char_name = chars[0].get("name", "")
            if not char_name:
                raise HTTPException(400, "识别结果缺少角色名")

        card = distiller.distill(text, char_name)
    except HTTPException:
        raise
    except Exception as exc:
        print(f"蒸馏失败：{exc}")
        raise HTTPException(500, f"蒸馏失败：{exc}") from exc

    try:
        rag = RAGEngine(cfg["rag"])
        rag.index(text)
        engine = ChatEngine(llm, rag, card)
    except Exception as exc:
        print(f"初始化对话引擎失败：{exc}")
        raise HTTPException(500, f"初始化对话引擎失败：{exc}") from exc

    import hashlib, time
    session_id = hashlib.md5(f"{card.name}_{time.time()}".encode()).hexdigest()[:12]
    _sessions[session_id] = {"engine": engine, "card": card}

    card_dict = card.model_dump()
    card_dict["session_id"] = session_id
    return card_dict


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    """以角色身份回复用户消息。"""
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(404, "会话不存在，请先蒸馏角色")

    message = req.message.strip()
    if not message:
        raise HTTPException(400, "消息不能为空")

    try:
        resp, rag_ctx = session["engine"].chat(message)
        return {"reply": resp, "rag_context": rag_ctx[:200]}
    except Exception as exc:
        print(f"对话失败：{exc}")
        raise HTTPException(500, f"对话失败：{exc}") from exc


@app.post("/api/reset")
def api_reset(req: ResetRequest):
    """重置对话历史（保留角色卡）。"""
    session = _sessions.get(req.session_id)
    if session:
        session["engine"].reset()
    return {"ok": True}


# ── 静态文件 ─────────────────────────────────────────────────────

@app.get("/")
def serve_index():
    """返回前端首页。"""
    return FileResponse(_STATIC_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    print("启动服务器：http://localhost:7860")
    uvicorn.run(app, host="0.0.0.0", port=7860)
