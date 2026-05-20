"""Chat: send messages, revoke, reset."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from typing import Any, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from deps import get_sessions, get_storage, get_text_manager
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/chat", tags=["chat"])
legacy_router = APIRouter(tags=["legacy-chat"])


async def _ensure_session(
    session_id: str,
    storage: SQLiteStore,
    sessions: dict[str, Any],
    user_id: str = "",
) -> dict[str, Any]:
    """Return in-memory session dict, auto-resuming from DB if server restarted."""
    session = sessions.get(session_id)
    if session is not None:
        return session

    # Server restarted — rebuild from DB
    db_session = await storage.get_session(session_id)
    if not db_session:
        raise HTTPException(404, "Session not found")

    from core.schema import CharacterCard
    from deps import get_text_manager as _gtm
    text_manager = _gtm()
    if text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    card_id = db_session["card_id"]
    card_rec = await storage.get_card(card_id)
    if not card_rec:
        raise HTTPException(404, "Card not found")
    text_rec = await storage.get_text(card_rec["text_id"])
    if not text_rec:
        raise HTTPException(404, "Text not found")

    try:
        card = CharacterCard.model_validate_json(card_rec["card_json"])
    except Exception as exc:
        raise HTTPException(500, "Card data is corrupted") from exc

    existing_cards = await storage.list_cards(card_rec["text_id"], user_id)
    all_characters = [{"name": c["name"], "aliases": []} for c in existing_cards]

    rag = text_manager._get_or_build_rag(card_rec["text_id"], text_rec["content"], all_characters)
    new_id = await asyncio.to_thread(
        text_manager._create_session, text_rec["content"], card, all_characters, rag, card_id
    )

    # Steal engine into the original session_id
    if new_id != session_id:
        sessions[session_id] = sessions.pop(new_id, {})
    engine = sessions[session_id].get("engine")
    if engine is None:
        raise HTTPException(500, "Engine not found after rebuild")

    # Reload history (skip summary — it's not a valid LLM role)
    db_messages = await storage.get_messages(session_id)
    engine.history = [
        {"role": "assistant" if m["role"] == "char" else m["role"], "content": m["content"]}
        for m in db_messages
        if m["role"] in ("user", "char")
    ]
    # Restore last_summary from DB
    for m in reversed(db_messages):
        if m["role"] == "summary":
            engine.last_summary = m["content"]
            break
    if db_session.get("user_role"):
        engine.user_role = db_session["user_role"]
    sessions[session_id]["message_ids"] = [m["id"] for m in db_messages]

    print(f"[chat] Auto-resumed session {session_id}: history={len(engine.history) if engine else 0} messages")
    return sessions[session_id]


# ---- Request models ----

class ChatRequest(BaseModel):
    """Send a chat message."""
    session_id: str
    message: str
    stream: bool = False
    user_role: str = ""
    hidden: bool = False  # inject into LLM context without saving user msg (for revoke notice)


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
    hidden: bool = False,
    user_id: str = "",
) -> dict[str, Any]:
    """Core chat logic: call engine, dual-write to storage."""
    session = await _ensure_session(session_id, storage, sessions, user_id)

    msg = message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")

    if user_role:
        session["engine"].user_role = user_role

    try:
        engine = session.get("engine")
        if engine:
            engine._storage = storage
            engine._user_id = user_id
        print(f"[chat] _do_chat: history={len(engine.history) if engine else 0} messages")
        resp, rag_ctx = await asyncio.to_thread(engine.chat, msg)
    except Exception as exc:
        print(f"[chat] Chat failed: {exc}")
        raise HTTPException(500, f"Chat failed: {exc}") from exc

    # Dual-write to SQLite (non-fatal on failure)
    user_msg_id = None
    char_msg_id = None
    try:
        if not hidden:
            user_rec = await storage.save_message(session_id, "user", msg, "")
            user_msg_id = user_rec["id"]
        char_rec = await storage.save_message(session_id, "char", resp, rag_ctx[:500])
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
    hidden: bool = False,
    user_id: str = "",
):
    """Core streaming chat logic with SSE output."""
    session = await _ensure_session(session_id, storage, sessions, user_id)

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
            if not hidden:
                user_rec = await storage.save_message(session_id, "user", msg, "")
                msg_ids.append(user_rec["id"])
        except Exception as exc:
            print(f"[chat] Save user message failed (non-fatal): {exc}")

        try:
            engine = session["engine"]
            engine._storage = storage
            engine._user_id = user_id
            print(f"[chat] _do_chat_stream: history={len(engine.history) if engine else 0} messages")
            stream = engine.chat_stream(msg)
            while True:
                piece, done = await asyncio.to_thread(_next_piece, stream)
                if done:
                    break
                tokens.append(piece)
                yield f"data: {json.dumps({'token': piece}, ensure_ascii=False)}\n\n"

            full_reply = "".join(tokens)
            if not full_reply.strip():
                print(f"[chat] WARNING: LLM returned empty response (history={len(engine.history) if engine else 0}, sp_len={sp_len})")
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
    storage: SQLiteStore,
    sessions: dict[str, Any],
    user_id: str = "",
) -> dict[str, bool]:
    """Core reset logic: clear in-memory history."""
    session = await _ensure_session(session_id, storage, sessions, user_id)
    session["engine"].reset()
    return {"ok": True}


# ---- New routes ----

@router.post("/send", response_model=None)
async def send_message(
    req: ChatRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> Union[dict[str, Any], StreamingResponse]:
    """Send a message and get a JSON reply or SSE stream."""
    from deps import get_llm
    if get_llm() is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    user_id = request.state.user.get("id", "")
    if req.stream:
        return await _do_chat_stream(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id)
    return await _do_chat(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id)


@router.post("/revoke")
async def revoke_messages(
    req: RevokeRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any]:
    """Delete messages starting from the given DB message id.

    ``req.message_id`` is a real SQLite message row id (NOT a positional index).
    After DB deletion, rebuild ``engine.history`` from remaining messages to keep
    the in-memory context and ``message_ids`` tracking precisely in sync.
    """
    user_id = request.state.user.get("id", "")
    session = await _ensure_session(req.session_id, storage, sessions, user_id)

    # Delete from SQLite first
    try:
        count = await storage.delete_messages_after(req.session_id, req.message_id)
    except Exception as exc:
        print(f"[chat] Revoke messages failed: {exc}")
        raise HTTPException(500, f"Revoke failed: {exc}") from exc

    # Rebuild in-memory engine.history and message_ids from remaining DB rows
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
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, bool]:
    """Reset the in-memory chat history (keep the character card)."""
    user_id = request.state.user.get("id", "")
    return await _do_reset(req.session_id, storage, sessions, user_id)


# ---- Legacy compat routes (/api/chat, /api/reset) ----

@legacy_router.post("/api/chat")
async def legacy_chat(
    req: ChatRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any]:
    """Legacy /api/chat -> same as /api/chat/send."""
    user_id = request.state.user.get("id", "")
    return await _do_chat(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id)


@legacy_router.post("/api/reset")
async def legacy_reset(
    req: ResetRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, bool]:
    """Legacy /api/reset -> same as /api/chat/reset."""
    user_id = request.state.user.get("id", "")
    return await _do_reset(req.session_id, storage, sessions, user_id)
