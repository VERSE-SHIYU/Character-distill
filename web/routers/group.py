"""Group chat: create group, send message, list history."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from deps import get_storage, get_user_llm, get_sessions
from limiter import limiter
from storage.base import StorageBase
from routers.auth import get_current_user
from core.chat_engine import _calc_stage

router = APIRouter(prefix="/api/group", tags=["group"])

# In-memory group sessions: group_id → GroupSession
_group_sessions: dict[str, Any] = {}

# 群聊已消化点赞游标: group_id → 最大 reaction_id
_group_last_reaction_id: dict[str, int] = {}


class CreateGroupRequest(BaseModel):
    name: str = ""
    card_ids: list[str]
    user_persona_type: str = "director"  # "character" | "stranger" | "director"
    user_persona_card_id: str = ""
    user_persona_name: str = ""
    user_persona_desc: str = ""


class RenameGroupRequest(BaseModel):
    name: str


class SendMessageRequest(BaseModel):
    target_card_id: str
    message: str
    speaker: str = ""
    reply_to_id: int | None = None

class BroadcastRequest(BaseModel):
    target_card_ids: list[str]
    message: str
    speaker: str = ""
    auto_mode: bool = False
    reply_to_id: int | None = None

class ReactRequest(BaseModel):
    emoji: str


async def _rebuild_group_session(
    group_id: str,
    user_id: str,
    storage: StorageBase,
) -> Any | None:
    """Rebuild an in-memory GroupSession from the persisted DB record."""
    from core.schema import CharacterCard
    from core.chat_engine import ChatEngine
    from core.rag import RAGEngine
    from core.group_session import GroupSession
    from deps import get_rag_config, get_memory_manager

    session = await storage.get_group_session(group_id)
    if not session:
        return None
    if session.get("user_id") != user_id:
        return None

    per_user_llm = await get_user_llm(user_id, storage)
    if per_user_llm is None:
        return None

    rag_config = get_rag_config()
    memory_manager = get_memory_manager()

    persona_type = session.get("user_persona_type", "director")
    persona_card_id = session.get("user_persona_card_id", "")

    engines: dict[str, ChatEngine] = {}
    text_rag_cache: dict[str, RAGEngine] = {}
    played_card_name = ""

    for card_id in session["card_ids"]:
        # Skip the played character — user is that character, not AI
        if persona_type == "character" and card_id == persona_card_id:
            try:
                card_rec = await storage.get_card(card_id)
                if card_rec:
                    card = CharacterCard.model_validate_json(card_rec["card_json"])
                    played_card_name = card.name
            except Exception:
                pass
            continue

        card_rec = await storage.get_card(card_id)
        if not card_rec:
            continue
        if card_rec.get("user_id") != user_id:
            continue
        try:
            card = CharacterCard.model_validate_json(card_rec["card_json"])
        except Exception:
            continue

        text_id = card_rec["text_id"]
        if text_id not in text_rag_cache:
            text_rec = await storage.get_text(text_id)
            if not text_rec:
                continue
            rag = RAGEngine(rag_config)
            try:
                rag.load_existing(f"text_{text_id}")
            except Exception:
                rag.index(text_rec["content"])
            text_rag_cache[text_id] = rag

        engine = ChatEngine(
            per_user_llm, text_rag_cache[text_id], card,
            memory_manager=memory_manager,
            card_id=card_id,
        )
        engines[card_id] = engine

    # Verify AI count by persona mode
    if persona_type == "director" and len(engines) < 2:
        raise HTTPException(400, "导演模式需要至少2个AI角色")
    if len(engines) < 1:
        raise HTTPException(400, "至少需要1个AI角色陪你对话")

    # Resolve persona name for character type
    persona_name = session.get("user_persona_name", "")
    if persona_type == "character" and not persona_name:
        persona_name = played_card_name

    group = GroupSession(
        group_id, engines,
        user_persona_type=persona_type,
        user_persona_card_id=persona_card_id,
        user_persona_name=persona_name,
        user_persona_desc=session.get("user_persona_desc", ""),
        storage=storage,
    )
    _group_sessions[group_id] = group

    # Restore message history from DB
    try:
        history = await storage.get_group_messages(group_id)
        group.group_history = [
            {
                "speaker": m["speaker"],
                "role": m["role"],
                "content": m["content"],
                "speaker_card_id": m.get("speaker_card_id", ""),
            }
            for m in history
        ]
    except Exception as exc:
        print(f"[Group] Session rebuild failed: {exc}")

    return group


async def _run_group_affinity(
    group, group_id: str, card_ids: list[str],
    user_message: str, storage: StorageBase,
) -> None:
    """获取群聊未消化点赞，按 speaker_card_id 分桶，对每个角色跑 memory-only 亲和力评估。"""
    cursor = _group_last_reaction_id.get(group_id, 0)
    try:
        new_reactions = await storage.get_group_reactions_after(group_id, cursor)
    except Exception as exc:
        print(f"[Group Affinity] Fetch reactions failed (non-fatal): {exc}")
        return

    if not new_reactions:
        return

    _group_last_reaction_id[group_id] = max(r["reaction_id"] for r in new_reactions)

    # 按角色分桶
    by_card: dict[str, list[dict]] = {}
    for r in new_reactions:
        cid = r.get("speaker_card_id", "")
        if cid and cid in group.engines:
            by_card.setdefault(cid, []).append(r)

    if not by_card:
        return

    main_loop = asyncio.get_running_loop()
    for card_id, signals in by_card.items():
        engine = group.engines[card_id]
        engine._storage = storage
        engine._main_loop = main_loop
        # 不设 _session_id —— 群聊只更新内存，不写 DB
        engine.ingest_reaction_signals([
            {"emoji": r["emoji"], "msg_content": r.get("msg_content", "")}
            for r in signals
        ])
        await asyncio.to_thread(engine._evaluate_affinity, user_message, "")


def _get_group_sessions() -> dict[str, Any]:
    return _group_sessions


@router.post("/create")
@limiter.limit("30/minute")
async def create_group(
    req: CreateGroupRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict:
    """创建群聊，包含多个角色的 ChatEngine。"""
    user_id = user["id"]
    per_user_llm = await get_user_llm(user_id, storage)
    if per_user_llm is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    if not req.card_ids:
        raise HTTPException(400, "请至少选择角色")

    # Validate persona (must do before AI count check — mode-dependent)
    persona_type = req.user_persona_type
    persona_card_id = req.user_persona_card_id
    persona_name = req.user_persona_name
    persona_desc = req.user_persona_desc
    if persona_type not in ("director", "character", "stranger"):
        raise HTTPException(400, "无效的身份类型")
    if persona_type == "character" and persona_card_id not in req.card_ids:
        raise HTTPException(400, "扮演角色必须在已选角色中")
    if persona_type == "stranger" and not persona_name.strip():
        raise HTTPException(400, "路人身份需要填写名字")

    # AI count check by persona mode
    ai_card_ids = [cid for cid in req.card_ids if persona_type != "character" or cid != persona_card_id]
    if persona_type == "director":
        if len(ai_card_ids) < 2:
            raise HTTPException(400, "导演模式需要至少2个AI角色")
    else:
        if len(ai_card_ids) < 1:
            raise HTTPException(400, "至少需要1个AI角色陪你对话")

    from core.schema import CharacterCard
    from core.chat_engine import ChatEngine
    from core.rag import RAGEngine
    from deps import get_rag_config, get_memory_manager

    rag_config = get_rag_config()
    memory_manager = get_memory_manager()

    engines: dict[str, ChatEngine] = {}
    card_infos: list[dict] = []
    text_rag_cache: dict[str, RAGEngine] = {}
    played_card_name = ""

    for card_id in req.card_ids:
        card_rec = await storage.get_card(card_id)
        if not card_rec:
            raise HTTPException(404, f"角色卡 {card_id} 不存在")
        if card_rec.get("user_id") != user_id:
            raise HTTPException(403, f"无权使用角色卡 {card_id}")

        try:
            card = CharacterCard.model_validate_json(card_rec["card_json"])
        except Exception as exc:
            import traceback
            print(f"[GroupCreate ERROR] card_id={card_id} model_validate_json: {exc}")
            traceback.print_exc()
            raise HTTPException(500, "操作失败，请稍后重试") from exc

        # Track played character name
        if persona_type == "character" and card_id == persona_card_id:
            played_card_name = card.name
            # Don't add to engines — user plays this character
            card_infos.append({"card_id": card_id, "name": card.name, "played_by_user": True})
            continue

        text_id = card_rec["text_id"]
        if text_id not in text_rag_cache:
            text_rec = await storage.get_text(text_id)
            if not text_rec:
                raise HTTPException(404, f"原文 {text_id} 不存在")
            rag = RAGEngine(rag_config)
            try:
                rag.load_existing(f"text_{text_id}")
            except Exception:
                import traceback
                print(f"[GroupCreate WARN] card_id={card_id} text_id={text_id} RAG load_existing failed, falling back to index")
                traceback.print_exc()
                rag.index(text_rec["content"])
            text_rag_cache[text_id] = rag

        engine = ChatEngine(
            per_user_llm, text_rag_cache[text_id], card,
            memory_manager=memory_manager,
            card_id=card_id,
        )
        engines[card_id] = engine
        card_infos.append({"card_id": card_id, "name": card.name})

    # After removing played character, verify AI count by persona mode
    if persona_type == "director":
        if len(engines) < 2:
            raise HTTPException(400, "导演模式需要至少2个AI角色")
    else:
        if len(engines) < 1:
            raise HTTPException(400, "至少需要1个AI角色陪你对话")

    if persona_type == "character":
        persona_name = persona_name or played_card_name

    from core.group_session import GroupSession
    group_id = uuid.uuid4().hex[:12]
    group = GroupSession(
        group_id, engines,
        user_persona_type=persona_type,
        user_persona_card_id=persona_card_id,
        user_persona_name=persona_name.strip() if persona_name else "",
        user_persona_desc=persona_desc.strip(),
        storage=storage,
    )
    _group_sessions[group_id] = group

    # Persist to DB
    await storage.create_group_session(
        group_id, req.name, req.card_ids, user_id,
        user_persona_type=persona_type,
        user_persona_card_id=persona_card_id,
        user_persona_name=persona_name.strip() if persona_name else "",
        user_persona_desc=persona_desc.strip(),
    )

    return {
        "group_id": group_id,
        "name": req.name or "群聊",
        "characters": card_infos,
        "user_persona_type": persona_type,
        "user_persona_card_id": persona_card_id,
        "user_persona_name": persona_name.strip() if persona_name else "",
        "user_persona_desc": persona_desc.strip(),
    }


async def _filter_valid_card_ids(groups: list[dict], user_id: str, storage: StorageBase) -> list[dict]:
    """Remove orphan card_ids from a list of group dicts in place. Returns the list for chaining."""
    all_ids = set()
    for g in groups:
        for cid in g.get("card_ids", []):
            all_ids.add(cid)
    if not all_ids:
        return groups

    valid: set[str] = set()
    for cid in all_ids:
        try:
            card = await storage.get_card(cid)
            if card and card.get("user_id") == user_id:
                valid.add(cid)
        except Exception:
            pass

    for g in groups:
        g["card_ids"] = [cid for cid in g.get("card_ids", []) if cid in valid]
    return groups


@router.get("/list")
@limiter.limit("60/minute")
async def list_groups(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """列出用户的群聊会话。"""
    groups = await storage.list_group_sessions(user["id"])
    await _filter_valid_card_ids(groups, user["id"], storage)
    return {"groups": groups}


@router.post("/cleanup-orphan-cards")
@limiter.limit("10/minute")
async def cleanup_orphan_card_ids(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Scan all groups for this user, remove card_ids that no longer exist, and persist."""
    groups = await storage.list_group_sessions(user["id"])
    user_id = user["id"]

    # Collect + validate all unique card_ids
    all_ids = set()
    for g in groups:
        for cid in g.get("card_ids", []):
            all_ids.add(cid)

    valid: set[str] = set()
    for cid in all_ids:
        try:
            card = await storage.get_card(cid)
            if card and card.get("user_id") == user_id:
                valid.add(cid)
        except Exception:
            pass

    stats = {"groups_checked": len(groups), "groups_cleaned": 0, "card_ids_removed": 0}
    for g in groups:
        original = g.get("card_ids", [])
        cleaned = [cid for cid in original if cid in valid]
        if len(cleaned) != len(original):
            stats["groups_cleaned"] += 1
            stats["card_ids_removed"] += len(original) - len(cleaned)
            try:
                await storage.update_group_card_ids(g["id"], cleaned)
            except Exception as exc:
                print(f"[Group] cleanup-orphan-cards failed for {g['id']}: {exc}")

    return {"ok": True, **stats}


@router.post("/{group_id}/send")
@limiter.limit("30/minute")
async def send_message(
    group_id: str,
    req: SendMessageRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """导演模式下向指定角色发消息。"""
    user_id = user["id"]
    group = _group_sessions.get(group_id)
    if group is None:
        group = await _rebuild_group_session(group_id, user_id, storage)
    if group is None:
        raise HTTPException(404, "群聊会话已过期，请重新创建")

    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    if req.target_card_id not in group.engines:
        raise HTTPException(400, "目标角色不在群聊中")

    session_rec = await storage.get_group_session(group_id)
    if session_rec and session_rec.get("deleted_at"):
        raise HTTPException(410, "群聊已被删除")

    async with group.lock:
        try:
            resp = await group.send(req.target_card_id, req.message)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, "操作失败，请稍后重试") from exc

    # Persist to DB
    reply_preview = ""
    if req.reply_to_id:
        try:
            history = await storage.get_group_messages(group_id)
            replied = next((m for m in history if m["id"] == req.reply_to_id), None)
            if replied:
                reply_preview = (replied.get("speaker", "") + ": " + replied["content"])[:80]
        except Exception:
            pass
    user_speaker = req.speaker or group.speaker_name
    user_speaker_card_id = group.user_persona_card_id if group.user_persona_type == "character" else ""

    try:
        await storage.save_group_message(
            group_id, user_speaker, "user", req.message, user_speaker_card_id,
            reply_to_id=req.reply_to_id, reply_to_preview=reply_preview,
        )
        await storage.save_group_message(
            group_id, group.engines[req.target_card_id].card.name,
            "assistant", resp, req.target_card_id,
        )
    except Exception as exc:
        print(f"[group] Save messages failed (non-fatal): {exc}")

    # 后台评估点赞触发的 affinity 变化（不阻塞回复）
    asyncio.create_task(
        _run_group_affinity(group, group_id, [req.target_card_id], req.message, storage)
    )

    return {"reply": resp, "speaker": group.engines[req.target_card_id].card.name}


@router.post("/{group_id}/broadcast")
@limiter.limit("30/minute")
async def broadcast_message(
    group_id: str,
    req: BroadcastRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """导演发一条消息，多个角色并行回复（仅记录一条导演消息）。"""
    user_id = user["id"]
    group = _group_sessions.get(group_id)
    if group is None:
        group = await _rebuild_group_session(group_id, user_id, storage)
    if group is None:
        raise HTTPException(404, "群聊会话已过期，请重新创建")

    if not req.message.strip() and not req.auto_mode:
        raise HTTPException(400, "消息不能为空")
    invalid = [cid for cid in req.target_card_ids if cid not in group.engines]
    if invalid:
        raise HTTPException(400, f"目标角色不在群聊中: {invalid}")

    session_rec = await storage.get_group_session(group_id)
    if session_rec and session_rec.get("deleted_at"):
        raise HTTPException(410, "群聊已被删除")

    async with group.lock:
        try:
            results = await group.broadcast(req.message, req.target_card_ids, auto_mode=req.auto_mode)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, "操作失败，请稍后重试") from exc

    # 持久化
    reply_preview = ""
    if req.reply_to_id:
        try:
            history = await storage.get_group_messages(group_id)
            replied = next((m for m in history if m["id"] == req.reply_to_id), None)
            if replied:
                reply_preview = (replied.get("speaker", "") + ": " + replied["content"])[:80]
        except Exception:
            pass
    user_speaker = req.speaker or group.speaker_name
    user_speaker_card_id = group.user_persona_card_id if group.user_persona_type == "character" else ""

    user_msg_id = None
    try:
        if not req.auto_mode:
            user_msg_id = await storage.save_group_message(
                group_id, user_speaker, "user", req.message, user_speaker_card_id,
                reply_to_id=req.reply_to_id, reply_to_preview=reply_preview,
            )
        for r in results:
            if not r["reply"]:
                continue
            # [REACT:x] → attach reaction to user message instead of saving as message
            m = re.fullmatch(r'\s*\[REACT:(.+?)\]\s*', r["reply"])
            if m:
                if not req.auto_mode and user_msg_id is not None:
                    await storage.toggle_reaction(user_msg_id, f"char:{r['card_id']}", m.group(1))
                # auto_mode: discard silently (no user message to attach to)
                continue
            await storage.save_group_message(
                group_id, r["speaker"], "assistant", r["reply"], r["card_id"],
            )
    except Exception as exc:
        print(f"[group] Save broadcast messages failed (non-fatal): {exc}")

    # 后台评估点赞触发的 affinity 变化（不阻塞回复）
    asyncio.create_task(
        _run_group_affinity(group, group_id, req.target_card_ids, req.message, storage)
    )

    return {"replies": results}


@router.get("/{group_id}/affinities")
@limiter.limit("60/minute")
async def list_group_affinities(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> list[dict]:
    """List all characters' affinity / stage in this group."""
    session_rec = await storage.get_group_session(group_id)
    if not session_rec:
        raise HTTPException(404, "群聊不存在")
    if session_rec.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此群聊")

    card_ids: list[str] = session_rec.get("card_ids", [])
    result: list[dict] = []
    for cid in card_ids:
        row = await storage.get_group_affinity(group_id, cid)
        affinity = row["affinity"] if row else 50
        stage_name, stage_emoji = _calc_stage(affinity)
        result.append({
            "card_id": cid,
            "affinity": affinity,
            "stage_name": stage_name,
            "stage_emoji": stage_emoji,
        })

    return result


@router.post("/{group_id}/message/{message_id}/react")
@limiter.limit("60/minute")
async def toggle_reaction(
    group_id: str,
    message_id: int,
    req: ReactRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Toggle a reaction emoji on a message."""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")

    if not req.emoji.strip():
        raise HTTPException(400, "emoji 不能为空")

    added = await storage.toggle_reaction(message_id, user["id"], req.emoji.strip())
    return {"ok": True, "added": added}

@router.get("/{group_id}/history")
@limiter.limit("60/minute")
async def get_history(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """获取群聊历史消息。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此群聊")

    if session.get("deleted_at"):
        raise HTTPException(410, "群聊已被删除")

    messages = await storage.get_group_messages(group_id)
    return {"messages": messages}


@router.patch("/{group_id}/rename")
@limiter.limit("30/minute")
async def rename_group(
    group_id: str,
    req: RenameGroupRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """重命名群聊。"""
    if not req.name.strip():
        raise HTTPException(400, "名称不能为空")
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此群聊")
    if session.get("deleted_at"):
        raise HTTPException(410, "群聊已被删除")
    await storage.update_group_session(group_id, req.name.strip())
    return {"ok": True}


@router.delete("/{group_id}")
@limiter.limit("30/minute")
async def delete_group(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """软删除群聊会话（移入回收站）。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")
    await storage.delete_group_session(group_id)
    _group_sessions.pop(group_id, None)
    return {"ok": True}


@router.get("/trash")
@limiter.limit("30/minute")
async def list_trash_groups(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """列出已删除的群聊。"""
    groups = await storage.get_deleted_group_sessions(user["id"])
    return {"groups": groups}


@router.post("/{group_id}/restore")
@limiter.limit("30/minute")
async def restore_group(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """恢复已删除的群聊。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")
    if not session.get("deleted_at"):
        raise HTTPException(400, "群聊未被删除")
    await storage.restore_group_session(group_id)
    return {"ok": True}


@router.delete("/{group_id}/permanent")
@limiter.limit("30/minute")
async def permanent_delete_group(
    group_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """永久删除群聊及其所有消息。"""
    session = await storage.get_group_session(group_id)
    if not session:
        raise HTTPException(404, "群聊不存在")
    if session.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作")
    await storage.hard_delete_group_session(group_id)
    _group_sessions.pop(group_id, None)
    return {"ok": True}
