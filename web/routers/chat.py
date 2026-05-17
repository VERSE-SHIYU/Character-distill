"""Chat: send messages, revoke, reset."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from deps import get_sessions, get_storage
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/chat", tags=["chat"])
legacy_router = APIRouter(tags=["legacy-chat"])


# ---- Request models ----

class ChatRequest(BaseModel):
    """Send a chat message."""
    session_id: str
    message: str
    stream: bool = False


class RevokeRequest(BaseModel):
    """Revoke messages after a given message_id."""
    session_id: str
    message_id: int


class ResetRequest(BaseModel):
    """Reset in-memory chat history."""
    session_id: str


# ---- Shared helpers ----

async def _do_chat(
    session_id: str,
    message: str,
    storage: SQLiteStore,
    sessions: dict[str, Any],
) -> dict[str, Any]:
    """Core chat logic: call engine, dual-write to storage."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found, please distill a character first")

    msg = message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")

    try:
        resp, rag_ctx = await asyncio.to_thread(session["engine"].chat, msg)
    except Exception as exc:
        print(f"[chat] Chat failed: {exc}")
        raise HTTPException(500, f"Chat failed: {exc}") from exc

    # Dual-write to SQLite (non-fatal on failure)
    try:
        await storage.save_message(session_id, "user", msg, "")
        await storage.save_message(session_id, "char", resp, rag_ctx[:500])
    except Exception as exc:
        print(f"[chat] Dual-write messages failed (non-fatal): {exc}")

    return {"reply": resp, "rag_context": rag_ctx[:200]}


async def _do_chat_stream(
    session_id: str,
    message: str,
    storage: SQLiteStore,
    sessions: dict[str, Any],
):
    """Core streaming chat logic with SSE output."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found, please distill a character first")

    msg = message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")

    def _next_piece(stream_obj):
        """Read next stream piece with StopIteration sentinel."""
        try:
            return next(stream_obj), False
        except StopIteration:
            return "", True

    async def _event_generator():
        tokens: list[str] = []
        rag_context = ""
        try:
            await storage.save_message(session_id, "user", msg, "")
        except Exception as exc:
            print(f"[chat] Save user message failed (non-fatal): {exc}")

        try:
            stream = session["engine"].chat_stream(msg)
            while True:
                piece, done = await asyncio.to_thread(_next_piece, stream)
                if done:
                    break
                tokens.append(piece)
                yield f"data: {json.dumps({'token': piece}, ensure_ascii=False)}\n\n"

            full_reply = "".join(tokens)
            try:
                rag_context = "\n".join(session["engine"].rag.query(msg))
            except Exception as exc:
                print(f"[chat] Query rag context after stream failed (non-fatal): {exc}")
                rag_context = ""

            try:
                await storage.save_message(session_id, "char", full_reply, rag_context[:500])
            except Exception as exc:
                print(f"[chat] Save assistant message failed (non-fatal): {exc}")

            done_payload = {"done": True, "rag_context": rag_context[:200]}
            yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            print(f"[chat] Chat stream failed: {exc}")
            err_payload = {"error": str(exc)}
            yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


async def _do_reset(
    session_id: str,
    sessions: dict[str, Any],
) -> dict[str, bool]:
    """Core reset logic: clear in-memory history."""
    session = sessions.get(session_id)
    if session:
        session["engine"].reset()
    return {"ok": True}


# ---- New routes ----

@router.post("/send")
async def send_message(
    req: ChatRequest,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> Any:
    """Send a message and get a JSON reply or SSE stream."""
    if req.stream:
        return await _do_chat_stream(req.session_id, req.message, storage, sessions)
    return await _do_chat(req.session_id, req.message, storage, sessions)


@router.post("/revoke")
async def revoke_messages(
    req: RevokeRequest,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Delete messages after (and including) a given message_id."""
    try:
        count = await storage.delete_messages_after(req.session_id, req.message_id)
    except Exception as exc:
        print(f"[chat] Revoke messages failed: {exc}")
        raise HTTPException(500, f"Revoke failed: {exc}") from exc
    return {"deleted": count}


@router.post("/reset")
async def reset_session(
    req: ResetRequest,
    sessions: dict = Depends(get_sessions),
) -> dict[str, bool]:
    """Reset the in-memory chat history (keep the character card)."""
    return await _do_reset(req.session_id, sessions)


# ---- Legacy compat routes (/api/chat, /api/reset) ----

@legacy_router.post("/api/chat")
async def legacy_chat(
    req: ChatRequest,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any]:
    """Legacy /api/chat -> same as /api/chat/send."""
    return await _do_chat(req.session_id, req.message, storage, sessions)


@legacy_router.post("/api/reset")
async def legacy_reset(
    req: ResetRequest,
    sessions: dict = Depends(get_sessions),
) -> dict[str, bool]:
    """Legacy /api/reset -> same as /api/chat/reset."""
    return await _do_reset(req.session_id, sessions)
