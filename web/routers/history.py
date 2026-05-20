"""History: list sessions, get conversation, delete, export."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from deps import get_sessions, get_storage, get_text_manager
from core.schema import CharacterCard
from core.text_manager import TextManager
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/history", tags=["history"])


class ResumeRequest(BaseModel):
    """Empty body placeholder — session_id comes from the URL path."""
    pass


@router.get("/list")
async def list_sessions(
    keyword: str = Query("", description="Search keyword in messages"),
    character: str = Query("", description="Filter by character name"),
    text_id: str = Query("", description="Filter by text_id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Paginated session list with optional keyword and character filters."""
    try:
        return await storage.list_sessions(keyword, character, text_id, page, page_size)
    except Exception as exc:
        print(f"[history] List sessions failed: {exc}")
        raise HTTPException(500, f"List sessions failed: {exc}") from exc


@router.get("/{session_id}/export")
async def export_session(
    session_id: str,
    format: str = Query("json", description="Export format: json or txt"),
    storage: SQLiteStore = Depends(get_storage),
) -> Response:
    """Export a session as json or txt."""
    try:
        content = await storage.export_session(session_id, format)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[history] Export session failed: {exc}")
        raise HTTPException(500, f"Export failed: {exc}") from exc

    if format.lower().strip() == "txt":
        return PlainTextResponse(content)
    return Response(content=content, media_type="application/json")


@router.get("/{session_id}")
async def get_session_detail(
    session_id: str,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Get a session with its full message list."""
    session = await storage.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    try:
        messages = await storage.get_messages(session_id)
    except Exception as exc:
        print(f"[history] Get messages failed: {exc}")
        raise HTTPException(500, f"Get messages failed: {exc}") from exc
    return {"session": session, "messages": messages}


@router.post("/{session_id}/resume")
async def resume_session(
    session_id: str,
    _body: ResumeRequest,
    storage: SQLiteStore = Depends(get_storage),
    text_manager: TextManager = Depends(get_text_manager),
    sessions: dict[str, dict[str, Any]] = Depends(get_sessions),
) -> dict[str, Any]:
    """Rebuild the in-memory ChatEngine for a persisted session.

    Used when the server has restarted (``_sessions`` dict is empty) or
    the session was created in a different process.  Reconstructs the
    RAG index, ChatEngine, and replays history messages so the user can
    pick up the conversation where it left off.
    """
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
    existing_cards = await storage.list_cards(card_rec["text_id"])
    all_characters: list[dict[str, Any]] = [
        {"name": c["name"], "aliases": []} for c in existing_cards
    ]

    # 4. Rebuild RAG + ChatEngine via _create_session (with timeout)
    try:
        rag = text_manager._get_or_build_rag(
            card_rec["text_id"], text_rec["content"], all_characters
        )
        new_session_id = await asyncio.wait_for(
            asyncio.to_thread(
                text_manager._create_session,
                text_rec["content"],
                card,
                all_characters,
                rag,
                card_rec["id"],
            ),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "会话恢复超时，请稍后重试")
    except Exception as exc:
        print(f"[history] Rebuild engine for {session_id} failed: {exc}")
        raise HTTPException(500, f"Rebuild session failed: {exc}") from exc

    # 5. Steal the engine into the original session_id
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
        raise HTTPException(500, f"Load messages failed: {exc}") from exc

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

    # 8. Return session detail + messages (same shape as GET)
    frontend_messages = [
        {"role": m["role"], "content": m["content"], "id": m["id"]}
        for m in db_messages
    ]

    # 9. Rebuild message_ids so revoke works after resume
    sessions[session_id]["message_ids"] = [m["id"] for m in db_messages]

    return {"session": db_session, "messages": frontend_messages}


@router.post("/clear-all")
async def clear_all_sessions(
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Delete all sessions and messages."""
    try:
        count = await storage.clear_all_sessions()
        return {"ok": True, "deleted": count}
    except Exception as exc:
        print(f"[history] Clear all sessions failed: {exc}")
        raise HTTPException(500, f"Clear all sessions failed: {exc}") from exc


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, bool]:
    """Delete a session and all its messages."""
    try:
        ok = await storage.delete_session(session_id)
    except Exception as exc:
        print(f"[history] Delete session failed: {exc}")
        raise HTTPException(500, f"Delete session failed: {exc}") from exc
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"ok": True}
