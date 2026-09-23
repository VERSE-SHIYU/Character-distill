"""Chat: send messages, revoke, reset."""

from __future__ import annotations

import asyncio
import json
import random
import time
import traceback
from typing import Any, Union

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from deps import get_sessions, get_storage, get_text_manager, touch_session
from storage.base import StorageBase
from limiter import limiter
from routers.auth import get_current_user
from core.affinity_service import read_persisted_affinity, resolve_session_affinity
from core.message_outbox import FlushReport, SaveState, save_field
from core.nonfatal import nonfatal
from core.schema import evidence_snapshots, evidence_to_json
from core import telemetry as T  # OTel 埋点（OTEL_ENABLED 关时零开销）
from adapters.llm_adapter import llm_error_payload, user_facing_error

router = APIRouter(prefix="/api/chat", tags=["chat"])
legacy_router = APIRouter(tags=["legacy-chat"])

MAX_MESSAGE_LENGTH = 5000


def _stream_error_payload(exc: Exception) -> dict[str, Any]:
    """SSE 错误帧。LLM 侧已知失败（经 adapter 边界映射）带 code / finish_reason，前端据此
    区分「被截断 / 被过滤 / 资源不足」与网络故障；其余异常只给通用文案。

    文案统一走 ``user_facing_error`` 这一唯一出口（与蒸馏路径同一份口径链）。上游原文
    与内部标识都不上屏，只留在日志里（缺陷 94 的泄漏那半）。"""
    payload = llm_error_payload(exc)
    if payload is not None:
        return payload
    return {"error": user_facing_error(exc)}


# 非流式：上游返回不完整响应不是「服务端出错」——content_filter 更是用户输入问题，
# 用 500 会让用户当 bug 反复重试。
#
# 「失败种类 → 状态码」那张表已**搬到 web/server.py 的 `_LLM_ERROR_STATUS`**（统一出口，
# 按判别键 `kind` 查：未完成终态 `incomplete:<finish_reason>`、调用点门拒绝 `call_refused`）：
# 同一个异常类型下码不同，但「怎么配码」不该散在路由里。此处只放行（见 `_do_chat`），
# 文案仍走同一份口径链，与 SSE 帧同文案。

# Retraction state machine
RETRACT_COOLDOWN_TURNS = 4      # 距上次撤回至少间隔的轮数
RETRACT_MAX_PER_SESSION = 3     # 单会话最大撤回次数
RETRACT_BASE_PROB = 0.2         # 通过冷却与上限后的基础概率


def _rebuild_history_from_db(db_messages: list[dict]) -> list[dict[str, object]]:
    """Filter DB messages and map roles to engine.history format.

    Only user and char messages are kept (whitelist approach); summary,
    system, and other synthetic roles are excluded.  Retracted char
    messages ARE kept (with retracted=True) so the LLM stays aware of
    what was said; the frontend hides them via the retracted flag.
    """
    result: list[dict[str, object]] = []
    for m in db_messages:
        if m["role"] not in ("user", "char"):
            continue
        entry: dict[str, object] = {
            "role": "assistant" if m["role"] == "char" else m["role"],
            "content": m["content"],
        }
        if m.get("retracted"):
            entry["retracted"] = True
        result.append(entry)
    return result


async def _decide_retraction(engine, session: dict, reply: str) -> bool:
    """Session-level retraction state machine — decide if this reply should be retracted.

    All conditions must pass:
      1. Cooldown: at least RETRACT_COOLDOWN_TURNS since last retract
      2. Session cap: retract_count < RETRACT_MAX_PER_SESSION
      3. Probability gate: random() < RETRACT_BASE_PROB
      4. LLM judgement: engine._should_retract(reply) returns True

    On hit, updates last_retract_turn / retract_count / turn_index.
    """
    state = session.setdefault("retract_state", {
        "last_retract_turn": -999,
        "retract_count": 0,
        "turn_index": 0,
    })
    state["turn_index"] += 1

    if not engine:
        return False

    if state["turn_index"] - state["last_retract_turn"] < RETRACT_COOLDOWN_TURNS:
        return False

    if state["retract_count"] >= RETRACT_MAX_PER_SESSION:
        return False

    if random.random() >= RETRACT_BASE_PROB:
        return False

    try:
        retracted = await asyncio.to_thread(engine._should_retract, reply)
    except Exception:
        retracted = False

    if retracted:
        state["last_retract_turn"] = state["turn_index"]
        state["retract_count"] += 1

    return retracted


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
        session.setdefault("retract_state", {
            "last_retract_turn": -999,
            "retract_count": 0,
            "turn_index": 0,
        })
        # SECURITY: verify session ownership even on memory hit.
        # 与下面的 DB 分支同判 404（含同一条文案）：命中/未命中的状态码若不同，
        # 反复请求就能靠差异推断资源是否存在——内存路径会把 DB 路径的防枚举漏掉。
        #
        # 这里**故意**不写 `session.get("user_id") and ...` 那种前置短路：条目没登记属主时
        # `None != user_id`，判「不是你的」。失败即关 —— 将来若又冒出一个忘了登记的构造点，
        # 后果是属主本人 404（响亮，当场暴露），而不是所有人都能进（静默）。所以不需要再为
        # 「构造点有没有登记」另建一把清单锁。
        if session.get("user_id") != user_id:
            raise HTTPException(404, "Session not found")
        touch_session(session)
        return session

    # Server restarted — rebuild from DB
    db_session = await storage.get_session_owned(session_id, user_id)
    if not db_session:
        raise HTTPException(404, "Session not found")

    from core.schema import CharacterCard
    from deps import get_text_manager as _gtm, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    text_manager = _gtm(llm=per_user_llm)
    if text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    card_id = db_session["card_id"]
    card_rec = await storage.get_card_owned(card_id, user_id)
    if not card_rec:
        raise HTTPException(404, "Card not found")
    text_rec = await storage.get_text_owned(card_rec["text_id"], user_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    try:
        card = CharacterCard.model_validate_json(card_rec["card_json"])
    except Exception as exc:
        raise HTTPException(500, "Card data is corrupted") from exc

    existing_cards = await storage.list_cards(card_rec["text_id"], user_id)
    all_characters = await text_manager._build_all_characters(card_rec["text_id"], existing_cards, user_id)

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
        text_manager._create_session, card,
        all_characters=all_characters, rag=rag,
        card_id=card_id, user_id=user_id,
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
    try:
        data, source = await read_persisted_affinity(session_id, storage)
        if source == 'state':
            engine.load_affinity(data, initialized=True)
        else:
            # legacy（已评估旧格式）或全新默认行 → load_affinity 自行判定升级/初值计算
            engine.load_affinity(data)
    except Exception as exc:
        print(f"[chat] Restore affinity failed (non-fatal): {exc}")
    sessions[session_id]["message_ids"] = [m["id"] for m in db_messages]
    sessions[session_id].setdefault("lock", asyncio.Lock())
    sessions[session_id].setdefault("retract_state", {
        "last_retract_turn": -999,
        "retract_count": 0,
        "turn_index": 0,
    })

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
    agent_mode: bool = False


class RevokeRequest(BaseModel):
    """Revoke messages after a given message_id."""
    session_id: str
    message_id: int


class ResetRequest(BaseModel):
    """Reset in-memory chat history."""
    session_id: str


# ---- Shared helpers ----

def _msg_fields(rec: dict | None) -> tuple[Any, str]:
    """`save_message` 的返回值 → `(msg_id, created_at)`；`None` → `(None, "")`。

    存在的理由是「保存**可能**失败」这件事得在代码里有个地方表达。保存点套在
    `nonfatal` 里，异常被吞掉之后 `*_rec` 仍是上面给的初值 `None` —— 两个入口的
    四个取值点（各自 id + created_at）若各自写 `(rec or {}).get(...)`，就把同一个
    判据抄了四遍，漏一处又是一次 `UnboundLocalError`（缺陷 98）。

    `created_at` 失败时给空串而不是 `None`：对外它是「这一条的时间戳」，没有这条
    消息时给空串，与 `hidden` 时原本给 `""` 同口径。
    """
    if rec is None:
        return None, ""
    return rec["id"], rec.get("created_at", "")


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
    agent_mode: bool = False,
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
                db_s = await storage.get_session_owned(session_id, user_id)
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
            engine._session_id = session_id
            engine._ctx_engine.web_search_enabled = web_search
            engine.affinity_enabled = affinity_enabled
            engine.agent_mode = agent_mode
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
        # LLM 侧未完成终态放行到统一出口（web/server.py 按 finish_reason 配 400/502/503）；
        # 其余仍就地 500。判据走 llm_error_payload，不 import 异常类（边界锁）。
        if llm_error_payload(exc) is not None:
            raise
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    # Determine retraction before persisting (session-level state machine)
    retracted = await _decide_retraction(session.get("engine"), session, resp)

    # If retracted, mark as retracted so LLM still knows it was said (annotation handled in _build_llm_messages)
    if retracted and engine and engine.history and engine.history[-1].get("role") == "assistant":
        engine.history[-1]["retracted"] = True

    # 三笔各自入队：写失败的那条**留在队列里**，等下一次写 / 用户点重试 / 关停时按原顺序
    # 补上 —— 不再各自包一个 nonfatal 就地丢掉。`*_save` 是逐条上报给前端的「没落库」依据。
    outbox = session["outbox"]
    ping = storage.ping

    user_msg_id = None
    char_msg_id = None
    # `*_created_at` 必须有初值：没落库时这一轮就没有这条记录，下面按「没有」取值（缺陷 98）。
    user_created_at = ""
    char_created_at = ""
    ids_to_add: list[Any] = []
    report = FlushReport()
    user_save: SaveState | None = None
    char_save: SaveState | None = None

    if not hidden:
        # write_fn 只回行 id（队列的契约）；`created_at` 由这一笔自己记在闭包里 —— 队列不碰
        # 存储的返回形状（`messages` 与 `group_messages` 两张表返回的根本不是同一种东西）。
        user_rows: list[dict] = []

        async def _save_user_message(key: str) -> int:
            rec = await storage.save_message(
                session_id, "user", msg, "",
                reply_to_id=reply_to_id, reply_to_preview=reply_to_preview, client_key=key,
            )
            user_rows.append(rec)
            return rec["id"]

        user_save, rep = await outbox.write(_save_user_message, ping=ping)
        report.merge(rep)
        user_msg_id, user_created_at = _msg_fields(user_rows[0] if user_rows else None)

    char_rows: list[dict] = []

    async def _save_char_message(key: str) -> int:
        # 检索来源快照落库（evidence_to_json 是唯一编码出口）。读的是本轮 chat 刚写下的
        # engine.last_traces —— 必须在 post_stream_process 之前取，那是另一轮。
        rec = await storage.save_message(
            session_id, "char", resp, rag_ctx[:500], retracted=retracted,
            evidence=evidence_to_json(getattr(engine, "last_traces", [])), client_key=key,
        )
        char_rows.append(rec)
        return rec["id"]

    char_save, rep = await outbox.write(_save_char_message, ping=ping)
    report.merge(rep)
    char_msg_id, char_created_at = _msg_fields(char_rows[0] if char_rows else None)
    ids_to_add.extend([user_msg_id, char_msg_id])

    # Save summary if newly generated
    engine = session.get("engine")
    if engine and engine.last_summary:
        # 读也包进块里：摘要只是个附赠品，读它失败不该把本轮回复打断。
        pending_summary = ""
        async with nonfatal("chat", "save summary"):
            existing_summaries = [
                m for m in await storage.get_messages(session_id)
                if m["role"] == "summary"
            ]
            last_saved = existing_summaries[-1]["content"] if existing_summaries else ""
            new_summary = f"历史摘要：{engine.last_summary}"
            if new_summary != last_saved:
                pending_summary = new_summary
        if pending_summary:
            # 摘要也走队列（写失败会补上），但**不**上报给前端：摘要不在界面上，
            # 标「未保存」只会让人去找一条看不见的消息。
            sum_save, rep = await outbox.write(
                lambda key, text=pending_summary: storage.save_message(
                    session_id, "summary", text, "", client_key=key,
                ),
                ping=ping,
            )
            report.merge(rep)
            if sum_save.id is not None:
                ids_to_add.append(sum_save.id)

    session.setdefault("message_ids", []).extend(ids_to_add)

    result: dict[str, Any] = {
        "reply": resp, "retracted": retracted, "rag_context": rag_ctx[:200],
        "user_msg_id": user_msg_id, "char_msg_id": char_msg_id,
        "user_created_at": user_created_at,
        "char_created_at": char_created_at,
        "reply_to_id": reply_to_id, "reply_to_preview": reply_to_preview,
        **save_field(user_save, "user_save"),
        **save_field(char_save, "char_save"),
        **report.as_json(),
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
    agent_mode: bool = False,
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
                db_s = await storage.get_session_owned(session_id, user_id)
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
        engine._session_id = session_id
        engine._ctx_engine.web_search_enabled = web_search
        engine.affinity_enabled = affinity_enabled
        engine.agent_mode = agent_mode
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
        # 与 `_do_chat` 同因（缺陷 98）：没落库时这些取值就是「没有这条记录」。少了初值，
        # 末尾 done 帧的取值就是 UnboundLocalError —— 正文已整段流给用户，却在收尾时把
        # done 帧换成 error 帧。
        user_created_at = ""
        char_created_at = ""
        # 三笔各自入队（同 `_do_chat`）：一条写失败不再连坐别条，`*_save` 是逐条上报给前端
        # 的「没落库」依据 —— 只有这个字段说了算，不由 `msg_id is None` 反推（hidden 消息
        # 本来就没有 id，却是存成功的）。
        outbox = session["outbox"]
        ping = storage.ping
        report = FlushReport()
        user_save: SaveState | None = None
        char_save: SaveState | None = None

        if not hidden:
            user_rows: list[dict] = []

            async def _save_user_message(key: str) -> int:
                rec = await storage.save_message(
                    session_id, "user", msg, "",
                    reply_to_id=reply_to_id, reply_to_preview=reply_to_preview, client_key=key,
                )
                user_rows.append(rec)
                return rec["id"]

            user_save, rep = await outbox.write(_save_user_message, ping=ping)
            report.merge(rep)
            user_msg_id, user_created_at = _msg_fields(user_rows[0] if user_rows else None)

        try:
            engine = session["engine"]
            print(f"[chat] _do_chat_stream session={session_id} history={len(engine.history) if engine else 0} messages")
            # Prepend quote context for LLM if replying
            llm_msg = f'[引用: "{reply_to_preview}"]\n{msg}' if reply_to_preview else msg
            async with session["lock"]:
                stream = engine.chat_stream(llm_msg, voice_mode=voice_mode)
                # Drive full stream generation under lock to prevent history interleaving
                first_piece, done = await asyncio.to_thread(_next_piece, stream)
                # evidence 帧**必须排在 token 流之前**：驱动首个 next() 时，chat_stream 的
                # 生成器体已跑到第一个 yield 之前（_compose_context / _run_agent_phase 都在
                # 那之前），故此刻 last_traces 已是**本轮**检索的事实，早取晚取都是错的那一轮。
                # 顺序反过来（先流文字、结束后再补证据）说服力就没了 —— 证据先渲染、文字后
                # 流入，用户看到的才是「先查了什么，再据此说话」。
                yield f"data: {json.dumps({'type': 'evidence', 'evidence': evidence_snapshots(getattr(engine, 'last_traces', []))}, ensure_ascii=False, default=str)}\n\n"
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

            # Determine retraction before persisting (session-level state machine)
            retracted = await _decide_retraction(session.get("engine"), session, full_reply)
            engine = session.get("engine")

            # If retracted, mark as retracted so LLM still knows it was said (annotation handled in _build_llm_messages)
            if retracted and engine and engine.history and engine.history[-1].get("role") == "assistant":
                engine.history[-1]["retracted"] = True

            char_rows: list[dict] = []

            async def _save_char_message(key: str) -> int:
                # 同 _do_chat：证据在本轮流式生成期间写入 engine.last_traces，
                # 后面的 post_stream_process 是另一轮，取早了/晚了都是错的那一轮。
                rec = await storage.save_message(
                    session_id, "char", full_reply, rag_context[:500], retracted=retracted,
                    evidence=evidence_to_json(getattr(engine, "last_traces", [])), client_key=key,
                )
                char_rows.append(rec)
                return rec["id"]

            char_save, rep = await outbox.write(_save_char_message, ping=ping)
            report.merge(rep)
            char_msg_id, char_created_at = _msg_fields(char_rows[0] if char_rows else None)

            msg_ids = [uid for uid in (user_msg_id, char_msg_id) if uid is not None]
            if msg_ids:
                session.setdefault("message_ids", []).extend(msg_ids)

            if engine and engine.last_summary:
                # 读也包进块里（同 `_do_chat`）：摘要只是附赠品，读它失败不该把本轮打断。
                pending_summary = ""
                async with nonfatal("chat", "save summary"):
                    existing_summaries = [
                        m for m in await storage.get_messages(session_id)
                        if m["role"] == "summary"
                    ]
                    last_saved = existing_summaries[-1]["content"] if existing_summaries else ""
                    new_summary = f"历史摘要：{engine.last_summary}"
                    if new_summary != last_saved:
                        pending_summary = new_summary
                if pending_summary:
                    sum_save, rep = await outbox.write(
                        lambda key, text=pending_summary: storage.save_message(
                            session_id, "summary", text, "", client_key=key,
                        ),
                        ping=ping,
                    )
                    report.merge(rep)
                    if sum_save.id is not None:
                        session.setdefault("message_ids", []).append(sum_save.id)

            done_payload: dict[str, Any] = {
                "done": True, "retracted": retracted, "rag_context": rag_context[:200],
                "user_msg_id": user_msg_id,
                "char_msg_id": char_msg_id,
                "user_created_at": user_created_at,
                "char_created_at": char_created_at,
                "reply_to_id": reply_to_id, "reply_to_preview": reply_to_preview,
                **save_field(user_save, "user_save"),
                **save_field(char_save, "char_save"),
                **report.as_json(),
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
            if not tokens:
                # 两条路都要收：已落库的从库里删（`delete_messages_after`），还没落库的从
                # 队里摘 —— 只删库不摘队的话，补写会把这条「用户看到失败了」的消息又写回来。
                if user_save is not None:
                    outbox.discard(user_save.key)
                if user_msg_id is not None:
                    try:
                        await storage.delete_messages_after(session_id, user_msg_id)
                    except Exception as rollback_exc:
                        print(f"[chat] Rollback user message failed (non-fatal): {rollback_exc}")
            # Sync engine.history: pop phantom user message if chat_stream's own
            # except block didn't clean up (e.g. exception after generator exit)
            engine = session.get("engine")
            if engine and engine.history and engine.history[-1].get("role") == "user":
                engine.history.pop()
            yield f"data: {json.dumps(_stream_error_payload(exc), ensure_ascii=False, default=str)}\n\n"

    # OTel context 传播点：invoke_agent 根 span 包住整个 SSE（TTFT→首 token），
    # 子链路在 to_thread / ctx_submit 线程里经 context 拷贝自动挂到本根下。
    return StreamingResponse(
        T.trace_sse_async(_event_generator(), "chat.invoke_agent",
                          op="invoke_agent", wf="chat"),
        media_type="text/event-stream",
    )


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
        return await _do_chat_stream(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id, req.web_search, req.voice_mode, req.affinity_enabled, req.client_tz, req.reply_to_id, req.reply_to_preview, req.agent_mode)
    return await _do_chat(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id, req.web_search, req.voice_mode, req.affinity_enabled, req.client_tz, req.reply_to_id, req.reply_to_preview, req.agent_mode)


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

    # 撤回 = 这段历史整个不要了：队里还没落库的那几条也得摘掉。不清队的话补写会把它们
    # 又写回来，用户看到的是「撤回之后它自己又冒出来了」。**先清队再删库** —— 顺序反过来
    # 时，两步之间插进来的补写，正好就是删完之后又出现的那条。
    session["outbox"].clear()

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


@router.post("/{session_id}/flush")
@limiter.limit("30/minute")
async def flush_session_messages(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any]:
    """把这条会话里没落库的消息按原顺序补写一遍 —— 前端「重试」按钮打的就是这里。

    属主校验走 `_ensure_session`（与 `/send` 同一处）：非属主与不存在的会话同判 404，
    不给存在性枚举留信道。响应体与 done 帧同形（`flushed` / `dropped`）。
    """
    session = await _ensure_session(session_id, storage, sessions, user["id"])
    outbox = session["outbox"]
    return (await outbox.flush(ping=storage.ping)).as_json()


@router.get("/affinity/{session_id}", response_model=None)
async def get_affinity(
    session_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict = Depends(get_sessions),
) -> dict[str, Any] | Response:
    """Return affinity scores for a session (incl. inner_voice, mood_emoji, stage).

    无已评估 affinity 数据时返回 204（前端以 affinity=null 表达"无数据"），
    不返回长得像真实数据的假默认值。
    """
    # 属主过滤在 SQL 里完成：非属主与不存在同判 404，不靠状态码区分
    db_session = await storage.get_session_owned(session_id, user["id"])
    if not db_session:
        raise HTTPException(404, "Session not found")

    aff = await resolve_session_affinity(session_id, storage, sessions)
    if aff is None:
        return Response(status_code=204)
    return aff


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
    # 属主过滤在 SQL 里完成：非属主与不存在同判 404，不靠状态码区分
    db_session = await storage.get_session_owned(session_id, user["id"])
    if not db_session:
        raise HTTPException(404, "Session not found")

    # 反应按会话读取，属主谓词在 SQL 里（JOIN sessions）：不再由调用方拼 message_ids
    reactions = await storage.get_session_reactions_owned(session_id, user["id"])
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
    return await _do_chat(req.session_id, req.message, storage, sessions, req.user_role, req.hidden, user_id, req.web_search, voice_mode=False, affinity_enabled=req.affinity_enabled, client_tz=req.client_tz, agent_mode=req.agent_mode)


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
