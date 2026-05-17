"""Text management: upload, list, delete."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from deps import get_storage, get_text_manager
from core.text_manager import TextManager
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/text", tags=["text"])

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
ALLOWED_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".log", ".pdf", ".docx"}


def _validate_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式。允许：{', '.join(sorted(ALLOWED_EXTENSIONS))}")


@router.post("/upload")
async def upload_text(
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    filename: str | None = Form(None),
    title: str = Form(""),
    description: str = Form(""),
    text_manager: TextManager = Depends(get_text_manager),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Accept a multipart file or text form field, parse format, save."""
    if file and file.filename:
        _validate_extension(file.filename)

        temp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{file.filename}"
        try:
            total_size = 0
            async with aiofiles.open(temp_path, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        await f.close()
                        os.unlink(temp_path)
                        raise HTTPException(413, "文件超过100MB限制")
                    await f.write(chunk)

            try:
                text_id = await text_manager.upload_text_from_file(
                    str(temp_path), file.filename, title, description
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        finally:
            if temp_path.exists():
                os.unlink(temp_path)

    elif text:
        content = text
        name = filename or "pasted_text.txt"
        try:
            text_id = await text_manager.upload_text(name, content, title, description)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        raise HTTPException(400, "Must provide file or text")

    try:
        record = await storage.get_text(text_id)
    except Exception as exc:
        print(f"[text] Get text record failed: {exc}")
        raise HTTPException(500, f"Get text record failed: {exc}") from exc

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
        content = t.pop("content", None) or ""
        t["preview"] = content[:300]
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
