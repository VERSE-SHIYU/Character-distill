"""Text management: upload, list, delete."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from deps import get_storage, get_text_manager
from core.text_manager import TextManager
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/text", tags=["text"])


@router.post("/upload")
async def upload_text(
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    filename: str | None = Form(None),
    text_manager: TextManager = Depends(get_text_manager),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Accept a multipart file or text form field, parse format, save."""
    if file and file.filename:
        try:
            raw = await file.read()
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, f"File encoding error: {exc}") from exc
        name = file.filename
    elif text:
        content = text
        name = filename or "pasted_text.txt"
    else:
        raise HTTPException(400, "Must provide file or text")

    try:
        text_id = await text_manager.upload_text(name, content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[text] Upload text failed: {exc}")
        raise HTTPException(500, f"Upload text failed: {exc}") from exc

    record = await storage.get_text(text_id)
    if record:
        record.pop("content", None)
    return record or {"id": text_id}


@router.get("/list")
async def list_texts(
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List all uploaded texts (without full content body)."""
    try:
        texts = await storage.list_texts()
    except Exception as exc:
        print(f"[text] List texts failed: {exc}")
        raise HTTPException(500, f"List texts failed: {exc}") from exc

    for t in texts:
        t.pop("content", None)
    return texts


@router.delete("/{text_id}")
async def delete_text(
    text_id: str,
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, bool]:
    """Delete a text and its cascading cards/sessions."""
    try:
        ok = await storage.delete_text(text_id)
    except Exception as exc:
        print(f"[text] Delete text failed: {exc}")
        raise HTTPException(500, f"Delete text failed: {exc}") from exc
    if not ok:
        raise HTTPException(404, "Text not found")
    return {"ok": True}
