"""Text management: upload, list, delete."""

from __future__ import annotations

import logging

import asyncio
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from urllib.parse import quote

from adapters.llm_adapter import user_facing_error
from core.authz import fetch_for_actor
from core.trash_service import hard_delete, restore, soft_delete
from core import concurrency as C  # 派生与上下文传播（ctx_thread）
from core.scheduling import submit_to_main_loop
from deps import get_storage
from storage.base import StorageBase

from limiter import limiter
from routers.auth import get_current_user
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Upload task store (background upload progress) ─────
_upload_tasks: dict[str, dict[str, Any]] = {}
_upload_task_lock = threading.Lock()


def cancel_upload_tasks_by_text_id(text_id: str) -> int:
    """Cancel all in-flight upload tasks matching the given text_id.

    Returns the number of tasks cancelled.
    """
    count = 0
    with _upload_task_lock:
        for tid, task in list(_upload_tasks.items()):
            if task.get("text_id") == text_id and task.get("status") not in ("done", "error"):
                task.update({"status": "error", "message": "文本已删除，任务已取消"})
                count += 1
    return count


def _check_upload_cancelled(task_id: str) -> bool:
    """Return True if the upload task has been cancelled (status set to error)."""
    with _upload_task_lock:
        return _upload_tasks.get(task_id, {}).get("status") == "error"


def _run_upload_task(task_id: str, text_id: str, user_id: str) -> None:
    """Background: finish the upload task, update task progress."""
    try:
        from deps import get_distiller

        with _upload_task_lock:
            _upload_tasks[task_id] = {"status": "parsing", "progress_pct": 5, "message": "解析文件中…", "text_id": text_id, "user_id": user_id}

        # Run on the main event loop (run_coroutine_threadsafe) so the
        # global asyncpg pool stays on its home loop.
        async def _do():
            from deps import get_storage
            store = get_storage()

            text_rec = await store.get_text_owned(text_id, user_id)
            if not text_rec:
                with _upload_task_lock:
                    _upload_tasks[task_id].update({"status": "error", "message": "文本未找到"})
                return

            # Check cancellation before proceeding
            if _check_upload_cancelled(task_id):
                return

            with _upload_task_lock:
                _upload_tasks[task_id].update({"status": "done", "progress_pct": 100, "message": "上传完成", "text_id": text_id})

        submit_to_main_loop(_do())
    except Exception as exc:
        logger.error("Upload task %s failed: %s", task_id, exc, exc_info=True)
        with _upload_task_lock:
            _upload_tasks[task_id] = {
                "status": "error", "message": user_facing_error(exc), "user_id": user_id}


class CommentCreate(BaseModel):
    content: str
    parent_id: str = ""


class VisibilityUpdate(BaseModel):
    visibility: str

router = APIRouter(prefix="/api/text", tags=["text"])

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 30 * 1024 * 1024  # 30MB
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
    title: str = Form(""),
    description: str = Form(""),
    text_type: str = Form("story"),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Accept a multipart file, parse format, save.

    正文只能走 `file`。另有一条 urlencoded 的 `text` 字段（+ 给它起落盘名的 `filename`）
    已整条下掉 —— 成因与判据见 AGENTS.md 缺陷 40，形态锁见
    tests/test_text_failure_messages.py::test_l5_upload_route_accepts_no_form_text_payload。
    """
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
                        raise HTTPException(413, "文件体积超过 30MB 上限（内容字数上限另为 100 万字，PDF/Word 因格式体积更大）")
                    await f.write(chunk)

            try:
                result = await text_manager.upload_text_from_file(
                    str(temp_path), safe_name, title, description, text_type, user_id
                )
                text_id = result["text_id"]
                cleaning_stats = {k: result[k] for k in ("original_chars", "cleaned_chars")}
            except ValueError as exc:
                # **不迁到统一出口**（web/server.py）。判据是「**这段文字是为谁写的**」：
                # TextManager 抛的文案是本仓为人写的用户指令（如「文件编码无法识别，请另存为
                # UTF-8 后重新上传」），本就是上屏口径 —— 走 user_facing_error 会落到通用
                # 文案，**那是删信息，不是统一**。
                #
                # 判的对象是**抛出点那条 `raise` 的实参构成**，不是「异常类型是不是裸
                # ValueError」。缺陷 39 的 DOCX 双包证明按类型判必然判错：同一个
                # `except ValueError` 接住的，既有本仓人话、也有第三方库的异常原文
                # （后者已由 core/text_failure.py + 形态锁收口）。
                # 契约锁：tests/test_security_authz.py::TestErrorSanitization::test_10_value_error_400。
                raise HTTPException(400, str(exc)) from exc
        finally:
            # 清理**不许顶掉响应**：坏 PDF 的失败路径上，句柄由 pymupdf `Document.__init__`
            # 开出的 `fz_stream` 持有，只在那个异常对象被回收时才释放（上游失败分支既不
            # close、也不赋 `self.this` —— AGENTS.md 缺陷 47），于是 Windows 上这一次
            # unlink 会撞上 WinError 32；下一个事件循环迭代自愈、不累积（Linux 同形但允许
            # 删已打开的文件，无后果 —— 缺陷 48）。原先这行裸着，于是这个 cleanup 异常
            # 把本该抛出的 HTTPException(400) 顶成了 500、上屏文案也丢了。
            try:
                if temp_path.exists():
                    os.unlink(temp_path)
            except OSError as exc:
                logger.warning(
                    "[upload] 临时文件清理失败（不顶掉响应）：%s —— %r"
                    "；已知上游行为见 AGENTS.md 缺陷 47/48",
                    temp_path, exc,
                )

    else:
        raise HTTPException(400, "Must provide file")

    # Start background upload task for story/classic (progress polling)
    upload_task_id = ""
    if text_type in ("story", "classic"):
        upload_task_id = uuid.uuid4().hex[:12]
        thread = C.ctx_thread(  # context 传播点
            _run_upload_task,
            args=(upload_task_id, text_id, user_id),
            daemon=True,
        )
        thread.start()

    record = await storage.get_text_owned(text_id, user_id)

    response: dict[str, Any] = record or {"id": text_id}
    if record:
        record.pop("content", None)
        if cleaning_stats:
            record["cleaning_stats"] = cleaning_stats
    if upload_task_id:
        response["upload_task_id"] = upload_task_id
        with _upload_task_lock:
            _upload_tasks.setdefault(upload_task_id, {})
            _upload_tasks[upload_task_id]["text_id"] = text_id
            _upload_tasks[upload_task_id]["user_id"] = user_id
    return response


@router.get("/upload-task/{task_id}")
async def get_upload_task_status(
    task_id: str,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Poll upload preprocessing task status."""
    with _upload_task_lock:
        task = _upload_tasks.get(task_id)
    # 不存在与非属主同判 404、同一条文案（fail closed：条目缺 user_id 也拒）。此前非属主返
    # 403，与不存在可区分 —— 拿 403/404 就能枚举出哪些 task_id 真实存在（见 AGENTS.md §四
    # 「授权失败一律 404」）。修复前更早的问题是任何登录用户拿 task_id 就能读别人的上传任务
    # （含其 text_id / 进度 / 错误信息）。
    if task is None or task.get("user_id") != user["id"]:
        raise HTTPException(404, "Upload task not found")
    # Clean up done/error tasks after 5 minutes
    if task.get("status") in ("done", "error"):
        now = time.time()
        with _upload_task_lock:
            if "completed_at" not in task:
                task["completed_at"] = now
            elif now - task["completed_at"] > 300:
                _upload_tasks.pop(task_id, None)
    # user_id 只用于归属校验，不上屏
    return {k: v for k, v in task.items() if k != "user_id"}


@router.get("/list")
async def list_texts(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List all uploaded texts (without full content body)."""
    user_id = user["id"]
    texts = await storage.list_texts(user_id)

    for t in texts:
        content = t.pop("content", None) or ""
        t["preview"] = content[:300]
    return texts


@router.put("/{text_id}/cover")
@limiter.limit("10/minute")
async def update_text_cover(
    request: Request,
    text_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Update cover_data for a text (owner only)."""
    body = await request.json()
    cover = body.get("cover_data", "")
    if len(cover) > 300_000:
        raise HTTPException(400, "封面图过大，请压缩后上传")

    text = await storage.get_text_owned(text_id, user["id"])
    if not text:
        raise HTTPException(404, "文本不存在")

    await storage.update_text_cover(text_id, cover)
    return {"ok": True}


@router.get("/reading-progress/all")
@limiter.limit("30/minute")
async def get_all_reading_progress(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> list:
    """Get reading progress for all texts of the current user."""
    return await storage.get_all_reading_progress(user["id"])


@router.post("/comments/{comment_id}/like")
async def toggle_comment_like(
    comment_id: str,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Toggle like on a comment."""
    return await storage.toggle_text_comment_like(comment_id, user["id"])


@router.delete("/comments/{comment_id}")
async def delete_text_comment(
    comment_id: str,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Delete your own comment."""
    ok = await storage.delete_text_comment(comment_id, user["id"])
    if not ok:
        raise HTTPException(404, "评论不存在或无权删除")
    return {"ok": True}


@router.get("/{text_id}/deletion-impact")
async def get_text_deletion_impact(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get impact stats before deleting a text (cards, sessions, messages)."""
    # admin 跨属主查看删除影响：显式逃生口，受 roles.is_admin 保护（由 fetch_for_actor 裁决）。
    text = await fetch_for_actor(
        storage.get_text_owned, storage.get_text_unscoped, text_id, user, allow_admin=True)
    if not text:
        raise HTTPException(404, "Text not found")
    return await storage.get_text_deletion_impact(text_id, user["id"])


async def _cancel_text_tasks(record: dict) -> None:
    """取消本文本的在途上传/蒸馏任务（供 trash_service 的 before_mutation 钩子调用）。

    必须跑在**鉴权之后**：提前到路由开头，等于任何已登录用户拿别人的 text_id 就能掐掉
    对方的在途任务（缺陷 75）。钩子由 trash_service 在 _fetch 通过后才 await，顺序由
    那里保证。`import` 留在函数内是为避开 routers.distill 与本模块的循环导入。
    """
    from routers.distill import cancel_distill_tasks_by_text_id

    cancel_upload_tasks_by_text_id(record["id"])
    await cancel_distill_tasks_by_text_id(record["id"])


@router.delete("/{text_id}")
async def delete_text(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    keep_cards: bool = Query(False, description="保留角色卡（置 text_id=NULL 使之独立存活）"),
) -> dict[str, bool]:
    """Soft-delete a text (move to trash).

    keep_cards=True detaches cards from the text so they survive as standalone
    characters; the text goes to trash but cards+sessions remain accessible.
    """
    ok = await soft_delete("text", text_id, user, storage,
                           before_mutation=_cancel_text_tasks, keep_cards=keep_cards)
    if not ok:
        raise HTTPException(404, "Text not found")
    return {"ok": True}


@router.get("/trash")
@limiter.limit("30/minute")
async def list_trash(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> list[dict]:
    """List soft-deleted texts for the current user."""
    texts = await storage.get_deleted_texts(user["id"])
    for t in texts:
        t.pop("content", None)
    return texts


@router.post("/{text_id}/restore")
@limiter.limit("30/minute")
async def restore_text(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Restore a soft-deleted text."""
    ok = await restore("text", text_id, user, storage)
    if not ok:
        raise HTTPException(400, "文本未处于删除状态")
    return {"ok": True}


@router.delete("/{text_id}/permanent")
@limiter.limit("30/minute")
async def permanent_delete_text(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    keep_cards: bool = Query(False, description="保留角色卡（置 text_id=NULL 使之独立存活）"),
) -> dict:
    """Permanently delete a text and all associated data.

    keep_cards=True detaches cards (text_id→NULL) so they and their chat
    sessions survive as standalone characters.
    """
    ok = await hard_delete("text", text_id, user, storage,
                           before_mutation=_cancel_text_tasks, keep_cards=keep_cards)
    if not ok:
        raise HTTPException(404, "Text not found")
    return {"ok": True}


@router.get("/{text_id}/download-cleaned")
async def download_cleaned(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> Response:
    """Download cleaned plain text for chat-type imports."""
    text_rec = await storage.get_text_owned(text_id, user["id"])
    if not text_rec:
        raise HTTPException(404, "Text not found")

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
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get text metadata and comment count (no full content body)."""
    text = await storage.get_text_owned(text_id, user["id"])
    if not text:
        raise HTTPException(404, "Text not found")
    # Count comments
    comments = await storage.get_text_comments_owned(text_id, user["id"], 1, 1)
    text.pop("content", None)
    text["comment_count"] = comments["total"]
    return {"text": text}


@router.get("/{text_id}/comments")
async def get_text_comments(
    text_id: str,
    page: int = 1,
    page_size: int = 20,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get paginated comments for a text."""
    # 属主过滤在 SQL（JOIN texts）—— 非属主与「无评论」同判，都返回空页。
    result = await storage.get_text_comments_owned(text_id, user["id"], page, page_size)
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
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Add a comment to a text."""
    if not body.content.strip():
        raise HTTPException(400, "评论内容不能为空")
    comment = await storage.add_text_comment(
        text_id, user["id"], user["username"], body.content.strip(), body.parent_id,
    )
    comment["liked_by_me"] = False
    return {"comment": comment}


@router.get("/{text_id}/read")
@limiter.limit("60/minute")
async def read_text(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Return full text content for reading."""
    text = await storage.get_text_owned(text_id, user["id"])
    if not text:
        raise HTTPException(404, "Text not found")
    text["content"] = text.get("content", "")
    return {"text": text}


class ProgressUpdate(BaseModel):
    progress: float = 0
    scroll_position: int = 0


@router.post("/{text_id}/progress")
@limiter.limit("60/minute")
async def save_progress(
    text_id: str,
    body: ProgressUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Save reading progress for a text."""
    await storage.save_reading_progress(user["id"], text_id, body.progress, body.scroll_position)
    return {"ok": True}


@router.get("/{text_id}/progress")
@limiter.limit("60/minute")
async def get_progress(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Get reading progress for a text."""
    progress = await storage.get_reading_progress(user["id"], text_id)
    if not progress:
        return {"progress": 0, "scroll_position": 0}
    return progress


@router.patch("/{text_id}/visibility")
async def update_text_visibility(
    text_id: str,
    body: VisibilityUpdate,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """Toggle a text's visibility between public and private."""
    if body.visibility not in ("public", "private"):
        raise HTTPException(400, "visibility 必须是 'public' 或 'private'")
    ok = await storage.update_text_visibility(text_id, user["id"], body.visibility)
    if not ok:
        raise HTTPException(404, "文本不存在或无权操作")
    return {"ok": True, "visibility": body.visibility}

