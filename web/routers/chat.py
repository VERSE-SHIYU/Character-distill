"""Chat: send messages, revoke, reset."""

from __future__ import annotations

import asyncio
import json
import random
import traceback
from typing import Any, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from deps import get_sessions, get_storage, get_text_manager, touch_session
from storage.base import StorageBase
from limiter import limiter
from routers.auth import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])
legacy_router = APIRouter(tags=["legacy-chat"])

MAX_MESSAGE_LENGTH = 5000


def _rebuild_history_from_db(db_messages: list[dict]) -> list[dict[str, str]]:
    """Filter DB messages and map roles to engine.history format.

    Only user and char messages are kept (whitelist approach); summary,
    system, and other synthetic roles are excluded.  Retracted messages
    are also excluded — a retracted reply is invisible to both the user
    and the LLM.
    """
    return [
        {"role": "assistant" if m["role"] == "char" else m["role"], "content": m["content"]}
        for m in db_messages
        if m["role"] in ("user", "char") and not m.get("retracted")
    ]


async def _ensure_session(
    session_id: str,
    storage: StorageBase,
    sessions: dict[str, Any],
    user_id: str = "",
) -> dict[str, Any]:
    """Return in-memory session dict, auto-resuming from DB if server restarted."""
    session = sessions.get(session_id)
    if session is not None:
        session.setdefault("lock", asyncio.Lock())
        # SECURITY: verify session ownership even on memory hit
        if session.get("user_id") and session["user_id"] != user_id:
            raise HTTPException(403, "无权访问此会话")
        touch_session(session)
        return session

    # Server restarted — rebuild from DB
    db_session = await storage.get_session(session_id)
    if not db_session:
        raise HTTPException(404, "Session not found")

    from core.schema import CharacterCard
    from deps import get_text_manager as _gtm, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    text_manager = _gtm(llm=per_user_llm)
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
    all_characters = await text_manager._build_all_characters(card_rec["text_id"], existing_cards)

    emb_key = ""
    emb_region = ""
    try:
        user_cfg = await storage.get_user_api_config(user_id)
        if user_cfg.get("embedding_key"):
            emb_key = user_cfg["embedding_key"]
            emb_region = user_cfg.get("embedding_region", "cn")
    except Exception:
        pass

    rag = text_manager._indexing_service.get_rag_for_session(
        card_rec["text_id"], text_rec["content"], all_characters, emb_key, emb_region
    )
    new_id = await asyncio.to_thread(
        text_manager._create_session, text_rec["content"], card, all_characters, rag, card_id, user_id,
    )

    # Steal engine into the original session_id
    if new_id != session_id:
        sessions[session_id] = sessions.pop(new_id, {})
    engine = sessions[session_id].get("engine")
    if engine is None:
        raise HTTPException(500, "Engine not found after rebuild")

    # Reload history from DB (retracted messages filtered by _rebuild_history_from_db)
    db_messages = await storage.get_messages(session_id)
    engine.history = _rebuild_history_from_db(db_messages)
    # Restore last_summary from DB
    for m in reversed(db_messages):
        if m["role"] == "summary":
            engine.last_summary = m["content"]
            break
    if db_session.get("user_role"):
        engine.user_role = db_session["user_role"]
    # Restore affinity from DB
    engine._session_id = session_id
    engine._user_id = user_id
    try:
        affinity_data = await storage.get_session_affinity(session_id)
        if affinity_data:
            engine.load_affinity(affinity_data)
    except Exception as exc:
        print(f"[chat] Restore affinity failed (non-fatal): {exc}")
    sessions[session_id]["message_ids"] = [m["id"] for m in db_messages]
    sessions[session_id].setdefault("lock", asyncio.Lock())

    print(f"[chat] Auto-resumed session {session_id}: history={len(engine.history) if engine else 0} messages")
    touch_session(sessions[session_id])
    return sessions[session_id]


# ---- Request models ----

class ChatRequest(BaseModel):
    """Send a chat message."""
    session_id: str
    message: str
    stream: bool = False
    user_role: str = ""
    hidden: bool = False
    web_search: bool = False
    voice_mode: bool = False
    affinity_enabled: bool = True
    client_tz: str = ""
    reply_to_id: int | None = None
    reply_to_preview: str = ""


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
    storage: StorageBase,
    sessions: dict[str, Any],
    user_role: str = "",
    hidden: bool = False,
    user_id: str = "",
    web_search: bool = False,
    voice_mode: bool = False,
    affinity_enabled: bool = True,
    client_tz: str = "",
    reply_to_id: int | None = None,
    reply_to_preview: str = "",
) -> dict[str, Any]:
    """Core chat logic: call engine, dual-write to storage."""
    session = await _ensure_session(session_id, storage, sessions, user_id)
    touch_session(session)

    msg = message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")

    if user_role:
        session["engine"].user_role = user_role
        # Only persist to DB when the value actually changes
        if session.get("_persisted_user_role") != user_role:
            session["_persisted_user_role"] = user_role
            try:
                db_s = await storage.get_session(session_id)
                if db_s:
                    await storage.save_session(
                        session_id, db_s.get("card_id", ""), user_role, db_s.get("avatar_data", ""), user_id,
                    )
            except Exception as exc:
                print(f"[chat] Save user_role failed (non-fatal): {exc}")
    if client_tz and session.get("engine"):
        session["engine"]._user_tz = client_tz

    try:
        engine = session.get("engine")
        if engine:
            engine._storage = storage
            engine._user_id = user_id
            engine._session_id = session_id
            engine._ctx_engine.web_search_enabled = web_search
            engine.affinity_enabled = affinity_enabled
            engine._main_loop = asyncio.get_running_loop()
        # Prepend quote context for LLM if replying
        llm_msg = f'[引用: "{reply_to_preview}"]\n{msg}' if reply_to_preview else msg
        print(f"[chat] _do_chat session={session_id} history={len(engine.history) if engine else 0} messages")
        async with session["lock"]:
            import time as _t; _t0 = _t.time()
            resp = await asyncio.to_thread(engine.chat, llm_msg, voice_mode=voice_mode)
            print(f"[perf] _do_chat total took {_t.time()-_t0:.2f}s")
            rag_ctx = getattr(engine, '_last_rag_context', '') or ''
    except Exception as exc:
        print(f"[chat] Chat failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    # Determine retraction before persisting
    engine = session.get("engine")
    retracted = False
    if engine and random.random() < 0.2:
        try:
            retracted = await asyncio.to_thread(engine._should_retract, resp)
        except Exception:
            retracted = False

    # If retracted, remove assistant from memory so LLM never sees it
    if retracted and engine and engine.history and engine.history[-1].get("role") == "assistant":
        engine.history.pop()

    # Dual-write to SQLite (non-fatal on failure)
    user_msg_id = None
    char_msg_id = None
    try:
        if not hidden:
            user_rec = await storage.save_message(session_id, "user", msg, "", reply_to_id, reply_to_preview)
            user_msg_id = user_rec["id"]
        char_rec = await storage.save_message(session_id, "char", resp, rag_ctx[:500], retracted=retracted)
        char_msg_id = char_rec["id"]
        ids_to_add = [user_msg_id, char_msg_id]

        # Save summary if newly generated
        engine = session.get("engine")
        if engine and engine.last_summary:
            existing_summaries = [
                m for m in await storage.get_messages(session_id)
                if m["role"] == "summary"
            ]
            last_saved = existing_summaries[-1]["content"] if existing_summaries else ""
            new_summary = f"历史摘要：{engine.last_summary}"
            if new_summary != last_saved:
                try:
                    sum_rec = await storage.save_message(
                        session_id, "summary", new_summary, "",
                    )
                    ids_to_add.append(sum_rec["id"])
                except Exception as exc:
                    print(f"[chat] Save summary failed (non-fatal): {exc}")

        session.setdefault("message_ids", []).extend(ids_to_add)
    except Exception as exc:
        print(f"[chat] Dual-write messages failed (non-fatal): {exc}")

    result: dict[str, Any] = {
        "reply": resp, "retracted": retracted, "rag_context": rag_ctx[:200],
        "user_msg_id": user_msg_id, "char_msg_id": char_msg_id,
        "user_created_at": user_rec.get("created_at", "") if not hidden else "",
        "char_created_at": char_rec.get("created_at", ""),
        "reply_to_id": reply_to_id, "reply_to_preview": reply_to_preview,
    }
    if engine and engine.last_summary:
        result["summary"] = engine.last_summary
    return result


async def _do_chat_stream(
    session_id: str,
    message: str,
    storage: StorageBase,
    sessions: dict[str, Any],
    user_role: str = "",
    hidden: bool = False,
    user_id: str = "",
    web_search: bool = False,
    voice_mode: bool = False,
    affinity_enabled: bool = True,
    client_tz: str = "",
    reply_to_id: int | None = None,
    reply_to_preview: str = "",
):
    """Core streaming chat logic with SSE output."""
    session = await _ensure_session(session_id, storage, sessions, user_id)
    touch_session(session)

    msg = message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")

    if user_role:
        session["engine"].user_role = user_role
        # Only persist to DB when the value actually changes
        if session.get("_persisted_user_role") != user_role:
            session["_persisted_user_role"] = user_role
            try:
                db_s = await storage.get_session(session_id)
                if db_s:
                    await storage.save_session(
                        session_id, db_s.get("card_id", ""), user_role, db_s.get("avatar_data", ""), user_id,
                    )
            except Exception as exc:
                print(f"[chat] Save user_role failed (non-fatal): {exc}")
    if client_tz and session.get("engine"):
        session["engine"]._user_tz = client_tz

    engine = session.get("engine")
    if engine:
        engine._storage = storage
        engine._user_id = user_id
        engine._session_id = session_id
        engine._ctx_engine.web_search_enabled = web_search
        engine.affinity_enabled = affinity_enabled
        engine._main_loop = asyncio.get_running_loop()

    def _next_piece(stream_obj):
        """Read next stream piece with StopIteration sentinel."""
        try:
            return next(stream_obj), False
        except StopIteration:
            return "", True

    async def _event_generator():
        tokens: list[str] = []
        rag_context = ""
        user_msg_id: int | None = None
        char_msg_id: int | None = None
        try:
            if not hidden:
                user_rec = await storage.save_message(session_id, "user", msg, "", reply_to_id, reply_to_preview)
                user_msg_id = user_rec["id"]
        except Exception as exc:
            print(f"[chat] Save user message failed (non-fatal): {exc}")

        try:
            engine = session["engine"]
            engine._storage = storage
            engine._user_id = user_id
            print(f"[chat] _do_chat_stream session={session_id} history={len(engine.history) if engine else 0} messages")
            # Prepend quote context for LLM if replying
            llm_msg = f'[引用: "{reply_to_preview}"]\n{msg}' if reply_to_preview else msg
            async with session["lock"]:
                stream = engine.chat_stream(llm_msg, voice_mode=voice_mode)
                # Drive full stream generation under lock to prevent history interleaving
                first_piece, done = await asyncio.to_thread(_next_piece, stream)
                if not done:
                    tokens.append(first_piece)
                    yield f"data: {json.dumps({'token': first_piece}, ensure_ascii=False, default=str)}\n\n"
                while True:
                    piece, done = await asyncio.to_thread(_next_piece, stream)
                    if done:
                        break
                    tokens.append(piece)
                    yield f"data: {json.dumps({'token': piece}, ensure_ascii=False, default=str)}\n\n"

            full_reply = "".join(tokens)
            if not full_reply.strip():
                print(f"[chat] WARNING: LLM returned empty response (history={len(engine.history) if engine else 0})")
            rag_context = getattr(session["engine"], "_last_rag_context", "") or ""

            # Determine retraction before persisting
            engine = session.get("engine")
            retracted = False
            if engine and random.random() < 0.2:
                try:
                    retracted = await asyncio.to_thread(engine._should_retract, full_reply)
                except Exception:
                    retracted = False

            # If retracted, remove assistant from memory so LLM never sees it
            if retracted and engine and engine.history and engine.history[-1].get("role") == "assistant":
                engine.history.pop()

            try:
                char_rec = await storage.save_message(session_id, "char", full_reply, rag_context[:500], retracted=retracted)
                char_msg_id = char_rec["id"]
            except Exception as exc:
                print(f"[chat] Save assistant message failed (non-fatal): {exc}")

            msg_ids = [uid for uid in (user_msg_id, char_msg_id) if uid is not None]
            if msg_ids:
                session.setdefault("message_ids", []).extend(msg_ids)

            if engine and engine.last_summary:
                existing_summaries = [
                    m for m in await storage.get_messages(session_id)
                    if m["role"] == "summary"
                ]
                last_saved = existing_summaries[-1]["content"] if existing_summaries else ""
                new_summary = f"历史摘要：{engine.last_summary}"
                if new_summary != last_saved:
                    try:
                        sum_rec = await storage.save_message(
                            session_id, "summary", new_summary, "",
                        )
                        session.setdefault("message_ids", []).append(sum_rec["id"])
                    except Exception as exc:
                        print(f"[chat] Save summary failed (non-fatal): {exc}")

            done_payload: dict[str, Any] = {
                "done": True, "retracted": retracted, "rag_context": rag_context[:200],
                "user_msg_id": user_msg_id,
                "char_msg_id": char_msg_id,
                "user_created_at": user_rec.get("created_at", "") if not hidden else "",
                "char_created_at": char_rec.get("created_at", ""),
                "reply_to_id": reply_to_id, "reply_to_preview": reply_to_preview,
            }
            if engine and engine.last_summary:
                done_payload["summary"] = engine.last_summary
            yield f"data: {json.dumps(done_payload, ensure_ascii=False, default=str)}\n\n"

            # ── Post-done housekeeping (does NOT block UI unlock) ──
            try:
                engine = session.get("engine")
                if engine:
                    if full_reply.strip():
                        await asyncio.to_thread(engine.post_stream_process, llm_msg, full_reply)
            except Exception as hk_exc:
                print(f"[chat] Post-stream housekeeping failed (non-fatal): {hk_exc}")

        except Exception as exc:
            print(f"[chat] Chat stream failed: {exc}")
            print(f"[chat] Traceback:\n{traceback.format_exc()}")
            # Only roll back when NOTHING was produced — if any token streamed out,
            # the user already saw partial content; keep their message + partial reply.
            if user_msg_id is not None and not tokens:
                try:
                    await storage.delete_messages_after(session_id, user_msg_id)
                except Exception as rollback_exc:
                    print(f"[chat] Rollback user message failed (non-fatal): {rollback_exc}")
            # Sync engine.history: pop phantom user message if chat_stream's own
            # except block didn't clean up (e.g. exception after generator exit)
            engine = session.get("engine")
            if engine and engine.history and engine.history[-1].get("role") == "user":
                engine.history.pop()
            err_payload = {"error": str(exc)}
            yield f"data: {json.dumps(err_payload, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


async def _do_reset(
    session_id: str,
    storage: StorageBase,
    sessions: dict[str, Any],
    user_id: str = "",
) -> dict[str, bool]:
    """Core reset logic: clear in-memory history."""
    session = await _ensure_session(session_id, storage, sessions, user_id)
    session["engine"].reset()
    return {"ok": True}


# ---- New routes ----

@router.post("/send", response_model=None)
@limiter.limit("30/minute")
async def send_message(
    req: ChatRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> Union[dict[str, Any], StreamingResponse]:
    """Send a message and get a JSON reply or SSE stream."""
    from deps import get_user_llm
    user_id = user["id"]
    if len(req.message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(400, f"消息过长，最多{MAX_MESSAGE_LENGTH}字")
    if await get_user_llm(user_id, storage) is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    if req.stream:
        return await _do_chat_stream(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id, req.web_search, req.voice_mode, req.affinity_enabled, req.client_tz, req.reply_to_id, req.reply_to_preview)
    return await _do_chat(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id, req.web_search, req.voice_mode, req.affinity_enabled, req.client_tz, req.reply_to_id, req.reply_to_preview)


@router.post("/revoke")
async def revoke_messages(
    req: RevokeRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any]:
    """Delete messages starting from the given DB message id.

    ``req.message_id`` is a real SQLite message row id (NOT a positional index).
    After DB deletion, rebuild ``engine.history`` from remaining messages to keep
    the in-memory context and ``message_ids`` tracking precisely in sync.
    """
    user_id = user["id"]
    session = await _ensure_session(req.session_id, storage, sessions, user_id)

    # Delete from SQLite first
    try:
        count = await storage.delete_messages_after(req.session_id, req.message_id)
    except Exception as exc:
        print(f"[chat] Revoke messages failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    # Rebuild in-memory engine.history and message_ids from remaining DB rows
    try:
        messages = await storage.get_messages(req.session_id)
        engine = session.get("engine")
        if engine:
            engine.history = _rebuild_history_from_db(messages)
        session["message_ids"] = [m["id"] for m in messages]
    except Exception as exc:
        print(f"[chat] Rebuild history after revoke failed (non-fatal): {exc}")

    return {"deleted": count}

@router.get("/affinity/{session_id}")
async def get_affinity(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any]:
    """Return affinity scores for a session (incl. inner_voice, mood_emoji, stage)."""
    # Always verify ownership via DB first
    db_session = await storage.get_session(session_id)
    if not db_session:
        raise HTTPException(404, "Session not found")
    if db_session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此会话")

    session = sessions.get(session_id)
    if session and session.get("engine"):
        return session["engine"].get_affinity()

    # Fallback to DB (server restarted)
    data = await storage.get_session_affinity(session_id)
    if data:
        aff = data.get("affinity", 50)
        reason = data.get("reason", "")
        # Try to parse extended JSON from affinity_reason
        extended = {}
        if reason:
            try:
                extended = json.loads(reason)
            except (json.JSONDecodeError, TypeError):
                extended = {"inner_voice": reason, "mood_emoji": "😊"}
        # Calculate stage
        from core.chat_engine import calc_stage
        stage_name, stage_emoji = calc_stage(aff)
        return {
            "affinity": aff,
            "trust": data.get("trust", 30),
            "mood": data.get("mood", "平静"),
            "guard": data.get("guard", 70),
            "reason": reason,
            "inner_voice": extended.get("inner_voice", ""),
            "mood_emoji": extended.get("mood_emoji", "😊"),
            "stage": stage_name,
            "stage_emoji": stage_emoji,
        }
    return {"affinity": 50, "trust": 30, "mood": "平静", "guard": 70, "reason": "", "inner_voice": "", "mood_emoji": "😊", "stage": "陌生", "stage_emoji": "🫥"}


@router.post("/reset")
async def reset_session(
    req: ResetRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, bool]:
    """Reset the in-memory chat history (keep the character card)."""
    user_id = user["id"]
    return await _do_reset(req.session_id, storage, sessions, user_id)


class ReactRequest(BaseModel):
    emoji: str


@router.post("/message/{message_id}/react")
async def react_to_message(
    message_id: int,
    req: ReactRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, bool]:
    """Toggle a reaction on a chat message."""
    if not req.emoji.strip():
        raise HTTPException(400, "Emoji cannot be empty")
    added = await storage.toggle_reaction(message_id, user["id"], req.emoji)
    return {"added": added}


@router.get("/session/{session_id}/reactions")
async def get_session_reactions(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict:
    """Return all reactions for messages in a session."""
    # Verify ownership
    db_session = await storage.get_session(session_id)
    if not db_session:
        raise HTTPException(404, "Session not found")
    if db_session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此会话")

    messages = await storage.get_messages(session_id)
    msg_ids = [m["id"] for m in messages if m.get("id")]
    reactions = await storage.get_reactions(msg_ids)
    return {"reactions": reactions}


# ---- Legacy compat routes (/api/chat, /api/reset) ----

@legacy_router.post("/api/chat")
async def legacy_chat(
    req: ChatRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any]:
    """Legacy /api/chat -> same as /api/chat/send."""
    user_id = user["id"]
    return await _do_chat(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id, req.web_search, affinity_enabled=req.affinity_enabled)


@legacy_router.post("/api/reset")
async def legacy_reset(
    req: ResetRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, bool]:
    """Legacy /api/reset -> same as /api/chat/reset."""
    user_id = user["id"]
    return await _do_reset(req.session_id, storage, sessions, user_id)
