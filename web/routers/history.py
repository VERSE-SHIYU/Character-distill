"""History: list sessions, get conversation, delete, export, trash."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from deps import get_sessions, get_storage
from core.schema import CharacterCard
from storage.base import StorageBase
from routers.auth import get_current_user

router = APIRouter(prefix="/api/history", tags=["history"])


class ResumeRequest(BaseModel):
    """Empty body placeholder — session_id comes from the URL path."""
    pass


# ---- Static-path routes first (before /{session_id} parameterized routes) ----

@router.get("/list")
async def list_sessions(
    request: Request,
    user: dict = Depends(get_current_user),
    keyword: str = Query("", description="Search keyword in messages"),
    character: str = Query("", description="Filter by character name"),
    text_id: str = Query("", description="Filter by text_id"),
    card_id: str = Query("", description="Filter by card_id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Paginated session list with optional keyword and character filters."""
    user_id = user["id"]
    try:
        return await storage.list_sessions(keyword, character, text_id, page, page_size, user_id, card_id)
    except Exception as exc:
        print(f"[history] List sessions failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.get("/trash")
async def list_trash(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> list[dict]:
    """List soft-deleted sessions (trash bin)."""
    try:
        user_id = user["id"]
        return await storage.list_trash_sessions(user_id)
    except Exception as exc:
        print(f"[history] List trash failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.delete("/trash/purge")
async def purge_trash(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Permanently delete all sessions in trash."""
    try:
        user_id = user["id"]
        count = await storage.purge_trash(user_id)
        return {"ok": True, "purged": count}
    except Exception as exc:
        print(f"[history] Purge trash failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.post("/clear-all")
async def clear_all_sessions(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Soft-delete all sessions (move to trash)."""
    try:
        user_id = user["id"]
        count = await storage.clear_all_sessions(user_id)
        return {"ok": True, "deleted": count}
    except Exception as exc:
        print(f"[history] Clear all sessions failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


# ---- Parameterized routes (/{session_id}/...) ----

@router.get("/{session_id}/export")
async def export_session(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    format: str = Query("json", description="Export format: json or txt"),
    storage: StorageBase = Depends(get_storage),
) -> Response:
    """Export a session as json or txt."""
    try:
        session = await storage.get_session(session_id)
    except Exception:
        raise HTTPException(404, "Session not found")
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此会话")
    try:
        content = await storage.export_session(session_id, format)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[history] Export session failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    if format.lower().strip() == "txt":
        return PlainTextResponse(content)
    return Response(content=content, media_type="application/json")


@router.get("/{session_id}")
async def get_session_detail(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Get a session with its full message list."""
    session = await storage.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此会话")
    try:
        messages = await storage.get_messages(session_id)
    except Exception as exc:
        print(f"[history] Get messages failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    return {"session": session, "messages": messages}


@router.post("/{session_id}/resume")
async def resume_session(
    session_id: str,
    _body: ResumeRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict[str, dict[str, Any]] = Depends(get_sessions),
) -> dict[str, Any]:
    """Rebuild the in-memory ChatEngine for a persisted session.

    Used when the server has restarted (``_sessions`` dict is empty) or
    the session was created in a different process.  Reconstructs the
    RAG index, ChatEngine, and replays history messages so the user can
    pick up the conversation where it left off.
    """
    user_id = user["id"]
    from deps import get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    text_manager = get_text_manager(llm=per_user_llm)
    if text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    # 1. Load session + card + text from DB
    db_session = await storage.get_session(session_id)
    if not db_session:
        raise HTTPException(404, "Session not found")

    card_id = db_session["card_id"]
    card_rec = await storage.get_card(card_id)
    if not card_rec:
        raise HTTPException(404, "Card not found")

    text_rec = await storage.get_text(card_rec["text_id"])
    if not text_rec:
        raise HTTPException(404, "Text not found")

    # 2. Parse card
    try:
        card = CharacterCard.model_validate_json(card_rec["card_json"])
    except Exception as exc:
        print(f"[history] Parse card {card_id} failed: {exc}")
        raise HTTPException(500, "Card data is corrupted") from exc

    # 3. Build all_characters from sibling cards
    existing_cards = await storage.list_cards(card_rec["text_id"], user_id)
    all_characters: list[dict[str, Any]] = [
        {"name": c["name"], "aliases": []} for c in existing_cards
    ]

    # 4. Fetch embedding config for this user
    emb_key = ""
    emb_region = ""
    try:
        user_cfg = await storage.get_user_api_config(user_id)
        if user_cfg.get("embedding_key"):
            emb_key = user_cfg["embedding_key"]
            emb_region = user_cfg.get("embedding_region", "cn")
    except Exception:
        pass

    # 5. Rebuild RAG + ChatEngine via _create_session (with timeout)
    try:
        rag = text_manager._indexing_service.get_rag_for_session(
            card_rec["text_id"], text_rec["content"], all_characters, emb_key, emb_region
        )
        new_session_id = await asyncio.wait_for(
            asyncio.to_thread(
                text_manager._create_session,
                text_rec["content"],
                card,
                all_characters,
                rag,
                card_rec["id"],
                user_id,
                emb_key,
                emb_region,
            ),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "会话恢复超时，请稍后重试")
    except Exception as exc:
        print(f"[history] Rebuild engine for {session_id} failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    # 6. Steal the engine into the original session_id
    if new_session_id != session_id:
        sessions[session_id] = sessions.pop(new_session_id, {})
    engine = sessions[session_id].get("engine")
    if engine is None:
        raise HTTPException(500, "Engine not found after rebuild")

    # 6. Load history from DB and inject into engine
    try:
        db_messages = await storage.get_messages(session_id)
    except Exception as exc:
        print(f"[history] Load messages for {session_id} failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    # Convert DB roles to engine roles, skipping summary (not a valid LLM role)
    engine.history = [
        {
            "role": "assistant" if m["role"] == "char" else m["role"],
            "content": m["content"],
        }
        for m in db_messages
        if m["role"] in ("user", "char")
    ]
    # Restore last_summary from DB
    for m in reversed(db_messages):
        if m["role"] == "summary":
            engine.last_summary = m["content"]
            break

    # 7. Restore user_role
    if db_session.get("user_role"):
        engine.user_role = db_session["user_role"]

    # 8. Restore affinity from DB — each session has independent scores
    engine._session_id = session_id
    try:
        affinity_data = await storage.get_session_affinity(session_id)
        if affinity_data:
            engine.load_affinity(affinity_data)
    except Exception as exc:
        print(f"[history] Restore affinity failed (non-fatal): {exc}")

    # 10. Return session detail + messages (same shape as GET)
    frontend_messages = [
        {"role": m["role"], "content": m["content"], "id": m["id"], "created_at": m["created_at"],
         "retracted": m.get("retracted", False)}
        for m in db_messages
    ]

    # 11. Rebuild message_ids so revoke works after resume
    sessions[session_id]["message_ids"] = [m["id"] for m in db_messages]

    return {"session": db_session, "messages": frontend_messages}


@router.post("/{session_id}/restore")
async def restore_session(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, bool]:
    """Restore a soft-deleted session from trash."""
    session = await storage.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此会话")
    try:
        ok = await storage.restore_session(session_id)
    except Exception as exc:
        print(f"[history] Restore session failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    if not ok:
        raise HTTPException(404, "Session not found in trash")
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    permanent: bool = Query(False, description="If true, hard-delete permanently"),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, bool]:
    """Soft-delete a session (move to trash), or hard-delete if permanent=true."""
    session = await storage.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此会话")
    try:
        if permanent:
            ok = await storage.hard_delete_session(session_id)
        else:
            ok = await storage.delete_session(session_id)
    except Exception as exc:
        print(f"[history] Delete session failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"ok": True}
