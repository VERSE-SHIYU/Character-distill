"""History: list sessions, get conversation, delete, export."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from deps import get_storage
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/list")
async def list_sessions(
    keyword: str = Query("", description="Search keyword in messages"),
    character: str = Query("", description="Filter by character name"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Paginated session list with optional keyword and character filters."""
    try:
        return await storage.list_sessions(keyword, character, page, page_size)
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
