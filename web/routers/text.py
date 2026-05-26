"""Text management: upload, list, delete."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from urllib.parse import quote

from deps import get_storage
from storage.sqlite_store import SQLiteStore
from limiter import limiter
from routers.auth import get_current_user
from pydantic import BaseModel


class CommentCreate(BaseModel):
    content: str
    parent_id: str = ""


class VisibilityUpdate(BaseModel):
    visibility: str

router = APIRouter(prefix="/api/text", tags=["text"])

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".log", ".pdf", ".docx"}


def _validate_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式。允许：{', '.join(sorted(ALLOWED_EXTENSIONS))}")


@router.post("/upload")
@limiter.limit("5/minute")
async def upload_text(
    request: Request,
    user: dict = Depends(get_current_user),
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    filename: str | None = Form(None),
    title: str = Form(""),
    description: str = Form(""),
    text_type: str = Form("story"),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, Any]:
    """Accept a multipart file or text form field, parse format, save."""
    user_id = user["id"]
    from deps import get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage)
    text_manager = get_text_manager(llm=per_user_llm)
    if text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    cleaning_stats = None

    if file and file.filename:
        _validate_extension(file.filename)
        safe_name = Path(file.filename).name  # strip directory traversal

        temp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
        try:
            total_size = 0
            async with aiofiles.open(temp_path, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        await f.close()
                        os.unlink(temp_path)
                        raise HTTPException(413, "文件超过10MB限制")
                    await f.write(chunk)

            try:
                result = await text_manager.upload_text_from_file(
                    str(temp_path), safe_name, title, description, text_type, user_id
                )
                text_id = result["text_id"]
                cleaning_stats = {k: result[k] for k in ("original_chars", "cleaned_chars")}
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        finally:
            if temp_path.exists():
                os.unlink(temp_path)

    elif text:
        content = text
        name = filename or "pasted_text.txt"
        try:
            result = await text_manager.upload_text(name, content, title, description, text_type, user_id)
            text_id = result["text_id"]
            cleaning_stats = {k: result[k] for k in ("original_chars", "cleaned_chars")}
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
        if cleaning_stats:
            record["cleaning_stats"] = cleaning_stats
    return record or {"id": text_id}


@router.get("/list")
async def list_texts(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List all uploaded texts (without full content body)."""
    user_id = user["id"]
    try:
        texts = await storage.list_texts(user_id)
    except Exception as exc:
        print(f"[text] List texts failed: {exc}")
        raise HTTPException(500, f"List texts failed: {exc}") from exc

    for t in texts:
        content = t.pop("content", None) or ""
        t["preview"] = content[:300]
    return texts


@router.post("/comments/{comment_id}/like")
async def toggle_comment_like(
    comment_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Toggle like on a comment."""
    return await storage.toggle_text_comment_like(comment_id, user["id"])


@router.delete("/comments/{comment_id}")
async def delete_text_comment(
    comment_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Delete your own comment."""
    ok = await storage.delete_text_comment(comment_id, user["id"])
    if not ok:
        raise HTTPException(404, "评论不存在或无权删除")
    return {"ok": True}


@router.delete("/{text_id}")
async def delete_text(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict[str, bool]:
    """Delete a text and its cascading cards/sessions."""
    text = await storage.get_text(text_id)
    if not text:
        raise HTTPException(404, "Text not found")
    if text.get("user_id") != user["id"]:
        raise HTTPException(403, "无权删除此文本")
    try:
        ok = await storage.delete_text(text_id)
    except Exception as exc:
        print(f"[text] Delete text failed: {exc}")
        raise HTTPException(500, f"Delete text failed: {exc}") from exc
    if not ok:
        raise HTTPException(404, "Text not found")
    return {"ok": True}


@router.get("/{text_id}/download-cleaned")
async def download_cleaned(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> Response:
    """Download cleaned plain text for chat-type imports."""
    text_rec = await storage.get_text(text_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")
    if text_rec.get("user_id") != user["id"]:
        raise HTTPException(403, "无权下载此文本")

    content = text_rec.get("content", "")
    title = text_rec.get("title", "text")
    safe_name = quote(f"{title}_cleaned.txt")

    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}",
        },
    )


@router.get("/{text_id}/detail")
async def get_text_detail(
    text_id: str,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get text metadata and comment count (no full content body)."""
    text = await storage.get_text(text_id)
    if not text:
        raise HTTPException(404, "Text not found")
    if text.get("user_id") != user["id"]:
        raise HTTPException(403, "无权查看此文本")
    # Count comments
    comments = await storage.get_text_comments(text_id, 1, 1)
    text.pop("content", None)
    text["comment_count"] = comments["total"]
    return {"text": text}


@router.get("/{text_id}/comments")
async def get_text_comments(
    text_id: str,
    page: int = 1,
    page_size: int = 20,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Get paginated comments for a text."""
    result = await storage.get_text_comments(text_id, page, page_size)
    # Mark liked comments
    all_ids = []
    for c in result["comments"]:
        all_ids.append(c["id"])
        all_ids.extend(r["id"] for r in c.get("replies", []))
    if all_ids:
        liked = await storage.get_liked_comment_ids(all_ids, user["id"])
        for c in result["comments"]:
            c["liked_by_me"] = c["id"] in liked
            for r in c.get("replies", []):
                r["liked_by_me"] = r["id"] in liked
    else:
        for c in result["comments"]:
            c["liked_by_me"] = False
            for r in c.get("replies", []):
                r["liked_by_me"] = False
    return result


@router.post("/{text_id}/comments")
async def add_text_comment(
    text_id: str,
    body: CommentCreate,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Add a comment to a text."""
    if not body.content.strip():
        raise HTTPException(400, "评论内容不能为空")
    comment = await storage.add_text_comment(
        text_id, user["id"], user["username"], body.content.strip(), body.parent_id,
    )
    comment["liked_by_me"] = False
    return {"comment": comment}


@router.patch("/{text_id}/visibility")
async def update_text_visibility(
    text_id: str,
    body: VisibilityUpdate,
    user: dict = Depends(get_current_user),
    storage: SQLiteStore = Depends(get_storage),
) -> dict:
    """Toggle a text's visibility between public and private."""
    if body.visibility not in ("public", "private"):
        raise HTTPException(400, "visibility 必须是 'public' 或 'private'")
    ok = await storage.update_text_visibility(text_id, user["id"], body.visibility)
    if not ok:
        raise HTTPException(404, "文本不存在或无权操作")
    return {"ok": True, "visibility": body.visibility}

