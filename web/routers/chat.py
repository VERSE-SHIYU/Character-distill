"""Chat: send messages, revoke, reset."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from typing import Any, Union

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
    user_role: str = ""


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
    user_role: str = "",
) -> dict[str, Any]:
    """Core chat logic: call engine, dual-write to storage."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found, please distill a character first")

    msg = message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")

    if user_role:
        session["engine"].user_role = user_role

    try:
        resp, rag_ctx = await asyncio.to_thread(session["engine"].chat, msg)
    except Exception as exc:
        print(f"[chat] Chat failed: {exc}")
        raise HTTPException(500, f"Chat failed: {exc}") from exc

    # Dual-write to SQLite (non-fatal on failure)
    user_msg_id = None
    char_msg_id = None
    try:
        user_rec = await storage.save_message(session_id, "user", msg, "")
        char_rec = await storage.save_message(session_id, "char", resp, rag_ctx[:500])
        user_msg_id = user_rec["id"]
        char_msg_id = char_rec["id"]
        ids_to_add = [user_msg_id, char_msg_id]

        # Save summary if newly generated
        engine = session.get("engine")
        if engine and engine.last_summary:
            try:
                sum_rec = await storage.save_message(
                    session_id, "summary",
                    f"历史摘要：{engine.last_summary}", "",
                )
                ids_to_add.append(sum_rec["id"])
            except Exception as exc:
                print(f"[chat] Save summary failed (non-fatal): {exc}")

        session.setdefault("message_ids", []).extend(ids_to_add)
    except Exception as exc:
        print(f"[chat] Dual-write messages failed (non-fatal): {exc}")

    engine = session.get("engine")
    result: dict[str, Any] = {
        "reply": resp, "rag_context": rag_ctx[:200],
        "user_msg_id": user_msg_id, "char_msg_id": char_msg_id,
    }
    if engine and engine.last_summary:
        result["summary"] = engine.last_summary
    return result


async def _do_chat_stream(
    session_id: str,
    message: str,
    storage: SQLiteStore,
    sessions: dict[str, Any],
    user_role: str = "",
):
    """Core streaming chat logic with SSE output."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found, please distill a character first")

    msg = message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")

    if user_role:
        session["engine"].user_role = user_role

    def _next_piece(stream_obj):
        """Read next stream piece with StopIteration sentinel."""
        try:
            return next(stream_obj), False
        except StopIteration:
            return "", True

    async def _event_generator():
        tokens: list[str] = []
        rag_context = ""
        msg_ids: list[int] = []
        try:
            user_rec = await storage.save_message(session_id, "user", msg, "")
            msg_ids.append(user_rec["id"])
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
            rag_context = getattr(session["engine"], "_last_rag_context", "") or ""

            try:
                char_rec = await storage.save_message(session_id, "char", full_reply, rag_context[:500])
                msg_ids.append(char_rec["id"])
            except Exception as exc:
                print(f"[chat] Save assistant message failed (non-fatal): {exc}")

            if msg_ids:
                session.setdefault("message_ids", []).extend(msg_ids)

            engine = session.get("engine")
            if engine and engine.last_summary:
                try:
                    sum_rec = await storage.save_message(
                        session_id, "summary",
                        f"历史摘要：{engine.last_summary}", "",
                    )
                    session.setdefault("message_ids", []).append(sum_rec["id"])
                except Exception as exc:
                    print(f"[chat] Save summary failed (non-fatal): {exc}")

            done_payload: dict[str, Any] = {
                "done": True, "rag_context": rag_context[:200],
                "user_msg_id": msg_ids[0] if len(msg_ids) >= 1 else None,
                "char_msg_id": msg_ids[1] if len(msg_ids) >= 2 else None,
            }
            if engine and engine.last_summary:
                done_payload["summary"] = engine.last_summary
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

@router.post("/send", response_model=None)
async def send_message(
    req: ChatRequest,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> Union[dict[str, Any], StreamingResponse]:
    """Send a message and get a JSON reply or SSE stream."""
    if not sessions:
        raise HTTPException(503, "请先在设置页配置 API Key")
    if req.stream:
        return await _do_chat_stream(req.session_id, req.message, storage, sessions, req.user_role)
    return await _do_chat(req.session_id, req.message, storage, sessions, req.user_role)


@router.post("/revoke")
async def revoke_messages(
    req: RevokeRequest,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any]:
    """Delete messages starting from the given DB message id.

    ``req.message_id`` is a real SQLite message row id (NOT a positional index).
    After DB deletion, rebuild ``engine.history`` from remaining messages to keep
    the in-memory context and ``message_ids`` tracking precisely in sync.
    """
    session = sessions.get(req.session_id)

    # Delete from SQLite first
    try:
        count = await storage.delete_messages_after(req.session_id, req.message_id)
    except Exception as exc:
        print(f"[chat] Revoke messages failed: {exc}")
        raise HTTPException(500, f"Revoke failed: {exc}") from exc

    # Rebuild in-memory engine.history and message_ids from remaining DB rows
    if session:
        try:
            messages = await storage.get_messages(req.session_id)
            engine = session.get("engine")
            if engine:
                engine.history = [
                    {
                        "role": "assistant" if m["role"] == "char" else m["role"],
                        "content": m["content"],
                    }
                    for m in messages if m.get("role") != "summary"
                ]
            session["message_ids"] = [m["id"] for m in messages]
        except Exception as exc:
            print(f"[chat] Rebuild history after revoke failed (non-fatal): {exc}")

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
    return await _do_chat(req.session_id, req.message, storage, sessions, req.user_role)


@legacy_router.post("/api/reset")
async def legacy_reset(
    req: ResetRequest,
    sessions: dict = Depends(get_sessions),
) -> dict[str, bool]:
    """Legacy /api/reset -> same as /api/chat/reset."""
    return await _do_reset(req.session_id, sessions)
