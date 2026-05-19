"""Distillation: identify characters and generate character cards."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from deps import get_distiller, get_sessions, get_storage, get_text_manager
from core.distiller import Distiller
from core.export import export_tavern_json
from core.schema import CharacterCard
from core.text_manager import TextManager
from storage.sqlite_store import SQLiteStore

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
    storage: SQLiteStore = Depends(get_storage),
    distiller: Distiller = Depends(get_distiller),
) -> dict[str, Any]:
    """Identify characters from a text stored in the database."""
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
    storage: SQLiteStore = Depends(get_storage),
    distiller: Distiller = Depends(get_distiller),
    text_manager: TextManager = Depends(get_text_manager),
) -> dict[str, Any]:
    """Distill a character from a stored text, persist card + session."""
    if text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text_rec = await storage.get_text(req.text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    content = text_rec["content"]
    char_name = await _resolve_character_name(content, req.character_name, distiller)

    try:
        return await text_manager.get_or_distill(
            req.text_id, char_name, force=req.force
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[distill] Distill failed: {exc}")
        raise HTTPException(500, f"Distill failed: {exc}") from exc


def _next_piece(stream_obj):
    """Read next token from a generator; returns (token, done). Thread-safe."""
    try:
        return next(stream_obj), False
    except StopIteration:
        return "", True


@router.post("/run_stream")
async def distill_stream(
    req: DistillByIdRequest,
    storage: SQLiteStore = Depends(get_storage),
    distiller: Distiller = Depends(get_distiller),
    text_manager: TextManager = Depends(get_text_manager),
):
    """Stream distillation via SSE — no timeout, frontend renders tokens in real-time."""
    if text_manager is None or distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text_rec = await storage.get_text(req.text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    content = text_rec["content"]
    char_name = await _resolve_character_name(content, req.character_name, distiller)

    async def _event_gen():
        yield f"data: {json.dumps({'status': 'identifying'}, ensure_ascii=False)}\n\n"

        # Resolve aliases so Map phase catches all name variants
        aliases: list[str] = []
        try:
            chars = await asyncio.to_thread(distiller.identify_characters, content)
            for c in chars:
                if c.get("name") == char_name:
                    aliases = c.get("aliases", [])
                    break
        except Exception as exc:
            print(f"[distill] Identify aliases failed: {exc}")

        # Incremental distillation with aliases for broader chunk matching
        full = ""
        stream = distiller.distill_incremental_stream(content, char_name, aliases)
        while True:
            try:
                piece, done = await asyncio.to_thread(_next_piece, stream)
            except Exception as exc:
                print(f"[distill] Stream failed: {exc}")
                yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
                return
            if done:
                break
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
            result = await text_manager.save_distilled_card(req.text_id, card)
        except Exception as exc:
            print(f"[distill] Save card failed: {exc}")
            yield f"data: {json.dumps({'error': f'保存角色卡失败：{exc}'}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'done': True, **result}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.post("/reindex/{text_id}")
async def reindex_rag(
    text_id: str,
    storage: SQLiteStore = Depends(get_storage),
    distiller: Distiller = Depends(get_distiller),
    sessions: dict[str, dict[str, Any]] = Depends(get_sessions),
) -> dict[str, Any]:
    """Rebuild RAG indices for all in-memory sessions with character metadata.

    Reads the text from storage, runs identify_characters, then rebuilds
    each session's RAG index to include character tags so that
    ``character_name`` filtering works in subsequent chat queries.
    """
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
    storage: SQLiteStore = Depends(get_storage),
):
    result = await storage.update_card(card_id, req.card_json)
    return {"ok": True, "card": result}


class GenerateOpeningRequest(BaseModel):
    card_json: dict
    user_role: str

@router.post("/generate-opening")
async def generate_opening(
    req: GenerateOpeningRequest,
    distiller: Distiller = Depends(get_distiller),
):
    opening = await asyncio.to_thread(
        distiller.generate_opening, req.card_json, req.user_role
    )
    return {"opening": opening}


@router.get("/cards/{card_id}/export")
async def export_card(
    card_id: str,
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
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List all distilled character cards for a text."""
    try:
        return await storage.list_cards(text_id)
    except Exception as exc:
        print(f"[distill] List cards failed: {exc}")
        raise HTTPException(500, f"List cards failed: {exc}") from exc


@router.post("/start_session")
async def start_session(
    req: StartSessionRequest,
    storage: SQLiteStore = Depends(get_storage),
    text_manager: TextManager = Depends(get_text_manager),
    sessions: dict[str, dict[str, Any]] = Depends(get_sessions),
) -> dict[str, Any]:
    """Create a chat session for an already-distilled card.

    Reads the text and card from storage, rebuilds RAG+ChatEngine,
    injects into the in-memory ``_sessions`` dict, persists the session
    record to SQLite, and returns the card data with ``session_id``.
    """
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

    existing_cards = await storage.list_cards(req.text_id)
    all_characters: list[dict[str, Any]] = [
        {"name": c["name"], "aliases": []} for c in existing_cards
    ]

    try:
        rag = text_manager._get_or_build_rag(req.text_id, content, all_characters)
        session_id = await asyncio.to_thread(
            text_manager._create_session, content, card, all_characters, rag
        )
    except Exception as exc:
        print(f"[distill] Create session for card {req.card_id} failed: {exc}")
        raise HTTPException(500, f"Create session failed: {exc}") from exc

    try:
        await storage.save_session(session_id, req.card_id, "", "")
    except Exception as exc:
        print(f"[distill] Persist session failed (non-fatal): {exc}")

    result = card.model_dump()
    result["session_id"] = session_id
    result["card_id"] = req.card_id
    return result



# ---- Legacy compat routes (/api/identify, /api/distill) ----

@legacy_router.post("/api/identify")
async def legacy_identify(
    req: IdentifyRequest,
    distiller: Distiller = Depends(get_distiller),
) -> dict[str, Any]:
    """Legacy: identify characters from raw text body."""
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    return await _do_identify(req.text, distiller)


@legacy_router.post("/api/distill")
async def legacy_distill(
    req: DistillRequest,
    distiller: Distiller = Depends(get_distiller),
    text_manager: TextManager = Depends(get_text_manager),
) -> dict[str, Any]:
    """Legacy: distill from raw text, auto-save text + persist card."""
    if distiller is None or text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Text cannot be empty")

    try:
        text_id = await text_manager.upload_text("legacy_upload.txt", text)
    except Exception as exc:
        print(f"[distill] Auto-save text failed: {exc}")
        raise HTTPException(500, f"Save text failed: {exc}") from exc

    char_name = await _resolve_character_name(text, req.character_name, distiller)

    try:
        return await text_manager.get_or_distill(text_id, char_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[distill] Distill failed: {exc}")
        raise HTTPException(500, f"Distill failed: {exc}") from exc
