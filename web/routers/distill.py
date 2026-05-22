"""Distillation: identify characters and generate character cards."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid as _uuid
from typing import Any

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from deps import get_sessions, get_storage
from core.distiller import Distiller
from core.export import export_tavern_json
from core.schema import CharacterCard
from storage.sqlite_store import SQLiteStore
from limiter import limiter

router = APIRouter(prefix="/api/distill", tags=["distill"])
legacy_router = APIRouter(tags=["legacy-distill"])


# ---- Request models ----

class IdentifyByIdRequest(BaseModel):
    """New: identify from a stored text by text_id."""
    text_id: str


class DistillByIdRequest(BaseModel):
    """New: distill from a stored text by text_id."""
    text_id: str
    character_name: str = ""
    force: bool = False


class StartSessionRequest(BaseModel):
    """Create a chat session for an existing card without re-distilling."""
    text_id: str
    card_id: str


class IdentifyRequest(BaseModel):
    """Legacy: identify from raw text."""
    text: str


class DistillRequest(BaseModel):
    """Legacy: distill from raw text."""
    text: str
    character_name: str = ""


# ---- Background task store ----

_tasks: dict[str, dict[str, Any]] = {}
_task_lock = threading.Lock()


class DistillTaskRequest(BaseModel):
    text_id: str
    character_name: str = ""
    force: bool = False


def _run_distill_task(
    task_id: str, text_id: str, char_name: str, force: bool, user_id: str,
    content: str, text_type: str, api_config: dict | None = None,
) -> None:
    """Background thread: run distillation end-to-end, update _tasks[task_id].

    All I/O that requires an event loop (text read, card save) is performed
    via ``asyncio.run()`` which creates a fresh loop per call — safe across
    threads since each thread owns its own loop.

    If *api_config* is provided (api_key, base_url, model), a per-user LLM
    is created so distillation uses the user's own API key, not the global fallback.
    """
    try:
        from adapters.llm_adapter import LLMAdapter
        from deps import get_distiller, get_text_manager

        per_user_llm = None
        if api_config and api_config.get("api_key"):
            try:
                per_user_llm = LLMAdapter(
                    api_key=api_config["api_key"],
                    base_url=api_config.get("base_url", "https://api.deepseek.com"),
                    model=api_config.get("model", "deepseek-v4-pro"),
                )
            except Exception as exc:
                print(f"[distill] Per-user LLM init failed, falling back: {exc}")

        distiller = get_distiller(llm=per_user_llm)
        text_manager = get_text_manager(llm=per_user_llm)

        if not distiller or not text_manager:
            with _task_lock:
                _tasks[task_id] = {"status": "error", "message": "请先在设置页配置 API Key"}
            return

        # Step 1: resolve character name + aliases in ONE LLM call
        name = char_name.strip()
        aliases: list[str] = []
        try:
            chars = distiller.identify_characters(content)
        except Exception:
            chars = []
        if not name:
            if not chars:
                with _task_lock:
                    _tasks[task_id] = {"status": "error", "message": "No characters identified"}
                return
            name = chars[0].get("name", "")
            if not name:
                with _task_lock:
                    _tasks[task_id] = {"status": "error", "message": "Identified result missing name"}
                return
        for c in chars:
            if c.get("name") == name:
                aliases = c.get("aliases", [])
                break

        with _task_lock:
            _tasks[task_id] = {"status": "analyzing", "current": 0, "total": 0, "progress_pct": 0, "character": name}

        # Step 2: run incremental distill (synchronous, collect full output)
        full = ""
        stream = distiller.distill_incremental_stream(content, name, aliases, text_type)
        for piece in stream:
            with _task_lock:
                if _tasks.get(task_id, {}).get("status") == "error":
                    return
            if isinstance(piece, dict):
                if piece.get("heartbeat"):
                    continue
                with _task_lock:
                    current = piece.get("current", 0)
                    total = piece.get("total", 1)
                    status = piece.get("status", "analyzing")
                    pct = int((current / total) * 100) if total > 0 else 0
                    _tasks[task_id] = {
                        "status": status,
                        "current": current,
                        "total": total,
                        "progress_pct": pct,
                        "character": name,
                    }
            else:
                full += piece

        # Step 3: parse + validate
        stripped = full.strip()
        data = None
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(stripped[start:end + 1])
                except json.JSONDecodeError:
                    pass

        if data is None:
            with _task_lock:
                _tasks[task_id] = {"status": "error", "message": "蒸馏失败：LLM 返回格式不正确", "character": name}
            return

        from core.schema import CharacterCard
        try:
            card = CharacterCard.model_validate(data)
        except Exception as exc:
            with _task_lock:
                _tasks[task_id] = {"status": "error", "message": f"蒸馏失败：数据校验错误 {exc}", "character": name}
            return

        # Step 4: persist via fresh store + text_manager in a new event loop.
        # Using the singleton text_manager's _storage would reuse a connection
        # created in the main event loop — unsafe across threads.  Instead,
        # build a fresh SQLiteStore + TextManager inside ``asyncio.run()`` so
        # aiosqlite connections are bound to the thread's own event loop.
        async def _save_card():
            from pathlib import Path as _Path
            from deps import get_config, get_rag_config, get_sessions
            from core.text_manager import TextManager
            from storage.sqlite_store import SQLiteStore

            cfg = get_config()
            db_path = str(_Path(__file__).resolve().parent.parent.parent / cfg["storage"]["path"])
            store = SQLiteStore(db_path)
            await store._ensure_initialized()

            llm_for_save = per_user_llm
            if llm_for_save is None:
                from deps import get_llm
                llm_for_save = get_llm()

            tm = TextManager(
                store,
                get_distiller(llm=llm_for_save),
                llm_for_save,
                get_rag_config(),
                get_sessions(),
                cfg.get("llm", {}).get("summary_threshold", 50),
            )
            return await tm.save_distilled_card(text_id, card, user_id)

        result = asyncio.run(_save_card())

        with _task_lock:
            _tasks[task_id] = {
                "status": "done",
                "card_id": result.get("card_id", ""),
                "character": name,
                "progress_pct": 100,
            }

    except Exception as exc:
        print(f"[distill] Background task {task_id} failed: {exc}")
        with _task_lock:
            _tasks[task_id] = {"status": "error", "message": str(exc)}


# ---- Shared helpers ----

async def _do_identify(text: str, distiller: Distiller) -> dict[str, Any]:
    """Core identify logic shared by new and legacy routes."""
    if not text.strip():
        raise HTTPException(400, "Text cannot be empty")
    try:
        chars = await asyncio.to_thread(distiller.identify_characters, text)
    except Exception as exc:
        print(f"[distill] Identify characters failed: {exc}")
        raise HTTPException(500, f"Identify failed: {exc}") from exc
    return {"characters": chars}


async def _resolve_character_name(
    text: str, character_name: str, distiller: Distiller
) -> str:
    """Auto-identify the first character if no name was provided."""
    name = character_name.strip()
    if name:
        return name
    try:
        chars = await asyncio.to_thread(distiller.identify_characters, text)
    except Exception as exc:
        print(f"[distill] Auto-identify failed: {exc}")
        raise HTTPException(500, f"Identify failed: {exc}") from exc
    if not chars:
        raise HTTPException(400, "No characters identified")
    name = chars[0].get("name", "")
    if not name:
        raise HTTPException(400, "Identified result missing name")
    return name


# ---- New routes (storage-backed, via TextManager) ----

@router.post("/identify")
async def identify_by_text_id(
    req: IdentifyByIdRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Identify characters from a text stored in the database."""
    user_id = request.state.user.get("id", "")
    from deps import get_distiller, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    cached = await storage.get_characters(req.text_id)
    if cached:
        return {"characters": cached}
    text_rec = await storage.get_text(req.text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")
    result = await _do_identify(text_rec["content"], distiller)
    await storage.save_characters(req.text_id, result["characters"])
    return result


@router.post("/run")
async def distill_by_text_id(
    req: DistillByIdRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Distill a character from a stored text, persist card + session."""
    user_id = request.state.user.get("id", "")
    from deps import get_distiller, get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    distiller = get_distiller(llm=per_user_llm)
    text_manager = get_text_manager(llm=per_user_llm)
    if text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text_rec = await storage.get_text(req.text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    content = text_rec["content"]
    char_name = await _resolve_character_name(content, req.character_name, distiller)

    try:
        return await text_manager.get_or_distill(
            req.text_id, char_name, force=req.force, user_id=user_id
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[distill] Distill failed: {exc}")
        raise HTTPException(500, f"Distill failed: {exc}") from exc


@router.post("/start")
async def distill_start(
    req: DistillTaskRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Start distillation as a background task, return task_id immediately."""
    from deps import get_distiller
    user_id = request.state.user.get("id", "")

    # Resolve per-user LLM config for the background thread
    api_config = await storage.get_user_api_config(user_id)
    per_user_llm = None
    if api_config and api_config.get("api_key"):
        from adapters.llm_adapter import LLMAdapter
        try:
            per_user_llm = LLMAdapter(
                api_key=api_config["api_key"],
                base_url=api_config.get("base_url", "https://api.deepseek.com"),
                model=api_config.get("model", "deepseek-v4-pro"),
            )
        except Exception:
            pass

    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    # Read text content in the async endpoint so the background thread
    # doesn't need to call asyncio storage methods (cross-thread safe).
    text_rec = await storage.get_text(req.text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    content = text_rec["content"]
    text_type = text_rec.get("text_type", "story")
    task_id = _uuid.uuid4().hex[:12]

    with _task_lock:
        _tasks[task_id] = {"status": "queued", "progress_pct": 0}

    thread = threading.Thread(
        target=_run_distill_task,
        args=(task_id, req.text_id, req.character_name, req.force, user_id, content, text_type, api_config),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}


@router.get("/task/{task_id}")
async def distill_task_status(task_id: str) -> dict[str, Any]:
    """Poll distillation task status."""
    with _task_lock:
        task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.get("status") in ("done", "error"):
        with _task_lock:
            _tasks.pop(task_id, None)
    return task


@router.delete("/task/{task_id}")
async def cancel_distill_task(task_id: str) -> dict[str, bool]:
    """Cancel a running distillation task."""
    with _task_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        _tasks[task_id] = {"status": "error", "message": "已取消"}
    return {"ok": True}


def _next_piece(stream_obj):
    """Read next token from a generator; returns (token, done). Thread-safe."""
    try:
        return next(stream_obj), False
    except StopIteration:
        return "", True


@router.post("/run_stream")
@limiter.limit("16/hour")
async def distill_stream(
    req: DistillByIdRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
):
    """Stream distillation via SSE — no timeout, frontend renders tokens in real-time."""
    user_id = request.state.user.get("id", "")
    from deps import get_distiller, get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    distiller = get_distiller(llm=per_user_llm)
    text_manager = get_text_manager(llm=per_user_llm)
    if text_manager is None or distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text_rec = await storage.get_text(req.text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    content = text_rec["content"]
    text_type = text_rec.get("text_type", "story")
    char_name = req.character_name.strip()

    distiller._storage = storage
    distiller._user_id = user_id

    async def _event_gen():
        yield f"data: {json.dumps({'status': 'identifying'}, ensure_ascii=False)}\n\n"

        # ONE LLM call: resolve name (if needed) + aliases
        nonlocal char_name
        aliases: list[str] = []
        try:
            chars = await asyncio.to_thread(distiller.identify_characters, content)
        except Exception as exc:
            print(f"[distill] Identify failed: {exc}")
            chars = []
        if not char_name:
            if not chars:
                yield f"data: {json.dumps({'error': '未识别到任何角色'}, ensure_ascii=False)}\n\n"
                return
            char_name = chars[0].get("name", "")
            if not char_name:
                yield f"data: {json.dumps({'error': '识别结果缺少角色名'}, ensure_ascii=False)}\n\n"
                return
        for c in chars:
            if c.get("name") == char_name:
                aliases = c.get("aliases", [])
                break

        # Incremental distillation with aliases for broader chunk matching
        full = ""
        stream = distiller.distill_incremental_stream(content, char_name, aliases, text_type)
        while True:
            try:
                piece, done = await asyncio.to_thread(_next_piece, stream)
            except Exception as exc:
                print(f"[distill] Stream failed: {exc}")
                yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
                return
            if done:
                break
            if isinstance(piece, dict) and piece.get("heartbeat"):
                yield f"data: {json.dumps({'heartbeat': True})}\n\n"
                continue
            if isinstance(piece, dict):
                # Progress event from incremental chunk processing
                yield f"data: {json.dumps(piece, ensure_ascii=False)}\n\n"
            else:
                full += piece
                yield f"data: {json.dumps({'token': piece}, ensure_ascii=False)}\n\n"

        # Step 3: Parse + validate + save
        stripped = full.strip()
        data = None
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(stripped[start:end + 1])
                except json.JSONDecodeError:
                    pass

        if data is None:
            yield f"data: {json.dumps({'error': '蒸馏失败：LLM 返回格式不正确'}, ensure_ascii=False)}\n\n"
            return

        try:
            card = CharacterCard.model_validate(data)
        except Exception as exc:
            print(f"[distill] Card validation failed: {exc}")
            yield f"data: {json.dumps({'error': f'蒸馏失败：数据校验错误 {exc}'}, ensure_ascii=False)}\n\n"
            return

        # Persist card + create session (RAG built in _create_session for chat use)
        try:
            result = await text_manager.save_distilled_card(req.text_id, card, user_id)
        except Exception as exc:
            print(f"[distill] Save card failed: {exc}")
            yield f"data: {json.dumps({'error': f'保存角色卡失败：{exc}'}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'done': True, **result}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.post("/reindex/{text_id}")
async def reindex_rag(
    text_id: str,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict[str, dict[str, Any]] = Depends(get_sessions),
) -> dict[str, Any]:
    """Rebuild RAG indices for all in-memory sessions with character metadata.

    Reads the text from storage, runs identify_characters, then rebuilds
    each session's RAG index to include character tags so that
    ``character_name`` filtering works in subsequent chat queries.
    """
    user_id = request.state.user.get("id", "")
    from deps import get_distiller, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text_rec = await storage.get_text(text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")
    content = text_rec["content"]

    try:
        chars = await asyncio.to_thread(distiller.identify_characters, content)
    except Exception as exc:
        print(f"[distill] Reindex identify failed: {exc}")
        raise HTTPException(500, f"Identify failed: {exc}") from exc

    count = 0
    for sid, session in sessions.items():
        engine = session.get("engine")
        if engine is None:
            continue
        try:
            engine.rag.index(content, all_characters=chars)
            engine._all_characters = chars
            count += 1
        except Exception as exc:
            print(f"[distill] Reindex session {sid} failed: {exc}")

    return {"reindexed_sessions": count, "characters_found": len(chars)}


class UpdateCardRequest(BaseModel):
    card_json: dict

@router.patch("/card/{card_id}")
async def update_card(
    card_id: str,
    req: UpdateCardRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
):
    record = await storage.get_card(card_id)
    if not record:
        raise HTTPException(404, "Card not found")
    if record.get("user_id") != request.state.user.get("id", ""):
        raise HTTPException(403, "无权修改此角色卡")
    result = await storage.update_card(card_id, req.card_json)
    return {"ok": True, "card": result}


class GenerateOpeningRequest(BaseModel):
    card_json: dict
    user_role: str

@router.post("/generate-opening")
async def generate_opening(
    req: GenerateOpeningRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
):
    user_id = request.state.user.get("id", "")
    from deps import get_distiller, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    opening = await asyncio.to_thread(
        distiller.generate_opening, req.card_json, req.user_role
    )
    return {"opening": opening}


@router.get("/cards/{card_id}/export")
async def export_card(
    card_id: str,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
    format: str = Query(default="tavern"),
    first_mes: str = Query(default=""),
) -> Response:
    """Export a character card in the requested format.

    ``format=tavern`` returns SillyTavern character-card-v2 JSON
    with ``Content-Disposition: attachment`` for direct download.
    """
    record = await storage.get_card(card_id)
    if not record:
        raise HTTPException(404, "Card not found")
    if record.get("user_id") != request.state.user.get("id", ""):
        raise HTTPException(403, "无权导出此角色卡")

    try:
        card = CharacterCard.model_validate_json(record["card_json"])
    except Exception as exc:
        print(f"[distill] Parse card {card_id} failed: {exc}")
        raise HTTPException(500, "Card data is corrupted") from exc

    if format == "tavern":
        body = export_tavern_json(card, first_mes)
        safe_name = quote(f"{card.name}_tavern.json")
        return Response(
            content=body,
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{safe_name}"
                ),
            },
        )

    raise HTTPException(400, f"Unsupported export format: {format}")


@router.get("/cards/by-text/{text_id}")
async def list_cards(
    text_id: str,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List all distilled character cards for a text."""
    user_id = request.state.user.get("id", "")
    try:
        return await storage.list_cards(text_id, user_id)
    except Exception as exc:
        print(f"[distill] List cards failed: {exc}")
        raise HTTPException(500, f"List cards failed: {exc}") from exc


@router.post("/start_session")
async def start_session(
    req: StartSessionRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
    sessions: dict[str, dict[str, Any]] = Depends(get_sessions),
) -> dict[str, Any]:
    """Create a chat session for an already-distilled card.

    Reads the text and card from storage, rebuilds RAG+ChatEngine,
    injects into the in-memory ``_sessions`` dict, persists the session
    record to SQLite, and returns the card data with ``session_id``.
    """
    user_id = request.state.user.get("id", "")
    from deps import get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    text_manager = get_text_manager(llm=per_user_llm)
    text_rec = await storage.get_text(req.text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")
    content = text_rec["content"]

    card_rec = await storage.get_card(req.card_id)
    if not card_rec:
        raise HTTPException(404, "Card not found")

    try:
        card = CharacterCard.model_validate_json(card_rec["card_json"])
    except Exception as exc:
        print(f"[distill] Parse card {req.card_id} failed: {exc}")
        raise HTTPException(500, "Card data is corrupted") from exc

    existing_cards = await storage.list_cards(req.text_id, user_id)
    all_characters: list[dict[str, Any]] = [
        {"name": c["name"], "aliases": []} for c in existing_cards
    ]

    try:
        rag = text_manager._get_or_build_rag(req.text_id, content, all_characters)
        session_id = await asyncio.to_thread(
            text_manager._create_session, content, card, all_characters, rag, req.card_id
        )
    except Exception as exc:
        print(f"[distill] Create session for card {req.card_id} failed: {exc}")
        raise HTTPException(500, f"Create session failed: {exc}") from exc

    try:
        await storage.save_session(session_id, req.card_id, "", "", user_id)
    except Exception as exc:
        print(f"[distill] Persist session failed (non-fatal): {exc}")

    # Load history from the most recent old session so the character remembers
    # previous conversations. Mem0 provides long-term memory, but short-term
    # context (the last ~30 messages) must come from engine.history.
    try:
        recent = await storage.get_recent_card_session(req.card_id, exclude_id=session_id)
        if recent:
            old_messages = await storage.get_messages(recent["id"])
            engine = sessions[session_id].get("engine")
            if engine and old_messages:
                for m in old_messages:
                    role = "assistant" if m["role"] == "char" else m["role"]
                    eng_role = "user" if role == "user" else "assistant"
                    engine.history.append({"role": eng_role, "content": m["content"]})
                print(f"[start_session] Loaded {len(old_messages)} history messages from session {recent['id']}")
    except Exception as exc:
        print(f"[start_session] Load history failed (non-fatal): {exc}")

    result = card.model_dump()
    result["session_id"] = session_id
    result["card_id"] = req.card_id
    return result



# ---- Legacy compat routes (/api/identify, /api/distill) ----

@legacy_router.post("/api/identify")
async def legacy_identify(
    req: IdentifyRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Legacy: identify characters from raw text body."""
    user_id = request.state.user.get("id", "")
    from deps import get_distiller, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    return await _do_identify(req.text, distiller)


@legacy_router.post("/api/distill")
async def legacy_distill(
    req: DistillRequest,
    request: Request,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Legacy: distill from raw text, auto-save text + persist card."""
    user_id = request.state.user.get("id", "")
    from deps import get_distiller, get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    distiller = get_distiller(llm=per_user_llm)
    text_manager = get_text_manager(llm=per_user_llm)
    if distiller is None or text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Text cannot be empty")
    try:
        upload_result = await text_manager.upload_text("legacy_upload.txt", text, user_id=user_id)
        text_id = upload_result["text_id"]
    except Exception as exc:
        print(f"[distill] Auto-save text failed: {exc}")
        raise HTTPException(500, f"Save text failed: {exc}") from exc

    char_name = await _resolve_character_name(text, req.character_name, distiller)

    try:
        return await text_manager.get_or_distill(text_id, char_name, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[distill] Distill failed: {exc}")
        raise HTTPException(500, f"Distill failed: {exc}") from exc
