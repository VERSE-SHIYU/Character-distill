"""History: list sessions, get conversation, delete, export, trash."""

from __future__ import annotations

import logging

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from core.trash_service import hard_delete, restore, soft_delete
from core.affinity_service import read_persisted_affinity
from deps import get_sessions, get_storage
from core.schema import CharacterCard, parse_evidence
from core.clock import UserClock
from core.message_outbox import SaveState, save_field
from core.nonfatal import nonfatal
from storage.base import StorageBase
from routers.auth import get_current_user
from web.llm_resolution import resolve_embedding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])


# In-memory daily visit counter: session_id -> (date_str, count)
# Separate from _reunion_dates (reunion frequency gate in chat_engine).
_daily_visits: dict[str, tuple[str, int]] = {}


class ResumeRequest(BaseModel):
    """Resume a session after server restart."""
    voice_mode: bool = False


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
        session = await storage.get_session_owned(session_id, user["id"])
    except Exception:
        raise HTTPException(404, "Session not found")
    if not session:
        raise HTTPException(404, "Session not found")
    try:
        content = await storage.export_session(session_id, format)
    except ValueError as exc:
        # **不迁到统一出口**：判据是「这段文字是为谁写的」—— 这条 `raise` 的实参是本仓撰写的
        # 用户输入校验文案（`storage/*_store.py::export_session` 的
        # "format only supports json or txt" / "session not found"）。它没有 `user_message`，
        # 走 user_facing_error 会删掉用户需要的信息。
        # 契约锁：tests/test_security_authz.py::TestErrorSanitization::test_10_value_error_400。
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
    session = await storage.get_session_owned(session_id, user["id"])
    if not session:
        raise HTTPException(404, "Session not found")
    try:
        messages = await storage.get_messages(session_id)
    except Exception as exc:
        print(f"[history] Get messages failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    # 落库的 evidence 是快照 JSON 文本，在此解成结构 —— 这是前端「刷新后仍能看到检索来源」
    # 的唯一解码出口。老消息该列是 NULL → None（= 没有关联证据），不造 []。
    for m in messages:
        m["evidence"] = parse_evidence(m.get("evidence"))
    return {"session": session, "messages": messages}


class UpdateAvatarRequest(BaseModel):
    avatar_data: str


@router.put("/{session_id}/avatar")
async def update_session_avatar(
    session_id: str,
    body: UpdateAvatarRequest,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Update session-level user avatar (per-conversation, not global)."""
    if len(body.avatar_data) > 150_000:
        raise HTTPException(400, "头像数据过大")
    ok = await storage.update_session_avatar(session_id, user["id"], body.avatar_data)
    if not ok:
        raise HTTPException(404, "会话不存在或无权修改")
    return {"ok": True}


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
    db_session = await storage.get_session_owned(session_id, user_id)
    if not db_session:
        raise HTTPException(404, "Session not found")

    card_id = db_session["card_id"]
    card_rec = await storage.get_card_owned(card_id, user_id)
    if not card_rec:
        raise HTTPException(404, "Card not found")

    text_rec = await storage.get_text_owned(card_rec["text_id"], user_id)
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
    try:
        user_cfg = await storage.get_user_api_config(user_id) or {}
    except Exception:
        user_cfg = {}
    emb = resolve_embedding(user_cfg)

    # 5. Rebuild RAG + ChatEngine via _create_session (with timeout)
    try:
        rag = text_manager._indexing_service.get_rag_for_session(
            card_rec["text_id"], text_rec["content"],
            all_characters=all_characters, embedding_key=emb.key, embedding_region=emb.region,
        )
        # 原会话 id 直接进构造：引擎一出生就在原 id 名下，不再「新 id 造好再搬过来」。
        await asyncio.wait_for(
            asyncio.to_thread(
                text_manager._create_session,
                card,
                all_characters=all_characters,
                rag=rag,
                card_id=card_rec["id"],
                user_id=user_id,
                session_id=session_id,
            ),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "会话恢复超时，请稍后重试")
    except Exception as exc:
        print(f"[history] Rebuild engine for {session_id} failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    engine = sessions.get(session_id, {}).get("engine")
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
    # 重建会话时把角色从库里恢复回来。这句也是缺陷 55（凭据落进 `user_role`）射程的边界：
    # 进程内的旧会话一没，脏角色值就只能经这里重新流回引擎、再随 prompt 进模型。
    if db_session.get("user_role"):
        engine.user_role = db_session["user_role"]

    # 8. Restore affinity from DB — each session has independent scores
    try:
        data, source = await read_persisted_affinity(session_id, storage)
        if source == 'state':
            engine.load_affinity(data, initialized=True)
        else:
            # legacy（已评估旧格式）或全新默认行 → load_affinity 自行判定升级/初值计算
            engine.load_affinity(data)
    except Exception as exc:
        logger.warning("Restore affinity failed (non-fatal): %s", exc, exc_info=True)

    # 9. Generate reunion greeting (before any save_message — updated_at must not be polluted)
    # ── 今日到访计数 ──
    _daily_visit_today = UserClock.now().strftime("%Y-%m-%d")
    _prev_date, _prev_count = _daily_visits.get(session_id, ("", 0))
    _visit_count = _prev_count + 1 if _prev_date == _daily_visit_today else 1
    _daily_visits[session_id] = (_daily_visit_today, _visit_count)

    greeting = ""
    greeting_save: SaveState | None = None
    greeting_id: int | None = None
    greeting_created_at = ""
    async with nonfatal("history", "reunion greeting"):
        greeting = await asyncio.to_thread(
            engine.generate_reunion_greeting, None, _body.voice_mode,
        )
    if greeting:
        # 写完直接返回 `messages` 尾部，和**别处**的写入走同一条路：写失败就留在队里，
        # 下次写 / 重试 / 关停时补上，前端按 key 认领。所以「问候已生成」和「问候已落库」
        # 是两件事 —— 上面那个 `greeting` 才是「生成过」，后面的 id/时间戳可能还没有。
        outbox = sessions[session_id]["outbox"]
        greeting_rows: list[dict] = []

        async def _save_greeting(key: str) -> int:
            rec = await storage.save_message(session_id, "char", greeting, "", client_key=key)
            greeting_rows.append(rec)
            return rec["id"]

        greeting_save, _greeting_report = await outbox.write(_save_greeting, ping=storage.ping)
        # 记忆无条件追加（同 start_session 的开场白）：写失败只是「没落库」，不是「没说过」。
        # 绑在 `if greeting_rows:` 上，库一抖动这句问候就只出现在返回的 `messages` 里、
        # 不在记忆里 —— 下一轮 LLM 不知道刚打过招呼，会再打一次。
        engine.history.append({"role": "assistant", "content": greeting})
        if greeting_rows:
            rec = greeting_rows[0]
            greeting_id = rec["id"]
            greeting_created_at = rec["created_at"]

    # ── 今日到访觉察：count>=3 且当次未触发重逢问候 → 传给 engine ──
    if _visit_count >= 3 and not greeting:
        engine.set_daily_visits(_visit_count)

    # 10. Build messages array for frontend (includes greeting as a regular message)
    # evidence 与 GET /{session_id} 同一个解码出口（parse_evidence）：重逢后的历史消息
    # 也要看得见检索来源，否则「刷新/重逢后仍能看到」在两条路径上只兑现了一条。
    frontend_messages = [
        {"role": m["role"], "content": m["content"], "id": m["id"], "created_at": m["created_at"],
         "retracted": m.get("retracted", False),
         "evidence": parse_evidence(m.get("evidence"))}
        for m in db_messages
    ]
    result: dict[str, Any] = {"session": db_session, "messages": frontend_messages}
    if greeting:
        frontend_messages.append({
            "role": "char",
            "content": greeting,
            "id": greeting_id,
            "created_at": greeting_created_at,
            "retracted": False,
            # 重逢问候在本轮检索之外生成，如实 None
            "evidence": None,
            # 前端靠这个标记放打字机动画 —— 不能靠 id：写失败的问候此刻还没有 id。
            "reunion": True,
            **save_field(greeting_save, "save"),
        })
        result["reunion_greeting"] = greeting
        result["reunion_greeting_id"] = greeting_id
        result["reunion_greeting_created_at"] = greeting_created_at

    return result


@router.post("/{session_id}/restore")
async def restore_session(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, bool]:
    """Restore a soft-deleted session from trash."""
    try:
        ok = await restore("session", session_id, user, storage)
    except HTTPException:
        raise
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
    try:
        if permanent:
            ok = await hard_delete("session", session_id, user, storage)
        else:
            ok = await soft_delete("session", session_id, user, storage)
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[history] Delete session failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"ok": True}


@router.delete("/{session_id}/permanent")
async def permanent_delete_session(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, bool]:
    """Permanently delete a session (irreversible).

    Note: `DELETE /{session_id}?permanent=true` remains as a deprecated alias
    for backward compatibility.
    """
    try:
        ok = await hard_delete("session", session_id, user, storage)
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[history] Hard delete session failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"ok": True}
